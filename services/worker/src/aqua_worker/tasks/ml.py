"""Ecosystem-classifier tasks (queue `ml`): a per-pixel classification of one project's dry season.

`aqua.ml.classify_project(project_id, season_start_year, run_id)`:
1. Scene stack, chosen as in training (`ml/pipelines/features_extract.py`): Sentinel-2 L2A items of
   the dry season Nov <year> - Mar <year + 1> with tile cloud cover < 80 %, grouped by acquisition
   day; the 12 days with the least cloudy tile are kept (ties: date, item id). All tiles of a kept
   day are loaded (odc-stac mosaics one solar day), so a project spanning two MGRS tiles is fully
   covered; training sampled the least cloudy tile at each point, which reads the same acquisition.
2. Masked reflectance via `sentinel2.load` (SCL mask, BOA offset rule), Copernicus DEM on the same
   grid, the classifier feature vector and tile-wise prediction (`aqua_geo.classify`).
3. Two COGs in object storage (class index uint8 with nodata 255; highest class probability), a
   `classifications` row (areas, confidence, scenes, model version + SHA-256) and, when the mangrove
   area fell versus the previous season, a `mangrove_area_loss` alert.

Failures are never turned into numbers: no scenes, no classifiable pixel, a read error or a model
whose files do not match their pinned hashes fail the run (recorded in `analysis_runs`), and no
classification row is written. A read failure is not retried on another STAC provider (the stack
must come from one catalogue); re-queue the job instead.

`aqua.ml.classify_dry_season` (beat, once a year in April): queues the classification of the dry
season that has just ended for every active project that has none for the current model version.
"""

import asyncio
import tempfile
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date
from pathlib import Path
from typing import Any

from celery import shared_task
from shapely.geometry.base import BaseGeometry
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aqua_core import audit, notifications
from aqua_core.logging import get_logger
from aqua_core.models import (
    Alert,
    AnalysisKind,
    AnalysisStatus,
    Classification,
    Project,
)
from aqua_core.settings import get_settings
from aqua_core.storage import ObjectStorage, S3ObjectStorage
from aqua_worker.alerts import (
    AlertCandidate,
    AreaLossRule,
    SeasonArea,
    detect_mangrove_area_loss,
)
from aqua_worker.model_store import (
    CURRENT_ECOSYSTEM_CLASSIFIER,
    LoadedModel,
    ModelManifest,
    get_model,
)
from aqua_worker.scenes import DemRef, DemSource, SceneRef, SceneSource, StacDemSource
from aqua_worker.tasks.geo import (
    ACTIVE_STATUSES,
    COG_CONTENT_TYPE,
    DRY_SEASON,
    GEO_THREADS,
    _fail_run,
    _finish_run,
    _project_geometry,
    _start_run,
    _today,
    _with_db,
    season_label,
    season_window,
)

log = get_logger("aqua_worker.ml")

ML_QUEUE = "ml"


def classification_prefix(
    project_id: uuid.UUID, season_start_year: int, model_version: str, run_id: uuid.UUID
) -> str:
    return (
        f"projects/{project_id}/classifications/{season_label(season_start_year)}/"
        f"{model_version}/{run_id}"
    )


def select_season_scenes(scenes: Sequence[SceneRef], max_scenes: int) -> list[SceneRef]:
    """The training scene rule at project level: one entry per acquisition day ranked by its
    least cloudy tile (then date, then item id), the `max_scenes` best days kept; every tile of a
    kept day is returned, in acquisition order."""
    by_day: dict[date, list[SceneRef]] = {}
    for ref in scenes:
        by_day.setdefault(ref.acquired_at.astimezone(UTC).date(), []).append(ref)

    def rank(day: date) -> tuple[float, date, str]:
        refs = by_day[day]
        cloud = min(r.cloud_cover if r.cloud_cover is not None else 100.0 for r in refs)
        return cloud, day, min(r.item_id for r in refs)

    keep = set(sorted(by_day, key=rank)[:max_scenes])
    return sorted(
        (r for day in keep for r in by_day[day]), key=lambda r: (r.acquired_at, r.item_id)
    )


# ---------------------------------------------------------------- compute (thread)


@dataclass
class ClassificationProduct:
    classes_cog: bytes
    probability_cog: bytes
    summary: Any  # aqua_geo.classify.AreaSummary
    epsg: int
    scene_days: int
    tiles: int
    too_few_clear: int
    nonfinite: int
    dem_items: list[str]


def compute_classification(
    source: SceneSource,
    dem_source: DemSource,
    scenes: Sequence[SceneRef],
    geometry: BaseGeometry,
    model: LoadedModel,
    *,
    tile: int,
    low_confidence_threshold: float,
) -> ClassificationProduct:
    """Load the stack, predict tile by tile, compute areas and write both COGs (runs in a thread)."""
    import dask
    import numpy as np
    import xarray as xr

    from aqua_geo.classify import (
        NODATA_CLASS,
        area_summary,
        classify_stack,
        grid_pixel_area_m2,
        utm_areal_scale,
    )
    from aqua_geo.cog import write_class_cog, write_cog
    from aqua_geo.sentinel2 import BANDS

    loaded = source.load(scenes, geometry, chunk=tile, bands=tuple(BANDS))
    ds = loaded.ds
    dem_tiles: list[DemRef] = dem_source.search(geometry)
    elevation = dem_source.load(dem_tiles, ds["clear"], loaded.epsg, chunk=tile)
    with dask.config.set(scheduler="threads", num_workers=GEO_THREADS):
        inside = np.asarray(loaded.inside.values, dtype=bool)
        grid = classify_stack(ds, elevation, inside, model.predict, model.spec, tile=tile)
    centroid = geometry.centroid
    summary = area_summary(
        grid,
        inside,
        model.spec,
        pixel_area_m2=grid_pixel_area_m2(ds.x.values, ds.y.values),
        areal_scale=utm_areal_scale(loaded.epsg, centroid.x, centroid.y),
        low_confidence_threshold=low_confidence_threshold,
    )
    coords = {"y": ds.y.values, "x": ds.x.values}
    labels = xr.DataArray(grid.labels, dims=("y", "x"), coords=coords)
    max_prob = xr.Dataset({"max_probability": (("y", "x"), grid.max_probability)}, coords=coords)
    with tempfile.TemporaryDirectory(prefix="aqua-classify-") as tmp:
        classes_path, prob_path = Path(tmp) / "classes.tif", Path(tmp) / "max_probability.tif"
        write_class_cog(
            labels, classes_path, epsg=loaded.epsg, classes=model.spec.classes, nodata=NODATA_CLASS
        )
        write_cog(max_prob, prob_path, epsg=loaded.epsg)
        classes_cog, probability_cog = classes_path.read_bytes(), prob_path.read_bytes()
    return ClassificationProduct(
        classes_cog=classes_cog,
        probability_cog=probability_cog,
        summary=summary,
        epsg=loaded.epsg,
        scene_days=int(ds.sizes["time"]),
        tiles=grid.tiles,
        too_few_clear=grid.too_few_clear,
        nonfinite=grid.nonfinite,
        dem_items=[t.item_id for t in dem_tiles],
    )


# ---------------------------------------------------------------- alerts


async def _previous_season(session: AsyncSession, row: Classification) -> SeasonArea | None:
    prev = await session.scalar(
        select(Classification)
        .where(
            Classification.project_id == row.project_id,
            Classification.season_start_year == row.season_start_year - 1,
            Classification.model_version == row.model_version,
        )
        .order_by(Classification.created_at.desc(), Classification.id)
        .limit(1)
    )
    if prev is None:
        return None
    return SeasonArea(str(prev.id), prev.season_start_year, prev.model_version, prev.mangrove_ha)


async def _store_alert(
    session: AsyncSession, project_id: uuid.UUID, run_id: uuid.UUID, c: AlertCandidate
) -> uuid.UUID | None:
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
    if alert_id is not None:
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
        await notifications.alert_raised(session, project_id, alert_id, c.severity, c.message)
    return alert_id


# ---------------------------------------------------------------- task core


async def classify_project(
    sessionmaker: async_sessionmaker[AsyncSession],
    source: SceneSource,
    dem_source: DemSource,
    storage: ObjectStorage,
    model_provider: Callable[[], LoadedModel],
    project_id: uuid.UUID,
    season_start_year: int,
    *,
    run_id: uuid.UUID | None = None,
    manifest: ModelManifest = CURRENT_ECOSYSTEM_CLASSIFIER,
    tile: int = 256,
    low_confidence_threshold: float = 0.6,
    loss_rule: AreaLossRule | None = None,
) -> dict[str, Any]:
    """Classify one dry season of one project; see the module docstring."""
    rule = loss_rule or AreaLossRule()
    start, end = season_window(season_start_year)
    params: dict[str, Any] = {
        "season": season_label(season_start_year),
        "season_start_year": season_start_year,
        "months": DRY_SEASON,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "max_tile_cloud_pct": manifest.max_tile_cloud_pct,
        "max_scenes": manifest.max_scenes,
        "provider": source.name,
        "dem": dem_source.collection,
        "model_version": manifest.version,
        "tile_px": tile,
        "low_confidence_threshold": low_confidence_threshold,
    }
    async with sessionmaker() as session:
        geometry = await _project_geometry(session, project_id)
        run = await _start_run(session, project_id, AnalysisKind.CLASSIFICATION, params, run_id)
        try:
            model = await asyncio.to_thread(model_provider)
            if model.version != manifest.version:
                raise ValueError(f"loaded model {model.version}, expected {manifest.version}")
            found = await asyncio.to_thread(
                source.search, geometry, start, end, max_cloud=manifest.max_tile_cloud_pct
            )
            scenes = select_season_scenes(found, manifest.max_scenes)
            if not scenes:
                raise LookupError(
                    f"no Sentinel-2 scenes below {manifest.max_tile_cloud_pct:g} % tile cloud "
                    f"in dry season {season_label(season_start_year)}"
                )
            product = await asyncio.to_thread(
                compute_classification,
                source,
                dem_source,
                scenes,
                geometry,
                model,
                tile=tile,
                low_confidence_threshold=low_confidence_threshold,
            )
            summary = product.summary
            if summary.classified_pixels == 0:
                raise ValueError(
                    f"no pixel of the project could be classified: {product.too_few_clear} with "
                    f"fewer than {model.spec.min_clear} clear looks, {product.nonfinite} with "
                    "non-finite features"
                )
            prefix = classification_prefix(project_id, season_start_year, model.version, run.id)
            classes_key = f"{prefix}/classes.tif"
            prob_key = f"{prefix}/max_probability.tif"
            classes_sha = await asyncio.to_thread(
                storage.put_bytes, classes_key, product.classes_cog, COG_CONTENT_TYPE
            )
            prob_sha = await asyncio.to_thread(
                storage.put_bytes, prob_key, product.probability_cog, COG_CONTENT_TYPE
            )
        except Exception as exc:
            await _fail_run(session, run.id, exc)
            raise

        scene_list = [
            {
                "item_id": s.item_id,
                "acquired_on": s.acquired_at.astimezone(UTC).date().isoformat(),
                "cloud_cover": s.cloud_cover,
                "processing_baseline": s.processing_baseline,
            }
            for s in scenes
        ]
        row = Classification(
            id=uuid.uuid4(),
            project_id=project_id,
            run_id=run.id,
            season_start_year=season_start_year,
            model_version=model.version,
            model_sha256=model.model_sha256,
            features_sha256=model.features_sha256,
            provider=scenes[0].provider,
            scenes=scene_list,
            dem_items=product.dem_items,
            epsg=product.epsg,
            pixel_area_m2=summary.pixel_area_m2,
            areal_scale=summary.areal_scale,
            polygon_pixels=summary.polygon_pixels,
            classified_pixels=summary.classified_pixels,
            unclassified_pixels=summary.unclassified_pixels,
            classes=list(model.spec.classes),
            class_areas_ha=summary.class_areas_ha,
            mangrove_ha=summary.mangrove_ha,
            mangrove_mean_confidence=summary.mangrove_mean_confidence,
            low_confidence_threshold=summary.low_confidence_threshold,
            low_confidence_share=summary.low_confidence_share,
            classes_cog_key=classes_key,
            classes_cog_sha256=classes_sha,
            probability_cog_key=prob_key,
            probability_cog_sha256=prob_sha,
        )
        session.add(row)
        await session.flush()

        current = SeasonArea(str(row.id), season_start_year, model.version, row.mangrove_ha)
        previous = await _previous_season(session, row)
        candidate = detect_mangrove_area_loss(previous, current, rule)
        alert_id = await _store_alert(session, project_id, run.id, candidate) if candidate else None
        outputs: dict[str, Any] = {
            "classification_id": str(row.id),
            "model_version": model.version,
            "model_sha256": model.model_sha256,
            "features_sha256": model.features_sha256,
            "scene_count": len(scenes),
            "scene_days": product.scene_days,
            "item_ids": [s.item_id for s in scenes],
            "served_by": scenes[0].provider,
            "search_fallbacks": list(source.search_errors),
            "dem_items": product.dem_items,
            "mangrove_ha": row.mangrove_ha,
            "class_areas_ha": row.class_areas_ha,
            "mangrove_mean_confidence": row.mangrove_mean_confidence,
            "low_confidence_share": row.low_confidence_share,
            "polygon_pixels": summary.polygon_pixels,
            "classified_pixels": summary.classified_pixels,
            "unclassified_pixels": summary.unclassified_pixels,
            "unclassified_too_few_clear": product.too_few_clear,
            "unclassified_nonfinite": product.nonfinite,
            "tiles": product.tiles,
            "classes_cog_key": classes_key,
            "probability_cog_key": prob_key,
            "loss_check": {
                "previous_classification_id": previous.classification_id if previous else None,
                "previous_mangrove_ha": previous.mangrove_ha if previous else None,
                "threshold_pct": rule.threshold_pct,
                "min_ha": rule.min_ha,
                "alert_id": str(alert_id) if alert_id else None,
            },
        }
        await _finish_run(session, run, AnalysisStatus.SUCCEEDED, outputs)
        log.info(
            "ml.classify.done",
            project_id=str(project_id),
            run_id=str(run.id),
            season=params["season"],
            mangrove_ha=row.mangrove_ha,
            classified_pixels=summary.classified_pixels,
        )
        return {"run_id": str(run.id), **outputs}


async def seasons_to_classify(
    sessionmaker: async_sessionmaker[AsyncSession],
    season_start_year: int,
    model_version: str,
) -> list[uuid.UUID]:
    """Active projects without a classification of this season by this model version."""
    async with sessionmaker() as session:
        done = select(Classification.project_id).where(
            Classification.season_start_year == season_start_year,
            Classification.model_version == model_version,
        )
        rows = await session.scalars(
            select(Project.id)
            .where(Project.status.in_(ACTIVE_STATUSES), Project.id.not_in(done))
            .order_by(Project.id)
        )
        return list(rows)


def last_completed_season(today: date) -> int:
    """Start year of the most recent dry season that has fully ended (Nov–Mar)."""
    return today.year - 1 if today > date(today.year, 3, 31) else today.year - 2


# ---------------------------------------------------------------- Celery wrappers


def _model_provider() -> LoadedModel:
    settings = get_settings()
    return get_model(
        S3ObjectStorage(settings), settings.ml_model_cache_dir, CURRENT_ECOSYSTEM_CLASSIFIER
    )


@shared_task(name="aqua.ml.classify_project", soft_time_limit=2 * 3600, time_limit=2 * 3600 + 300)
def classify_project_task(
    project_id: str, season_start_year: str | int, run_id: str | None = None
) -> dict[str, Any]:
    from aqua_worker.scenes import StacSceneSource

    settings = get_settings()
    return asyncio.run(
        _with_db(
            lambda sm: classify_project(
                sm,
                StacSceneSource(provider_names=settings.geo_stac_providers),
                StacDemSource(),
                S3ObjectStorage(settings),
                _model_provider,
                uuid.UUID(project_id),
                int(season_start_year),
                run_id=uuid.UUID(run_id) if run_id else None,
                tile=settings.ml_classify_tile_px,
                low_confidence_threshold=settings.ml_low_confidence_threshold,
                loss_rule=AreaLossRule.from_settings(settings),
            )
        )
    )


@shared_task(name="aqua.ml.classify_dry_season", soft_time_limit=600, time_limit=660)
def classify_dry_season_task(season_start_year: str | int | None = None) -> dict[str, Any]:
    year = int(season_start_year) if season_start_year else last_completed_season(_today())
    version = CURRENT_ECOSYSTEM_CLASSIFIER.version
    project_ids = asyncio.run(_with_db(lambda sm: seasons_to_classify(sm, year, version)))
    for pid in project_ids:
        classify_project_task.apply_async(args=[str(pid), year], queue=ML_QUEUE)
    log.info("ml.classify.queued", season=season_label(year), projects=len(project_ids))
    return {"season_start_year": year, "queued": [str(p) for p in project_ids]}
