"""Schemas for the forward-funding (reverse auction) API.

Amounts are payment-token base units (mUSDC: 6 decimals); quantities are whole tCO2e (lCCC).
Every response carries the `disclaimer`, and any upfront payment is flagged `buyer_risk: true`:
an advance is never a guarantee of credits."""

import uuid
from typing import Literal

from pydantic import BaseModel, Field

from aqua_chain.forward_fund import DISCLAIMER, OfferStatus, RequestStatus


class UpfrontOut(BaseModel):
    """The optional share paid to winners at award time. It is never refunded."""

    bps: int
    share_pct: float
    amount_paid: int  # 0 until the request is awarded
    buyer_risk: Literal[True] = True
    refundable: Literal[False] = False
    note: str = (
        "Paid to the project at award time to cover planting costs, at the buyer's risk. "
        "Not refunded if credits are never delivered."
    )


class OfferOut(BaseModel):
    offer_id: int
    request_id: int
    project_key: str
    project_id: uuid.UUID | None  # platform project, when the key matches one
    seller: str
    price_per_unit: int
    quantity: int
    status: OfferStatus
    tx_hash: str
    withdrawn_tx_hash: str | None


class DeliveryOut(BaseModel):
    token_id: int
    amount: int
    released: int
    tx_hash: str
    block_number: int


class AwardOut(BaseModel):
    award_id: int
    request_id: int
    project_key: str
    project_id: uuid.UUID | None
    seller: str
    quantity: int
    price_per_unit: int
    value: int  # quantity × price
    upfront_paid: int
    upfront_buyer_risk: Literal[True] = True
    delivered_quantity: int
    released: int
    open_quantity: int
    escrow_left: int
    refundable_after_deadline: int  # what refundUndelivered would credit the buyer
    refunded: int | None
    settled: bool
    deliveries: list[DeliveryOut]


class OnchainCheckOut(BaseModel):
    """Live contract reads compared against the indexed figures."""

    block_number: int
    escrow: int
    awarded: bool
    awarded_quantity: int
    awards: dict[int, dict[str, int | bool]]
    refundable: dict[int, int]
    consistent: bool
    mismatches: list[str]


class RequestSummaryOut(BaseModel):
    request_id: int
    buyer: str
    status: RequestStatus
    quantity: int
    max_price: int
    escrow_deposited: int
    offer_window_end: int  # unix seconds (chain time)
    delivery_deadline: int
    upfront: UpfrontOut
    awarded_quantity: int
    awarded_value: int
    unused_escrow_refunded: int
    delivered_quantity: int
    released: int
    refunded_undelivered: int
    refundable_after_deadline: int
    escrow_left: int
    active_offers: int
    tx_hash: str


class RequestDetailOut(RequestSummaryOut):
    offers: list[OfferOut]
    awards: list[AwardOut]
    onchain: OnchainCheckOut | None = None
    disclaimer: str = DISCLAIMER


class RequestListOut(BaseModel):
    items: list[RequestSummaryOut]
    disclaimer: str = DISCLAIMER


class CapacitySourceOut(BaseModel):
    name: Literal["mrv_reports", "onchain_report_approved"]
    total: int  # signed sum of lCCC (negative after uncovered reversals)
    description: str
    report_ids: list[str]  # MRV report UUIDs, or on-chain AquaRegistry report ids


class CapacityOut(BaseModel):
    total: int  # the smaller of the sources when they disagree, never negative
    committed: int  # active offers + undelivered open awards
    available: int
    source: str
    report_ids: list[int]  # on-chain report ids
    sources_agree: bool
    capacity_sources: list[CapacitySourceOut]
    note: str = "Estimated eligibility screen only; it does not make delivery certain."


class ProjectForwardFundOut(BaseModel):
    project_id: uuid.UUID
    project_key: str
    capacity: CapacityOut
    offers: list[OfferOut]
    awards: list[AwardOut]
    disclaimer: str = DISCLAIMER


class OfferIn(BaseModel):
    request_id: int = Field(gt=0)
    price_per_unit: int = Field(gt=0, description="payment-token base units per tCO2e")
    quantity: int = Field(gt=0, description="whole tCO2e")
