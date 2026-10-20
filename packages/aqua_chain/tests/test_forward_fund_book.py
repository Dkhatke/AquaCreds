"""Unit tests: the AquaForwardFund read model folded from (synthetic) indexed events."""

from typing import Any

import pytest

from aqua_chain.forward_fund import (
    InconsistentEventsError,
    OfferStatus,
    RequestStatus,
    build_book,
    pending_offer_actions,
    project_commitment,
)
from aqua_core.models import ChainEvent, ChainTransaction

PA, PB = "0x" + "aa" * 32, "0x" + "BB" * 32


def ev(name: str, **args: Any) -> ChainEvent:
    ev.n = getattr(ev, "n", 0) + 1  # type: ignore[attr-defined]
    return ChainEvent(
        event=name,
        args=args,
        tx_hash=f"0x{ev.n:064x}",
        block_number=ev.n,
        log_index=0,  # type: ignore[attr-defined]
    )


def created(rid: int = 1, quantity: int = 80, max_price: int = 10, bps: int = 2000) -> ChainEvent:
    return ev(
        "RequestCreated",
        requestId=rid,
        buyer="0xBuyer",
        quantity=quantity,
        maxPrice=max_price,
        offerWindowEnd=100,
        deliveryDeadline=200,
        upfrontBps=bps,
        escrow=quantity * max_price,
    )


def offered(oid: int, project: str, price: int, qty: int, rid: int = 1) -> ChainEvent:
    return ev(
        "OfferSubmitted",
        offerId=oid,
        requestId=rid,
        projectId=project,
        seller="0xSeller",
        pricePerUnit=price,
        quantity=qty,
    )


def awarded(aid: int, project: str, qty: int, price: int, bps: int = 2000) -> ChainEvent:
    return ev(
        "OfferAwarded",
        awardId=aid,
        requestId=1,
        projectId=project,
        seller="0xSeller",
        quantity=qty,
        pricePerUnit=price,
        upfrontPaid=qty * price * bps // 10_000,
    )


def test_full_lifecycle_arithmetic_matches_the_contract() -> None:
    events = [
        created(),
        offered(1, PA, 8, 60),
        offered(2, PB, 9, 30),
        offered(3, PB, 9, 30, rid=1),
    ]
    book = build_book(events[:2])
    assert book[1].status is RequestStatus.OPEN
    assert book[1].escrow_left == 800
    assert project_commitment(book, PA) == 60
    events.insert(3, ev("OfferWithdrawn", offerId=2, requestId=1))
    events += [
        awarded(1, PA, 60, 8),
        awarded(3, PB, 20, 9),
        ev(
            "RequestAwarded",
            requestId=1,
            awardedQuantity=80,
            awardedValue=660,
            unusedEscrowRefunded=140,
        ),
        ev("Delivered", awardId=1, tokenId=7, buyer="0xBuyer", amount=30, released=192),
    ]
    r = build_book(events)[1]
    assert r.status is RequestStatus.AWARDED
    assert [r.offers[i].status for i in (1, 2, 3)] == [
        OfferStatus.AWARDED,
        OfferStatus.WITHDRAWN,
        OfferStatus.AWARDED,
    ]
    a, b = r.awards[1], r.awards[3]
    assert (a.value, a.upfront_paid, a.refundable) == (480, 96, 480 - 96 - 192)
    assert (b.value, b.upfront_paid, b.refundable) == (180, 36, 144)
    assert r.awarded_quantity <= r.quantity
    assert r.escrow_left == 800 - 140 - 132 - 192 == a.escrow_left + b.escrow_left
    assert project_commitment(build_book(events), PA) == 30
    assert project_commitment(build_book(events), PB.lower()) == 20

    events += [
        ev("UndeliveredRefunded", awardId=1, requestId=1, undelivered=30, refund=192),
        ev("UndeliveredRefunded", awardId=3, requestId=1, undelivered=20, refund=144),
        ev("Withdrawn", account="0xBuyer", amount=476),
    ]
    r = build_book(events)[1]
    assert r.status is RequestStatus.SETTLED
    assert r.escrow_left == 0
    assert r.refundable == 0
    assert r.awards[1].open_quantity == 0
    assert r.refunded_undelivered == 336


def test_not_awarded_offer_and_empty_award_settles() -> None:
    events = [
        created(quantity=10),
        offered(1, PA, 9, 10),
        ev(
            "RequestAwarded", requestId=1, awardedQuantity=0, awardedValue=0, unusedEscrowRefunded=0
        ),
    ]
    r = build_book(events)[1]
    assert r.offers[1].status is OfferStatus.NOT_AWARDED
    assert r.status is RequestStatus.SETTLED  # no awards left open


def test_full_delivery_closes_an_award_without_refund() -> None:
    events = [
        created(quantity=10, bps=0),
        offered(1, PA, 5, 10),
        awarded(1, PA, 10, 5, bps=0),
        ev(
            "RequestAwarded",
            requestId=1,
            awardedQuantity=10,
            awardedValue=50,
            unusedEscrowRefunded=50,
        ),
        ev("Delivered", awardId=1, tokenId=1, buyer="0xBuyer", amount=10, released=50),
    ]
    r = build_book(events)[1]
    assert r.status is RequestStatus.SETTLED
    assert r.escrow_left == 0
    assert r.awards[1].refundable == 0


@pytest.mark.parametrize(
    "event",
    [
        offered(1, PA, 1, 1, rid=9),
        ev("OfferWithdrawn", offerId=9, requestId=1),
        ev("Delivered", awardId=9, tokenId=1, buyer="0x", amount=1, released=1),
        ev("UndeliveredRefunded", awardId=9, requestId=1, undelivered=1, refund=1),
        ev(
            "RequestAwarded", requestId=9, awardedQuantity=0, awardedValue=0, unusedEscrowRefunded=0
        ),
    ],
)
def test_incomplete_index_is_an_error(event: ChainEvent) -> None:
    with pytest.raises(InconsistentEventsError):
        build_book([created(), event])


def test_pending_offer_actions_filters_by_function_and_first_arg() -> None:
    txs = [
        ChainTransaction(contract="AquaForwardFund", function="submitOffer", args=[1, PA, 1, 1]),
        ChainTransaction(contract="AquaForwardFund", function="withdrawOffer", args=[1]),
        ChainTransaction(contract="AquaRegistry", function="submitOffer", args=[1]),
    ]
    assert pending_offer_actions(txs, function="submitOffer", first_arg=1) == [txs[0]]
    assert pending_offer_actions(txs, function="submitOffer", first_arg=2) == []
