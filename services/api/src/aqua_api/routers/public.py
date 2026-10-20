"""Public transparency registry (Phase 8): read-only, no login.

Anyone can inspect non-draft, non-rejected projects, their approved MRV reports, credit vintages
and retirements, verify an MRV report hash against the chain (or the registry while anchoring is
pending), and fetch a QR code for a retirement certificate.

Privacy (RESEARCH_NOTES Q1, IMPLEMENTATION_PLAN §8): responses are built only from the allow-listed
schemas in `aqua_api.schemas.public`: project-level facts, aggregates, hashes and on-chain
records. No user identities, photo object keys/URLs, photo coordinates, tenure documents or audit
log entries are ever returned. Only approved MRV reports are public; their PDF is served through
a short-lived presigned URL behind a stable redirect, their JSON record only as an allow-listed
public variant (see `aqua_api.services.public_reports`), and the CSV export not at all.

Caching: JSON responses carry `Cache-Control: public, max-age=60` and a weak ETag (304 on
`If-None-Match`); QR images are cached for a day.
"""

import hashlib
import io
import uuid
from typing import Annotated, Any, Literal

import segno
from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool

from aqua_api.deps import SessionDep, SettingsDep, StorageDep
from aqua_api.schemas.analysis import TimeseriesIndex
from aqua_api.schemas.public import (
    BoundaryInfo,
    Page,
    PeriodSummary,
    ProjectRef,
    PublicProjectDetail,
    PublicProjectSummary,
    PublicRecordVariant,
    PublicReport,
    PublicReportDetail,
    PublicTimeseries,
    PublicTimeseriesPoint,
    ReportDownloadLinks,
    RetirementDetail,
    RetirementOut,
    VerifyHashOut,
    VerifyHashRequest,
    VintageOut,
    normalise_sha256,
)
from aqua_api.services import public_registry as reg
from aqua_api.services import public_reports as pub_reports
from aqua_core.models import (
    ChainEvent,
    Ecosystem,
    MonitoringPeriod,
    MRVReport,
    Organization,
    Project,
    ProjectStatus,
    SceneObservation,
)

router = APIRouter(prefix="/public", tags=["public registry"])

MAX_AGE_S = 60
QR_MAX_AGE_S = 86_400
PUBLIC_STATUSES = [s for s in ProjectStatus if s not in reg.HIDDEN_STATUSES]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]


def cached(request: Request, payload: BaseModel, max_age: int = MAX_AGE_S) -> Response:
    """JSON with public cache headers and a weak ETag; 304 when the client already has it."""
    body = payload.model_dump_json().encode()
    etag = f'W/"{hashlib.sha256(body).hexdigest()[:32]}"'
    headers = {"Cache-Control": f"public, max-age={max_age}", "ETag": etag}
    match = request.headers.get("if-none-match", "")
    if etag in {tag.strip() for tag in match.split(",")}:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    return Response(body, media_type="application/json", headers=headers)


async def _public_project(
    session: SessionDep, project_id: uuid.UUID
) -> tuple[Project, Organization]:
    row = (
        await session.execute(reg.public_projects_query().where(Project.id == project_id))
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return row[0], row[1]


def _links(request: Request) -> pub_reports.LinksFor:
    def links(report_id: uuid.UUID) -> ReportDownloadLinks:
        return ReportDownloadLinks(
            pdf_url=str(request.url_for("public_report_pdf", report_id=report_id)),
            record_url=str(request.url_for("public_report_record", report_id=report_id)),
        )

    return links


# ---------------------------------------------------------------- projects


@router.get("/projects", response_model=Page[PublicProjectSummary])
async def list_projects(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    state: str | None = None,
    ecosystem: Ecosystem | None = None,
    project_status: Annotated[ProjectStatus | None, Query(alias="status")] = None,
    limit: Limit = 20,
    offset: Offset = 0,
) -> Response:
    """Registered and in-review projects (drafts and rejected projects are never listed)."""
    query = reg.public_projects_query()
    if state:
        query = query.where(func.lower(Project.state) == state.lower())
    if ecosystem:
        query = query.where(Project.ecosystem == ecosystem)
    if project_status:
        if project_status not in PUBLIC_STATUSES:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Status is not public")
        query = query.where(Project.status == project_status)
    total = await session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = (
        await session.execute(
            query.order_by(Project.created_at, Project.id).limit(limit).offset(offset)
        )
    ).all()
    ids = [p.id for p, _ in rows]
    centroids = await reg.centroids(session, ids)
    keys = {pid: reg.project_key(pid) for pid in ids}
    registered = await reg.registered_keys(session, settings.chain_id, list(keys.values()))
    items = [reg.summary(p, org, centroids[p.id], keys[p.id] in registered) for p, org in rows]
    return cached(
        request,
        Page[PublicProjectSummary](items=items, total=total, limit=limit, offset=offset),
    )


@router.get("/projects/{project_id}", response_model=PublicProjectDetail)
async def get_project(
    project_id: uuid.UUID, request: Request, session: SessionDep, settings: SettingsDep
) -> Response:
    project, org = await _public_project(session, project_id)
    chain_id = settings.chain_id
    registration = await reg.registration(session, chain_id, project.id)
    centroid = (await reg.centroids(session, [project.id]))[project.id]
    periods = await session.scalars(
        select(MonitoringPeriod)
        .where(MonitoringPeriod.project_id == project.id)
        .order_by(MonitoringPeriod.start_date)
    )
    detail = PublicProjectDetail(
        **reg.summary(project, org, centroid, registration is not None).model_dump(),
        description=project.description,
        boundary=BoundaryInfo(
            geometry=await reg.simplified_boundary(session, project.id),
            simplification="ST_SimplifyPreserveTopology",
            tolerance_deg=reg.BOUNDARY_TOLERANCE_DEG,
        ),
        periods=[
            PeriodSummary(
                id=p.id, kind=p.kind, status=p.status, start_date=p.start_date, end_date=p.end_date
            )
            for p in periods
        ],
        evidence=await reg.evidence_stats(session, project.id),
        satellite=await reg.satellite_indicators(session, project.id),
        alerts=await reg.alert_counts(session, project.id),
        registration=registration,
        reports=await reg.project_reports(session, chain_id, project.id),
        mrv_reports=await pub_reports.project_reports(session, project.id, _links(request)),
        vintages=await reg.vintages(session, chain_id, project_id=project.id),
    )
    return cached(request, detail)


@router.get("/projects/{project_id}/timeseries", response_model=PublicTimeseries)
async def project_timeseries(
    project_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    index: TimeseriesIndex = "ndvi",
    min_clear: Annotated[float, Query(ge=reg.PUBLIC_MIN_CLEAR, le=1)] = reg.PUBLIC_MIN_CLEAR,
) -> Response:
    """Project-level zonal statistics per Sentinel-2 scene (clear scenes only)."""
    await _public_project(session, project_id)
    rows = await session.scalars(
        select(SceneObservation)
        .where(
            SceneObservation.project_id == project_id,
            SceneObservation.clear_fraction >= min_clear,
        )
        .order_by(SceneObservation.acquired_at, SceneObservation.item_id)
    )
    points = []
    for obs in rows:
        stats = obs.index_stats.get(index) or {}
        if stats.get("p50") is None:
            continue
        points.append(
            PublicTimeseriesPoint(
                date=obs.acquired_on,
                item_id=obs.item_id,
                clear_fraction=obs.clear_fraction,
                valid_pixels=int(stats.get("valid_pixels", 0)),
                mean=stats.get("mean"),
                p10=stats.get("p10"),
                p50=stats.get("p50"),
                p90=stats.get("p90"),
            )
        )
    return cached(
        request,
        PublicTimeseries(
            project_id=project_id, index=index, min_clear_fraction=min_clear, points=points
        ),
    )


# ---------------------------------------------------------------- MRV reports


async def _approved(
    session: SessionDep, request: Request, report_id: uuid.UUID
) -> tuple[MRVReport, PublicReport]:
    found = await pub_reports.approved_report(session, report_id, _links(request))
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found")
    return found


async def _record(storage: StorageDep, report: MRVReport) -> dict[str, Any]:
    """The stored canonical record (hash-checked); storage or integrity failures are a 502."""
    try:
        data = await run_in_threadpool(
            storage.get_bytes, report.json_key, pub_reports.MAX_RECORD_BYTES
        )
    except Exception as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"MRV record unavailable ({type(exc).__name__})"
        ) from exc
    try:
        return pub_reports.parse_record(data, report.report_hash)
    except (pub_reports.RecordUnavailableError, ValueError) as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.get("/reports/{report_id}", response_model=PublicReportDetail)
async def get_report(
    report_id: uuid.UUID, request: Request, session: SessionDep, storage: StorageDep
) -> Response:
    """An approved MRV report with the methodology profile and software versions read from its
    stored record (hash-checked). Unapproved reports are 404."""
    report, public = await _approved(session, request, report_id)
    project = await session.get(Project, report.project_id)
    if project is None:  # pragma: no cover - FK guarantees it
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found")
    record = await _record(storage, report)
    detail = PublicReportDetail(
        **public.model_dump(),
        project=ProjectRef(
            id=project.id, name=project.name, project_key=reg.project_key(project.id)
        ),
        project_methodology=project.methodology,
        tree_stock_tco2e=report.tree_stock_tco2e,
        tccc_tco2e=report.tccc_tco2e,
        traceability=pub_reports.traceability(record),
    )
    return cached(request, detail)


@router.get("/reports/{report_id}/record.json", name="public_report_record")
async def get_report_record(
    report_id: uuid.UUID, request: Request, session: SessionDep, storage: StorageDep
) -> Response:
    """Public variant of the hashed record: notes, the full-precision boundary and plot
    coordinates are removed (listed in `redacted`)."""
    report, _ = await _approved(session, request, report_id)
    variant, redacted = pub_reports.public_record(await _record(storage, report))
    return cached(
        request,
        PublicRecordVariant(
            report_id=report.id,
            full_record_sha256=report.report_hash,
            redacted=redacted,
            record=variant,
        ),
    )


@router.get(
    "/reports/{report_id}/report.pdf",
    name="public_report_pdf",
    status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    response_class=RedirectResponse,
)
async def get_report_pdf(
    report_id: uuid.UUID, request: Request, session: SessionDep, storage: StorageDep
) -> RedirectResponse:
    """Redirect to a short-lived presigned download of the report PDF."""
    report, _ = await _approved(session, request, report_id)
    url = storage.presign_get(report.pdf_key, f"mrv-report-{report.id}-v{report.version}.pdf")
    return RedirectResponse(
        url, status.HTTP_307_TEMPORARY_REDIRECT, headers={"Cache-Control": "no-store"}
    )


# ---------------------------------------------------------------- vintages


@router.get("/vintages", response_model=Page[VintageOut])
async def list_vintages(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    project_id: uuid.UUID | None = None,
    limit: Limit = 20,
    offset: Offset = 0,
) -> Response:
    items = await reg.vintages(session, settings.chain_id, project_id=project_id)
    return cached(
        request,
        Page[VintageOut](
            items=items[offset : offset + limit], total=len(items), limit=limit, offset=offset
        ),
    )


@router.get("/vintages/{token_id}", response_model=VintageOut)
async def get_vintage(
    token_id: int, request: Request, session: SessionDep, settings: SettingsDep
) -> Response:
    found = await reg.vintages(session, settings.chain_id, token_id=token_id)
    if not found:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Vintage not found")
    return cached(request, found[0])


# ---------------------------------------------------------------- retirements


@router.get("/retirements", response_model=Page[RetirementOut])
async def list_retirements(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    limit: Limit = 20,
    offset: Offset = 0,
) -> Response:
    found = [r for r, _ in await reg.retirements(session, settings.chain_id)]
    return cached(
        request,
        Page[RetirementOut](
            items=found[offset : offset + limit], total=len(found), limit=limit, offset=offset
        ),
    )


async def _retirement(
    session: SessionDep, chain_id: int, retirement_id: int
) -> tuple[RetirementOut, ChainEvent]:
    found = await reg.retirements(session, chain_id, retirement_id=retirement_id)
    if not found:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Retirement not found")
    return found[0]


@router.get("/retirements/{retirement_id}", response_model=RetirementDetail)
async def get_retirement(
    retirement_id: int, request: Request, session: SessionDep, settings: SettingsDep
) -> Response:
    retirement, event = await _retirement(session, settings.chain_id, retirement_id)
    base = str(request.url_for("retirement_qr", retirement_id=retirement_id, fmt="png"))
    detail = RetirementDetail(
        **retirement.model_dump(),
        verification=reg.certificate_verification(settings.public_base_url, retirement, event),
        qr_png_url=base,
        qr_svg_url=base.removesuffix(".png") + ".svg",
    )
    return cached(request, detail)


@router.get(
    "/retirements/{retirement_id}/qr.{fmt}",
    name="retirement_qr",
    responses={200: {"content": {"image/png": {}, "image/svg+xml": {}}}},
    response_class=Response,
)
async def retirement_qr(
    retirement_id: int,
    fmt: Literal["png", "svg"],
    session: SessionDep,
    settings: SettingsDep,
) -> Response:
    """QR code of the public certificate URL (`PUBLIC_BASE_URL/registry/retirements/{id}`)."""
    await _retirement(session, settings.chain_id, retirement_id)
    url = reg.certificate_url(settings.public_base_url, retirement_id)
    qr = segno.make(url, error="m", micro=False)
    buffer = io.BytesIO()
    qr.save(buffer, kind=fmt, scale=8, border=4)
    media = "image/png" if fmt == "png" else "image/svg+xml"
    return Response(
        buffer.getvalue(),
        media_type=media,
        headers={
            "Cache-Control": f"public, max-age={QR_MAX_AGE_S}",
            "ETag": f'"{hashlib.sha256(buffer.getvalue()).hexdigest()[:32]}"',
        },
    )


# ---------------------------------------------------------------- hash verification


async def _verify(request: Request, session: SessionDep, chain_id: int, digest: str) -> Response:
    matches = await reg.verify_hash(session, chain_id, digest)
    return cached(request, VerifyHashOut(sha256_hex=digest, found=bool(matches), matches=matches))


@router.post("/verify-hash", response_model=VerifyHashOut)
async def verify_hash_post(
    body: VerifyHashRequest, request: Request, session: SessionDep, settings: SettingsDep
) -> Response:
    """Is this SHA-256 (of a downloaded MRV record's RFC 8785 JSON) anchored on-chain in a
    ReportSubmitted event (`source="chain"`), or the hash of an approved report in this registry
    whose anchoring is not indexed yet (`source="registry_db"`)? Idempotent: no writes."""
    return await _verify(request, session, settings.chain_id, body.sha256_hex)


@router.get("/verify-hash", response_model=VerifyHashOut)
async def verify_hash_get(
    sha256_hex: str, request: Request, session: SessionDep, settings: SettingsDep
) -> Response:
    try:
        digest = normalise_sha256(sha256_hex)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return await _verify(request, session, settings.chain_id, digest)
