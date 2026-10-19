"""Satellite pipeline tasks (queue `geo`): backfill, nightly incremental ingestion + loss alerts,
and seasonal composites.

Design (IMPLEMENTATION_PLAN Phase 4, RESEARCH_NOTES Q2):
- Bounded memory: scenes are ingested ONE at a time (polygon-clipped COG reads, a few MB each);
  composites are computed chunk-wise with dask (2 threads, 256 px chunks).
- Idempotent: a scene is stored once per project (unique (project_id, item_id) + ON CONFLICT DO
  NOTHING); scenes already stored are skipped before any pixel is read. A failed scene is not
  stored, so the next run retries it. Alerts are unique per (project, kind, metric, date).
- Every run is recorded in `analysis_runs` (params, progress, failures, outputs) for provenance.

Each task is an async core function with injected dependencies (sessionmaker, scene source,
storage) and a thin Celery wrapper.
"""

import asyncio
import tempfile
import uuid
import warnings
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from celery import shared_task
from geoalchemy2.shape import to_shape
from shapely.geometry.base import BaseGeometry
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aqua_core import audit
from aqua_core.db import create_engine, session_factory
from aqua_core.logging import get_logger
from aqua_core.models import (
    Alert,
    AlertKind,
    AnalysisKind,
    AnalysisRun,
    AnalysisStatus,
    Project,
    ProjectStatus,
    SceneObservation,
)
from aqua_core.models._types import utcnow
from aqua_core.settings import get_settings
from aqua_core.storage import ObjectStorage, S3ObjectStorage
from aqua_worker.alerts import (
    DEFAULT_RULES,
    METRIC_NDVI_P50,
    AlertRules,
    Observation,
    detect_vegetation_loss,
)
from aqua_worker.scenes import SceneRef, SceneSource, StacSceneSource

log = get_logger("aqua_worker.geo")

# Projects monitored by the nightly job.
ACTIVE_STATUSES = (
    ProjectStatus.SUBMITTED,
    ProjectStatus.BASELINE_READY,
    ProjectStatus.REGISTERED,
    ProjectStatus.MONITORING,
)
DRY_SEASON = "Nov–Mar"
COG_CONTENT_TYPE = "image/tiff; application=geotiff; profile=cloud-optimized"
COMPOSITE_INDICES = ("ndvi", "evi", "savi", "mvi", "ndwi", "mndwi", "ndmi", "cmri", "ndre")
PERCENTILE_INDICES = ("ndvi", "mvi")
MAX_RECORDED_FAILURES = 50
GEO_THREADS = 2  # dask threads; one geo worker with concurrency 1 on the free VM


def season_label(start_year: int) -> str:
    """2025 → "2025-26" (dry season 1 Nov 2025 – 31 Mar 2026)."""
    return f"{start_year}-{(start_year + 1) % 100:02d}"


def season_window(start_year: int) -> tuple[date, date]:
    return date(start_year, 11, 1), date(start_year + 1, 3, 31)


def composite_key(project_id: uuid.UUID, start_year: int) -> str:
    return f"projects/{project_id}/composites/{season_label(start_year)}.tif"


# ---------------------------------------------------------------- run bookkeeping


async def _start_run(
    session: AsyncSession,
    project_id: uuid.UUID,
    kind: AnalysisKind,
    params: dict[str, Any],
    run_id: uuid.UUID | None,
) -> AnalysisRun:
    run = await session.get(AnalysisRun, run_id) if run_id else None
    if run is None:
        run = AnalysisRun(id=run_id or uuid.uuid4(), project_id=project_id, kind=kind)
        session.add(run)
    elif run.project_id != project_id or run.kind != kind:
        raise ValueError(f"analysis run {run_id} does not belong to this {kind} job")
    run.params = {**(run.params or {}), **params}
    run.status = AnalysisStatus.RUNNING
    run.started_at = utcnow()
    run.finished_at = None
    run.error = None
    run.outputs = {}
    await session.commit()
    return run


async def _finish_run(
    session: AsyncSession,
    run: AnalysisRun,
    status: AnalysisStatus,
    outputs: dict[str, Any],
    error: str | None = None,
) -> None:
    run.status = status
    run.outputs = outputs
    run.error = error
    run.finished_at = utcnow()
    await audit.record(
        session,
        actor_id=None,
        action=f"analysis.{status.value}",
        entity_type="analysis_run",
        entity_id=run.id,
        data={
            "project_id": str(run.project_id),
            "kind": run.kind.value,
            **{k: v for k, v in outputs.items() if isinstance(v, (int, str))},
        },
    )
    await session.commit()


async def _fail_run(session: AsyncSession, run_id: uuid.UUID, exc: BaseException) -> None:
    """Record a failure even if the session's transaction was broken by the error."""
    await session.rollback()
    run = await session.get(AnalysisRun, run_id, populate_existing=True)
    if run is not None:
        await _finish_run(session, run, AnalysisStatus.FAILED, {}, error=_err(exc))


async def _project_geometry(session: AsyncSession, project_id: uuid.UUID) -> BaseGeometry:
    project = await session.get(Project, project_id)
    if project is None:
        raise LookupError(f"project {project_id} not found")
    geometry: BaseGeometry = to_shape(project.boundary)
    return geometry


# ---------------------------------------------------------------- per-scene ingestion


def _scene_statistics(source: SceneSource, scene: SceneRef, geometry: BaseGeometry) -> Any:
    """Load ONE scene clipped to the polygon and reduce it to zonal statistics (thread)."""
    import dask

    from aqua_geo.timeseries import scene_statistics

    loaded = source.load([scene], geometry)
    with dask.config.set(scheduler="threads", num_workers=GEO_THREADS):
        ds = loaded.ds.load()
        inside = loaded.inside.load()
    if ds.sizes.get("time", 1) != 1:
        raise ValueError(f"{scene.item_id}: expected one time step, got {ds.sizes['time']}")
    return scene_statistics(ds, inside)


@dataclass
class IngestResult:
    run_id: uuid.UUID
    status: AnalysisStatus
    outputs: dict[str, Any]
    new_dates: list[date]


async def ingest_project(
    sessionmaker: async_sessionmaker[AsyncSession],
    source: SceneSource,
    project_id: uuid.UUID,
    *,
    start: date,
    end: date,
    kind: AnalysisKind = AnalysisKind.BACKFILL,
    run_id: uuid.UUID | None = None,
    max_cloud: float = 80.0,
) -> IngestResult:
    """Search scenes in [start, end] and store per-scene zonal statistics, one scene at a time,
    skipping scenes already stored for this project."""
    params = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "max_cloud_pct": max_cloud,
        "provider": source.name,
    }
    async with sessionmaker() as session:
        geometry = await _project_geometry(session, project_id)
        run = await _start_run(session, project_id, kind, params, run_id)
        try:
            scenes = await asyncio.to_thread(
                source.search, geometry, start, end, max_cloud=max_cloud
            )
        except Exception as exc:
            await _fail_run(session, run.id, exc)
            raise

        stored = set(
            (
                await session.scalars(
                    select(SceneObservation.item_id).where(
                        SceneObservation.project_id == project_id
                    )
                )
            ).all()
        )
        todo = [s for s in scenes if s.item_id not in stored]
        outputs: dict[str, Any] = {
            "scenes_found": len(scenes),
            "already_ingested": len(scenes) - len(todo),
            "to_ingest": len(todo),
            "ingested": 0,
            "failed_count": 0,
            "failed": [],
        }
        run.outputs = dict(outputs)
        await session.commit()
        log.info("geo.ingest.start", project_id=str(project_id), run_id=str(run.id), **params)

        new_dates: list[date] = []
        for scene in todo:
            try:
                stats = await asyncio.to_thread(_scene_statistics, source, scene, geometry)
            except Exception as exc:
                log.warning("geo.scene.failed", item_id=scene.item_id, error=_err(exc))
                outputs["failed_count"] += 1
                if len(outputs["failed"]) < MAX_RECORDED_FAILURES:
                    outputs["failed"].append({"item_id": scene.item_id, "error": _err(exc)})
            else:
                acquired = scene.acquired_at.astimezone(UTC)
                inserted = await session.scalar(
                    insert(SceneObservation)
                    .values(
                        id=uuid.uuid4(),
                        project_id=project_id,
                        run_id=run.id,
                        item_id=scene.item_id,
                        acquired_at=acquired,
                        acquired_on=acquired.date(),
                        cloud_cover=scene.cloud_cover,
                        clear_fraction=stats.clear_fraction,
                        clear_pixels=stats.clear_pixels,
                        polygon_pixels=stats.polygon_pixels,
                        index_stats=stats.index_stats,
                        provider=scene.provider,
                        processing_baseline=scene.processing_baseline,
                    )
                    .on_conflict_do_nothing(constraint="uq_scene_observation_item")
                    .returning(SceneObservation.id)
                )
                if inserted is not None:  # None: a concurrent run stored it first
                    outputs["ingested"] += 1
                    new_dates.append(acquired.date())
            # progress is committed after every scene, so a crash loses at most one scene
            run.outputs = {**outputs, "failed": list(outputs["failed"])}
            await session.commit()

        if new_dates:
            outputs["first_new_acquired"] = min(new_dates).isoformat()
            outputs["last_new_acquired"] = max(new_dates).isoformat()
        failed_all = bool(todo) and outputs["ingested"] == 0 and outputs["failed_count"] > 0
        status = AnalysisStatus.FAILED if failed_all else AnalysisStatus.SUCCEEDED
        await _finish_run(
            session,
            run,
            status,
            outputs,
            error="every scene failed to load" if failed_all else None,
        )
        log.info("geo.ingest.done", run_id=str(run.id), status=status.value, **_counts(outputs))
        return IngestResult(run.id, status, outputs, new_dates)


def _err(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:500]


def _counts(outputs: dict[str, Any]) -> dict[str, int]:
    return {k: v for k, v in outputs.items() if isinstance(v, int)}


# ---------------------------------------------------------------- alerts


async def detect_alerts(
    sessionmaker: async_sessionmaker[AsyncSession],
    project_id: uuid.UUID,
    *,
    since: date | None,
    run_id: uuid.UUID | None = None,
    rules: AlertRules = DEFAULT_RULES,
) -> list[uuid.UUID]:
    """Run vegetation-loss detection over the project's time series; store new alerts."""
    async with sessionmaker() as session:
        rows = (
            await session.execute(
                select(
                    SceneObservation.acquired_on,
                    SceneObservation.clear_fraction,
                    SceneObservation.index_stats["ndvi"]["p50"].as_float(),
                    SceneObservation.item_id,
                )
                .where(SceneObservation.project_id == project_id)
                .order_by(SceneObservation.acquired_on, SceneObservation.item_id)
            )
        ).all()
        existing = set(
            (
                await session.scalars(
                    select(Alert.observed_on).where(
                        Alert.project_id == project_id,
                        Alert.kind == AlertKind.VEGETATION_LOSS,
                        Alert.metric == METRIC_NDVI_P50,
                    )
                )
            ).all()
        )
        observations = [Observation(r[0], r[1], r[2], r[3]) for r in rows]
        candidates = detect_vegetation_loss(observations, rules, since=since, existing=existing)
        created: list[uuid.UUID] = []
        for c in candidates:
            alert_id = await session.scalar(
                insert(Alert)
                .values(
                    id=uuid.uuid4(),
                    project_id=project_id,
                    run_id=run_id,
                    kind=c.kind,
                    metric=c.metric,
                    observed_on=c.observed_on,
                    observed_value=c.observed_value,
                    expected=c.expected,
                    severity=c.severity,
                    message=c.message,
                )
                .on_conflict_do_nothing(constraint="uq_alert_observation")
                .returning(Alert.id)
            )
            if alert_id is None:
                continue
            created.append(alert_id)
            await audit.record(
                session,
                actor_id=None,
                action="alert.raised",
                entity_type="project",
                entity_id=project_id,
                data={
                    "alert_id": str(alert_id),
                    "kind": c.kind.value,
                    "observed_on": c.observed_on.isoformat(),
                    "severity": c.severity.value,
                    "observed_value": c.observed_value,
                },
            )
        await session.commit()
        if created:
            log.warning("geo.alerts.raised", project_id=str(project_id), count=len(created))
        return created


# ---------------------------------------------------------------- nightly


async def ingest_new_scenes(
    sessionmaker: async_sessionmaker[AsyncSession],
    source: SceneSource,
    *,
    today: date,
    backfill_start: date,
    max_cloud: float = 80.0,
    rules: AlertRules = DEFAULT_RULES,
) -> dict[str, Any]:
    """For every active project: ingest scenes acquired since its latest observation (or since
    `backfill_start` if it has none), then run loss detection on the new observations."""
    async with sessionmaker() as session:
        rows = (
            await session.execute(
                select(Project.id, func.max(SceneObservation.acquired_on))
                .outerjoin(SceneObservation, SceneObservation.project_id == Project.id)
                .where(Project.status.in_(ACTIVE_STATUSES))
                .group_by(Project.id)
                .order_by(Project.id)
            )
        ).all()

    summary: dict[str, Any] = {"projects": len(rows), "ingested": 0, "alerts": 0, "failed": []}
    for project_id, latest in rows:
        # Inclusive start: a second tile of the latest day may have been published later;
        # already-stored items are skipped by id.
        start = latest or backfill_start
        try:
            result = await ingest_project(
                sessionmaker,
                source,
                project_id,
                start=start,
                end=today,
                kind=AnalysisKind.INCREMENTAL,
                max_cloud=max_cloud,
            )
            summary["ingested"] += result.outputs["ingested"]
            if result.new_dates:
                alerts = await detect_alerts(
                    sessionmaker,
                    project_id,
                    since=min(result.new_dates),
                    run_id=result.run_id,
                    rules=rules,
                )
                summary["alerts"] += len(alerts)
        except Exception as exc:  # one broken project must not stop the others
            log.error("geo.nightly.project_failed", project_id=str(project_id), error=_err(exc))
            summary["failed"].append({"project_id": str(project_id), "error": _err(exc)})
    return summary


# ---------------------------------------------------------------- composites


@dataclass
class CompositeProduct:
    cog: bytes
    bands: list[str]
    zonal: dict[str, Any]


def _compute_composite(
    source: SceneSource, scenes: Sequence[SceneRef], geometry: BaseGeometry, min_clear: int
) -> CompositeProduct:
    """Dry-season median + percentile composite → COG bytes (runs in a thread)."""
    import dask
    import xarray as xr

    from aqua_geo.cog import write_cog
    from aqua_geo.composite import median_composite, percentile_composite, zonal_summary
    from aqua_geo.indices import add_indices

    loaded = source.load(scenes, geometry, chunk=256)
    ds = add_indices(loaded.ds)
    bands = [str(v) for v in loaded.ds.data_vars if v != "clear"]
    ds = ds[[*bands, *COMPOSITE_INDICES, "clear"]]
    if ds.chunks:  # dask-backed: one time chunk per 256 px tile, so memory stays bounded
        ds = ds.chunk({"time": -1})
    inside = loaded.inside
    with (
        dask.config.set(scheduler="threads", num_workers=GEO_THREADS),
        warnings.catch_warnings(),
    ):
        # pixels outside the polygon / never clear are all-NaN along time: expected, not an error
        warnings.filterwarnings("ignore", "All-NaN slice", RuntimeWarning)
        median = median_composite(ds, min_clear=min_clear)
        pct = percentile_composite(ds, PERCENTILE_INDICES)
        out = xr.merge([median, pct]).where(inside)
        out["clear_count"] = median["clear_count"].where(inside, 0).astype("float32")
        out = out.load()

    names = [*COMPOSITE_INDICES, *bands, *pct.data_vars, "clear_count"]
    with tempfile.TemporaryDirectory(prefix="aqua-composite-") as tmp:
        path = Path(tmp) / "composite.tif"
        written = write_cog(out, path, epsg=loaded.epsg, variables=[str(n) for n in names])
        data = path.read_bytes()
    zonal = {name: zonal_summary(out[name]) for name in (*COMPOSITE_INDICES, *pct.data_vars)}
    zonal["clear_count"] = zonal_summary(out["clear_count"].where(inside))
    return CompositeProduct(cog=data, bands=written, zonal=zonal)


async def build_composite(
    sessionmaker: async_sessionmaker[AsyncSession],
    source: SceneSource,
    storage: ObjectStorage,
    project_id: uuid.UUID,
    season_start_year: int,
    *,
    run_id: uuid.UUID | None = None,
    max_cloud: float = 40.0,
    min_clear: int = 3,
) -> dict[str, Any]:
    """Dry-season (Nov–Mar) composite for one season, stored as a COG in object storage."""
    start, end = season_window(season_start_year)
    label = season_label(season_start_year)
    params = {
        "season": label,
        "season_start_year": season_start_year,
        "months": DRY_SEASON,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "max_cloud_pct": max_cloud,
        "min_clear_observations": min_clear,
        "provider": source.name,
    }
    async with sessionmaker() as session:
        geometry = await _project_geometry(session, project_id)
        run = await _start_run(session, project_id, AnalysisKind.COMPOSITE, params, run_id)
        try:
            scenes = await asyncio.to_thread(
                source.search, geometry, start, end, max_cloud=max_cloud
            )
            if not scenes:
                raise LookupError(f"no Sentinel-2 scenes found for dry season {label}")
            product = await asyncio.to_thread(
                _compute_composite, source, scenes, geometry, min_clear
            )
            key = composite_key(project_id, season_start_year)
            sha256 = await asyncio.to_thread(storage.put_bytes, key, product.cog, COG_CONTENT_TYPE)
        except Exception as exc:
            await _fail_run(session, run.id, exc)
            raise
        outputs = {
            "cog_key": key,
            "sha256": sha256,
            "size_bytes": len(product.cog),
            "bands": product.bands,
            "scene_count": len(scenes),
            "item_ids": [s.item_id for s in scenes],
            "zonal": product.zonal,
        }
        await _finish_run(session, run, AnalysisStatus.SUCCEEDED, outputs)
        return {"run_id": str(run.id), **outputs}


# ---------------------------------------------------------------- Celery wrappers


async def _with_db[T](job: Callable[[async_sessionmaker[AsyncSession]], Awaitable[T]]) -> T:
    engine = create_engine(get_settings().database_url)
    try:
        return await job(session_factory(engine))
    finally:
        await engine.dispose()


def _today() -> date:
    return datetime.now(UTC).date()


@shared_task(name="aqua.geo.backfill_project", soft_time_limit=6 * 3600, time_limit=6 * 3600 + 300)
def backfill_project(
    project_id: str, start_date: str | None = None, run_id: str | None = None
) -> dict[str, Any]:
    settings = get_settings()
    start = date.fromisoformat(start_date) if start_date else settings.geo_backfill_start
    result = asyncio.run(
        _with_db(
            lambda sm: ingest_project(
                sm,
                StacSceneSource(),
                uuid.UUID(project_id),
                start=start,
                end=_today(),
                kind=AnalysisKind.BACKFILL,
                run_id=uuid.UUID(run_id) if run_id else None,
                max_cloud=settings.geo_max_scene_cloud_pct,
            )
        )
    )
    return {"run_id": str(result.run_id), "status": result.status.value, **_counts(result.outputs)}


@shared_task(name="aqua.geo.ingest_new_scenes", soft_time_limit=6 * 3600, time_limit=6 * 3600 + 300)
def ingest_new_scenes_task() -> dict[str, Any]:
    settings = get_settings()
    return asyncio.run(
        _with_db(
            lambda sm: ingest_new_scenes(
                sm,
                StacSceneSource(),
                today=_today(),
                backfill_start=settings.geo_backfill_start,
                max_cloud=settings.geo_max_scene_cloud_pct,
            )
        )
    )


@shared_task(name="aqua.geo.build_composite", soft_time_limit=2 * 3600, time_limit=2 * 3600 + 300)
def build_composite_task(
    project_id: str, season_start_year: str | int, run_id: str | None = None
) -> dict[str, Any]:
    settings = get_settings()
    return asyncio.run(
        _with_db(
            lambda sm: build_composite(
                sm,
                StacSceneSource(),
                S3ObjectStorage(settings),
                uuid.UUID(project_id),
                int(season_start_year),
                run_id=uuid.UUID(run_id) if run_id else None,
                max_cloud=settings.geo_composite_max_cloud_pct,
            )
        )
    )
