"""Approved MRV reports in the public registry, and the public variant of their hashed record.

Only `approved` reports of public projects are ever returned. Draft, under-review,
changes-requested and rejected reports stay private (the period status still shows a rejection).

What the stored artifacts contain (schema `aquacreds.mrv_report/1`, built by `aqua_mrv`):
- canonical JSON: project facts and the *full-precision* boundary, the org admin's free-text
  `notes`, every sample plot with its exact centre `lat`/`lon`, per-tree measurements, wood
  densities, aggregate evidence and satellite summaries, the calculation trace and results.
  No user names, emails, ids or photo keys.
- CSV zip: plots.csv (with exact plot `lat`/`lon`), trees.csv, trace.csv.
- PDF: headline results, methodology, trace, warnings, per-plot biomass, wood densities and the
  evidence/satellite summaries; no coordinates, boundary, notes or user data.

So the PDF is public as is (through a short-lived presigned URL), while the JSON is served as a
public variant built from an allow-list (no notes, boundary or plot coordinates) and the CSV zip is
not public. The record's SHA-256 always refers to the full canonical JSON, which the project's
organisation, verifiers and regulators can download and check against the chain.
"""

import hashlib
import json
import uuid
from collections.abc import Callable, Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aqua_api.schemas.public import (
    PublicReport,
    PublicTx,
    ReportAnchoring,
    ReportDownloadLinks,
    ReportTraceability,
)
from aqua_api.services.public_registry import HIDDEN_STATUSES, explorer_tx_url
from aqua_core.models import (
    ChainTransaction,
    ChainTxStatus,
    MonitoringPeriod,
    MRVReport,
    PeriodKind,
    Project,
    ReportStatus,
)

RECORD_SCHEMA = "aquacreds.mrv_report/1"
MAX_RECORD_BYTES = 16 * 1024 * 1024

# Allow-lists of the public record variant. Anything not listed (now or in a future schema) is
# dropped and reported in `redacted`.
_TOP_LEVEL = frozenset(
    {
        "schema",
        "status",
        "generated_at",
        "methodology",
        "software",
        "project",
        "period",
        "inputs",
        "plots",
        "wood_density",
        "evidence",
        "satellite",
        "biomass_estimate",
        "result",
        "trace",
        "warnings",
    }
)
_PROJECT = frozenset({"id", "name", "state", "methodology", "area_ha", "start_year"})
_PLOT = frozenset(
    {
        "code",
        "stratum",
        "area_m2",
        "live_trees",
        "dead_trees",
        "agb_t_dm_ha",
        "bgb_t_dm_ha",
        "total_t_dm_ha",
        "trees",
    }
)
_TREE = frozenset(
    {"n", "species", "dbh_cm", "height_m", "alive", "wood_density_g_cm3", "agb_kg", "bgb_kg"}
)

LinksFor = Callable[[uuid.UUID], ReportDownloadLinks]


class RecordUnavailableError(RuntimeError):
    """The stored canonical record is missing, too large, unreadable or does not match its hash."""


def approved_reports_query() -> Any:
    return (
        select(MRVReport, MonitoringPeriod.kind)
        .join(MonitoringPeriod, MonitoringPeriod.id == MRVReport.period_id)
        .join(Project, Project.id == MRVReport.project_id)
        .where(
            MRVReport.status == ReportStatus.APPROVED,
            Project.status.not_in(HIDDEN_STATUSES),
        )
    )


def _public_tx(tx: ChainTransaction | None) -> PublicTx | None:
    if tx is None or tx.status is not ChainTxStatus.CONFIRMED or not tx.tx_hash:
        return None
    return PublicTx(
        chain_id=tx.chain_id,
        tx_hash=tx.tx_hash,
        block_number=tx.block_number,
        explorer_url=explorer_tx_url(tx.chain_id, tx.tx_hash),
    )


async def _anchoring(
    session: AsyncSession, reports: Sequence[MRVReport]
) -> dict[uuid.UUID, ReportAnchoring]:
    tx_ids = {t for r in reports for t in (r.submit_tx_id, r.approve_tx_id) if t is not None}
    txs: dict[uuid.UUID, ChainTransaction] = {}
    if tx_ids:
        rows = await session.scalars(
            select(ChainTransaction).where(ChainTransaction.id.in_(sorted(tx_ids)))
        )
        txs = {tx.id: tx for tx in rows}
    out = {}
    for r in reports:
        submit = _public_tx(txs.get(r.submit_tx_id) if r.submit_tx_id else None)
        approve = _public_tx(txs.get(r.approve_tx_id) if r.approve_tx_id else None)
        out[r.id] = ReportAnchoring(
            hash_anchored=submit is not None,
            chain_report_id=r.chain_report_id,
            submit_tx=submit,
            approve_tx=approve,
            completed_at=r.anchored_at,
        )
    return out


def _public(
    report: MRVReport, kind: PeriodKind, anchoring: ReportAnchoring, links: LinksFor
) -> PublicReport:
    return PublicReport(
        id=report.id,
        project_id=report.project_id,
        period_id=report.period_id,
        period_kind=kind,
        version=report.version,
        period_start_year=report.period_start_year,
        period_end_year=report.period_end_year,
        generated_at=report.generated_at,
        decided_at=report.decided_at,
        report_hash=report.report_hash,
        lccc_units=report.lccc_units,
        tccc_units=report.tccc_units,
        anchoring=anchoring,
        downloads=links(report.id),
    )


async def project_reports(
    session: AsyncSession, project_id: uuid.UUID, links: LinksFor
) -> list[PublicReport]:
    rows = (
        await session.execute(
            approved_reports_query()
            .where(MRVReport.project_id == project_id)
            .order_by(MRVReport.period_end_year, MRVReport.generated_at, MRVReport.version)
        )
    ).all()
    anchoring = await _anchoring(session, [r for r, _ in rows])
    return [_public(r, kind, anchoring[r.id], links) for r, kind in rows]


async def approved_report(
    session: AsyncSession, report_id: uuid.UUID, links: LinksFor
) -> tuple[MRVReport, PublicReport] | None:
    row = (await session.execute(approved_reports_query().where(MRVReport.id == report_id))).first()
    if row is None:
        return None
    report, kind = row
    anchoring = await _anchoring(session, [report])
    return report, _public(report, kind, anchoring[report.id], links)


def parse_record(data: bytes, report_hash: str) -> dict[str, Any]:
    """The stored canonical record, after checking SHA-256(data) == report_hash."""
    if hashlib.sha256(data).hexdigest() != report_hash:
        raise RecordUnavailableError("Stored MRV record does not match its report hash")
    record = json.loads(data)
    if not isinstance(record, dict) or record.get("schema") != RECORD_SCHEMA:
        raise RecordUnavailableError("Stored MRV record has an unsupported schema")
    return record


def traceability(record: dict[str, Any]) -> ReportTraceability:
    return ReportTraceability(
        record_schema=str(record["schema"]),
        status_label=str(record.get("status", "")),
        methodology_profile=dict(record.get("methodology") or {}),
        software={str(k): str(v) for k, v in (record.get("software") or {}).items()},
    )


def _pick(
    item: Any, allowed: frozenset[str], path: str, redacted: set[str]
) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    redacted.update(f"{path}{k}" for k in item if k not in allowed)
    return {k: v for k, v in item.items() if k in allowed}


def public_record(record: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Allow-listed copy of the record without notes, the full-precision boundary or plot
    coordinates. Returns (variant, sorted redacted paths)."""
    redacted: set[str] = set()
    out = _pick(record, _TOP_LEVEL, "", redacted) or {}
    out["project"] = _pick(record.get("project"), _PROJECT, "project.", redacted)
    plots = []
    for plot in record.get("plots") or []:
        kept = _pick(plot, _PLOT, "plots[].", redacted)
        if kept is None:
            continue
        trees = (_pick(t, _TREE, "plots[].trees[].", redacted) for t in kept.get("trees") or [])
        kept["trees"] = [t for t in trees if t is not None]
        plots.append(kept)
    out["plots"] = plots
    return out, sorted(redacted)
