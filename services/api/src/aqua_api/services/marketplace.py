"""Read-only marketplace views derived from indexed AquaMarketplace events.

Listing state is folded from `Listed`, `Purchased` and `Cancelled` events of the configured
marketplace address (so a redeployed marketplace on the same chain never mixes listing ids), and
joined with vintages (AquaCredit `CreditsIssued`), the anchored report hash (AquaRegistry
`ReportSubmitted`), public project facts and the project's current stewardship score.

The platform never signs buyer actions: list, buy, cancel and retire happen in the buyer's or
seller's own wallet. Wallet addresses are left out of every response.

Sizes: the whole marketplace is folded in memory per request; fine at testnet scale (thousands of
events). Move to SQL aggregation before the event count reaches the hundreds of thousands.
"""

import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aqua_api.schemas.marketplace import (
    ListingOut,
    ListingStatus,
    PaymentToken,
    Price,
    PricePoint,
    StewardshipSummary,
    TradeOut,
    VintageFacts,
)
from aqua_api.schemas.public import ProjectRef, PublicProjectSummary, VintageOut
from aqua_api.services import public_registry as reg
from aqua_chain import artifacts
from aqua_chain.client import ChainClient
from aqua_core.logging import get_logger
from aqua_core.models import ChainEvent, Project
from aqua_core.settings import Settings
from aqua_core.stewardship.loader import project_stewardship

log = get_logger("aqua_api.marketplace")

MARKET_EVENTS = ("Listed", "Purchased", "Cancelled")
# (chain id, marketplace address) → (payment token address, decimals); immutable on-chain.
_TOKEN_CACHE: dict[tuple[int, str], PaymentToken] = {}


class MarketplaceUnavailableError(Exception):
    """The marketplace is not configured, or its payment token cannot be read."""


def format_units(units: int, decimals: int) -> str:
    """Exact decimal string: 12_500_000 at 6 decimals → "12.500000"."""
    sign = "-" if units < 0 else ""
    whole, frac = divmod(abs(units), 10**decimals)
    return f"{sign}{whole}.{frac:0{decimals}d}" if decimals else f"{sign}{whole}"


def price(units: int, token: PaymentToken) -> Price:
    return Price(units=units, decimal=format_units(units, token.decimals))


def marketplace_address(settings: Settings) -> str:
    if not settings.chain_marketplace_address:
        raise MarketplaceUnavailableError("The marketplace contract is not configured")
    return settings.chain_marketplace_address.lower()


async def payment_token(
    settings: Settings, client_factory: Callable[[], ChainClient]
) -> PaymentToken:
    """The marketplace's payment token. Decimals come from `CHAIN_PAYMENT_TOKEN_DECIMALS` when set,
    otherwise from the token's `decimals()` (token address from the marketplace's immutable
    `paymentToken()`), read once per process."""
    market = marketplace_address(settings)
    if settings.chain_payment_token_decimals is not None:
        return PaymentToken(
            address=settings.chain_usdc_address, decimals=settings.chain_payment_token_decimals
        )
    key = (settings.chain_id, market)
    cached = _TOKEN_CACHE.get(key)
    if cached is not None:
        return cached
    client = client_factory()
    try:
        await client.check()
        token_address = await client.contract("AquaMarketplace").functions.paymentToken().call()
        # Any ERC-20 answers decimals(); the MockUSDC ABI carries it.
        token = client.w3.eth.contract(address=token_address, abi=artifacts.abi("MockUSDC"))
        decimals = int(await token.functions.decimals().call())
    except Exception as exc:
        log.warning("marketplace.payment_token_unavailable", error=type(exc).__name__)
        raise MarketplaceUnavailableError(
            f"Payment token decimals unavailable ({type(exc).__name__}); "
            "set CHAIN_PAYMENT_TOKEN_DECIMALS or check the RPC"
        ) from exc
    finally:
        await client.close()
    resolved = PaymentToken(address=str(token_address), decimals=decimals)
    _TOKEN_CACHE[key] = resolved
    return resolved


# ---------------------------------------------------------------- folding events


@dataclass
class Listing:
    listed: ChainEvent
    purchases: list[ChainEvent] = field(default_factory=list)
    cancelled: ChainEvent | None = None

    @property
    def listing_id(self) -> int:
        return reg._int(self.listed.args.get("listingId"))

    @property
    def token_id(self) -> int:
        return reg._int(self.listed.args.get("tokenId"))

    @property
    def amount(self) -> int:
        return reg._int(self.listed.args.get("amount"))

    @property
    def price_per_unit(self) -> int:
        return reg._int(self.listed.args.get("pricePerUnit"))

    @property
    def sold(self) -> int:
        return sum(reg._int(p.args.get("amount")) for p in self.purchases)

    @property
    def remaining(self) -> int:
        return max(self.amount - self.sold, 0)

    @property
    def status(self) -> ListingStatus:
        if self.cancelled is not None:
            return "cancelled"
        return "sold_out" if self.remaining == 0 else "active"

    @property
    def closed(self) -> ChainEvent | None:
        if self.cancelled is not None:
            return self.cancelled
        return self.purchases[-1] if self.status == "sold_out" and self.purchases else None


async def market_events(session: AsyncSession, chain_id: int, address: str) -> list[ChainEvent]:
    rows = await session.scalars(
        select(ChainEvent)
        .where(
            ChainEvent.chain_id == chain_id,
            ChainEvent.contract == "AquaMarketplace",
            func.lower(ChainEvent.address) == address,
            ChainEvent.event.in_(MARKET_EVENTS),
        )
        .order_by(ChainEvent.block_number, ChainEvent.log_index, ChainEvent.id)
    )
    return list(rows)


def fold(events: Iterable[ChainEvent]) -> dict[int, Listing]:
    listings: dict[int, Listing] = {}
    for event in events:
        lid = reg._int(event.args.get("listingId"))
        if event.event == "Listed":
            listings[lid] = Listing(listed=event)
            continue
        listing = listings.get(lid)
        if listing is None:  # pragma: no cover - the contract emits Listed first
            continue
        if event.event == "Purchased":
            listing.purchases.append(event)
        else:
            listing.cancelled = event
    return listings


# ---------------------------------------------------------------- joins


@dataclass
class Context:
    """Vintages, report hashes and public project facts behind a set of listings."""

    vintages: dict[int, VintageOut]
    report_hashes: dict[int, str]
    mrv_links: dict[int, uuid.UUID]
    projects: dict[uuid.UUID, tuple[Project, PublicProjectSummary]]

    def vintage_facts(self, token_id: int) -> VintageFacts | None:
        v = self.vintages.get(token_id)
        if v is None:
            return None
        return VintageFacts(
            token_id=v.token_id,
            credit_type=v.credit_type,
            credit_unit=v.credit_unit,
            report_id=v.report_id,
            report_hash=self.report_hashes.get(v.report_id),
            mrv_report_id=self.mrv_links.get(v.report_id),
            expires_at=v.expires_at,
            expired=v.expired,
        )

    def project_of(self, token_id: int) -> ProjectRef | None:
        v = self.vintages.get(token_id)
        return v.project if v else None

    def facts(self, token_id: int) -> PublicProjectSummary | None:
        ref = self.project_of(token_id)
        if ref is None or ref.id is None:
            return None
        found = self.projects.get(ref.id)
        return found[1] if found else None


async def context(session: AsyncSession, chain_id: int, token_ids: Iterable[int]) -> Context:
    wanted = set(token_ids)
    vintages = {
        v.token_id: v for v in await reg.vintages(session, chain_id) if v.token_id in wanted
    }
    report_ids = {v.report_id for v in vintages.values()}
    submitted = [
        e
        for e in await reg.events(session, chain_id, ["ReportSubmitted"])
        if reg._int(e.args.get("reportId")) in report_ids
    ]
    hashes = {
        reg._int(e.args.get("reportId")): str(e.args.get("reportHash", "")) for e in submitted
    }
    links = await reg.mrv_report_links(session, submitted)
    ids = sorted({v.project.id for v in vintages.values() if v.project.id is not None})
    projects: dict[uuid.UUID, tuple[Project, PublicProjectSummary]] = {}
    if ids:
        rows = (await session.execute(reg.public_projects_query().where(Project.id.in_(ids)))).all()
        centroids = await reg.centroids(session, [p.id for p, _ in rows])
        for project, org in rows:
            # Resolved through ProjectRegistered, so it is registered on-chain by construction.
            projects[project.id] = (project, reg.summary(project, org, centroids[project.id], True))
    return Context(vintages, hashes, links, projects)


async def stewardship(
    session: AsyncSession, projects: Sequence[Project]
) -> dict[uuid.UUID, StewardshipSummary]:
    out: dict[uuid.UUID, StewardshipSummary] = {}
    for project in projects:
        if project.id in out:
            continue
        score = await project_stewardship(session, project)
        out[project.id] = StewardshipSummary(
            score=score.score,
            insufficient_data=score.insufficient_data,
            score_version=score.score_version,
        )
    return out


def listing_out(
    listing: Listing,
    ctx: Context,
    token: PaymentToken,
    scores: dict[uuid.UUID, StewardshipSummary],
) -> ListingOut | None:
    vintage = ctx.vintage_facts(listing.token_id)
    project = ctx.project_of(listing.token_id)
    if vintage is None or project is None:  # pragma: no cover - listing needs a minted token
        return None
    closed = listing.closed
    return ListingOut(
        listing_id=listing.listing_id,
        status=listing.status,
        vintage=vintage,
        project=project,
        project_facts=ctx.facts(listing.token_id),
        stewardship=scores.get(project.id) if project.id else None,
        amount_listed=listing.amount,
        amount_sold=listing.sold,
        remaining=listing.remaining,
        price_per_unit=price(listing.price_per_unit, token),
        payment_token=token,
        listed=reg.chain_ref(listing.listed),
        listed_at=listing.listed.block_time,
        closed=reg.chain_ref(closed) if closed is not None else None,
    )


def trades(listings: dict[int, Listing], ctx: Context, token: PaymentToken) -> list[TradeOut]:
    """Every purchase, newest first."""
    out = []
    for listing in listings.values():
        vintage = ctx.vintage_facts(listing.token_id)
        for event in listing.purchases:
            out.append(
                TradeOut(
                    listing_id=listing.listing_id,
                    token_id=listing.token_id,
                    credit_type=vintage.credit_type if vintage else None,
                    project=ctx.project_of(listing.token_id),
                    amount=reg._int(event.args.get("amount")),
                    price_per_unit=price(listing.price_per_unit, token),
                    total_price=price(reg._int(event.args.get("totalPrice")), token),
                    fee=price(reg._int(event.args.get("fee")), token),
                    payment_token=token,
                    chain=reg.chain_ref(event),
                    block_time=event.block_time,
                )
            )
    out.sort(key=lambda t: (t.chain.block_number, t.chain.log_index), reverse=True)
    return out


def price_history(trade_list: Sequence[TradeOut]) -> list[PricePoint]:
    return [
        PricePoint(
            time=t.block_time,
            block_number=t.chain.block_number,
            token_id=t.token_id,
            credit_type=t.credit_type,
            amount=t.amount,
            price_per_unit=t.price_per_unit,
        )
        for t in reversed(trade_list)
    ]


def project_listings(listings: dict[int, Listing], ctx: Context, key: str) -> list[Listing]:
    by_project: dict[str, list[Listing]] = defaultdict(list)
    for listing in listings.values():
        ref = ctx.project_of(listing.token_id)
        if ref is not None:
            by_project[ref.project_key].append(listing)
    return by_project.get(key, [])
