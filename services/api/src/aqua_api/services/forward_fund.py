"""Off-chain eligibility for forward-fund offers (IMPLEMENTATION_PLAN §Phase 7 step 2).

Two gates, each a FastAPI dependency so tests can override them:

1. Removal capacity (`capacity_provider`): an offer may not exceed the project's verified removal
   capacity minus what it already committed. Capacity comes from two independent records:
   - `mrv_reports` (preferred): sum of the signed `lccc_units` of the project's **approved** MRV
     reports. A reversal report carries negative lCCC, so the sum is already net of reversals.
   - on-chain `AquaRegistry.ReportApproved` events (indexed): sum of the signed `lccc`.
   The DB figure is used when the two agree. When they disagree (e.g. an approval not anchored or
   not indexed yet, or a chain approval with no matching DB report) the smaller one is used, and
   `sources` explains both. Never negative.
2. Stewardship (`eligibility_check`): the organisation must pass
   `aqua_core.stewardship.loader.forward_funding_eligibility` (criteria `forward-funding-v1`).

Commitments (active offers, undelivered open awards, in-flight relays) are netted in the router.
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aqua_chain.encoding import project_id_bytes32, to_hex
from aqua_core.models import ChainEvent, MRVReport, ReportStatus
from aqua_core.stewardship.loader import forward_funding_eligibility

MRV_SOURCE = "mrv_reports"
CHAIN_SOURCE = "onchain_report_approved"


@dataclass(frozen=True)
class CapacitySource:
    name: str  # MRV_SOURCE | CHAIN_SOURCE
    total: int  # signed sum (may be negative after reversals); `RemovalCapacity.total` clamps
    description: str
    report_ids: tuple[str, ...]  # MRV report UUIDs, or on-chain report ids


@dataclass(frozen=True)
class RemovalCapacity:
    """Verified removal capacity of a project, in whole tCO2e (lCCC)."""

    total: int  # capacity used for the check (0 when nothing verified yet)
    source: str  # human-readable provenance of `total`
    report_ids: tuple[int, ...]  # on-chain report ids (ReportApproved)
    sources: tuple[CapacitySource, ...] = ()

    @property
    def sources_agree(self) -> bool:
        return len({s.total for s in self.sources}) <= 1


RemovalCapacityProvider = Callable[[AsyncSession, int, uuid.UUID], Awaitable[RemovalCapacity]]
EligibilityCheck = Callable[[AsyncSession, uuid.UUID], Awaitable[tuple[bool, list[str]]]]


async def approved_mrv_source(session: AsyncSession, project_id: uuid.UUID) -> CapacitySource:
    rows = (
        await session.execute(
            select(MRVReport.id, MRVReport.lccc_units)
            .where(MRVReport.project_id == project_id, MRVReport.status == ReportStatus.APPROVED)
            .order_by(MRVReport.period_end_year, MRVReport.version)
        )
    ).all()
    return CapacitySource(
        name=MRV_SOURCE,
        total=sum(int(r.lccc_units) for r in rows),
        description="sum of approved MRV reports' lCCC units (mrv_reports, net of reversals)",
        report_ids=tuple(str(r.id) for r in rows),
    )


async def approved_onchain_source(
    session: AsyncSession, chain_id: int, project_key: str
) -> CapacitySource:
    rows = await session.scalars(
        select(ChainEvent).where(
            ChainEvent.chain_id == chain_id,
            ChainEvent.contract == "AquaRegistry",
            ChainEvent.event == "ReportApproved",
            func.lower(ChainEvent.project_key) == project_key.lower(),
        )
    )
    events = list(rows)
    return CapacitySource(
        name=CHAIN_SOURCE,
        total=sum(int(e.args["lccc"]) for e in events),
        description="sum of verifier-approved lCCC (AquaRegistry.ReportApproved, indexed)",
        report_ids=tuple(str(i) for i in sorted(int(e.args["reportId"]) for e in events)),
    )


async def mrv_capacity(
    session: AsyncSession, chain_id: int, project_id: uuid.UUID
) -> RemovalCapacity:
    """Approved MRV reports, cross-checked against on-chain approvals (smaller wins)."""
    db = await approved_mrv_source(session, project_id)
    chain = await approved_onchain_source(session, chain_id, to_hex(project_id_bytes32(project_id)))
    if db.total == chain.total:
        used, why = db, "approved MRV reports (agrees with on-chain ReportApproved)"
    elif chain.total < db.total:
        used, why = chain, "on-chain ReportApproved: lower than the approved MRV reports"
    else:
        used, why = db, "approved MRV reports: lower than on-chain ReportApproved"
    return RemovalCapacity(
        total=max(used.total, 0),
        source=f"{why}; {used.description}",
        report_ids=tuple(int(i) for i in chain.report_ids),
        sources=(db, chain),
    )


def capacity_provider() -> RemovalCapacityProvider:
    """FastAPI dependency: the active capacity source."""
    return mrv_capacity


def eligibility_check() -> EligibilityCheck:
    """FastAPI dependency: the organisation's forward-funding stewardship gate."""

    async def check(session: AsyncSession, org_id: uuid.UUID) -> tuple[bool, list[str]]:
        return await forward_funding_eligibility(session, org_id)

    return check
