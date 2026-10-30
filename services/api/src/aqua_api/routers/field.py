"""Field evidence: monitoring periods, capture nonces, photo submissions, sample plots."""

import hashlib
import math
import secrets
import uuid
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from geoalchemy2.shape import from_shape
from shapely.geometry import Point
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from aqua_api import idempotency
from aqua_api.deps import (
    CurrentUser,
    SessionDep,
    SettingsDep,
    StorageDep,
    can_read_all_projects,
    org_role,
)
from aqua_api.idempotency import IdempotencyKey
from aqua_api.schemas.field import (
    EvidencePointFeature,
    EvidencePointProperties,
    EvidencePoints,
    EvidencePrecision,
    MeasurementCreate,
    MeasurementOut,
    NonceBatchItem,
    NonceBatchOut,
    NonceBatchRequest,
    NonceOut,
    PeriodCreate,
    PeriodOut,
    PhotoUploadRequest,
    PlotCreate,
    PlotOut,
    PointGeometry,
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
    NonceMode,
    OrgRole,
    PeriodStatus,
    Plot,
    PlotMeasurement,
    Project,
    ProjectStatus,
    User,
)
from aqua_core.models._types import utcnow
from aqua_core.settings import Settings

router = APIRouter(tags=["field evidence"])

FIELD_ROLES = {OrgRole.ADMIN, OrgRole.FIELD_AGENT}
PERIOD_PROJECT_STATUSES = {
    ProjectStatus.SUBMITTED,
    ProjectStatus.BASELINE_READY,
    ProjectStatus.REGISTERED,
    ProjectStatus.MONITORING,
}


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


async def _period(
    session: AsyncSession, period_id: uuid.UUID, user: User, settings: Settings
) -> tuple[MonitoringPeriod, Project]:
    period = await session.get(MonitoringPeriod, period_id)
    if period is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Monitoring period not found")
    return period, await _project(session, period.project_id, user, settings)


def _require(user: User, project: Project, roles: set[OrgRole]) -> None:
    if org_role(user, project.org_id) not in roles:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Not allowed for your role in this organisation"
        )


# A verifier's "request changes" sends the period back to the field team for corrections.
EDITABLE_PERIOD_STATUSES = frozenset({PeriodStatus.OPEN, PeriodStatus.CHANGES_REQUESTED})


def _require_open(period: MonitoringPeriod) -> None:
    if period.status not in EDITABLE_PERIOD_STATUSES:
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
    project_id: uuid.UUID,
    body: PeriodCreate,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
) -> MonitoringPeriod:
    project = await _project(session, project_id, user, settings)
    _require(user, project, {OrgRole.ADMIN})
    if project.status not in PERIOD_PROJECT_STATUSES:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Submit the project before opening monitoring periods"
        )
    open_exists = await session.scalar(
        select(MonitoringPeriod.id).where(
            MonitoringPeriod.project_id == project_id,
            MonitoringPeriod.status.in_(EDITABLE_PERIOD_STATUSES),
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
    project_id: uuid.UUID, user: CurrentUser, session: SessionDep, settings: SettingsDep
) -> list[MonitoringPeriod]:
    await _project(session, project_id, user, settings)
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
    period, project = await _period(session, period_id, user, settings)
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
    return NonceOut(nonce=token, expires_at=nonce.expires_at, issued_at=now, server_time=utcnow())


@router.post("/periods/{period_id}/nonces/batch", response_model=NonceBatchOut, status_code=201)
async def issue_nonce_batch(
    period_id: uuid.UUID,
    body: NonceBatchRequest,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
) -> NonceBatchOut:
    """Pre-issue up to 20 single-use nonces for offline capture (field agents and org admins).

    Valid for `CAPTURE_NONCE_BATCH_TTL_H` (default 72 h), bound to the caller and this period.
    A photo taken with one gets a lower-weight `capture_nonce` check (`nonce_mode: batch`): it
    proves only that the photo was taken after the batch was issued. An agent may hold at most
    `CAPTURE_NONCE_BATCH_MAX_OUTSTANDING` unused, unexpired batch nonces per period (409)."""
    period, project = await _period(session, period_id, user, settings)
    _require(user, project, FIELD_ROLES)
    _require_open(period)
    if body.count > settings.capture_nonce_batch_max:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"At most {settings.capture_nonce_batch_max} nonces per request",
        )
    now = utcnow()
    # Serialise batches of one agent + period so two parallel requests cannot both pass the cap.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"nonce-batch:{user.id}:{period.id}"},
    )
    outstanding = (
        await session.scalar(
            select(func.count())
            .select_from(CaptureNonce)
            .where(
                CaptureNonce.user_id == user.id,
                CaptureNonce.period_id == period.id,
                CaptureNonce.mode == NonceMode.BATCH,
                CaptureNonce.used_at.is_(None),
                CaptureNonce.expires_at > now,
            )
        )
        or 0
    )
    if outstanding + body.count > settings.capture_nonce_batch_max_outstanding:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"You already hold {outstanding} unused offline nonces for this period (limit "
            f"{settings.capture_nonce_batch_max_outstanding}); use or let them expire first",
        )
    expires_at = now + timedelta(hours=settings.capture_nonce_batch_ttl_h)
    tokens = [secrets.token_urlsafe(32) for _ in range(body.count)]
    session.add_all(
        CaptureNonce(
            user_id=user.id,
            period_id=period.id,
            nonce_hash=hashlib.sha256(token.encode()).hexdigest(),
            issued_at=now,
            expires_at=expires_at,
            mode=NonceMode.BATCH,
        )
        for token in tokens
    )
    await audit.record(
        session,
        actor_id=user.id,
        action="nonces.batch_issued",
        entity_type="monitoring_period",
        entity_id=period.id,
        data={"count": body.count, "expires_at": expires_at.isoformat()},
    )
    await session.commit()
    return NonceBatchOut(
        period_id=period.id,
        issued_at=now,
        expires_at=expires_at,
        server_time=utcnow(),
        nonces=[NonceBatchItem(nonce=t, expires_at=expires_at) for t in tokens],
    )


@router.post("/periods/{period_id}/photos/upload-url", response_model=UploadTicket)
async def photo_upload_url(
    period_id: uuid.UUID,
    body: PhotoUploadRequest,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    storage: StorageDep,
) -> UploadTicket:
    period, project = await _period(session, period_id, user, settings)
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
    idempotency_key: IdempotencyKey = None,
) -> SubmissionOut | Response:
    """Create a photo submission (checks run in the worker). With an `Idempotency-Key`, a retry
    within 24 h returns the original 202 response instead of `409 Capture nonce already used`."""
    period, project = await _period(session, period_id, user, settings)
    _require(user, project, FIELD_ROLES)
    fingerprint = idempotency.request_fingerprint(request, body)
    if idempotency_key is not None:
        replayed = await idempotency.replay(
            session,
            user_id=user.id,
            key=idempotency_key,
            fingerprint=fingerprint,
            ttl_h=settings.idempotency_ttl_h,
        )
        if replayed is not None:
            return replayed
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
    await session.refresh(sub)  # server defaults (received_at) for the stored response
    out = _submission_out(sub)
    if idempotency_key is not None:
        await idempotency.remember(
            session,
            user_id=user.id,
            key=idempotency_key,
            scope="submission.create",
            fingerprint=fingerprint,
            status_code=status.HTTP_202_ACCEPTED,
            response=out,
            ttl_h=settings.idempotency_ttl_h,
        )
    await session.commit()
    request.app.state.enqueue("aqua.ml.check_submission", str(sub.id), queue="ml")
    return out


@router.get("/submissions/{submission_id}", response_model=SubmissionOut)
async def get_submission(
    submission_id: uuid.UUID, user: CurrentUser, session: SessionDep, settings: SettingsDep
) -> SubmissionOut:
    sub = await session.get(FieldSubmission, submission_id)
    if sub is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Submission not found")
    await _project(session, sub.project_id, user, settings)
    return _submission_out(sub)


TOTAL_COUNT_HEADER = "X-Total-Count"


@router.get("/periods/{period_id}/submissions", response_model=list[SubmissionOut])
async def list_submissions(
    period_id: uuid.UUID,
    response: Response,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    sha256: Annotated[str | None, Query(pattern=r"^[0-9a-f]{64}$")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[SubmissionOut]:
    """Submissions of a period in arrival order, `limit`/`offset` paginated; the total (after
    the optional `sha256` filter) is in the `X-Total-Count` header."""
    await _period(session, period_id, user, settings)
    query = select(FieldSubmission).where(FieldSubmission.period_id == period_id)
    if sha256 is not None:
        query = query.where(FieldSubmission.sha256 == sha256)
    total = await session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = await session.scalars(
        query.order_by(FieldSubmission.received_at, FieldSubmission.id).limit(limit).offset(offset)
    )
    response.headers[TOTAL_COUNT_HEADER] = str(total)
    return [_submission_out(s) for s in rows]


# ---------------------------------------------------------------- evidence points (map)

MAX_EVIDENCE_POINTS = 5000


def _can_see_raw_points(user: User, project: Project, settings: Settings) -> bool:
    """Raw photo GPS: the project's field team (org admins, field agents), who took the photos and
    walk back to the plots, and staff (verifiers, registry admins) auditing the geofence. Not plain
    org members (including the Explore Demo visitor) and nobody outside the org."""
    return org_role(user, project.org_id) in FIELD_ROLES or can_read_all_projects(user, settings)


def _grid_centre(value: float, cell: float) -> float:
    return round((math.floor(value / cell) + 0.5) * cell, 6)


@router.get("/projects/{project_id}/evidence-points", response_model=EvidencePoints)
async def evidence_points(
    project_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    precision: EvidencePrecision = "snapped",
    period_id: uuid.UUID | None = None,
) -> EvidencePoints:
    """Field photos as map points with status and trust score only (GeoJSON).

    `snapped` (default; anyone who can see the project): a photo linked to a plot is placed on
    the plot centre; any other photo on the centre of its `EVIDENCE_GRID_DEG` grid cell (0.005°,
    ~0.5 km). The photo's own GPS fix is never returned, and snapped points carry no submission id
    or capture time. `raw` (field team and staff only, else 403): the app-reported fix, id and
    capture time. At most 5000 points, newest first (`truncated` says if more exist)."""
    project = await _project(session, project_id, user, settings)
    if precision == "raw" and not _can_see_raw_points(user, project, settings):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Raw photo locations are only for the project's field team and staff",
        )
    query = (
        select(
            FieldSubmission,
            Plot.code,
            func.ST_X(Plot.centre),
            func.ST_Y(Plot.centre),
        )
        .outerjoin(Plot, Plot.id == FieldSubmission.plot_id)
        .where(FieldSubmission.project_id == project_id)
    )
    if period_id is not None:
        query = query.where(FieldSubmission.period_id == period_id)
    total = await session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = (
        await session.execute(
            query.order_by(FieldSubmission.received_at.desc(), FieldSubmission.id).limit(
                MAX_EVIDENCE_POINTS
            )
        )
    ).all()
    cell = settings.evidence_grid_deg
    features = []
    for sub, plot_code, plot_lon, plot_lat in rows:
        props = EvidencePointProperties(
            snap="raw",
            status=sub.status,
            trust_score=sub.trust_score,
            kind=sub.kind,
            period_id=sub.period_id,
            plot_code=plot_code,
        )
        if precision == "raw":
            point = to_point(sub.location)
            lon, lat = point.x, point.y
            props.id, props.captured_at, props.gps_accuracy_m = (
                sub.id,
                sub.captured_at,
                sub.gps_accuracy_m,
            )
        elif plot_code is not None:
            lon, lat = round(plot_lon, 6), round(plot_lat, 6)
            props.snap = "plot_centre"
        else:
            point = to_point(sub.location)
            lon, lat = _grid_centre(point.x, cell), _grid_centre(point.y, cell)
            props.snap = "grid"
        features.append(
            EvidencePointFeature(geometry=PointGeometry(coordinates=(lon, lat)), properties=props)
        )
    return EvidencePoints(
        precision=precision,
        grid_deg=cell,
        features=features,
        total=total,
        truncated=total > len(features),
    )


# ---------------------------------------------------------------- plots


@router.post("/projects/{project_id}/plots", response_model=PlotOut, status_code=201)
async def create_plot(
    project_id: uuid.UUID,
    body: PlotCreate,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
) -> PlotOut:
    project = await _project(session, project_id, user, settings)
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
    project_id: uuid.UUID, user: CurrentUser, session: SessionDep, settings: SettingsDep
) -> list[PlotOut]:
    await _project(session, project_id, user, settings)
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
    request: Request,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    idempotency_key: IdempotencyKey = None,
) -> MeasurementOut | Response:
    """Record a plot's tree measurements for the period (one per plot and period). With an
    `Idempotency-Key`, a retry within 24 h returns the original 201 response instead of
    `409 already measured`."""
    period, project = await _period(session, period_id, user, settings)
    _require(user, project, FIELD_ROLES)
    fingerprint = idempotency.request_fingerprint(request, body)
    if idempotency_key is not None:
        replayed = await idempotency.replay(
            session,
            user_id=user.id,
            key=idempotency_key,
            fingerprint=fingerprint,
            ttl_h=settings.idempotency_ttl_h,
        )
        if replayed is not None:
            return replayed
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
    await session.refresh(m)  # server default created_at for the stored response
    out = MeasurementOut.model_validate(m)
    if idempotency_key is not None:
        await idempotency.remember(
            session,
            user_id=user.id,
            key=idempotency_key,
            scope="measurement.create",
            fingerprint=fingerprint,
            status_code=status.HTTP_201_CREATED,
            response=out,
            ttl_h=settings.idempotency_ttl_h,
        )
    await session.commit()
    return out


async def _measurements(
    session: AsyncSession, period_id: uuid.UUID, plot_id: uuid.UUID | None
) -> list[PlotMeasurement]:
    query = select(PlotMeasurement).where(PlotMeasurement.period_id == period_id)
    if plot_id is not None:
        query = query.where(PlotMeasurement.plot_id == plot_id)
    rows = await session.scalars(query.order_by(PlotMeasurement.created_at, PlotMeasurement.id))
    return list(rows)


@router.get("/periods/{period_id}/measurements", response_model=list[MeasurementOut])
async def list_measurements(
    period_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    plot_id: uuid.UUID | None = None,
) -> list[PlotMeasurement]:
    """Plot measurements recorded in a period (optionally one plot's), oldest first."""
    await _period(session, period_id, user, settings)
    return await _measurements(session, period_id, plot_id)


@router.get(
    "/periods/{period_id}/plots/{plot_id}/measurements", response_model=list[MeasurementOut]
)
async def list_plot_measurements(
    period_id: uuid.UUID,
    plot_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
) -> list[PlotMeasurement]:
    """Read-back of one plot's measurement in the period: `[]` or one item (a plot is measured
    once per period). Lets the app confirm a record after a lost response."""
    _, project = await _period(session, period_id, user, settings)
    plot = await session.get(Plot, plot_id)
    if plot is None or plot.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plot not found in this project")
    return await _measurements(session, period_id, plot_id)
