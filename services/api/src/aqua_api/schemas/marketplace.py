"""Public marketplace schemas (no login): fixed-price AquaMarketplace listings and trades.

Buyers list, buy and retire with their own wallets; the platform only indexes and presents. These
models are allow-lists like `aqua_api.schemas.public`: no seller or buyer wallet addresses (they are
public on-chain, but the registry never ties them to anything here), no user accounts.

Prices are integers in the payment token's smallest unit (mUSDC: 6 decimals) plus an exact decimal
string with exactly `decimals` fraction digits. One unit of credit is one tCO2e.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from aqua_api.schemas.public import ChainRef, CreditTypeName, ProjectRef, PublicProjectSummary

ListingStatus = Literal["active", "sold_out", "cancelled"]
ListingSort = Literal["newest", "price_asc", "price_desc"]


class PaymentToken(BaseModel):
    address: str | None
    decimals: int


class Price(BaseModel):
    units: int  # smallest unit of the payment token
    decimal: str  # exact, e.g. "12.500000" for 12_500_000 units at 6 decimals


class StewardshipSummary(BaseModel):
    """The project's current stewardship score (off-chain, non-transferable; see
    `GET /projects/{id}/stewardship` for the explained breakdown, staff only)."""

    score: float | None
    insufficient_data: bool
    score_version: str


class VintageFacts(BaseModel):
    token_id: int
    credit_type: CreditTypeName
    credit_unit: Literal["tCCC", "lCCC"]
    report_id: int  # AquaRegistry reportId the vintage was issued against
    report_hash: str | None  # SHA-256 of the MRV record anchored by ReportSubmitted (0x-hex)
    mrv_report_id: uuid.UUID | None  # public MRV report (GET /public/reports/{id}) when linked
    expires_at: datetime | None
    expired: bool


class ListingOut(BaseModel):
    listing_id: int
    status: ListingStatus
    vintage: VintageFacts
    project: ProjectRef
    project_facts: PublicProjectSummary | None  # null when the project is not public here
    stewardship: StewardshipSummary | None
    amount_listed: int
    amount_sold: int
    remaining: int
    price_per_unit: Price
    payment_token: PaymentToken
    listed: ChainRef
    listed_at: datetime | None  # block timestamp
    closed: ChainRef | None  # Cancelled or the Purchased event that sold it out


class TradeOut(BaseModel):
    listing_id: int
    token_id: int
    credit_type: CreditTypeName | None
    project: ProjectRef | None
    amount: int
    price_per_unit: Price
    total_price: Price
    fee: Price
    payment_token: PaymentToken
    chain: ChainRef
    block_time: datetime | None


class PricePoint(BaseModel):
    time: datetime | None  # block timestamp of the trade
    block_number: int
    token_id: int
    credit_type: CreditTypeName | None
    amount: int
    price_per_unit: Price


class ProjectMarket(BaseModel):
    project: ProjectRef
    payment_token: PaymentToken
    active_listings: list[ListingOut]
    trades: list[TradeOut]  # newest first
    price_history: list[PricePoint]  # oldest first, one point per trade
    volume_units: int  # credits traded in total
    last_price: Price | None
