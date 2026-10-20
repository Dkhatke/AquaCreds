"""Satellite analysis: trigger backfills/composites, read runs, the per-scene time series, alerts.

The time series serves Sentinel-2 optical indices (`scene_observations`) and Sentinel-1 backscatter
metrics (`sar_observations`) through the same endpoint; the two are not comparable (see
`aqua_api.schemas.analysis.NOT_COMPARABLE`).

Heavy work runs in the worker `geo` queue; the API only records an `AnalysisRun` (queued) and
enqueues the task by name with the run id, so the client can poll `GET /analysis-runs/{id}`.
"""

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aqua_api.deps import (
    CurrentUser,
    SessionDep,
    SettingsDep,
    can_read_all_projects,
    effective_platform_role,
    org_role,
)
from aqua_api.schemas.analysis import (
    NOT_COMPARABLE,
    SAR_INDICES,
    SAR_STATS_KEY,
    SAR_UNITS,
    AlertOut,
    AnalysisRunOut,
    BackfillRequest,
    CompositeRequest,
    TimeseriesIndex,
    TimeseriesOut,
    TimeseriesPoint,
)
from aqua_api.services import audit
from aqua_core.models import (
    Alert,
    AnalysisKind,
    AnalysisRun,
    AnalysisStatus,
    OrgRole,
    PlatformRole,
    Project,
    ProjectStatus,
    SarObservation,
    SceneObservation,
    User,
)
from aqua_core.models._types import utcnow
from aqua_core.settings import Settings

router = APIRouter(tags=["satellite analysis"])

ANALYSIS_PROJECT_STATUSES = {
    ProjectStatus.SUBMITTED,
    ProjectStatus.BASELINE_READY,
    ProjectStatus.REGISTERED,
    ProjectStatus.MONITORING,
}
ACTIVE_RUN = (AnalysisStatus.QUEUED, AnalysisStatus.RUNNING)
GEO_QUEUE = "geo"


# ---------------------------------------------------------------- helpers


async def _project(
    session: AsyncSession, project_id: uuid.UUID, user: User, settings: Settings
) -> Project:
    project = await session.get(Project, project_id)
    if project is None or (
        org_role(user, project.org_id) is None and not can_read_all_projects(user, settings)
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return project


def _require_admin(user: User, project: Project, settings: Settings) -> None:
    """Org admins of the project's organisation, or registry admins (with MFA when required)."""
    if (
        org_role(user, project.org_id) != OrgRole.ADMIN
        and effective_platform_role(user, settings) != PlatformRole.REGISTRY_ADMIN
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Only organisation or registry admins can run analyses"
        )


def _require_analysable(project: Project) -> None:
    if project.status not in ANALYSIS_PROJECT_STATUSES:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Submit the project before running satellite analysis"
        )


async def _queue_run(
    request: Request,
    session: AsyncSession,
    *,
    project: Project,
    user: User,
    kind: AnalysisKind,
    params: dict[str, object],
    task: str,
    args: tuple[str, ...],
) -> AnalysisRun:
    run = AnalysisRun(
        project_id=project.id,
        kind=kind,
        status=AnalysisStatus.QUEUED,
        params=params,
        requested_by=user.id,
        outputs={},
    )
    session.add(run)
    await session.flush()
    await audit.record(
        session,
        actor_id=user.id,
        action=f"analysis.{kind.value}_requested",
        entity_type="project",
        entity_id=project.id,
        data={"run_id": str(run.id), **{k: v for k, v in params.items() if v is not None}},
    )
    await session.commit()
    await session.refresh(run)
    try:
        request.app.state.enqueue(task, *args, str(run.id), queue=GEO_QUEUE)
    except Exception as exc:
        run.status = AnalysisStatus.FAILED
        run.error = "could not enqueue the job"
        run.finished_at = utcnow()
        await session.commit()
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Task queue unavailable, try again later"
        ) from exc
    return run


# ---------------------------------------------------------------- triggers


@router.post(
    "/projects/{project_id}/analysis/backfill", response_model=AnalysisRunOut, status_code=202
)
async def request_backfill(
    project_id: uuid.UUID,
    request: Request,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    body: BackfillRequest | None = None,
) -> AnalysisRun:
    """Ingest every Sentinel-2 scene since `start_date` (default 2017-01-01) into the time
    series. Scenes already ingested are skipped, so re-running is safe."""
    project = await _project(session, project_id, user, settings)
    _require_admin(user, project, settings)
    _require_analysable(project)
    start = body.start_date if body else None
    if start and start > date.today():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "start_date is in the future")
    active = await session.scalar(
        select(AnalysisRun.id).where(
            AnalysisRun.project_id == project_id,
            AnalysisRun.kind == AnalysisKind.BACKFILL,
            AnalysisRun.status.in_(ACTIVE_RUN),
        )
    )
    if active:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"A backfill is already queued or running ({active})"
        )
    return await _queue_run(
        request,
        session,
        project=project,
        user=user,
        kind=AnalysisKind.BACKFILL,
        params={"start": start.isoformat() if start else None},
        task="aqua.geo.backfill_project",
        args=(str(project.id), start.isoformat() if start else ""),
    )


@router.post(
    "/projects/{project_id}/analysis/composite", response_model=AnalysisRunOut, status_code=202
)
async def request_composite(
    project_id: uuid.UUID,
    body: CompositeRequest,
    request: Request,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
) -> AnalysisRun:
    """Dry-season (Nov–Mar) median + percentile composite, stored as a COG."""
    project = await _project(session, project_id, user, settings)
    _require_admin(user, project, settings)
    _require_analysable(project)
    if date(body.season_start_year, 11, 1) > date.today():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "That dry season has not started yet"
        )
    return await _queue_run(
        request,
        session,
        project=project,
        user=user,
        kind=AnalysisKind.COMPOSITE,
        params={"season_start_year": body.season_start_year},
        task="aqua.geo.build_composite",
        args=(str(project.id), str(body.season_start_year)),
    )


# ---------------------------------------------------------------- reads


@router.get("/analysis-runs/{run_id}", response_model=AnalysisRunOut)
async def get_run(
    run_id: uuid.UUID, user: CurrentUser, session: SessionDep, settings: SettingsDep
) -> AnalysisRun:
    run = await session.get(AnalysisRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Analysis run not found")
    await _project(session, run.project_id, user, settings)
    return run


@router.get("/projects/{project_id}/analysis-runs", response_model=list[AnalysisRunOut])
async def list_runs(
    project_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[AnalysisRun]:
    await _project(session, project_id, user, settings)
    rows = await session.scalars(
        select(AnalysisRun)
        .where(AnalysisRun.project_id == project_id)
        .order_by(AnalysisRun.created_at.desc())
        .limit(limit)
    )
    return list(rows)


@router.get("/projects/{project_id}/timeseries", response_model=TimeseriesOut)
async def timeseries(
    project_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    index: TimeseriesIndex = "ndvi",
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    min_clear: Annotated[
        float,
        Query(
            ge=0,
            le=1,
            description="Minimum clear fraction (Sentinel-2) or valid fraction (Sentinel-1)",
        ),
    ] = 0.0,
) -> TimeseriesOut:
    """Per-scene zonal statistics of one index. Every point carries its clear fraction so
    clients can tell a real decline from a cloudy scene (filter with `min_clear`).

    Sentinel-1 indices (`vh_db`, `vv_db`, `vh_vv_ratio_db`, `rvi`) return one point per radar
    pass with its valid fraction: per-polygon P50 (plus mean/P10/P90) after a 3×3 speckle filter.
    Radar backscatter and optical indices are NOT comparable and must not be mixed."""
    await _project(session, project_id, user, settings)
    if index in SAR_INDICES:
        return await _sar_timeseries(session, project_id, index, date_from, date_to, min_clear)
    query = (
        select(SceneObservation)
        .where(SceneObservation.project_id == project_id)
        .where(SceneObservation.clear_fraction >= min_clear)
        .order_by(SceneObservation.acquired_at, SceneObservation.item_id)
    )
    if date_from:
        query = query.where(SceneObservation.acquired_on >= date_from)
    if date_to:
        query = query.where(SceneObservation.acquired_on <= date_to)
    points = []
    for obs in await session.scalars(query):
        stats = obs.index_stats.get(index) or {}
        points.append(
            TimeseriesPoint(
                date=obs.acquired_on,
                acquired_at=obs.acquired_at,
                item_id=obs.item_id,
                clear_fraction=obs.clear_fraction,
                cloud_cover=obs.cloud_cover,
                valid_pixels=int(stats.get("valid_pixels", 0)),
                mean=stats.get("mean"),
                p10=stats.get("p10"),
                p50=stats.get("p50"),
                p90=stats.get("p90"),
            )
        )
    return TimeseriesOut(
        project_id=project_id,
        index=index,
        sensor="sentinel-2-l2a",
        units="unitless spectral index (surface reflectance)",
        note=NOT_COMPARABLE,
        min_clear_fraction=min_clear,
        points=points,
    )


async def _sar_timeseries(
    session: AsyncSession,
    project_id: uuid.UUID,
    index: TimeseriesIndex,
    date_from: date | None,
    date_to: date | None,
    min_valid: float,
) -> TimeseriesOut:
    query = (
        select(SarObservation)
        .where(
            SarObservation.project_id == project_id,
            SarObservation.valid_fraction >= min_valid,
        )
        .order_by(SarObservation.acquired_at, SarObservation.acquisition_key)
    )
    if date_from:
        query = query.where(SarObservation.acquired_on >= date_from)
    if date_to:
        query = query.where(SarObservation.acquired_on <= date_to)
    key = SAR_STATS_KEY[index]
    points = []
    for obs in await session.scalars(query):
        stats = obs.stats.get(key) or {}
        points.append(
            TimeseriesPoint(
                date=obs.acquired_on,
                acquired_at=obs.acquired_at,
                item_id=obs.acquisition_key,
                clear_fraction=None,
                cloud_cover=None,
                valid_fraction=obs.valid_fraction,
                orbit_state=obs.orbit_state,
                relative_orbit=obs.relative_orbit,
                valid_pixels=int(stats.get("valid_pixels", 0)),
                mean=stats.get("mean"),
                p10=stats.get("p10"),
                p50=stats.get("p50"),
                p90=stats.get("p90"),
            )
        )
    return TimeseriesOut(
        project_id=project_id,
        index=index,
        sensor="sentinel-1-rtc",
        units=SAR_UNITS[index],
        note=NOT_COMPARABLE,
        min_clear_fraction=min_valid,
        points=points,
    )


@router.get("/projects/{project_id}/alerts", response_model=list[AlertOut])
async def list_alerts(
    project_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    unacknowledged: bool = False,
) -> list[Alert]:
    await _project(session, project_id, user, settings)
    query = select(Alert).where(Alert.project_id == project_id)
    if unacknowledged:
        query = query.where(Alert.acknowledged_at.is_(None))
    rows = await session.scalars(query.order_by(Alert.observed_on.desc(), Alert.created_at))
    return list(rows)


@router.post("/alerts/{alert_id}/acknowledge", response_model=AlertOut)
async def acknowledge_alert(
    alert_id: uuid.UUID, user: CurrentUser, session: SessionDep, settings: SettingsDep
) -> Alert:
    """Org admins, verifiers and registry admins confirm they have seen (and are following up
    on) an alert. Acknowledging does not delete it: the alert stays in the project record."""
    alert = await session.get(Alert, alert_id, with_for_update=True)
    if alert is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alert not found")
    project = await _project(session, alert.project_id, user, settings)
    if org_role(user, project.org_id) != OrgRole.ADMIN and not can_read_all_projects(
        user, settings
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Only organisation admins or verifiers can acknowledge"
        )
    if alert.acknowledged_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Alert already acknowledged")
    alert.acknowledged_by = user.id
    alert.acknowledged_at = utcnow()
    await audit.record(
        session,
        actor_id=user.id,
        action="alert.acknowledged",
        entity_type="project",
        entity_id=project.id,
        data={"alert_id": str(alert.id), "observed_on": alert.observed_on.isoformat()},
    )
    await session.commit()
    await session.refresh(alert)
    return alert
