"""Off-chain eligibility for forward-fund offers (IMPLEMENTATION_PLAN §Phase 7 step 2): an offer
may not exceed the project's estimated removal capacity.

The capacity source is pluggable: `RemovalCapacityProvider` is any async callable, resolved per
request through the `capacity_provider` dependency (tests or a future MRV-based source override
it with `app.dependency_overrides[capacity_provider]`).

TODO(#19): once the MRV report model lands, switch the default to the latest MRV report's
estimated removals (lCCC) for the project, keeping the commitment netting below. Until then the
only verified figure available on this branch is the verifier-approved lCCC anchored on-chain
(`AquaRegistry.ReportApproved`, net of reversals), read from the indexed `chain_events`.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aqua_core.models import ChainEvent


@dataclass(frozen=True)
class RemovalCapacity:
    """Verified removal capacity of a project, in whole tCO2e (lCCC)."""

    total: int  # capacity from the source below (0 when nothing verified yet)
    source: str  # human-readable provenance of `total`
    report_ids: tuple[int, ...]  # on-chain report ids it was derived from


RemovalCapacityProvider = Callable[[AsyncSession, int, str], Awaitable[RemovalCapacity]]


async def approved_onchain_capacity(
    session: AsyncSession, chain_id: int, project_key: str
) -> RemovalCapacity:
    """Sum of verifier-approved lCCC (`ReportApproved.lccc`, negative = reversal) for the
    project, from indexed AquaRegistry events. Never negative."""
    rows = await session.scalars(
        select(ChainEvent).where(
            ChainEvent.chain_id == chain_id,
            ChainEvent.contract == "AquaRegistry",
            ChainEvent.event == "ReportApproved",
            func.lower(ChainEvent.project_key) == project_key.lower(),
        )
    )
    events = list(rows)
    total = sum(int(e.args["lccc"]) for e in events)
    return RemovalCapacity(
        total=max(total, 0),
        source="sum of verifier-approved lCCC (AquaRegistry.ReportApproved, indexed)",
        report_ids=tuple(sorted(int(e.args["reportId"]) for e in events)),
    )


def capacity_provider() -> RemovalCapacityProvider:
    """FastAPI dependency: the active capacity source. Swap here (or override) for MRV."""
    return approved_onchain_capacity
