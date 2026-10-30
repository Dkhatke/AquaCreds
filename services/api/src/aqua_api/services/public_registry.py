"""Read-only queries behind the public transparency registry.

Everything here reads existing tables (projects, organisations, periods, aggregate evidence,
scene observations, alerts and indexed chain events) and returns allow-listed public schemas.
Registry state (vintages, retirements, report hashes) is derived from `chain_events`, the
indexer's decoded copy of AquaRegistry/AquaCredit logs, filtered to the configured chain id.
"""

import hashlib
import json
import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

import rfc8785
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from aqua_api.schemas.public import (
    AlertCounts,
    CertificateVerification,
    ChainRef,
    CreditTotals,
    CreditTypeName,
    EvidenceStats,
    HashMatch,
    IndexerStatus,
    IssuanceOut,
    MonthlyIndexValue,
    OnChainReport,
    ProjectRef,
    ProjectRegistration,
    PublicOrg,
    PublicProjectSummary,
    PublicStats,
    ReportChainStatus,
    RetirementOut,
    SatelliteIndicators,
    VintageOut,
)
from aqua_chain.encoding import project_id_bytes32, to_hex
from aqua_core.models import (
    ChainCursor,
    ChainEvent,
    ChainTransaction,
    ChainTxStatus,
    MRVReport,
    Organization,
    Project,
    ProjectStatus,
    ReportStatus,
)

# Drafts are private work in progress; rejected projects never entered the registry.
HIDDEN_STATUSES = (ProjectStatus.DRAFT, ProjectStatus.REJECTED)
# ~11 m at the equator: below one Sentinel-2 pixel (10-20 m), so the public map loses nothing
# meaningful, while dense hand-drawn/GPS-traced vertices are thinned. The full-precision boundary
# stays verifiable through the on-chain boundary hash.
BOUNDARY_TOLERANCE_DEG = 0.0001
# Scenes below this clear-sky fraction are left out of public satellite aggregates.
PUBLIC_MIN_CLEAR = 0.5
METADATA_URI_PREFIX = "urn:aquacreds:project:"
CREDIT_TYPES: dict[int, CreditTypeName] = {0: "temporary", 1: "long_term"}
EXPLORERS = {80002: "https://amoy.polygonscan.com", 137: "https://polygonscan.com"}


def explorer_tx_url(chain_id: int, tx_hash: str) -> str | None:
    base = EXPLORERS.get(chain_id)
    return f"{base}/tx/{tx_hash}" if base else None


def chain_ref(event: ChainEvent) -> ChainRef:
    return ChainRef(
        chain_id=event.chain_id,
        contract_address=event.address,
        tx_hash=event.tx_hash,
        log_index=event.log_index,
        block_number=event.block_number,
        block_hash=event.block_hash,
        explorer_url=explorer_tx_url(event.chain_id, event.tx_hash),
    )


def project_key(project_id: uuid.UUID) -> str:
    return to_hex(project_id_bytes32(project_id))


def _int(value: Any) -> int:
    return int(value) if value is not None else 0


# ---------------------------------------------------------------- chain events


async def events(
    session: AsyncSession,
    chain_id: int,
    names: Sequence[str],
    *,
    project_keys: Iterable[str] | None = None,
    arg_equals: tuple[str, str] | None = None,
) -> list[ChainEvent]:
    query = select(ChainEvent).where(ChainEvent.chain_id == chain_id, ChainEvent.event.in_(names))
    if project_keys is not None:
        query = query.where(ChainEvent.project_key.in_(list(project_keys)))
    if arg_equals is not None:
        name, value = arg_equals
        query = query.where(ChainEvent.args[name].astext == value)
    rows = await session.scalars(
        query.order_by(ChainEvent.block_number, ChainEvent.log_index, ChainEvent.id)
    )
    return list(rows)


async def project_refs(
    session: AsyncSession, chain_id: int, keys: Iterable[str]
) -> dict[str, ProjectRef]:
    """Resolve bytes32 project keys to public projects through the ProjectRegistered metadata URI
    (`urn:aquacreds:project:<uuid>`), double-checked against keccak256(uuid)."""
    wanted = {k for k in keys if k}
    refs = {k: ProjectRef(id=None, name=None, project_key=k) for k in wanted}
    if not wanted:
        return refs
    ids: dict[uuid.UUID, str] = {}
    for event in await events(session, chain_id, ["ProjectRegistered"], project_keys=wanted):
        uri = str(event.args.get("metadataURI", ""))
        if not uri.startswith(METADATA_URI_PREFIX):
            continue
        try:
            pid = uuid.UUID(uri.removeprefix(METADATA_URI_PREFIX))
        except ValueError:
            continue
        if event.project_key and project_key(pid) == event.project_key:
            ids[pid] = event.project_key
    if ids:
        rows = await session.execute(
            select(Project.id, Project.name).where(
                Project.id.in_(list(ids)), Project.status.not_in(HIDDEN_STATUSES)
            )
        )
        for pid, name in rows:
            refs[ids[pid]] = ProjectRef(id=pid, name=name, project_key=ids[pid])
    return refs


async def registration(
    session: AsyncSession, chain_id: int, project_id: uuid.UUID
) -> ProjectRegistration | None:
    found = await events(
        session, chain_id, ["ProjectRegistered"], project_keys=[project_key(project_id)]
    )
    if not found:
        return None
    event = found[0]
    return ProjectRegistration(
        **chain_ref(event).model_dump(),
        boundary_hash=str(event.args.get("boundaryHash", "")),
        start_year=_int(event.args.get("startYear")),
    )


def _decisions(decided: Iterable[ChainEvent]) -> dict[int, ChainEvent]:
    out: dict[int, ChainEvent] = {}
    for event in decided:
        out[_int(event.args.get("reportId"))] = event  # latest wins (ordered by block)
    return out


def _hash_hex(value: Any) -> str:
    return str(value or "").lower().removeprefix("0x")


async def mrv_report_links(
    session: AsyncSession, submitted: Sequence[ChainEvent]
) -> dict[int, uuid.UUID]:
    """On-chain reportId → id of the approved MRV report of a public project with the same hash,
    for the same project key (and the same reportId when the anchor task recorded one)."""
    hashes = {_hash_hex(e.args.get("reportHash")) for e in submitted} - {""}
    if not hashes:
        return {}
    rows = await session.execute(
        select(MRVReport.id, MRVReport.report_hash, MRVReport.project_id, MRVReport.chain_report_id)
        .join(Project, Project.id == MRVReport.project_id)
        .where(
            MRVReport.report_hash.in_(sorted(hashes)),
            MRVReport.status == ReportStatus.APPROVED,
            Project.status.not_in(HIDDEN_STATUSES),
        )
    )
    by_hash = {row.report_hash: row for row in rows}
    links: dict[int, uuid.UUID] = {}
    for event in submitted:
        row = by_hash.get(_hash_hex(event.args.get("reportHash")))
        report_id = _int(event.args.get("reportId"))
        if (
            row is not None
            and event.project_key == project_key(row.project_id)
            and row.chain_report_id in (None, report_id)
        ):
            links[report_id] = row.id
    return links


def _report_status(decision: ChainEvent | None) -> ReportChainStatus:
    if decision is None:
        return "submitted"
    return "approved" if decision.event == "ReportApproved" else "rejected"


async def project_reports(
    session: AsyncSession, chain_id: int, project_id: uuid.UUID
) -> list[OnChainReport]:
    key = project_key(project_id)
    found = await events(
        session,
        chain_id,
        ["ReportSubmitted", "ReportApproved", "ReportRejected"],
        project_keys=[key],
    )
    decisions = _decisions(e for e in found if e.event != "ReportSubmitted")
    submitted = [e for e in found if e.event == "ReportSubmitted"]
    links = await mrv_report_links(session, submitted)
    reports = []
    for event in submitted:
        report_id = _int(event.args.get("reportId"))
        decision = decisions.get(report_id)
        approved = decision is not None and decision.event == "ReportApproved"
        reports.append(
            OnChainReport(
                report_id=report_id,
                mrv_report_id=links.get(report_id),
                period_start=_int(event.args.get("periodStart")),
                period_end=_int(event.args.get("periodEnd")),
                report_hash=str(event.args.get("reportHash", "")),
                lccc=_int(event.args.get("lccc")),
                tccc=_int(decision.args.get("tccc")) if approved and decision else None,
                status=_report_status(decision),
                submitted=chain_ref(event),
                decided=chain_ref(decision) if decision else None,
            )
        )
    return reports


async def vintages(
    session: AsyncSession,
    chain_id: int,
    *,
    token_id: int | None = None,
    project_id: uuid.UUID | None = None,
) -> list[VintageOut]:
    """Vintages (ERC-1155 token ids) with issued / retired / replaced / outstanding totals summed
    from CreditsIssued, CreditsRetired and ReversalReplaced events."""
    arg = ("tokenId", str(token_id)) if token_id is not None else None
    keys = [project_key(project_id)] if project_id is not None else None
    issued = await events(session, chain_id, ["CreditsIssued"], project_keys=keys, arg_equals=arg)
    if not issued:
        return []
    token_ids = {_int(e.args.get("tokenId")) for e in issued}
    burns = await events(
        session,
        chain_id,
        ["CreditsRetired", "ReversalReplaced"],
        arg_equals=arg,
    )
    retired: dict[int, int] = defaultdict(int)
    replaced: dict[int, int] = defaultdict(int)
    for event in burns:
        tid = _int(event.args.get("tokenId"))
        if tid in token_ids:
            bucket = retired if event.event == "CreditsRetired" else replaced
            bucket[tid] += _int(event.args.get("amount"))
    by_token: dict[int, list[ChainEvent]] = defaultdict(list)
    for event in issued:
        by_token[_int(event.args.get("tokenId"))].append(event)
    refs = await project_refs(session, chain_id, (e.project_key or "" for e in issued))
    now = datetime.now(UTC)
    out = []
    for tid in sorted(by_token):
        first = by_token[tid][0]
        credit_type = CREDIT_TYPES.get(_int(first.args.get("creditType")), "temporary")
        expires = _int(first.args.get("expiresAt"))
        expires_at = datetime.fromtimestamp(expires, UTC) if expires else None
        total = sum(_int(e.args.get("amount")) for e in by_token[tid])
        key = first.project_key or ""
        out.append(
            VintageOut(
                token_id=tid,
                project=refs.get(key) or ProjectRef(id=None, name=None, project_key=key),
                credit_type=credit_type,
                credit_unit="tCCC" if credit_type == "temporary" else "lCCC",
                report_id=_int(first.args.get("reportId")),
                issued=total,
                retired=retired[tid],
                replaced=replaced[tid],
                outstanding=total - retired[tid] - replaced[tid],
                expires_at=expires_at,
                expired=expires_at is not None and expires_at <= now,
                issuances=[
                    IssuanceOut(
                        amount=_int(e.args.get("amount")),
                        tx_hash=e.tx_hash,
                        block_number=e.block_number,
                        block_time=e.block_time,
                        explorer_url=explorer_tx_url(e.chain_id, e.tx_hash),
                    )
                    for e in by_token[tid]
                ],
            )
        )
    return out


async def retirements(
    session: AsyncSession, chain_id: int, *, retirement_id: int | None = None
) -> list[tuple[RetirementOut, ChainEvent]]:
    arg = ("retirementId", str(retirement_id)) if retirement_id is not None else None
    found = await events(session, chain_id, ["CreditsRetired"], arg_equals=arg)
    if not found:
        return []
    token_ids = {str(_int(e.args.get("tokenId"))) for e in found}
    issued = await events(session, chain_id, ["CreditsIssued"])
    token_meta: dict[int, ChainEvent] = {}
    for event in issued:
        tid = _int(event.args.get("tokenId"))
        if str(tid) in token_ids:
            token_meta.setdefault(tid, event)
    refs = await project_refs(session, chain_id, (e.project_key or "" for e in token_meta.values()))
    out = []
    for event in found:
        tid = _int(event.args.get("tokenId"))
        meta = token_meta.get(tid)
        out.append(
            (
                RetirementOut(
                    retirement_id=_int(event.args.get("retirementId")),
                    token_id=tid,
                    amount=_int(event.args.get("amount")),
                    project=refs.get(meta.project_key or "") if meta else None,
                    credit_type=CREDIT_TYPES.get(_int(meta.args.get("creditType")))
                    if meta
                    else None,
                    beneficiary=str(event.args.get("beneficiary", "")),
                    purpose=str(event.args.get("purpose", "")),
                    tx_hash=event.tx_hash,
                    block_number=event.block_number,
                    block_time=event.block_time,
                    indexed_at=event.created_at,
                ),
                event,
            )
        )
    out.sort(key=lambda item: item[0].retirement_id)
    return out


def certificate_url(base_url: str, retirement_id: int) -> str:
    return f"{base_url.rstrip('/')}/registry/retirements/{retirement_id}"


def certificate_verification(
    base_url: str, retirement: RetirementOut, event: ChainEvent
) -> CertificateVerification:
    fields: dict[str, Any] = {
        "chain_id": event.chain_id,
        "contract_address": event.address.lower(),
        "tx_hash": event.tx_hash.lower(),
        "log_index": event.log_index,
        "retirement_id": retirement.retirement_id,
        "token_id": retirement.token_id,
        "amount": retirement.amount,
        "beneficiary": retirement.beneficiary,
        "purpose": retirement.purpose,
    }
    return CertificateVerification(
        certificate_url=certificate_url(base_url, retirement.retirement_id),
        chain=chain_ref(event),
        fields=fields,
        payload_sha256=hashlib.sha256(rfc8785.dumps(fields)).hexdigest(),
    )


async def verify_hash(session: AsyncSession, chain_id: int, sha256_hex: str) -> list[HashMatch]:
    """Matches for a SHA-256 (lower-case hex): ReportSubmitted events whose reportHash equals it
    (`source="chain"`), then approved MRV reports of public projects with that hash that no indexed
    event matches yet (`source="registry_db"`). Unapproved reports are never matched."""
    matches = await _chain_matches(session, chain_id, sha256_hex)
    linked = {m.mrv_report_id for m in matches if m.mrv_report_id is not None}
    rows = await session.execute(
        select(MRVReport, Project)
        .join(Project, Project.id == MRVReport.project_id)
        .where(
            MRVReport.report_hash == sha256_hex,
            MRVReport.status == ReportStatus.APPROVED,
            Project.status.not_in(HIDDEN_STATUSES),
        )
    )
    for report, project in rows:
        if report.id in linked:
            continue
        anchored = False
        if report.submit_tx_id is not None:
            tx = await session.get(ChainTransaction, report.submit_tx_id)
            anchored = tx is not None and tx.status is ChainTxStatus.CONFIRMED and bool(tx.tx_hash)
        matches.append(
            HashMatch(
                source="registry_db",
                anchored=anchored,
                report_id=report.chain_report_id,
                mrv_report_id=report.id,
                project=ProjectRef(
                    id=project.id, name=project.name, project_key=project_key(project.id)
                ),
                period_start=report.period_start_year,
                period_end=report.period_end_year,
                status="approved",
                chain=None,
            )
        )
    return matches


async def _chain_matches(session: AsyncSession, chain_id: int, sha256_hex: str) -> list[HashMatch]:
    found = await events(
        session, chain_id, ["ReportSubmitted"], arg_equals=("reportHash", "0x" + sha256_hex)
    )
    if not found:
        return []
    report_ids = [str(_int(e.args.get("reportId"))) for e in found]
    decided = await events(session, chain_id, ["ReportApproved", "ReportRejected"])
    decisions = _decisions(e for e in decided if str(_int(e.args.get("reportId"))) in report_ids)
    refs = await project_refs(session, chain_id, (e.project_key or "" for e in found))
    links = await mrv_report_links(session, found)
    return [
        HashMatch(
            source="chain",
            anchored=True,
            report_id=_int(e.args.get("reportId")),
            mrv_report_id=links.get(_int(e.args.get("reportId"))),
            project=refs.get(e.project_key or "")
            or ProjectRef(id=None, name=None, project_key=e.project_key or ""),
            period_start=_int(e.args.get("periodStart")),
            period_end=_int(e.args.get("periodEnd")),
            status=_report_status(decisions.get(_int(e.args.get("reportId")))),
            chain=chain_ref(e),
        )
        for e in found
    ]


# ---------------------------------------------------------------- projects


def public_projects_query() -> Any:
    return (
        select(Project, Organization)
        .join(Organization, Organization.id == Project.org_id)
        .where(Project.status.not_in(HIDDEN_STATUSES))
    )


async def centroids(session: AsyncSession, ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, Any]:
    if not ids:
        return {}
    rows = await session.execute(
        select(
            Project.id,
            func.ST_X(func.ST_PointOnSurface(Project.boundary)),
            func.ST_Y(func.ST_PointOnSurface(Project.boundary)),
        ).where(Project.id.in_(ids))
    )
    return {pid: (round(x, 4), round(y, 4)) for pid, x, y in rows}


async def registered_keys(session: AsyncSession, chain_id: int, keys: Sequence[str]) -> set[str]:
    if not keys:
        return set()
    rows = await session.scalars(
        select(ChainEvent.project_key).where(
            ChainEvent.chain_id == chain_id,
            ChainEvent.event == "ProjectRegistered",
            ChainEvent.project_key.in_(keys),
        )
    )
    return {k for k in rows if k}


def summary(
    project: Project, org: Organization, centroid: tuple[float, float], registered: bool
) -> PublicProjectSummary:
    return PublicProjectSummary(
        id=project.id,
        name=project.name,
        org=PublicOrg(name=org.name, org_type=org.org_type),
        state=project.state,
        ecosystem=project.ecosystem,
        methodology=project.methodology,
        status=project.status,
        area_ha=round(project.area_ha, 2),
        start_date=project.start_date,
        centroid=centroid,
        registered_on_chain=registered,
    )


async def simplified_boundary(session: AsyncSession, project_id: uuid.UUID) -> dict[str, Any]:
    geojson = await session.scalar(
        select(
            func.ST_AsGeoJSON(
                func.ST_Multi(
                    func.ST_SimplifyPreserveTopology(Project.boundary, BOUNDARY_TOLERANCE_DEG)
                ),
                6,
            )
        ).where(Project.id == project_id)
    )
    result: dict[str, Any] = json.loads(str(geojson))
    return result


async def evidence_stats(session: AsyncSession, project_id: uuid.UUID) -> EvidenceStats:
    row = (
        await session.execute(
            text(
                "SELECT count(*) AS total, "
                "count(*) FILTER (WHERE status = 'checked') AS checked, "
                "count(*) FILTER (WHERE status = 'rejected') AS rejected, "
                "count(*) FILTER (WHERE status = 'pending_checks') AS pending, "
                "percentile_cont(0.5) WITHIN GROUP (ORDER BY trust_score) "
                "  FILTER (WHERE status = 'checked' AND trust_score IS NOT NULL) AS median "
                "FROM field_submissions WHERE project_id = :p"
            ),
            {"p": project_id},
        )
    ).one()
    return EvidenceStats(
        submissions_total=row.total,
        checked=row.checked,
        rejected=row.rejected,
        pending_checks=row.pending,
        median_trust_score=float(row.median) if row.median is not None else None,
    )


def _slope_per_year(series: Sequence[MonthlyIndexValue]) -> float | None:
    if len(series) < 3:
        return None
    start = series[0].month
    xs = [((m.month.year - start.year) * 12 + m.month.month - start.month) / 12 for m in series]
    ys = [m.median_p50 for m in series]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    return round(sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / sxx, 4)


async def satellite_indicators(
    session: AsyncSession, project_id: uuid.UUID, today: date | None = None
) -> SatelliteIndicators:
    today = today or datetime.now(UTC).date()
    since = (today.replace(day=1) - timedelta(days=335)).replace(day=1)  # 12 calendar months
    p50 = "(index_stats->'ndvi'->>'p50')::float"
    base = (
        "FROM scene_observations WHERE project_id = :p AND clear_fraction >= :c "
        "AND index_stats->'ndvi'->>'p50' IS NOT NULL"
    )
    params = {"p": project_id, "c": PUBLIC_MIN_CLEAR}
    latest = (
        await session.execute(
            text(
                f"SELECT acquired_on, {p50} AS p50, clear_fraction {base} "
                "ORDER BY acquired_at DESC LIMIT 1"
            ),
            params,
        )
    ).first()
    monthly = await session.execute(
        text(
            f"SELECT date_trunc('month', acquired_on)::date AS month, "
            f"percentile_cont(0.5) WITHIN GROUP (ORDER BY {p50}) AS median, count(*) AS n "
            f"{base} AND acquired_on >= :since GROUP BY 1 ORDER BY 1"
        ),
        {**params, "since": since},
    )
    series = [
        MonthlyIndexValue(month=r.month, median_p50=round(float(r.median), 4), scenes=r.n)
        for r in monthly
    ]
    return SatelliteIndicators(
        min_clear_fraction=PUBLIC_MIN_CLEAR,
        latest_date=latest.acquired_on if latest else None,
        latest_p50=round(float(latest.p50), 4) if latest else None,
        latest_clear_fraction=latest.clear_fraction if latest else None,
        last_12_months=series,
        trend_p50_per_year=_slope_per_year(series),
    )


async def alert_counts(session: AsyncSession, project_id: uuid.UUID) -> AlertCounts:
    row = (
        await session.execute(
            text(
                "SELECT count(*) AS total, "
                "count(*) FILTER (WHERE severity = 'low') AS low, "
                "count(*) FILTER (WHERE severity = 'medium') AS medium, "
                "count(*) FILTER (WHERE severity = 'high') AS high, "
                "max(observed_on) AS latest FROM alerts WHERE project_id = :p"
            ),
            {"p": project_id},
        )
    ).one()
    return AlertCounts(
        total=row.total,
        low=row.low,
        medium=row.medium,
        high=row.high,
        latest_observed_on=row.latest,
    )


# ---------------------------------------------------------------- landing-page stats


def _totals(vintages_: Sequence[VintageOut]) -> CreditTotals:
    return CreditTotals(
        vintages=len(vintages_),
        issued=sum(v.issued for v in vintages_),
        retired=sum(v.retired for v in vintages_),
        replaced=sum(v.replaced for v in vintages_),
        outstanding=sum(v.outstanding for v in vintages_),
    )


async def stats(session: AsyncSession, chain_id: int) -> PublicStats:
    """Aggregates over public projects and indexed chain events (see `PublicStats`)."""
    by_status_rows = await session.execute(
        select(Project.status, func.count(), func.coalesce(func.sum(Project.area_ha), 0.0))
        .where(Project.status.not_in(HIDDEN_STATUSES))
        .group_by(Project.status)
    )
    by_status: dict[str, int] = {}
    area = 0.0
    for status_, count, area_ha in by_status_rows:
        by_status[ProjectStatus(status_).value] = int(count)
        area += float(area_ha)
    public_keys = [
        project_key(pid)
        for pid in await session.scalars(
            select(Project.id).where(Project.status.not_in(HIDDEN_STATUSES))
        )
    ]
    on_chain = await registered_keys(session, chain_id, public_keys)
    all_vintages = await vintages(session, chain_id)
    retirement_count = await session.scalar(
        select(func.count())
        .select_from(ChainEvent)
        .where(ChainEvent.chain_id == chain_id, ChainEvent.event == "CreditsRetired")
    )
    cursor = (
        await session.execute(
            select(func.max(ChainCursor.last_block), func.max(ChainCursor.updated_at)).where(
                ChainCursor.chain_id == chain_id
            )
        )
    ).one()
    last_event_time = await session.scalar(
        select(func.max(ChainEvent.block_time)).where(ChainEvent.chain_id == chain_id)
    )
    credit_types: tuple[CreditTypeName, ...] = ("temporary", "long_term")
    return PublicStats(
        projects=sum(by_status.values()),
        area_ha=round(area, 2),
        projects_by_status=by_status,
        registered=by_status.get(ProjectStatus.REGISTERED.value, 0),
        monitoring=by_status.get(ProjectStatus.MONITORING.value, 0),
        registered_on_chain=len(on_chain),
        credits={t: _totals([v for v in all_vintages if v.credit_type == t]) for t in credit_types},
        credits_total=_totals(all_vintages),
        retirements=int(retirement_count or 0),
        chain=IndexerStatus(
            chain_id=chain_id,
            last_indexed_block=cursor[0],
            last_indexed_at=cursor[1],
            last_event_block_time=last_event_time,
        ),
    )
