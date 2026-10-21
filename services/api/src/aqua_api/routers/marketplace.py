"""Public marketplace read API and retirement certificate PDFs (Phase 7): no login, read-only.

Listing, buying, cancelling and retiring are done by buyers and sellers in their own wallets
(wagmi/viem in the web app) directly against AquaMarketplace / AquaCredit; this router only
presents what the indexer has stored in `chain_events`. It never signs anything.

Privacy: as in the public registry (`aqua_api.routers.public`), responses are allow-listed schemas
(`aqua_api.schemas.marketplace`). Seller and buyer wallet addresses are left out entirely.

Caching: JSON carries `Cache-Control: public, max-age=60` + weak ETag. Certificates are a pure
function of indexed facts, so they carry a strong ETag (304 on `If-None-Match`, no re-render).
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from starlette.concurrency import run_in_threadpool

from aqua_api.deps import SessionDep, SettingsDep
from aqua_api.routers.public import Limit, Offset, cached
from aqua_api.schemas.marketplace import (
    ListingOut,
    ListingSort,
    PaymentToken,
    ProjectMarket,
    TradeOut,
)
from aqua_api.schemas.public import CreditTypeName, Page, ProjectRef
from aqua_api.services import certificate as cert
from aqua_api.services import marketplace as market
from aqua_api.services import public_registry as reg
from aqua_chain.client import ChainClient
from aqua_core.models import Project
from aqua_core.settings import Settings

router = APIRouter(prefix="/public", tags=["public marketplace"])

CERT_MAX_AGE_S = 3600


async def _token(request: Request, settings: Settings) -> PaymentToken:
    def client() -> ChainClient:
        factory = getattr(request.app.state, "chain_client_factory", None)
        made: ChainClient = factory() if factory else ChainClient.from_settings(settings)
        return made

    try:
        return await market.payment_token(settings, client)
    except market.MarketplaceUnavailableError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


async def _state(
    request: Request, session: SessionDep, settings: Settings
) -> tuple[PaymentToken, dict[int, market.Listing], market.Context]:
    token = await _token(request, settings)
    events = await market.market_events(
        session, settings.chain_id, market.marketplace_address(settings)
    )
    listings = market.fold(events)
    ctx = await market.context(session, settings.chain_id, {x.token_id for x in listings.values()})
    return token, listings, ctx


async def _outs(
    session: SessionDep,
    listings: list[market.Listing],
    ctx: market.Context,
    token: PaymentToken,
) -> list[ListingOut]:
    projects = []
    for listing in listings:
        ref = ctx.project_of(listing.token_id)
        if ref is not None and ref.id is not None and ref.id in ctx.projects:
            projects.append(ctx.projects[ref.id][0])
    scores = await market.stewardship(session, projects)
    outs = [market.listing_out(x, ctx, token, scores) for x in listings]
    return [o for o in outs if o is not None]


def _buyable(listing: market.Listing, ctx: market.Context) -> bool:
    vintage = ctx.vintage_facts(listing.token_id)
    return listing.status == "active" and vintage is not None and not vintage.expired


@router.get("/marketplace/listings", response_model=Page[ListingOut])
async def list_listings(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    project_id: uuid.UUID | None = None,
    credit_type: CreditTypeName | None = None,
    state: str | None = None,
    max_price: Annotated[
        int | None, Query(ge=0, description="Max price per unit, payment-token base units")
    ] = None,
    sort: ListingSort = "newest",
    limit: Limit = 20,
    offset: Offset = 0,
) -> Response:
    """Active listings of unexpired vintages, joined with the project's public facts, the anchored
    MRV report hash and the project's current stewardship score (with its `score_version`)."""
    token, listings, ctx = await _state(request, session, settings)
    found = []
    for listing in listings.values():
        if not _buyable(listing, ctx):
            continue
        ref = ctx.project_of(listing.token_id)
        facts = ctx.facts(listing.token_id)
        vintage = ctx.vintage_facts(listing.token_id)
        if project_id is not None and (ref is None or ref.id != project_id):
            continue
        if credit_type is not None and (vintage is None or vintage.credit_type != credit_type):
            continue
        if state and (facts is None or facts.state.lower() != state.lower()):
            continue
        if max_price is not None and listing.price_per_unit > max_price:
            continue
        found.append(listing)
    if sort == "newest":
        found.sort(key=lambda x: (x.listed.block_number, x.listing_id), reverse=True)
    else:
        found.sort(key=lambda x: (x.price_per_unit, x.listing_id), reverse=sort == "price_desc")
    page = await _outs(session, found[offset : offset + limit], ctx, token)
    return cached(
        request, Page[ListingOut](items=page, total=len(found), limit=limit, offset=offset)
    )


@router.get("/marketplace/listings/{listing_id}", response_model=ListingOut)
async def get_listing(
    listing_id: int, request: Request, session: SessionDep, settings: SettingsDep
) -> Response:
    """One listing in any state (active, sold_out or cancelled)."""
    token, listings, ctx = await _state(request, session, settings)
    listing = listings.get(listing_id)
    outs = await _outs(session, [listing], ctx, token) if listing is not None else []
    if not outs:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Listing not found")
    return cached(request, outs[0])


@router.get("/marketplace/trades", response_model=Page[TradeOut])
async def list_trades(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    project_id: uuid.UUID | None = None,
    limit: Limit = 20,
    offset: Offset = 0,
) -> Response:
    """Purchases, newest first."""
    token, listings, ctx = await _state(request, session, settings)
    trades = market.trades(listings, ctx, token)
    if project_id is not None:
        trades = [t for t in trades if t.project is not None and t.project.id == project_id]
    return cached(
        request,
        Page[TradeOut](
            items=trades[offset : offset + limit], total=len(trades), limit=limit, offset=offset
        ),
    )


@router.get("/marketplace/projects/{project_id}/market", response_model=ProjectMarket)
async def project_market(
    project_id: uuid.UUID, request: Request, session: SessionDep, settings: SettingsDep
) -> Response:
    """A public project's active listings, its trades and the price history (one point per trade,
    oldest first)."""
    row = (
        await session.execute(reg.public_projects_query().where(Project.id == project_id))
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    project: Project = row[0]
    key = reg.project_key(project.id)
    token, listings, ctx = await _state(request, session, settings)
    mine = market.project_listings(listings, ctx, key)
    active = await _outs(session, [x for x in mine if _buyable(x, ctx)], ctx, token)
    active.sort(key=lambda o: (o.price_per_unit.units, o.listing_id))
    trades = [
        t for t in market.trades(listings, ctx, token) if t.project and t.project.project_key == key
    ]
    return cached(
        request,
        ProjectMarket(
            project=ProjectRef(id=project.id, name=project.name, project_key=key),
            payment_token=token,
            active_listings=active,
            trades=trades,
            price_history=market.price_history(trades),
            volume_units=sum(t.amount for t in trades),
            last_price=trades[0].price_per_unit if trades else None,
        ),
    )


# ---------------------------------------------------------------- retirement certificates


@router.get(
    "/retirements/{retirement_id}/certificate.pdf",
    name="retirement_certificate_pdf",
    responses={200: {"content": {"application/pdf": {}}}},
    response_class=Response,
)
async def retirement_certificate_pdf(
    retirement_id: int, request: Request, session: SessionDep, settings: SettingsDep
) -> Response:
    """Printable retirement certificate: retirement facts, the vintage's MRV report hash, the
    verification payload hash and a QR code to the public certificate page. The same retirement
    always gives the same bytes. 503 until the indexer has stored the block time."""
    found = await reg.retirements(session, settings.chain_id, retirement_id=retirement_id)
    if not found:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Retirement not found")
    retirement, event = found[0]
    if event.block_time is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The retirement's block time is not indexed yet; retry shortly",
            headers={"Retry-After": "60"},
        )
    verification = reg.certificate_verification(settings.public_base_url, retirement, event)
    vintages = await reg.vintages(session, settings.chain_id, token_id=retirement.token_id)
    vintage = vintages[0] if vintages else None
    report_hash = None
    methodology = "—"
    if vintage is not None:
        submitted = await reg.events(
            session,
            settings.chain_id,
            ["ReportSubmitted"],
            arg_equals=("reportId", str(vintage.report_id)),
        )
        if submitted:
            report_hash = str(submitted[0].args.get("reportHash", "")) or None
    project = retirement.project
    if project is not None and project.id is not None:
        found_project = await session.get(Project, project.id)
        if found_project is not None:
            value = found_project.methodology.value
            methodology = cert.METHODOLOGY_TEXT.get(value, value)
    credit_type = retirement.credit_type
    data = cert.CertificateData(
        retirement_id=retirement.retirement_id,
        amount=retirement.amount,
        credit_unit=vintage.credit_unit if vintage else "credits",
        credit_type=credit_type,
        token_id=retirement.token_id,
        project_name=project.name if project else None,
        project_key=project.project_key if project else None,
        methodology=methodology,
        report_id=vintage.report_id if vintage else None,
        report_hash=report_hash,
        beneficiary=retirement.beneficiary,
        purpose=retirement.purpose,
        chain_id=event.chain_id,
        contract_address=event.address,
        tx_hash=event.tx_hash,
        log_index=event.log_index,
        block_number=event.block_number,
        block_hash=event.block_hash,
        block_time=event.block_time,
        explorer_url=verification.chain.explorer_url,
        certificate_url=verification.certificate_url,
        payload_sha256=verification.payload_sha256,
    )
    html = cert.to_html(data)
    etag = cert.etag(html)
    headers = {
        "Cache-Control": f"public, max-age={CERT_MAX_AGE_S}",
        "ETag": etag,
        "Content-Disposition": f'inline; filename="aquacreds-retirement-{retirement_id}.pdf"',
    }
    if etag in {t.strip() for t in request.headers.get("if-none-match", "").split(",")}:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    pdf = await run_in_threadpool(cert.to_pdf, html)
    return Response(pdf, media_type="application/pdf", headers=headers)
