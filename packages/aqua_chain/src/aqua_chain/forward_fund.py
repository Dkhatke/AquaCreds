"""AquaForwardFund read model: requests, offers and awards rebuilt from indexed contract events,
plus the queued offer actions for custodial project owners.

The arithmetic mirrors the contract exactly (all amounts in payment-token base units):
- award value = quantity × pricePerUnit; upfront = floor(value × upfrontBps / 10 000), paid at
  award time and never refunded;
- released = sum of `Delivered.released`; refundable after the deadline (until refunded) =
  value − upfront − released, which is what `refundUndelivered` credits to the buyer;
- request escrow left = deposit − unused escrow returned at award − upfront − released − refunds,
  which equals `getRequest(id).escrow` on-chain.

Funding is never a guarantee of credits: see `DISCLAIMER`.
"""

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from aqua_chain.relayer import Relayer
from aqua_core.models import (
    ChainEvent,
    ChainSigner,
    ChainTransaction,
    ChainTxPurpose,
    OrgWallet,
)

CONTRACT = "AquaForwardFund"
BPS = 10_000
DISCLAIMER = (
    "Forward funding is not a guarantee of credits. Credits are issued ex post, years after "
    "planting, and only for removals an accredited verifier approves; a project may deliver less "
    "than it offered, or nothing. Any upfront share is paid to the project at award time at the "
    "buyer's risk and is never refunded. Escrow is released only as verified long-term credits "
    "are delivered; undelivered escrow is refundable to the buyer after the delivery deadline. "
    "Testnet registry units: not legally recognised carbon credits."
)
EVENTS = (
    "RequestCreated",
    "OfferSubmitted",
    "OfferWithdrawn",
    "OfferAwarded",
    "RequestAwarded",
    "Delivered",
    "UndeliveredRefunded",
    "Withdrawn",
)


class RequestStatus(StrEnum):
    OPEN = "open"  # not awarded yet (offers may still be accepted until the window ends)
    AWARDED = "awarded"  # awarded; some award is still open (deliverable or refundable)
    SETTLED = "settled"  # awarded; every award fully delivered or refunded


class OfferStatus(StrEnum):
    ACTIVE = "active"
    WITHDRAWN = "withdrawn"
    AWARDED = "awarded"
    NOT_AWARDED = "not_awarded"


@dataclass
class OfferState:
    offer_id: int
    request_id: int
    project_key: str
    seller: str
    price_per_unit: int
    quantity: int
    tx_hash: str
    block_number: int
    withdrawn_tx_hash: str | None = None
    awarded: bool = False
    request_awarded: bool = False

    @property
    def status(self) -> OfferStatus:
        if self.withdrawn_tx_hash is not None:
            return OfferStatus.WITHDRAWN
        if self.awarded:
            return OfferStatus.AWARDED
        if self.request_awarded:
            return OfferStatus.NOT_AWARDED
        return OfferStatus.ACTIVE


@dataclass
class AwardState:
    award_id: int  # == the winning offer id
    request_id: int
    project_key: str
    seller: str
    quantity: int
    price_per_unit: int
    upfront_paid: int
    tx_hash: str
    delivered: int = 0
    released: int = 0
    refund: int | None = None  # set once `refundUndelivered` ran
    refunded_quantity: int | None = None
    deliveries: list[dict[str, Any]] = field(default_factory=list)

    @property
    def value(self) -> int:
        return self.quantity * self.price_per_unit

    @property
    def settled(self) -> bool:
        return self.refund is not None

    @property
    def open_quantity(self) -> int:
        return 0 if self.settled else self.quantity - self.delivered

    @property
    def escrow_left(self) -> int:
        return self.value - self.upfront_paid - self.released - (self.refund or 0)

    @property
    def refundable(self) -> int:
        """Escrow `refundUndelivered` would credit the buyer (after the deadline)."""
        return 0 if self.settled else self.value - self.upfront_paid - self.released

    @property
    def closed(self) -> bool:
        return self.settled or self.delivered >= self.quantity


@dataclass
class RequestState:
    request_id: int
    buyer: str
    quantity: int
    max_price: int
    offer_window_end: int
    delivery_deadline: int
    upfront_bps: int
    escrow_deposited: int
    tx_hash: str
    block_number: int
    offers: dict[int, OfferState] = field(default_factory=dict)
    awards: dict[int, AwardState] = field(default_factory=dict)
    awarded: bool = False
    awarded_quantity: int = 0
    awarded_value: int = 0
    unused_escrow_refunded: int = 0
    award_tx_hash: str | None = None

    @property
    def status(self) -> RequestStatus:
        if not self.awarded:
            return RequestStatus.OPEN
        if all(a.closed for a in self.awards.values()):
            return RequestStatus.SETTLED
        return RequestStatus.AWARDED

    @property
    def upfront_paid(self) -> int:
        return sum(a.upfront_paid for a in self.awards.values())

    @property
    def delivered_quantity(self) -> int:
        return sum(a.delivered for a in self.awards.values())

    @property
    def released(self) -> int:
        return sum(a.released for a in self.awards.values())

    @property
    def refunded_undelivered(self) -> int:
        return sum(a.refund or 0 for a in self.awards.values())

    @property
    def refundable(self) -> int:
        return sum(a.refundable for a in self.awards.values())

    @property
    def escrow_left(self) -> int:
        if not self.awarded:
            return self.escrow_deposited
        return (
            self.escrow_deposited
            - self.unused_escrow_refunded
            - self.upfront_paid
            - self.released
            - self.refunded_undelivered
        )

    @property
    def active_offers(self) -> list[OfferState]:
        return [o for o in self.offers.values() if o.status is OfferStatus.ACTIVE]


class InconsistentEventsError(RuntimeError):
    """Indexed events reference an unknown request/offer/award (incomplete index)."""


def _int(args: dict[str, Any], name: str) -> int:
    return int(args[name])


def build_book(events: Iterable[ChainEvent]) -> dict[int, RequestState]:
    """Fold forward-fund events (in chain order) into request states keyed by request id."""
    requests: dict[int, RequestState] = {}
    offers: dict[int, OfferState] = {}
    awards: dict[int, AwardState] = {}

    def request(request_id: int) -> RequestState:
        try:
            return requests[request_id]
        except KeyError:
            raise InconsistentEventsError(f"request {request_id} has no RequestCreated") from None

    for e in events:
        a = e.args
        if e.event == "RequestCreated":
            rid = _int(a, "requestId")
            requests[rid] = RequestState(
                request_id=rid,
                buyer=str(a["buyer"]),
                quantity=_int(a, "quantity"),
                max_price=_int(a, "maxPrice"),
                offer_window_end=_int(a, "offerWindowEnd"),
                delivery_deadline=_int(a, "deliveryDeadline"),
                upfront_bps=_int(a, "upfrontBps"),
                escrow_deposited=_int(a, "escrow"),
                tx_hash=e.tx_hash,
                block_number=e.block_number,
            )
        elif e.event == "OfferSubmitted":
            offer = OfferState(
                offer_id=_int(a, "offerId"),
                request_id=_int(a, "requestId"),
                project_key=str(a["projectId"]).lower(),
                seller=str(a["seller"]),
                price_per_unit=_int(a, "pricePerUnit"),
                quantity=_int(a, "quantity"),
                tx_hash=e.tx_hash,
                block_number=e.block_number,
            )
            request(offer.request_id).offers[offer.offer_id] = offer
            offers[offer.offer_id] = offer
        elif e.event == "OfferWithdrawn":
            oid = _int(a, "offerId")
            if oid not in offers:
                raise InconsistentEventsError(f"offer {oid} withdrawn but never submitted")
            offers[oid].withdrawn_tx_hash = e.tx_hash
        elif e.event == "OfferAwarded":
            award = AwardState(
                award_id=_int(a, "awardId"),
                request_id=_int(a, "requestId"),
                project_key=str(a["projectId"]).lower(),
                seller=str(a["seller"]),
                quantity=_int(a, "quantity"),
                price_per_unit=_int(a, "pricePerUnit"),
                upfront_paid=_int(a, "upfrontPaid"),
                tx_hash=e.tx_hash,
            )
            request(award.request_id).awards[award.award_id] = award
            awards[award.award_id] = award
            if award.award_id in offers:
                offers[award.award_id].awarded = True
        elif e.event == "RequestAwarded":
            r = request(_int(a, "requestId"))
            r.awarded = True
            r.awarded_quantity = _int(a, "awardedQuantity")
            r.awarded_value = _int(a, "awardedValue")
            r.unused_escrow_refunded = _int(a, "unusedEscrowRefunded")
            r.award_tx_hash = e.tx_hash
            for offer in r.offers.values():
                offer.request_awarded = True
        elif e.event == "Delivered":
            aid = _int(a, "awardId")
            if aid not in awards:
                raise InconsistentEventsError(f"delivery for unknown award {aid}")
            awards[aid].delivered += _int(a, "amount")
            awards[aid].released += _int(a, "released")
            awards[aid].deliveries.append(
                {
                    "token_id": _int(a, "tokenId"),
                    "amount": _int(a, "amount"),
                    "released": _int(a, "released"),
                    "tx_hash": e.tx_hash,
                    "block_number": e.block_number,
                }
            )
        elif e.event == "UndeliveredRefunded":
            aid = _int(a, "awardId")
            if aid not in awards:
                raise InconsistentEventsError(f"refund for unknown award {aid}")
            awards[aid].refund = _int(a, "refund")
            awards[aid].refunded_quantity = _int(a, "undelivered")
        # `Withdrawn` is a per-account claim (not per request); it is indexed but not folded.
    return requests


async def load_events(session: AsyncSession, *, chain_id: int, address: str) -> list[ChainEvent]:
    rows = await session.scalars(
        select(ChainEvent)
        .where(
            ChainEvent.chain_id == chain_id,
            ChainEvent.contract == CONTRACT,
            func.lower(ChainEvent.address) == address.lower(),
        )
        .order_by(tuple_(ChainEvent.block_number, ChainEvent.log_index))
    )
    return list(rows)


async def load_book(
    session: AsyncSession, *, chain_id: int, address: str
) -> dict[int, RequestState]:
    return build_book(await load_events(session, chain_id=chain_id, address=address))


def project_commitment(book: dict[int, RequestState], project_key: str) -> int:
    """Quantity the project has already promised: active offers + undelivered open awards."""
    key = project_key.lower()
    total = 0
    for r in book.values():
        for o in r.active_offers:
            if o.project_key == key:
                total += o.quantity
        for aw in r.awards.values():
            if aw.project_key == key:
                total += aw.open_quantity
    return total


# ---------------------------------------------------------------- relayed project-owner actions


async def queue_submit_offer(
    session: AsyncSession,
    relayer: Relayer,
    *,
    project_id: uuid.UUID,
    project_key: str,
    wallet: OrgWallet,
    request_id: int,
    price_per_unit: int,
    quantity: int,
    requested_by: uuid.UUID | None = None,
) -> ChainTransaction:
    """submitOffer(requestId, projectId, pricePerUnit, quantity), signed by the org's custodial
    wallet (the project's registered on-chain owner)."""
    return await relayer.queue(
        session,
        purpose=ChainTxPurpose.FORWARD_FUND_OFFER,
        entity_type="project",
        entity_id=str(project_id),
        signer=ChainSigner.ORG_WALLET,
        contract=CONTRACT,
        function="submitOffer",
        args=[request_id, project_key, price_per_unit, quantity],
        requested_by=requested_by,
        org_wallet=wallet,
    )


async def queue_withdraw_offer(
    session: AsyncSession,
    relayer: Relayer,
    *,
    project_id: uuid.UUID,
    wallet: OrgWallet,
    offer_id: int,
    requested_by: uuid.UUID | None = None,
) -> ChainTransaction:
    return await relayer.queue(
        session,
        purpose=ChainTxPurpose.FORWARD_FUND_WITHDRAW,
        entity_type="project",
        entity_id=str(project_id),
        signer=ChainSigner.ORG_WALLET,
        contract=CONTRACT,
        function="withdrawOffer",
        args=[offer_id],
        requested_by=requested_by,
        org_wallet=wallet,
    )


def pending_offer_actions(
    txs: Sequence[ChainTransaction], *, function: str, first_arg: int
) -> list[ChainTransaction]:
    """Active relayed forward-fund transactions of `function` whose first argument matches
    (the request id for submitOffer, the offer id for withdrawOffer)."""
    return [
        t
        for t in txs
        if t.contract == CONTRACT and t.function == function and t.args and t.args[0] == first_arg
    ]
