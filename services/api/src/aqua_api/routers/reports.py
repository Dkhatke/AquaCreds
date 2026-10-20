"""MRV reports and the verifier review workflow.

Flow: an org admin requests a report for a period (`analysing`) → the `reports` worker builds the
hashed record from the period's data through `aqua_mrv`/`aqua_carbon` (`under_review`) → an
independent verifier approves, requests changes or rejects → on approval the `chain` worker anchors
the report hash on-chain (`aqua.reports.anchor`: register project if needed → submitReport with the
registrar key → approveReport with the verifier key; the tx ids are recorded on the report).

Conflict of interest: a verifier who belongs to the project's organisation cannot review it.
"""

import json
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from aqua_api.deps import (
    CurrentUser,
    SessionDep,
    StorageDep,
    can_read_all_projects,
    org_role,
    require_platform_role,
)
from aqua_api.schemas.report import (
    ReportDetail,
    ReportDownloads,
    ReportOut,
    ReportRequest,
    ReportRequested,
    ReportVerification,
    ReviewRequest,
)
from aqua_api.services import audit
from aqua_core.models import (
    ChainEvent,
    ChainTransaction,
    MonitoringPeriod,
    MRVReport,
    OrgRole,
    PeriodKind,
    PeriodStatus,
    PlatformRole,
    Plot,
    PlotMeasurement,
    Project,
    ProjectStatus,
    ReportStatus,
    User,
)
from aqua_core.models._types import utcnow

router = APIRouter(tags=["mrv reports"])

OnchainSource = Literal["indexer", "receipt"]

Verifier = Annotated[User, Depends(require_platform_role(PlatformRole.VERIFIER))]
REPORTS_QUEUE = "reports"
CHAIN_QUEUE = "chain"
REPORTABLE_PROJECT_STATUSES = {
    ProjectStatus.SUBMITTED,
    ProjectStatus.BASELINE_READY,
    ProjectStatus.CHANGES_REQUESTED,
    ProjectStatus.REGISTERED,
    ProjectStatus.MONITORING,
}
REPORTABLE_PERIOD_STATUSES = {PeriodStatus.OPEN, PeriodStatus.CHANGES_REQUESTED}
# Project statuses before the first approved report (registration not yet granted).
PRE_REGISTRATION = {
    ProjectStatus.SUBMITTED,
    ProjectStatus.BASELINE_READY,
    ProjectStatus.CHANGES_REQUESTED,
}

# decision → (report status, period status)
DECISIONS: dict[str, tuple[ReportStatus, PeriodStatus]] = {
    "approve": (ReportStatus.APPROVED, PeriodStatus.APPROVED),
    "request_changes": (ReportStatus.CHANGES_REQUESTED, PeriodStatus.CHANGES_REQUESTED),
    "reject": (ReportStatus.REJECTED, PeriodStatus.REJECTED),
}


# ---------------------------------------------------------------- helpers


async def _visible_project(session: AsyncSession, project_id: uuid.UUID, user: User) -> Project:
    project = await session.get(Project, project_id)
    if project is None or (
        org_role(user, project.org_id) is None and not can_read_all_projects(user)
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return project


async def _visible_report(
    session: AsyncSession, report_id: uuid.UUID, user: User, *, lock: bool = False
) -> tuple[MRVReport, Project]:
    report = await session.get(MRVReport, report_id, with_for_update=lock)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found")
    try:
        project = await _visible_project(session, report.project_id, user)
    except HTTPException as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found") from exc
    return report, project


def project_transition(kind: PeriodKind, decision: str, current: ProjectStatus) -> ProjectStatus:
    """Project lifecycle effect of a report decision (IMPLEMENTATION_PLAN §3).

    Baseline-period reports decide registration: approve → registered, request changes →
    changes_requested, reject → rejected (only while the project is not yet registered).
    Monitoring-period reports: approval moves the project to monitoring; changes/rejection of a
    monitoring report never change the project status."""
    if kind is PeriodKind.BASELINE:
        if current in PRE_REGISTRATION:
            return {
                "approve": ProjectStatus.REGISTERED,
                "request_changes": ProjectStatus.CHANGES_REQUESTED,
                "reject": ProjectStatus.REJECTED,
            }[decision]
        return current
    if decision == "approve" and current in PRE_REGISTRATION | {ProjectStatus.REGISTERED}:
        return ProjectStatus.MONITORING
    return current


# ---------------------------------------------------------------- generate


@router.post(
    "/periods/{period_id}/reports",
    response_model=ReportRequested,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_report(
    period_id: uuid.UUID,
    request: Request,
    user: CurrentUser,
    session: SessionDep,
    body: ReportRequest | None = None,
) -> ReportRequested:
    """Queue the MRV report for a period (org admins). The report is built in the background;
    poll `GET /periods/{id}/reports`."""
    body = body or ReportRequest()
    period = await session.get(MonitoringPeriod, period_id, with_for_update=True)
    if period is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Monitoring period not found")
    try:
        project = await _visible_project(session, period.project_id, user)
    except HTTPException as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Monitoring period not found") from exc
    if org_role(user, project.org_id) != OrgRole.ADMIN:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Only organisation admins can request MRV reports"
        )
    if project.status not in REPORTABLE_PROJECT_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Project is {project.status}")
    if period.status not in REPORTABLE_PERIOD_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Monitoring period is {period.status}")

    strata = set(
        await session.scalars(
            select(Plot.stratum)
            .join(PlotMeasurement, PlotMeasurement.plot_id == Plot.id)
            .where(PlotMeasurement.period_id == period.id)
            .distinct()
        )
    )
    if not strata:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Record plot measurements before requesting a report"
        )
    if body.strata_area_ha is not None:
        if set(body.strata_area_ha) != strata:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"strata_area_ha must list exactly the measured strata {sorted(strata)}",
            )
        if sum(body.strata_area_ha.values()) > project.area_ha * (1 + 1e-9):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"Stratum areas exceed the project area ({project.area_ha:.2f} ha)",
            )
        strata_area = dict(body.strata_area_ha)
    elif len(strata) == 1:
        strata_area = {next(iter(strata)): project.area_ha}
    else:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Plots cover several strata {sorted(strata)}: give strata_area_ha for each",
        )

    restore = period.status
    options = {
        "strata_area_ha": strata_area,
        "planting": [c.model_dump() for c in body.planting],
        "include_soc": body.include_soc,
        "notes": body.notes,
        "restore_status": restore.value,
    }
    period.status = PeriodStatus.ANALYSING
    await audit.record(
        session,
        actor_id=user.id,
        action="report.requested",
        entity_type="monitoring_period",
        entity_id=period.id,
        data={
            "from": restore.value,
            "to": PeriodStatus.ANALYSING.value,
            "strata_area_ha": strata_area,
        },
    )
    await session.commit()
    try:
        request.app.state.enqueue(
            "aqua.reports.build_report",
            str(period.id),
            str(user.id),
            json.dumps(options, sort_keys=True),
            queue=REPORTS_QUEUE,
        )
    except Exception as exc:
        period.status = restore
        await audit.record(
            session,
            actor_id=user.id,
            action="report.enqueue_failed",
            entity_type="monitoring_period",
            entity_id=period.id,
            data={"from": PeriodStatus.ANALYSING.value, "to": restore.value},
        )
        await session.commit()
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Task queue unavailable, try again later"
        ) from exc
    return ReportRequested(
        period_id=period.id, period_status=PeriodStatus.ANALYSING, strata_area_ha=strata_area
    )


# ---------------------------------------------------------------- read


@router.get("/periods/{period_id}/reports", response_model=list[ReportOut])
async def list_reports(
    period_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> list[MRVReport]:
    period = await session.get(MonitoringPeriod, period_id)
    if period is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Monitoring period not found")
    await _visible_project(session, period.project_id, user)
    rows = await session.scalars(
        select(MRVReport).where(MRVReport.period_id == period_id).order_by(MRVReport.version.desc())
    )
    return list(rows)


@router.get("/reports/{report_id}", response_model=ReportDetail)
async def get_report(
    report_id: uuid.UUID, user: CurrentUser, session: SessionDep, storage: StorageDep
) -> ReportDetail:
    report, _ = await _visible_report(session, report_id, user)
    stem = f"mrv-report-{report.id}-v{report.version}"
    downloads = ReportDownloads(
        json_url=storage.presign_get(report.json_key, f"{stem}.json"),
        pdf_url=storage.presign_get(report.pdf_key, f"{stem}.pdf"),
        csv_url=storage.presign_get(report.csv_key, f"{stem}-csv.zip"),
    )
    return ReportDetail(**ReportOut.model_validate(report).model_dump(), downloads=downloads)


# ---------------------------------------------------------------- review


@router.post("/reports/{report_id}/review", response_model=ReportOut)
async def review_report(
    report_id: uuid.UUID,
    body: ReviewRequest,
    request: Request,
    user: Verifier,
    session: SessionDep,
) -> MRVReport:
    """Verifier decision. The verifier must be independent of the project's organisation."""
    report, project = await _visible_report(session, report_id, user, lock=True)
    if org_role(user, project.org_id) is not None or report.generated_by == user.id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Conflict of interest: verifiers cannot review projects of their own organisation",
        )
    if report.status is not ReportStatus.UNDER_REVIEW:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Report is {report.status}")
    period = await session.get(MonitoringPeriod, report.period_id, with_for_update=True)
    if period is None or period.status is not PeriodStatus.UNDER_REVIEW:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Monitoring period is {period.status if period else 'missing'}",
        )
    await session.refresh(project, with_for_update=True)

    report_status, period_status = DECISIONS[body.decision]
    new_project_status = project_transition(period.kind, body.decision, project.status)
    report.status = report_status
    report.verifier_id = user.id
    report.decided_at = utcnow()
    report.decision_comment = body.comment.strip() or None
    await audit.record(
        session,
        actor_id=user.id,
        action=f"report.{report_status.value}",
        entity_type="mrv_report",
        entity_id=report.id,
        data={
            "from": ReportStatus.UNDER_REVIEW.value,
            "to": report_status.value,
            "report_hash": report.report_hash,
            "comment": report.decision_comment,
        },
    )
    await audit.record(
        session,
        actor_id=user.id,
        action="period.status_changed",
        entity_type="monitoring_period",
        entity_id=period.id,
        data={"from": period.status.value, "to": period_status.value, "report_id": str(report.id)},
    )
    period.status = period_status
    if new_project_status is not project.status:
        await audit.record(
            session,
            actor_id=user.id,
            action="project.status_changed",
            entity_type="project",
            entity_id=project.id,
            data={
                "from": project.status.value,
                "to": new_project_status.value,
                "report_id": str(report.id),
            },
        )
        project.status = new_project_status
    await session.commit()

    if report_status is ReportStatus.APPROVED:
        try:
            request.app.state.enqueue("aqua.reports.anchor", str(report.id), queue=CHAIN_QUEUE)
        except Exception:
            # The decision stands; anchoring can be re-queued by an operator.
            report.anchor_error = "could not enqueue on-chain anchoring"
            await session.commit()
    await session.refresh(report)
    return report


# ---------------------------------------------------------------- verify


def _norm(hex_hash: str) -> str:
    return hex_hash.lower().removeprefix("0x")


async def _onchain_hash(
    session: AsyncSession, report: MRVReport
) -> tuple[str | None, OnchainSource | None]:
    """reportHash from the ReportSubmitted event of the report's submit transaction: the indexed
    copy if the indexer has seen it, else the relayer's decoded receipt."""
    if report.submit_tx_id is None:
        return None, None
    tx = await session.get(ChainTransaction, report.submit_tx_id)
    if tx is None or tx.tx_hash is None:
        return None, None
    event = await session.scalar(
        select(ChainEvent).where(
            ChainEvent.tx_hash == tx.tx_hash, ChainEvent.event == "ReportSubmitted"
        )
    )
    if event is not None and "reportHash" in event.args:
        return _norm(str(event.args["reportHash"])), "indexer"
    for item in (tx.result or {}).get("events", []):
        if item.get("event") == "ReportSubmitted" and "reportHash" in item.get("args", {}):
            return _norm(str(item["args"]["reportHash"])), "receipt"
    return None, None


@router.get("/reports/{report_id}/verify", response_model=ReportVerification)
async def verify_report(
    report_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    storage: StorageDep,
) -> ReportVerification:
    """Recompute SHA-256 of the stored canonical JSON and compare it with the recorded hash and,
    once anchored, with the hash on-chain."""
    report, _ = await _visible_report(session, report_id, user)
    obj = await run_in_threadpool(storage.describe, report.json_key)  # streams + hashes
    stored = obj.sha256 if obj is not None else None
    onchain, source = await _onchain_hash(session, report)
    stored_ok = stored == report.report_hash
    onchain_ok = None if onchain is None else onchain == report.report_hash
    return ReportVerification(
        report_id=report.id,
        report_hash=report.report_hash,
        stored_hash=stored,
        stored_matches=stored_ok,
        onchain_hash=onchain,
        onchain_source=source,
        onchain_matches=onchain_ok,
        ok=stored_ok and onchain_ok is not False,
    )
