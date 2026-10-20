"""Satellite pipeline tasks (queue `geo`): backfill, nightly incremental ingestion + loss alerts,
and seasonal composites.

Design (IMPLEMENTATION_PLAN Phase 4, RESEARCH_NOTES Q2):
- Bounded memory: scenes are ingested ONE at a time (polygon-clipped COG reads, a few MB each);
  composites are computed chunk-wise with dask (2 threads, 256 px chunks).
- Idempotent: a scene is stored once per project (unique (project_id, item_id) + ON CONFLICT DO
  NOTHING); scenes already stored are skipped before any pixel is read. A failed scene is not
  stored, so the next run retries it. Alerts are unique per (project, kind, metric, date).
- Every run is recorded in `analysis_runs` (params, progress, failures, outputs) for provenance.
- Provider fallback: the scene source tries STAC catalogues in order (GEO_STAC_PROVIDERS); each
  stored scene names the provider that served it, and the run outputs count scenes per provider and
  list every fallback. The same acquisition served by two providers (different item ids) is
  detected by platform + MGRS tile + date and stored once.
- Sentinel-1 RTC (optional `sar` source): acquisitions in the monsoon months, or within ±N days of a
  Sentinel-2 scene below the clear-fraction floor, are reduced to per-polygon backscatter
  statistics and stored under `outputs["sentinel1"]` (no table of their own yet).

Each task is an async core function with injected dependencies (sessionmaker, scene source,
storage) and a thin Celery wrapper.
"""

import asyncio
import tempfile
import uuid
import warnings
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
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
from aqua_worker.scenes import (
    SarRef,
    SarSource,
    SceneRef,
    SceneSource,
    StacSarSource,
    StacSceneSource,
)

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
class ServedScene:
    ref: SceneRef  # the item actually read (may come from a fallback provider)
    stats: Any
    fallbacks: list[dict[str, str]]


def _serve_scene(source: SceneSource, scene: SceneRef, geometry: BaseGeometry) -> ServedScene:
    """`_scene_statistics`, retrying the same acquisition on the next provider when a read fails
    with a timeout, 5xx or auth error (thread)."""
    from aqua_geo.fallback import describe

    ref = scene
    fallbacks: list[dict[str, str]] = []
    while True:
        try:
            return ServedScene(ref, _scene_statistics(source, ref, geometry), fallbacks)
        except Exception as exc:
            alternative = source.fallback_for(ref, geometry, exc)
            if alternative is None:
                raise
            fallbacks.append(
                {
                    "item_id": ref.item_id,
                    "from": ref.provider,
                    "to": alternative.provider,
                    "served_item_id": alternative.item_id,
                    "error": describe(exc),
                }
            )
            ref = alternative


def _sar_statistics(source: SarSource, acquisition: SarRef, geometry: BaseGeometry) -> Any:
    """Load ONE Sentinel-1 pass clipped to the polygon and reduce it to statistics (thread)."""
    import dask

    from aqua_geo.sentinel1 import acquisition_statistics

    loaded = source.load(acquisition, geometry)
    with dask.config.set(scheduler="threads", num_workers=GEO_THREADS):
        ds = loaded.ds.load()
        inside = loaded.inside.load()
    if ds.sizes.get("time", 1) != 1:
        raise ValueError(f"{acquisition.key}: expected one time step, got {ds.sizes['time']}")
    return acquisition_statistics(ds, inside)


@dataclass(frozen=True)
class Sentinel1Policy:
    """When Sentinel-1 is ingested alongside Sentinel-2 (settings GEO_S1_*)."""

    monsoon_months: tuple[int, ...] = (6, 7, 8, 9)
    clear_floor: float = 0.3
    window_days: int = 6

    @classmethod
    def from_settings(cls, settings: Any) -> "Sentinel1Policy | None":
        if not settings.geo_s1_enabled:
            return None
        return cls(
            monsoon_months=tuple(settings.geo_s1_monsoon_months),
            clear_floor=settings.geo_s1_clear_floor,
            window_days=settings.geo_s1_window_days,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "monsoon_months": list(self.monsoon_months),
            "clear_floor": self.clear_floor,
            "window_days": self.window_days,
        }


def select_sar(
    acquisitions: Sequence[SarRef],
    policy: Sentinel1Policy,
    low_clear: Sequence[tuple[date, str]],
) -> list[tuple[SarRef, str]]:
    """Acquisitions to ingest, each with its trigger: "monsoon" when acquired in a monsoon month,
    else "low_clear:<S2 item id>" when within ±window_days of a Sentinel-2 scene whose clear
    fraction is below the floor. `low_clear` is [(S2 acquisition date, S2 item id)]."""
    window = timedelta(days=policy.window_days)
    selected: list[tuple[SarRef, str]] = []
    for acq in acquisitions:
        day = acq.acquired_at.astimezone(UTC).date()
        if day.month in policy.monsoon_months:
            selected.append((acq, "monsoon"))
            continue
        near = [(abs(day - d), item_id) for d, item_id in low_clear if abs(day - d) <= window]
        if near:
            selected.append((acq, f"low_clear:{min(near)[1]}"))
    return selected


async def _recorded_sar_keys(
    session: AsyncSession, project_id: uuid.UUID, run_id: uuid.UUID
) -> set[str]:
    """Sentinel-1 acquisitions already stored in earlier runs' outputs for this project."""
    rows = (
        await session.scalars(
            select(AnalysisRun.outputs["sentinel1"]["observations"]).where(
                AnalysisRun.project_id == project_id, AnalysisRun.id != run_id
            )
        )
    ).all()
    return {
        str(obs["key"])
        for observations in rows
        if isinstance(observations, list)
        for obs in observations
        if isinstance(obs, dict) and "key" in obs
    }


async def _ingest_sar(
    session: AsyncSession,
    run: AnalysisRun,
    outputs: dict[str, Any],
    source: SarSource,
    policy: Sentinel1Policy,
    geometry: BaseGeometry,
    start: date,
    end: date,
) -> None:
    """Sentinel-1 pass of `ingest_project`: progress is committed after every acquisition.
    Failures are recorded in `outputs["sentinel1"]`; they never fail the Sentinel-2 run.

    Low-clear triggers come from every stored Sentinel-2 scene in the window (this run's and
    earlier ones), so enabling Sentinel-1 on a project with an existing series still fills its
    cloudy gaps on the next backfill."""
    from aqua_geo.fallback import describe
    from aqua_geo.sentinel1 import SPECKLE_NOTE

    sar: dict[str, Any] = {
        "provider": source.name,
        "collection": source.collection,
        "units": "terrain-corrected gamma0; *_db = 10*log10(linear); rvi = 4*VH/(VV+VH) linear",
        "speckle_filter": SPECKLE_NOTE,
        "policy": policy.as_dict(),
        "found": 0,
        "selected": 0,
        "already_recorded": 0,
        "ingested": 0,
        "failed_count": 0,
        "failed": [],
        "observations": [],
    }
    outputs["sentinel1"] = sar
    try:
        found = await asyncio.to_thread(source.search, geometry, start, end)
    except Exception as exc:
        log.warning("geo.sar.search_failed", run_id=str(run.id), error=_err(exc))
        sar["error"] = describe(exc)
        return
    recorded = await _recorded_sar_keys(session, run.project_id, run.id)
    margin = timedelta(days=policy.window_days)
    low_clear = [
        (r.acquired_on, r.item_id)
        for r in (
            await session.execute(
                select(SceneObservation.acquired_on, SceneObservation.item_id)
                .where(
                    SceneObservation.project_id == run.project_id,
                    SceneObservation.acquired_on.between(start - margin, end + margin),
                    SceneObservation.clear_fraction < policy.clear_floor,
                )
                .order_by(SceneObservation.acquired_on, SceneObservation.item_id)
            )
        ).all()
    ]
    chosen = select_sar(found, policy, low_clear)
    todo = [(a, why) for a, why in chosen if a.key not in recorded]
    sar.update(found=len(found), selected=len(chosen), already_recorded=len(chosen) - len(todo))
    for acq, trigger in todo:
        try:
            st = await asyncio.to_thread(_sar_statistics, source, acq, geometry)
        except Exception as exc:
            log.warning("geo.sar.failed", key=acq.key, error=_err(exc))
            sar["failed_count"] += 1
            if len(sar["failed"]) < MAX_RECORDED_FAILURES:
                sar["failed"].append({"key": acq.key, "error": _err(exc)})
        else:
            sar["observations"].append(
                {
                    "key": acq.key,
                    "item_ids": list(acq.item_ids),
                    "acquired_at": acq.acquired_at.astimezone(UTC).isoformat(),
                    "acquired_on": acq.acquired_at.astimezone(UTC).date().isoformat(),
                    "platform": acq.platform,
                    "orbit_state": acq.orbit_state,
                    "relative_orbit": acq.relative_orbit,
                    "provider": acq.provider,
                    "trigger": trigger,
                    "polygon_pixels": st.polygon_pixels,
                    "valid_pixels": st.valid_pixels,
                    "valid_fraction": st.valid_fraction,
                    "stats": st.stats,
                }
            )
            sar["ingested"] += 1
        run.outputs = _snapshot(outputs)
        await session.commit()


def _snapshot(outputs: dict[str, Any]) -> dict[str, Any]:
    """A copy SQLAlchemy sees as a new value (JSONB is not mutation-tracked)."""
    import copy

    return copy.deepcopy(outputs)


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
    sar_source: SarSource | None = None,
    sar_policy: Sentinel1Policy | None = None,
) -> IngestResult:
    """Search scenes in [start, end] and store per-scene zonal statistics, one scene at a time,
    skipping scenes already stored for this project. With `sar_source` and `sar_policy`, also
    ingest the Sentinel-1 acquisitions the policy selects (see `select_sar`)."""
    from aqua_geo.sentinel2 import scene_key

    sar_enabled = sar_source is not None and sar_policy is not None
    params: dict[str, Any] = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "max_cloud_pct": max_cloud,
        "provider": source.name,
        "sentinel1": sar_policy.as_dict() if sar_enabled and sar_policy else None,
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

        stored_rows = (
            await session.execute(
                select(
                    SceneObservation.item_id,
                    SceneObservation.acquired_on,
                    SceneObservation.provider,
                ).where(SceneObservation.project_id == project_id)
            )
        ).all()
        stored = {r.item_id for r in stored_rows}
        # same acquisition already stored from ANOTHER provider (item ids differ by catalogue)
        stored_by: dict[str, set[str]] = {}
        for r in stored_rows:
            stored_by.setdefault(scene_key(r.item_id, r.acquired_on), set()).add(r.provider)

        def served_elsewhere(s: SceneRef) -> bool:
            key = scene_key(s.item_id, s.acquired_at.astimezone(UTC).date())
            return bool(stored_by.get(key, set()) - {s.provider})

        todo = [s for s in scenes if s.item_id not in stored and not served_elsewhere(s)]
        outputs: dict[str, Any] = {
            "scenes_found": len(scenes),
            "already_ingested": len(scenes) - len(todo),
            "to_ingest": len(todo),
            "ingested": 0,
            "failed_count": 0,
            "failed": [],
            "search_provider": scenes[0].provider if scenes else None,
            "search_fallbacks": list(source.search_errors),
            "providers_used": {},
            "provider_fallbacks": [],
        }
        run.outputs = _snapshot(outputs)
        await session.commit()
        log.info("geo.ingest.start", project_id=str(project_id), run_id=str(run.id), **params)

        new_dates: list[date] = []
        for scene in todo:
            try:
                served = await asyncio.to_thread(_serve_scene, source, scene, geometry)
            except Exception as exc:
                log.warning("geo.scene.failed", item_id=scene.item_id, error=_err(exc))
                outputs["failed_count"] += 1
                if len(outputs["failed"]) < MAX_RECORDED_FAILURES:
                    outputs["failed"].append({"item_id": scene.item_id, "error": _err(exc)})
            else:
                stats, ref = served.stats, served.ref
                if len(outputs["provider_fallbacks"]) < MAX_RECORDED_FAILURES:
                    outputs["provider_fallbacks"].extend(served.fallbacks)
                acquired = ref.acquired_at.astimezone(UTC)
                inserted = await session.scalar(
                    insert(SceneObservation)
                    .values(
                        id=uuid.uuid4(),
                        project_id=project_id,
                        run_id=run.id,
                        item_id=ref.item_id,
                        acquired_at=acquired,
                        acquired_on=acquired.date(),
                        cloud_cover=ref.cloud_cover,
                        clear_fraction=stats.clear_fraction,
                        clear_pixels=stats.clear_pixels,
                        polygon_pixels=stats.polygon_pixels,
                        index_stats=stats.index_stats,
                        provider=ref.provider,
                        processing_baseline=ref.processing_baseline,
                    )
                    .on_conflict_do_nothing(constraint="uq_scene_observation_item")
                    .returning(SceneObservation.id)
                )
                if inserted is not None:  # None: a concurrent run stored it first
                    outputs["ingested"] += 1
                    used = outputs["providers_used"]
                    used[ref.provider] = used.get(ref.provider, 0) + 1
                    new_dates.append(acquired.date())
            # progress is committed after every scene, so a crash loses at most one scene
            run.outputs = _snapshot(outputs)
            await session.commit()

        if sar_source is not None and sar_policy is not None:
            await _ingest_sar(session, run, outputs, sar_source, sar_policy, geometry, start, end)

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
    sar_source: SarSource | None = None,
    sar_policy: Sentinel1Policy | None = None,
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
                sar_source=sar_source,
                sar_policy=sar_policy,
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
            "served_by": scenes[0].provider,
            "search_fallbacks": list(source.search_errors),
            "zonal": product.zonal,
        }
        await _finish_run(session, run, AnalysisStatus.SUCCEEDED, outputs)
        return {"run_id": str(run.id), **outputs}


# ---------------------------------------------------------------- stale-run reaper


def _stale_error(stale_after: timedelta) -> str:
    minutes = int(stale_after.total_seconds() // 60)
    return (
        f"Run still 'running' after {minutes} min, beyond the Celery hard time limit: the worker "
        "was killed (time limit, out of memory) or restarted before it could record the outcome. "
        "Marked failed by aqua.geo.reap_stale_runs; partial outputs are kept. Re-queue the job "
        "to retry (ingestion is idempotent)."
    )


async def reap_stale_runs(
    sessionmaker: async_sessionmaker[AsyncSession], *, stale_after: timedelta, now: datetime
) -> dict[str, Any]:
    """Mark analysis runs stuck in `running` for longer than `stale_after` as failed (audit-logged).

    A run killed by the hard time limit (SIGKILL) never reaches `_fail_run`, so it would stay
    `running` forever and block nothing but mislead every reader of its status."""
    cutoff = now - stale_after
    async with sessionmaker() as session:
        runs = (
            await session.scalars(
                select(AnalysisRun)
                .where(
                    AnalysisRun.status == AnalysisStatus.RUNNING,
                    func.coalesce(AnalysisRun.started_at, AnalysisRun.created_at) < cutoff,
                )
                .order_by(AnalysisRun.started_at)
                .with_for_update(skip_locked=True)
            )
        ).all()
        error = _stale_error(stale_after)
        for run in runs:
            started = run.started_at or run.created_at
            run.status = AnalysisStatus.FAILED
            run.error = error
            run.finished_at = now
            await audit.record(
                session,
                actor_id=None,
                action="analysis.reaped",
                entity_type="analysis_run",
                entity_id=run.id,
                data={
                    "project_id": str(run.project_id),
                    "kind": run.kind.value,
                    "started_at": started.isoformat(),
                    "stale_after_min": int(stale_after.total_seconds() // 60),
                },
            )
        await session.commit()
    run_ids = [str(run.id) for run in runs]
    if run_ids:
        log.warning("geo.reaper.reaped", count=len(run_ids), run_ids=run_ids)
    return {"reaped": len(run_ids), "run_ids": run_ids}


# ---------------------------------------------------------------- Celery wrappers


async def _with_db[T](job: Callable[[async_sessionmaker[AsyncSession]], Awaitable[T]]) -> T:
    engine = create_engine(get_settings().database_url)
    try:
        return await job(session_factory(engine))
    finally:
        await engine.dispose()


def _today() -> date:
    return datetime.now(UTC).date()


def _sar_kwargs(settings: Any) -> dict[str, Any]:
    policy = Sentinel1Policy.from_settings(settings)
    if policy is None:
        return {"sar_source": None, "sar_policy": None}
    return {"sar_source": StacSarSource(), "sar_policy": policy}


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
                StacSceneSource(provider_names=settings.geo_stac_providers),
                uuid.UUID(project_id),
                start=start,
                end=_today(),
                kind=AnalysisKind.BACKFILL,
                run_id=uuid.UUID(run_id) if run_id else None,
                max_cloud=settings.geo_max_scene_cloud_pct,
                **_sar_kwargs(settings),
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
                StacSceneSource(provider_names=settings.geo_stac_providers),
                today=_today(),
                backfill_start=settings.geo_backfill_start,
                max_cloud=settings.geo_max_scene_cloud_pct,
                **_sar_kwargs(settings),
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
                StacSceneSource(provider_names=settings.geo_stac_providers),
                S3ObjectStorage(settings),
                uuid.UUID(project_id),
                int(season_start_year),
                run_id=uuid.UUID(run_id) if run_id else None,
                max_cloud=settings.geo_composite_max_cloud_pct,
            )
        )
    )


@shared_task(name="aqua.geo.reap_stale_runs", soft_time_limit=300, time_limit=360)
def reap_stale_runs_task() -> dict[str, Any]:
    stale_after = timedelta(minutes=get_settings().geo_stale_run_timeout_min)
    return asyncio.run(
        _with_db(lambda sm: reap_stale_runs(sm, stale_after=stale_after, now=datetime.now(UTC)))
    )
