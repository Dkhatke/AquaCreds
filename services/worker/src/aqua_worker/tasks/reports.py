"""Celery tasks for MRV reports: build (queue `reports`) and anchor on-chain (queue `chain`).

`aqua.reports.build_report(period_id, actor_id, options_json)` assembles `aqua_mrv` inputs from the
database (project, period, plot measurements, field-evidence and satellite summaries, the previous
approved report), runs the carbon engine through `aqua_mrv.build_report`, stores the canonical JSON,
PDF and CSV in object storage and records an `MRVReport` row (`under_review`). Every carbon figure
comes from `aqua_carbon` via `aqua_mrv`; nothing is computed here.

Stratum areas (`strata_area_ha`): plots carry a stratum label but no stratum area, so areas are an
explicit input from the requesting org admin (API body), in hectares, one per stratum that has
plot measurements in the period. Only when the measured plots all belong to a *single* stratum and
no areas are given does the whole project area (geodesic `projects.area_ha`) become that stratum's
area. With several strata and no areas the build is refused rather than guessing weights.

Verification period: t1 = end year of the previous approved monitoring-period report, or the
project start year; t2 = the period's end year (this is what `AquaRegistry.submitReport` checks:
`periodEnd > lastVerifiedYear`). Baseline-period reports are never used as the previous report.

`aqua.reports.anchor(report_id)` runs after a verifier approves: it makes sure the project is
registered on-chain, then submits the report hash (registrar key) and approves it (verifier key),
recording the transaction ids on the report. It is a resumable state machine: each run advances as
far as confirmations allow and the Celery wrapper re-schedules it until it is anchored.
"""

import asyncio
import json
import math
import statistics
import uuid
from collections import Counter
from datetime import datetime
from typing import Any

from celery import shared_task
from geoalchemy2.shape import to_shape
from shapely.geometry import mapping
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aqua_chain import actions
from aqua_chain.errors import RetryableChainError
from aqua_chain.relayer import Relayer
from aqua_chain.runtime import build_relayer
from aqua_core import audit
from aqua_core.db import create_engine, session_factory
from aqua_core.geometry import to_point
from aqua_core.models import (
    AnalysisRun,
    AnalysisStatus,
    ChainTransaction,
    ChainTxPurpose,
    ChainTxStatus,
    FieldSubmission,
    Methodology,
    MonitoringPeriod,
    MRVReport,
    PeriodKind,
    PeriodStatus,
    Plot,
    PlotMeasurement,
    Project,
    ReportStatus,
    SceneObservation,
    SubmissionStatus,
)
from aqua_core.models._types import utcnow
from aqua_core.settings import get_settings
from aqua_core.storage import ObjectStorage, S3ObjectStorage
from aqua_mrv.exports import to_csv_zip, to_pdf
from aqua_mrv.models import (
    EvidenceSummary,
    PeriodInfo,
    PlantingCohortRecord,
    PlotRecord,
    PreviousVerification,
    ProjectInfo,
    ReportInputs,
    SatelliteSummary,
    TreeRecord,
)
from aqua_mrv.report import MRVReport as BuiltReport
from aqua_mrv.report import build_report as mrv_build_report
from aqua_mrv.wood_density import UnknownSpeciesError

CLEAR_SCENE_FRACTION = 0.5  # scenes at least this clear count towards the satellite summary
TRUST_BUCKETS = ((0, 24), (25, 49), (50, 74), (75, 100))
EVIDENCE_URI_PREFIX = "urn:aquacreds:mrv-report:"
ANCHOR_POLL_S = 15
ANCHOR_MAX_POLLS = 240
_TREE_FIELDS = ("species", "dbh_cm", "height_m", "alive")


class ReportBuildError(ValueError):
    """The period's data cannot produce a report (the period is reopened for corrections)."""


class AnchorError(RuntimeError):
    """A chain transaction for the report failed or reverted; needs an operator."""


# ---------------------------------------------------------------- input assembly


def report_prefix(project_id: uuid.UUID, period_id: uuid.UUID, version: int) -> str:
    return f"projects/{project_id}/periods/{period_id}/reports/v{version}"


def resolve_strata(
    measured_strata: set[str], requested: dict[str, float] | None, project_area_ha: float
) -> dict[str, float]:
    """Stratum areas (ha) for the report; see the module docstring for the rule."""
    if requested:
        if set(requested) != measured_strata:
            raise ReportBuildError(
                f"strata_area_ha must give exactly the measured strata {sorted(measured_strata)}, "
                f"got {sorted(requested)}"
            )
        return {k: float(v) for k, v in requested.items()}
    if len(measured_strata) == 1:
        return {next(iter(measured_strata)): float(project_area_ha)}
    raise ReportBuildError(
        f"Plots cover several strata {sorted(measured_strata)}; give strata_area_ha for each"
    )


def _trees(raw: list[dict[str, Any]]) -> tuple[TreeRecord, ...]:
    return tuple(TreeRecord(**{k: t[k] for k in _TREE_FIELDS if k in t}) for t in raw)


async def _plots(session: AsyncSession, period_id: uuid.UUID) -> list[PlotRecord]:
    rows = await session.execute(
        select(Plot, PlotMeasurement)
        .join(PlotMeasurement, PlotMeasurement.plot_id == Plot.id)
        .where(PlotMeasurement.period_id == period_id)
        .order_by(Plot.code)
    )
    records = []
    for plot, measurement in rows:
        centre = to_point(plot.centre)
        records.append(
            PlotRecord(
                code=plot.code,
                stratum=plot.stratum,
                area_m2=plot.area_m2,
                lat=centre.y,
                lon=centre.x,
                trees=_trees(measurement.trees),
            )
        )
    return records


async def evidence_summary(session: AsyncSession, period_id: uuid.UUID) -> EvidenceSummary:
    rows = (
        await session.execute(
            select(FieldSubmission.status, FieldSubmission.trust_score).where(
                FieldSubmission.period_id == period_id
            )
        )
    ).all()
    distribution: Counter[str] = Counter()
    for status, score in rows:
        if status is not SubmissionStatus.CHECKED or score is None:
            continue
        for low, high in TRUST_BUCKETS:
            if low <= score <= high:
                distribution[f"{low}-{high}"] += 1
    return EvidenceSummary(
        submissions_total=len(rows),
        submissions_accepted=sum(s is SubmissionStatus.CHECKED for s, _ in rows),
        rejected=sum(s is SubmissionStatus.REJECTED for s, _ in rows),
        trust_score_distribution={
            f"{lo}-{hi}": distribution[f"{lo}-{hi}"] for lo, hi in TRUST_BUCKETS
        },
    )


async def satellite_summary(
    session: AsyncSession, project: Project, period: MonitoringPeriod
) -> SatelliteSummary | None:
    """Context only (satellites never create credits): scene counts and the median NDVI p50 of
    clear scenes in the period, plus provenance of the latest successful analysis run."""
    scenes = list(
        await session.scalars(
            select(SceneObservation)
            .where(
                SceneObservation.project_id == project.id,
                SceneObservation.acquired_on >= period.start_date,
                SceneObservation.acquired_on <= period.end_date,
            )
            .order_by(SceneObservation.acquired_at)
        )
    )
    run = await session.scalar(
        select(AnalysisRun)
        .where(AnalysisRun.project_id == project.id, AnalysisRun.status == AnalysisStatus.SUCCEEDED)
        .order_by(AnalysisRun.finished_at.desc().nulls_last(), AnalysisRun.created_at.desc())
        .limit(1)
    )
    if not scenes and run is None:
        return None
    clear = [s for s in scenes if s.clear_fraction >= CLEAR_SCENE_FRACTION]
    ndvi = [
        float(s.index_stats["ndvi"]["p50"])
        for s in clear
        if isinstance(s.index_stats.get("ndvi"), dict)
        and isinstance(s.index_stats["ndvi"].get("p50"), int | float)
        and math.isfinite(s.index_stats["ndvi"]["p50"])
    ]
    stats: dict[str, Any] = {
        "window": [period.start_date.isoformat(), period.end_date.isoformat()],
        "scenes_total": len(scenes),
        "clear_scenes": len(clear),
        "clear_fraction_threshold": CLEAR_SCENE_FRACTION,
        "ndvi_p50_median": round(statistics.median(ndvi), 6) if ndvi else None,
        "first_acquired": scenes[0].acquired_on.isoformat() if scenes else None,
        "last_acquired": scenes[-1].acquired_on.isoformat() if scenes else None,
    }
    provenance: dict[str, str] = {}
    providers = sorted({s.provider for s in scenes})
    if providers:
        provenance["providers"] = ",".join(providers)
    if run is not None:
        provenance["analysis_run_id"] = str(run.id)
        provenance["analysis_run_kind"] = run.kind.value
        if run.finished_at is not None:
            provenance["analysis_run_finished_at"] = run.finished_at.isoformat()
    return SatelliteSummary(stats=stats, provenance=provenance)


async def previous_report(
    session: AsyncSession, project_id: uuid.UUID, exclude_period: uuid.UUID
) -> MRVReport | None:
    found: MRVReport | None = await session.scalar(
        select(MRVReport)
        .join(MonitoringPeriod, MonitoringPeriod.id == MRVReport.period_id)
        .where(
            MRVReport.project_id == project_id,
            MRVReport.status == ReportStatus.APPROVED,
            MRVReport.period_id != exclude_period,
            MonitoringPeriod.kind == PeriodKind.MONITORING,
        )
        .order_by(MRVReport.period_end_year.desc(), MRVReport.decided_at.desc())
        .limit(1)
    )
    return found


async def assemble_inputs(
    session: AsyncSession,
    project: Project,
    period: MonitoringPeriod,
    options: dict[str, Any],
    generated_at: datetime,
) -> ReportInputs:
    if project.methodology is not Methodology.CCTS_BM_FR05_001:
        raise ReportBuildError(f"Methodology {project.methodology} has no report builder yet")
    plots = await _plots(session, period.id)
    if not plots:
        raise ReportBuildError("No plot measurements in this period")
    strata = resolve_strata(
        {p.stratum for p in plots}, options.get("strata_area_ha"), project.area_ha
    )
    prev = await previous_report(session, project.id, period.id)
    t1 = prev.period_end_year if prev else project.start_date.year
    t2 = period.end_date.year
    if t2 <= t1:
        raise ReportBuildError(
            f"Verification period must end after {t1} (previous verification or project start); "
            f"this period ends in {t2}"
        )
    return ReportInputs(
        project=ProjectInfo(
            id=str(project.id),
            name=project.name,
            state=project.state,
            area_ha=project.area_ha,
            start_year=project.start_date.year,
            boundary=dict(mapping(to_shape(project.boundary))),
        ),
        period=PeriodInfo(kind="verification", start_year=t1, end_year=t2),
        strata_area_ha=strata,
        plots=tuple(plots),
        planting=tuple(PlantingCohortRecord(**c) for c in options.get("planting") or ()),
        include_soc=bool(options.get("include_soc", True)),
        previous=PreviousVerification(
            tree_stock_tco2e=prev.tree_stock_tco2e, cumulative_removals_tco2e=prev.tccc_tco2e
        )
        if prev
        else PreviousVerification(),
        evidence=await evidence_summary(session, period.id),
        satellite=await satellite_summary(session, project, period),
        generated_at=generated_at.isoformat(),
        notes=options.get("notes"),
    )


# ---------------------------------------------------------------- build (dependency-injected)


def _render(report: BuiltReport) -> tuple[bytes, bytes, bytes]:
    return report.canonical_json(), to_pdf(report), to_csv_zip(report)


async def _reopen(
    sessionmaker: async_sessionmaker[AsyncSession],
    period_id: uuid.UUID,
    actor_id: uuid.UUID,
    restore: PeriodStatus,
    error: str,
) -> None:
    async with sessionmaker() as session:
        period = await session.get(MonitoringPeriod, period_id, with_for_update=True)
        if period is not None and period.status is PeriodStatus.ANALYSING:
            period.status = restore
        await audit.record(
            session,
            actor_id=actor_id,
            action="report.build_failed",
            entity_type="monitoring_period",
            entity_id=period_id,
            data={"error": error[:1000], "period_status": restore.value},
        )
        await session.commit()


async def build_report_core(
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: ObjectStorage,
    period_id: uuid.UUID,
    actor_id: uuid.UUID,
    options: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build and store the next report version for an `analysing` period.

    Data problems (no plots, unknown species, bad strata, …) reopen the period with the error in
    the audit log and return `{"status": "failed"}`; infrastructure errors propagate (retried)."""
    options = options or {}
    restore = PeriodStatus(options.get("restore_status", PeriodStatus.OPEN.value))
    try:
        async with sessionmaker() as session:
            period = await session.get(MonitoringPeriod, period_id, with_for_update=True)
            if period is None:
                raise LookupError(f"monitoring period {period_id} not found")
            if period.status is not PeriodStatus.ANALYSING:
                raise ReportBuildError(f"Period is {period.status}, expected analysing")
            project = await session.get(Project, period.project_id)
            if project is None:  # pragma: no cover - FK guarantees it
                raise LookupError(f"project {period.project_id} not found")
            generated_at = now or utcnow()
            inputs = await assemble_inputs(session, project, period, options, generated_at)
            report = mrv_build_report(inputs)
            canonical, pdf, csv_zip = await asyncio.to_thread(_render, report)

            version = (
                await session.scalar(
                    select(func.coalesce(func.max(MRVReport.version), 0)).where(
                        MRVReport.period_id == period.id
                    )
                )
                or 0
            ) + 1
            prefix = report_prefix(project.id, period.id, version)
            keys = {
                "json": f"{prefix}/report.json",
                "pdf": f"{prefix}/report.pdf",
                "csv": f"{prefix}/report-csv.zip",
            }
            await asyncio.to_thread(storage.put_bytes, keys["json"], canonical, "application/json")
            await asyncio.to_thread(storage.put_bytes, keys["pdf"], pdf, "application/pdf")
            await asyncio.to_thread(storage.put_bytes, keys["csv"], csv_zip, "application/zip")

            result = report.result
            row = MRVReport(
                project_id=project.id,
                period_id=period.id,
                version=version,
                status=ReportStatus.UNDER_REVIEW,
                report_hash=report.report_hash,
                lccc_units=report.lccc_units,
                tccc_units=math.floor(result.tccc),
                period_start_year=inputs.period.start_year,
                period_end_year=inputs.period.end_year,
                tree_stock_tco2e=result.tree_stock_tco2e,
                tccc_tco2e=result.tccc,
                json_key=keys["json"],
                pdf_key=keys["pdf"],
                csv_key=keys["csv"],
                generated_at=generated_at,
                generated_by=actor_id,
            )
            session.add(row)
            period.status = PeriodStatus.UNDER_REVIEW
            await session.flush()
            await audit.record(
                session,
                actor_id=actor_id,
                action="report.generated",
                entity_type="mrv_report",
                entity_id=row.id,
                data={
                    "period_id": str(period.id),
                    "version": version,
                    "report_hash": row.report_hash,
                    "lccc_units": row.lccc_units,
                    "tccc_units": row.tccc_units,
                    "warnings": len(report.warnings),
                },
            )
            await session.commit()
            return {
                "status": "built",
                "report_id": str(row.id),
                "version": version,
                "report_hash": row.report_hash,
                "lccc_units": row.lccc_units,
            }
    except (ValueError, UnknownSpeciesError) as exc:  # incl. ReportBuildError, ValidationError
        await _reopen(sessionmaker, period_id, actor_id, restore, f"{type(exc).__name__}: {exc}")
        return {"status": "failed", "error": str(exc)}


async def _build(period_id: str, actor_id: str, options_json: str) -> dict[str, Any]:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    try:
        return await build_report_core(
            session_factory(engine),
            S3ObjectStorage(settings),
            uuid.UUID(period_id),
            uuid.UUID(actor_id),
            json.loads(options_json) if options_json else {},
        )
    finally:
        await engine.dispose()


@shared_task(
    name="aqua.reports.build_report",
    autoretry_for=(ConnectionError, TimeoutError, OSError),
    retry_backoff=True,
    max_retries=5,
)
def build_report(period_id: str, actor_id: str, options_json: str = "") -> dict[str, Any]:
    return asyncio.run(_build(period_id, actor_id, options_json))


# ---------------------------------------------------------------- anchor (dependency-injected)


async def _advance(
    relayer: Relayer, tx_id: uuid.UUID, report_id: uuid.UUID, step: str
) -> ChainTransaction | None:
    """Process one transaction; returns it when confirmed, None while waiting."""
    await relayer.process(tx_id)
    async with relayer.sessionmaker() as session:
        tx = await session.get(ChainTransaction, tx_id)
        if tx is None:  # pragma: no cover - FK guarantees it
            raise LookupError(f"chain transaction {tx_id} not found")
        if tx.status is ChainTxStatus.CONFIRMED:
            return tx
        if tx.status in (ChainTxStatus.FAILED, ChainTxStatus.REVERTED):
            error = f"{step} {tx.status.value}: {tx.error_name or tx.error or 'unknown error'}"
            report = await session.get(MRVReport, report_id, with_for_update=True)
            if report is not None:
                report.anchor_error = error[:2000]
                await audit.record(
                    session,
                    actor_id=None,
                    action="report.anchor_failed",
                    entity_type="mrv_report",
                    entity_id=report_id,
                    data={"step": step, "chain_tx_id": str(tx_id), "error": error[:500]},
                )
                await session.commit()
            raise AnchorError(error)
        return None


async def _ensure_registered(relayer: Relayer, project_id: uuid.UUID, report_id: uuid.UUID) -> bool:
    async with relayer.sessionmaker() as session:
        tx = await actions.active_transaction(
            session, ChainTxPurpose.REGISTER_PROJECT, "project", str(project_id)
        )
        if tx is None:
            project = await session.get(Project, project_id)
            if project is None:  # pragma: no cover - FK guarantees it
                raise LookupError(f"project {project_id} not found")
            tx = await actions.queue_register_project(session, relayer, project)
            await audit.record(
                session,
                actor_id=None,
                action="chain.register_project.queued",
                entity_type="project",
                entity_id=project_id,
                data={"chain_tx_id": str(tx.id), "mrv_report_id": str(report_id)},
            )
            await session.commit()
        tx_id = tx.id
    return await _advance(relayer, tx_id, report_id, "register_project") is not None


async def anchor_report_core(relayer: Relayer, report_id: uuid.UUID) -> dict[str, Any]:
    """Advance the anchoring of an approved report as far as confirmations allow.

    Returns `{"state": "waiting", ...}` (call again later) or `{"state": "anchored", ...}`.
    Monitoring-period reports: register (if needed) → submitReport → approveReport. Baseline-period
    reports only need the project registered."""
    async with relayer.sessionmaker() as session:
        report = await session.get(MRVReport, report_id)
        if report is None:
            raise LookupError(f"MRV report {report_id} not found")
        if report.status is not ReportStatus.APPROVED:
            raise AnchorError(f"Report is {report.status}; only approved reports are anchored")
        kind = await session.scalar(
            select(MonitoringPeriod.kind).where(MonitoringPeriod.id == report.period_id)
        )
        project_id = report.project_id
        if report.anchored_at is not None:
            return _anchor_state("anchored", report)

    if not await _ensure_registered(relayer, project_id, report_id):
        return {"state": "waiting", "step": "register_project", "report_id": str(report_id)}

    if kind is PeriodKind.MONITORING:
        submit_tx = await _queued_step(relayer, report_id, ChainTxPurpose.SUBMIT_REPORT)
        confirmed = await _advance(relayer, submit_tx, report_id, "submit_report")
        if confirmed is None:
            return {"state": "waiting", "step": "submit_report", "report_id": str(report_id)}
        chain_report_id = int(actions.event_value(confirmed, "ReportSubmitted", "reportId"))
        async with relayer.sessionmaker() as session:
            report = await _locked_report(session, report_id)
            report.chain_report_id = chain_report_id
            await session.commit()
        approve_tx = await _queued_step(relayer, report_id, ChainTxPurpose.APPROVE_REPORT)
        if await _advance(relayer, approve_tx, report_id, "approve_report") is None:
            return {"state": "waiting", "step": "approve_report", "report_id": str(report_id)}

    async with relayer.sessionmaker() as session:
        report = await _locked_report(session, report_id)
        report.anchored_at = utcnow()
        report.anchor_error = None
        await audit.record(
            session,
            actor_id=None,
            action="report.anchored",
            entity_type="mrv_report",
            entity_id=report_id,
            data={
                "report_hash": report.report_hash,
                "chain_report_id": report.chain_report_id,
                "submit_tx_id": str(report.submit_tx_id) if report.submit_tx_id else None,
                "approve_tx_id": str(report.approve_tx_id) if report.approve_tx_id else None,
            },
        )
        await session.commit()
        return _anchor_state("anchored", report)


async def _locked_report(session: AsyncSession, report_id: uuid.UUID) -> MRVReport:
    report = await session.get(MRVReport, report_id, with_for_update=True)
    if report is None:  # pragma: no cover - checked at the start of the run
        raise LookupError(f"MRV report {report_id} not found")
    return report


def _anchor_state(state: str, report: MRVReport) -> dict[str, Any]:
    return {
        "state": state,
        "report_id": str(report.id),
        "chain_report_id": report.chain_report_id,
        "submit_tx_id": str(report.submit_tx_id) if report.submit_tx_id else None,
        "approve_tx_id": str(report.approve_tx_id) if report.approve_tx_id else None,
    }


async def _queued_step(
    relayer: Relayer, report_id: uuid.UUID, purpose: ChainTxPurpose
) -> uuid.UUID:
    """The report's submit/approve transaction, queued (and its id recorded) on first use."""
    async with relayer.sessionmaker() as session:
        report = await _locked_report(session, report_id)
        existing = (
            report.submit_tx_id if purpose is ChainTxPurpose.SUBMIT_REPORT else report.approve_tx_id
        )
        if existing is not None:
            return existing
        if purpose is ChainTxPurpose.SUBMIT_REPORT:
            tx = await actions.queue_submit_report(
                session,
                relayer,
                project_id=report.project_id,
                period_end=report.period_end_year,
                report_hash=report.report_hash,
                lccc=report.lccc_units,
                evidence_uri=f"{EVIDENCE_URI_PREFIX}{report.id}",
                entity_type="mrv_report",
                entity_id=str(report.id),
            )
            report.submit_tx_id = tx.id
        else:
            if report.chain_report_id is None:  # pragma: no cover - set before approval
                raise AnchorError("approveReport needs the on-chain report id")
            tx = await actions.queue_approve_report(
                session,
                relayer,
                report_id=report.chain_report_id,
                entity_type="mrv_report",
                entity_id=str(report.id),
            )
            report.approve_tx_id = tx.id
        await audit.record(
            session,
            actor_id=None,
            action=f"chain.{purpose.value}.queued",
            entity_type="mrv_report",
            entity_id=report.id,
            data={"chain_tx_id": str(tx.id)},
        )
        await session.commit()
        return tx.id


async def _anchor(report_id: str) -> dict[str, Any]:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    relayer = build_relayer(settings, session_factory(engine))
    try:
        await relayer.client.check()
        return await anchor_report_core(relayer, uuid.UUID(report_id))
    finally:
        await relayer.client.close()
        await engine.dispose()


class AwaitingAnchorError(Exception):
    """A transaction for the report still needs confirmations."""


@shared_task(
    name="aqua.reports.anchor",
    bind=True,
    autoretry_for=(ConnectionError, TimeoutError, OSError, RetryableChainError),
    retry_backoff=True,
    retry_backoff_max=300,
    max_retries=ANCHOR_MAX_POLLS,
)
def anchor(self: Any, report_id: str) -> dict[str, Any]:
    result = asyncio.run(_anchor(report_id))
    if result["state"] == "waiting":
        raise self.retry(exc=AwaitingAnchorError(report_id), countdown=ANCHOR_POLL_S, queue="chain")
    return result
