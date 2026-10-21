"""Forward funding by reverse auction (AquaForwardFund, IMPLEMENTATION_PLAN §Phase 7).

Reads are derived from indexed contract events (`chain_events`), optionally checked against live
contract views (`?onchain=true`). Writes are limited to custodial project owners: an org admin
can have the platform relay `submitOffer` / `withdrawOffer` from the organisation's HD wallet (the
project's on-chain owner), once the organisation passes the forward-funding stewardship
criteria and only within the project's verified removal capacity. Buyers act with their own wallets (createRequest, award,
refundUndelivered, withdraw); the platform never relays buyer actions.

An advance is never a guarantee of credits: every response carries a disclaimer, and upfront
payments are flagged `buyer_risk: true`.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select

from aqua_api.deps import CurrentUser, SessionDep, SettingsDep, can_read_all_projects, org_role
from aqua_api.routers.chain import _client, _relayer, _visible_project
from aqua_api.schemas.chain import ChainTxOut
from aqua_api.schemas.forward_fund import (
    AwardOut,
    CapacityOut,
    CapacitySourceOut,
    DeliveryOut,
    OfferIn,
    OfferOut,
    OnchainCheckOut,
    ProjectForwardFundOut,
    RequestDetailOut,
    RequestListOut,
    RequestSummaryOut,
    UpfrontOut,
)
from aqua_api.services import audit
from aqua_api.services.forward_fund import (
    EligibilityCheck,
    RemovalCapacity,
    RemovalCapacityProvider,
    capacity_provider,
    eligibility_check,
)
from aqua_chain import forward_fund as ff
from aqua_chain.encoding import project_id_bytes32, to_hex
from aqua_chain.relayer import Relayer
from aqua_core.models import (
    ChainEvent,
    ChainTransaction,
    ChainTxStatus,
    OrgRole,
    OrgWallet,
    Project,
    User,
)
from aqua_core.models.chain import PENDING_STATUSES
from aqua_core.settings import Settings

router = APIRouter(tags=["forward-fund"])

CapacityDep = Annotated[RemovalCapacityProvider, Depends(capacity_provider)]
EligibilityDep = Annotated[EligibilityCheck, Depends(eligibility_check)]
PENDING = PENDING_STATUSES  # queued, waiting for gas, or broadcast: still in flight


# ---------------------------------------------------------------- helpers


def _fund_address(request: Request) -> str:
    settings: Settings = request.app.state.settings
    if not settings.chain_forward_fund_address:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "forward funding is not configured: set CHAIN_FORWARD_FUND_ADDRESS",
        )
    return settings.chain_forward_fund_address


def _chain_id(request: Request) -> int:
    settings: Settings = request.app.state.settings
    return settings.chain_id


async def _book(request: Request, session: SessionDep) -> dict[int, ff.RequestState]:
    address = _fund_address(request)
    try:
        return await ff.load_book(session, chain_id=_chain_id(request), address=address)
    except ff.InconsistentEventsError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, f"forward-fund index is incomplete: {exc}"
        ) from exc


async def _project_ids(session: SessionDep, keys: set[str]) -> dict[str, uuid.UUID]:
    """bytes32 project key → platform project id, from the platform's registration txs."""
    if not keys:
        return {}
    txs = await session.scalars(
        select(ChainTransaction).where(
            ChainTransaction.contract == "AquaRegistry",
            ChainTransaction.function == "registerProject",
            ChainTransaction.status == ChainTxStatus.CONFIRMED,
        )
    )
    out: dict[str, uuid.UUID] = {}
    for tx in txs:
        key = str(tx.args[0]).lower()
        if key in keys:
            out[key] = uuid.UUID(tx.entity_id)
    return out


def _upfront(r: ff.RequestState) -> UpfrontOut:
    return UpfrontOut(bps=r.upfront_bps, share_pct=r.upfront_bps / 100, amount_paid=r.upfront_paid)


def _summary(r: ff.RequestState) -> dict[str, Any]:
    return {
        "request_id": r.request_id,
        "buyer": r.buyer,
        "status": r.status,
        "quantity": r.quantity,
        "max_price": r.max_price,
        "escrow_deposited": r.escrow_deposited,
        "offer_window_end": r.offer_window_end,
        "delivery_deadline": r.delivery_deadline,
        "upfront": _upfront(r),
        "awarded_quantity": r.awarded_quantity,
        "awarded_value": r.awarded_value,
        "unused_escrow_refunded": r.unused_escrow_refunded,
        "delivered_quantity": r.delivered_quantity,
        "released": r.released,
        "refunded_undelivered": r.refunded_undelivered,
        "refundable_after_deadline": r.refundable,
        "escrow_left": r.escrow_left,
        "active_offers": len(r.active_offers),
        "tx_hash": r.tx_hash,
    }


def _offer_out(o: ff.OfferState, ids: dict[str, uuid.UUID]) -> OfferOut:
    return OfferOut(
        offer_id=o.offer_id,
        request_id=o.request_id,
        project_key=o.project_key,
        project_id=ids.get(o.project_key),
        seller=o.seller,
        price_per_unit=o.price_per_unit,
        quantity=o.quantity,
        status=o.status,
        tx_hash=o.tx_hash,
        withdrawn_tx_hash=o.withdrawn_tx_hash,
    )


def _award_out(a: ff.AwardState, ids: dict[str, uuid.UUID]) -> AwardOut:
    return AwardOut(
        award_id=a.award_id,
        request_id=a.request_id,
        project_key=a.project_key,
        project_id=ids.get(a.project_key),
        seller=a.seller,
        quantity=a.quantity,
        price_per_unit=a.price_per_unit,
        value=a.value,
        upfront_paid=a.upfront_paid,
        delivered_quantity=a.delivered,
        released=a.released,
        open_quantity=a.open_quantity,
        escrow_left=a.escrow_left,
        refundable_after_deadline=a.refundable,
        refunded=a.refund,
        settled=a.settled,
        deliveries=[DeliveryOut(**d) for d in a.deliveries],
    )


async def _onchain_check(request: Request, r: ff.RequestState) -> OnchainCheckOut:
    """Live reads of getRequest/getAward/getOffer at one block, compared with the index."""
    client = _client(request)
    try:
        fund = client.contract(ff.CONTRACT)
        block = await client.latest_block()
        req = await fund.functions.getRequest(r.request_id).call(block_identifier=block)
        award_ids = await fund.functions.awardIds(r.request_id).call(block_identifier=block)
        awards: dict[int, dict[str, int | bool]] = {}
        refundable: dict[int, int] = {}
        for aid in award_ids:
            quantity, delivered, upfront, released, settled = await fund.functions.getAward(
                aid
            ).call(block_identifier=block)
            offer = await fund.functions.getOffer(aid).call(block_identifier=block)
            price = int(offer[4])
            awards[int(aid)] = {
                "quantity": int(quantity),
                "delivered": int(delivered),
                "upfront_paid": int(upfront),
                "released": int(released),
                "settled": bool(settled),
            }
            refundable[int(aid)] = (
                0 if settled else int(quantity) * price - int(upfront) - int(released)
            )
    except Exception as exc:  # RPC/provider failure: surfaced, never papered over
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"on-chain read failed: {type(exc).__name__}: {str(exc)[:200]}",
        ) from exc
    finally:
        await client.close()

    mismatches: list[str] = []
    if int(req[7]) != r.escrow_left:
        mismatches.append(f"escrow: chain {int(req[7])}, indexed {r.escrow_left}")
    if int(req[8]) != r.awarded_quantity:
        mismatches.append(f"awarded quantity: chain {int(req[8])}, indexed {r.awarded_quantity}")
    if set(awards) != set(r.awards):
        mismatches.append(f"award ids: chain {sorted(awards)}, indexed {sorted(r.awards)}")
    for aid, chain_award in awards.items():
        indexed = r.awards.get(aid)
        if indexed is None:
            continue
        if refundable[aid] != indexed.refundable:
            mismatches.append(
                f"award {aid} refundable: chain {refundable[aid]}, indexed {indexed.refundable}"
            )
        if chain_award["delivered"] != indexed.delivered:
            mismatches.append(
                f"award {aid} delivered: chain {chain_award['delivered']}, "
                f"indexed {indexed.delivered}"
            )
    return OnchainCheckOut(
        block_number=block,
        escrow=int(req[7]),
        awarded=bool(req[4]),
        awarded_quantity=int(req[8]),
        awards=awards,
        refundable=refundable,
        consistent=not mismatches,
        mismatches=mismatches,
    )


async def _admin_project(
    session: SessionDep, project_id: uuid.UUID, user: User, settings: Settings
) -> Project:
    project = await session.get(Project, project_id)
    role = org_role(user, project.org_id) if project else None
    if project is None or (role is None and not can_read_all_projects(user, settings)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    if role is not OrgRole.ADMIN:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Only an admin of the project's organisation can do this"
        )
    return project


async def _custodial_owner(session: SessionDep, request: Request, project: Project) -> OrgWallet:
    """The org wallet, which must be the project's indexed on-chain owner."""
    wallet = await session.get(OrgWallet, project.org_id)
    key = to_hex(project_id_bytes32(project.id))
    registered = None
    if wallet is not None:
        registered = await session.scalar(
            select(ChainEvent.id).where(
                ChainEvent.chain_id == _chain_id(request),
                ChainEvent.contract == "AquaRegistry",
                ChainEvent.event == "ProjectRegistered",
                ChainEvent.project_key == key,
                func.lower(ChainEvent.args["owner"].astext) == wallet.address.lower(),
            )
        )
    if wallet is None or registered is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Project is not registered on-chain under the organisation's custodial wallet "
            "(or the registration is not indexed yet)",
        )
    return wallet


async def _pending(session: SessionDep, project: Project) -> list[ChainTransaction]:
    rows = await session.scalars(
        select(ChainTransaction).where(
            ChainTransaction.entity_type == "project",
            ChainTransaction.entity_id == str(project.id),
            ChainTransaction.contract == ff.CONTRACT,
            ChainTransaction.status.in_(PENDING),
        )
    )
    return list(rows)


def _capacity_out(capacity: RemovalCapacity, committed: int) -> CapacityOut:
    return CapacityOut(
        total=capacity.total,
        committed=committed,
        available=max(capacity.total - committed, 0),
        source=capacity.source,
        report_ids=list(capacity.report_ids),
        sources_agree=capacity.sources_agree,
        capacity_sources=[
            CapacitySourceOut(
                name=src.name,
                total=src.total,
                description=src.description,
                report_ids=list(src.report_ids),
            )
            for src in capacity.sources
        ],
    )


async def _relay(
    request: Request,
    session: SessionDep,
    relayer: Relayer,
    tx: ChainTransaction,
    user: User,
    action: str,
) -> ChainTransaction:
    await audit.record(
        session,
        actor_id=user.id,
        action=f"forward_fund.{action}.requested",
        entity_type="project",
        entity_id=uuid.UUID(tx.entity_id),
        data={"chain_tx_id": str(tx.id), "from": tx.from_address, "args": tx.args},
    )
    await session.commit()
    request.app.state.enqueue("aqua.chain.send", str(tx.id), queue="chain")
    return tx


# ---------------------------------------------------------------- reads


@router.get("/forward-fund/requests", response_model=RequestListOut)
async def list_requests(
    request: Request,
    user: CurrentUser,
    session: SessionDep,
    status_filter: Annotated[ff.RequestStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> RequestListOut:
    book = await _book(request, session)
    items = [
        r
        for r in sorted(book.values(), key=lambda r: r.request_id, reverse=True)
        if status_filter is None or r.status is status_filter
    ]
    return RequestListOut(
        items=[RequestSummaryOut(**_summary(r)) for r in items[offset : offset + limit]]
    )


@router.get("/forward-fund/requests/{request_id}", response_model=RequestDetailOut)
async def get_request(
    request_id: int,
    request: Request,
    user: CurrentUser,
    session: SessionDep,
    onchain: bool = False,
) -> RequestDetailOut:
    book = await _book(request, session)
    r = book.get(request_id)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Forward-fund request not found")
    ids = await _project_ids(
        session,
        {o.project_key for o in r.offers.values()} | {a.project_key for a in r.awards.values()},
    )
    return RequestDetailOut(
        **_summary(r),
        offers=[_offer_out(o, ids) for o in sorted(r.offers.values(), key=lambda o: o.offer_id)],
        awards=[_award_out(a, ids) for a in sorted(r.awards.values(), key=lambda a: a.award_id)],
        onchain=await _onchain_check(request, r) if onchain else None,
    )


@router.get("/projects/{project_id}/forward-fund", response_model=ProjectForwardFundOut)
async def project_forward_fund(
    project_id: uuid.UUID,
    request: Request,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    capacity_of: CapacityDep,
) -> ProjectForwardFundOut:
    project = await _visible_project(session, project_id, user, settings)
    book = await _book(request, session)
    key = to_hex(project_id_bytes32(project.id))
    ids = {key: project.id}
    offers = [o for r in book.values() for o in r.offers.values() if o.project_key == key]
    awards = [a for r in book.values() for a in r.awards.values() if a.project_key == key]
    capacity = await capacity_of(session, _chain_id(request), project.id)
    return ProjectForwardFundOut(
        project_id=project.id,
        project_key=key,
        capacity=_capacity_out(capacity, ff.project_commitment(book, key)),
        offers=[_offer_out(o, ids) for o in sorted(offers, key=lambda o: o.offer_id)],
        awards=[_award_out(a, ids) for a in sorted(awards, key=lambda a: a.award_id)],
    )


# ---------------------------------------------------------------- relayed project-owner actions


@router.post(
    "/projects/{project_id}/forward-fund/offers",
    response_model=ChainTxOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_offer(
    project_id: uuid.UUID,
    body: OfferIn,
    request: Request,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    capacity_of: CapacityDep,
    eligible_org: EligibilityDep,
) -> ChainTransaction:
    project = await _admin_project(session, project_id, user, settings)
    book = await _book(request, session)
    relayer = _relayer(request)
    wallet = await _custodial_owner(session, request, project)
    key = to_hex(project_id_bytes32(project.id))

    r = book.get(body.request_id)
    if r is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Forward-fund request not found (or not indexed yet)"
        )
    if r.awarded:
        raise HTTPException(status.HTTP_409_CONFLICT, "Request is already awarded")
    if body.price_per_unit > r.max_price:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"price_per_unit {body.price_per_unit} exceeds the request's max price {r.max_price}",
        )
    if body.quantity > r.quantity:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"quantity {body.quantity} exceeds the requested {r.quantity}",
        )
    existing = [o for o in r.active_offers if o.project_key == key]
    if existing:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Project already has active offer {existing[0].offer_id} on this request",
        )
    pending = await _pending(session, project)
    if ff.pending_offer_actions(pending, function="submitOffer", first_arg=body.request_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "An offer for this request is already being relayed"
        )

    # Off-chain eligibility (§Phase 7 step 2). (a) stewardship of the organisation:
    eligible, reasons = await eligible_org(session, project.org_id)
    if not eligible:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "message": "Organisation does not meet the forward-funding stewardship criteria",
                "failing": [r for r in reasons if r.startswith("fail:")],
                "reasons": reasons,
            },
        )
    # (b) never offer more than verified capacity allows.
    capacity = await capacity_of(session, _chain_id(request), project.id)
    if capacity.total <= 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "no verified removal capacity yet")
    in_flight = sum(int(t.args[3]) for t in pending if t.function == "submitOffer")
    committed = ff.project_commitment(book, key) + in_flight
    available = capacity.total - committed
    if body.quantity > available:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"quantity {body.quantity} exceeds the project's available removal capacity "
            f"{max(available, 0)} (verified {capacity.total}, already committed {committed})",
        )

    tx = await ff.queue_submit_offer(
        session,
        relayer,
        project_id=project.id,
        project_key=key,
        wallet=wallet,
        request_id=body.request_id,
        price_per_unit=body.price_per_unit,
        quantity=body.quantity,
        requested_by=user.id,
    )
    return await _relay(request, session, relayer, tx, user, "submit_offer")


@router.delete(
    "/projects/{project_id}/forward-fund/offers/{offer_id}",
    response_model=ChainTxOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def withdraw_offer(
    project_id: uuid.UUID,
    offer_id: int,
    request: Request,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
) -> ChainTransaction:
    project = await _admin_project(session, project_id, user, settings)
    book = await _book(request, session)
    relayer = _relayer(request)
    wallet = await _custodial_owner(session, request, project)
    key = to_hex(project_id_bytes32(project.id))
    offer = next((r.offers[offer_id] for r in book.values() if offer_id in r.offers), None)
    if offer is None or offer.project_key != key:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Offer not found for this project")
    if offer.status is not ff.OfferStatus.ACTIVE:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Offer is {offer.status}")
    pending = await _pending(session, project)
    if ff.pending_offer_actions(pending, function="withdrawOffer", first_arg=offer_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "This offer is already being withdrawn")
    tx = await ff.queue_withdraw_offer(
        session,
        relayer,
        project_id=project.id,
        wallet=wallet,
        offer_id=offer_id,
        requested_by=user.id,
    )
    return await _relay(request, session, relayer, tx, user, "withdraw_offer")
