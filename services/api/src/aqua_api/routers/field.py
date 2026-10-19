"""Field evidence: monitoring periods, capture nonces, photo submissions, sample plots."""

import hashlib
import secrets
import uuid
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request, status
from geoalchemy2.shape import from_shape
from shapely.geometry import Point
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from aqua_api.deps import (
    CurrentUser,
    SessionDep,
    SettingsDep,
    StorageDep,
    can_read_all_projects,
    org_role,
)
from aqua_api.schemas.field import (
    MeasurementCreate,
    MeasurementOut,
    NonceOut,
    PeriodCreate,
    PeriodOut,
    PhotoUploadRequest,
    PlotCreate,
    PlotOut,
    SubmissionCreate,
    SubmissionOut,
)
from aqua_api.schemas.project import UploadTicket
from aqua_api.services import audit
from aqua_core.geometry import to_point
from aqua_core.models import (
    CaptureNonce,
    FieldSubmission,
    MonitoringPeriod,
    OrgRole,
    PeriodStatus,
    Plot,
    PlotMeasurement,
    Project,
    ProjectStatus,
    User,
)
from aqua_core.models._types import utcnow

router = APIRouter(tags=["field evidence"])

FIELD_ROLES = {OrgRole.ADMIN, OrgRole.FIELD_AGENT}
PERIOD_PROJECT_STATUSES = {
    ProjectStatus.SUBMITTED,
    ProjectStatus.BASELINE_READY,
    ProjectStatus.REGISTERED,
    ProjectStatus.MONITORING,
}


# ---------------------------------------------------------------- helpers


async def _project(session: AsyncSession, project_id: uuid.UUID, user: User) -> Project:
    project = await session.get(Project, project_id)
    if project is None or (
        org_role(user, project.org_id) is None and not can_read_all_projects(user)
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return project


async def _period(
    session: AsyncSession, period_id: uuid.UUID, user: User
) -> tuple[MonitoringPeriod, Project]:
    period = await session.get(MonitoringPeriod, period_id)
    if period is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Monitoring period not found")
    return period, await _project(session, period.project_id, user)


def _require(user: User, project: Project, roles: set[OrgRole]) -> None:
    if org_role(user, project.org_id) not in roles:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Not allowed for your role in this organisation"
        )


def _require_open(period: MonitoringPeriod) -> None:
    if period.status != PeriodStatus.OPEN:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Monitoring period is {period.status}")


def _submission_out(s: FieldSubmission) -> SubmissionOut:
    point = to_point(s.location)
    return SubmissionOut(
        id=s.id,
        period_id=s.period_id,
        kind=s.kind,
        status=s.status,
        plot_id=s.plot_id,
        sha256=s.sha256,
        lat=point.y,
        lon=point.x,
        gps_accuracy_m=s.gps_accuracy_m,
        captured_at=s.captured_at,
        received_at=s.received_at,
        trust_score=s.trust_score,
        checks=s.checks,
        exif=s.exif,
    )


def _plot_out(p: Plot) -> PlotOut:
    point = to_point(p.centre)
    return PlotOut(
        id=p.id,
        code=p.code,
        stratum=p.stratum,
        lat=point.y,
        lon=point.x,
        area_m2=p.area_m2,
        marker_id=p.marker_id,
    )


# ---------------------------------------------------------------- periods


@router.post("/projects/{project_id}/periods", response_model=PeriodOut, status_code=201)
async def create_period(
    project_id: uuid.UUID, body: PeriodCreate, user: CurrentUser, session: SessionDep
) -> MonitoringPeriod:
    project = await _project(session, project_id, user)
    _require(user, project, {OrgRole.ADMIN})
    if project.status not in PERIOD_PROJECT_STATUSES:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Submit the project before opening monitoring periods"
        )
    open_exists = await session.scalar(
        select(MonitoringPeriod.id).where(
            MonitoringPeriod.project_id == project_id, MonitoringPeriod.status == PeriodStatus.OPEN
        )
    )
    if open_exists:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This project already has an open monitoring period"
        )
    period = MonitoringPeriod(
        project_id=project_id,
        kind=body.kind,
        start_date=body.start_date,
        end_date=body.end_date,
        created_by=user.id,
    )
    session.add(period)
    await session.flush()
    await audit.record(
        session,
        actor_id=user.id,
        action="period.opened",
        entity_type="project",
        entity_id=project_id,
        data={"period_id": str(period.id), "kind": body.kind.value},
    )
    await session.commit()
    await session.refresh(period)
    return period


@router.get("/projects/{project_id}/periods", response_model=list[PeriodOut])
async def list_periods(
    project_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> list[MonitoringPeriod]:
    await _project(session, project_id, user)
    rows = await session.scalars(
        select(MonitoringPeriod)
        .where(MonitoringPeriod.project_id == project_id)
        .order_by(MonitoringPeriod.start_date)
    )
    return list(rows)


# ---------------------------------------------------------------- capture


@router.post("/periods/{period_id}/nonces", response_model=NonceOut, status_code=201)
async def issue_nonce(
    period_id: uuid.UUID, user: CurrentUser, session: SessionDep, settings: SettingsDep
) -> NonceOut:
    period, project = await _period(session, period_id, user)
    _require(user, project, FIELD_ROLES)
    _require_open(period)
    token = secrets.token_urlsafe(32)
    now = utcnow()
    nonce = CaptureNonce(
        user_id=user.id,
        period_id=period.id,
        nonce_hash=hashlib.sha256(token.encode()).hexdigest(),
        issued_at=now,
        expires_at=now + timedelta(minutes=settings.capture_nonce_ttl_min),
    )
    session.add(nonce)
    await session.commit()
    return NonceOut(nonce=token, expires_at=nonce.expires_at)


@router.post("/periods/{period_id}/photos/upload-url", response_model=UploadTicket)
async def photo_upload_url(
    period_id: uuid.UUID,
    body: PhotoUploadRequest,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    storage: StorageDep,
) -> UploadTicket:
    period, project = await _period(session, period_id, user)
    _require(user, project, FIELD_ROLES)
    _require_open(period)
    ext = "jpg" if body.content_type == "image/jpeg" else "png"
    key = f"projects/{project.id}/periods/{period.id}/photos/{uuid.uuid4().hex}.{ext}"
    url = await run_in_threadpool(storage.presign_put, key, body.content_type)
    return UploadTicket(
        object_key=key,
        upload_url=url,
        headers={"Content-Type": body.content_type},
        expires_in=settings.upload_url_ttl_s,
    )


@router.post("/periods/{period_id}/submissions", response_model=SubmissionOut, status_code=202)
async def create_submission(
    period_id: uuid.UUID,
    body: SubmissionCreate,
    request: Request,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    storage: StorageDep,
) -> SubmissionOut:
    period, project = await _period(session, period_id, user)
    _require(user, project, FIELD_ROLES)
    _require_open(period)
    if not body.object_key.startswith(f"projects/{project.id}/periods/{period.id}/photos/"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Object key not for this period")

    nonce = await session.scalar(
        select(CaptureNonce)
        .where(CaptureNonce.nonce_hash == hashlib.sha256(body.nonce.encode()).hexdigest())
        .with_for_update()
    )
    if nonce is None or nonce.user_id != user.id or nonce.period_id != period.id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown capture nonce for this user and period"
        )
    if nonce.used_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Capture nonce already used")

    stored = await run_in_threadpool(storage.describe, body.object_key)
    if stored is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Photo not found in storage")
    if stored.size_bytes > settings.upload_max_bytes:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Photo exceeds the upload limit")
    if stored.sha256 != body.sha256:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "SHA-256 mismatch between stored photo and client hash",
        )
    if not stored.content_type.startswith("image/"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Stored object is not an image")

    plot_id = None
    if body.plot_code:
        plot_id = await session.scalar(
            select(Plot.id).where(Plot.project_id == project.id, Plot.code == body.plot_code)
        )
        if plot_id is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown plot {body.plot_code}"
            )

    sub = FieldSubmission(
        period_id=period.id,
        project_id=project.id,
        submitted_by=user.id,
        nonce_id=nonce.id,
        plot_id=plot_id,
        kind=body.kind,
        object_key=body.object_key,
        sha256=stored.sha256,
        content_type=stored.content_type,
        size_bytes=stored.size_bytes,
        location=from_shape(Point(body.lon, body.lat), srid=4326),
        gps_accuracy_m=body.gps_accuracy_m,
        heading_deg=body.heading_deg,
        captured_at=body.captured_at,
        device_model=body.device_model,
        app_version=body.app_version,
    )
    nonce.used_at = utcnow()
    session.add(sub)
    await session.flush()
    await audit.record(
        session,
        actor_id=user.id,
        action="submission.received",
        entity_type="field_submission",
        entity_id=sub.id,
        data={"sha256": sub.sha256, "period_id": str(period.id)},
    )
    await session.commit()
    await session.refresh(sub)
    request.app.state.enqueue("aqua.ml.check_submission", str(sub.id), queue="ml")
    return _submission_out(sub)


@router.get("/submissions/{submission_id}", response_model=SubmissionOut)
async def get_submission(
    submission_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> SubmissionOut:
    sub = await session.get(FieldSubmission, submission_id)
    if sub is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Submission not found")
    await _project(session, sub.project_id, user)
    return _submission_out(sub)


@router.get("/periods/{period_id}/submissions", response_model=list[SubmissionOut])
async def list_submissions(
    period_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> list[SubmissionOut]:
    await _period(session, period_id, user)
    rows = await session.scalars(
        select(FieldSubmission)
        .where(FieldSubmission.period_id == period_id)
        .order_by(FieldSubmission.received_at)
    )
    return [_submission_out(s) for s in rows]


# ---------------------------------------------------------------- plots


@router.post("/projects/{project_id}/plots", response_model=PlotOut, status_code=201)
async def create_plot(
    project_id: uuid.UUID, body: PlotCreate, user: CurrentUser, session: SessionDep
) -> PlotOut:
    project = await _project(session, project_id, user)
    _require(user, project, {OrgRole.ADMIN})
    inside = await session.scalar(
        text(
            "SELECT ST_Covers(boundary, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)) FROM projects WHERE id = :id"
        ),
        {"lon": body.lon, "lat": body.lat, "id": project_id},
    )
    if not inside:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Plot centre must lie inside the project boundary",
        )
    exists = await session.scalar(
        select(Plot.id).where(Plot.project_id == project_id, Plot.code == body.code)
    )
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Plot {body.code} already exists")
    plot = Plot(
        project_id=project_id,
        code=body.code,
        stratum=body.stratum,
        centre=from_shape(Point(body.lon, body.lat), srid=4326),
        area_m2=body.area_m2,
        marker_id=body.marker_id,
    )
    session.add(plot)
    await session.commit()
    await session.refresh(plot)
    return _plot_out(plot)


@router.get("/projects/{project_id}/plots", response_model=list[PlotOut])
async def list_plots(
    project_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> list[PlotOut]:
    await _project(session, project_id, user)
    rows = await session.scalars(
        select(Plot).where(Plot.project_id == project_id).order_by(Plot.code)
    )
    return [_plot_out(p) for p in rows]


@router.post(
    "/periods/{period_id}/plots/{plot_id}/measurements",
    response_model=MeasurementOut,
    status_code=201,
)
async def record_measurement(
    period_id: uuid.UUID,
    plot_id: uuid.UUID,
    body: MeasurementCreate,
    user: CurrentUser,
    session: SessionDep,
) -> PlotMeasurement:
    period, project = await _period(session, period_id, user)
    _require(user, project, FIELD_ROLES)
    _require_open(period)
    plot = await session.get(Plot, plot_id)
    if plot is None or plot.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plot not found in this project")
    if not period.start_date <= body.measured_on <= period.end_date:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Measurement date outside the monitoring period"
        )
    if await session.scalar(
        select(PlotMeasurement.id).where(
            PlotMeasurement.plot_id == plot_id, PlotMeasurement.period_id == period_id
        )
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This plot was already measured in this period"
        )
    m = PlotMeasurement(
        plot_id=plot_id,
        period_id=period_id,
        measured_by=user.id,
        measured_on=body.measured_on,
        trees=[t.model_dump() for t in body.trees],
        notes=body.notes,
    )
    session.add(m)
    await session.flush()
    await audit.record(
        session,
        actor_id=user.id,
        action="plot.measured",
        entity_type="plot",
        entity_id=plot_id,
        data={"period_id": str(period_id), "trees": len(body.trees)},
    )
    await session.commit()
    await session.refresh(m)
    return m
