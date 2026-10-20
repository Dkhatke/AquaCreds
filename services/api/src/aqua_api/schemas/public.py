"""Public transparency-registry schemas (no login).

Privacy: these models are the whole public surface, so they are allow-lists. They carry
project-level facts, aggregates, hashes and on-chain records only: never user ids, names or
emails, photo object keys or URLs, photo/plot coordinates, tenure document files or the audit log.
"""

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from aqua_core.models import (
    Ecosystem,
    Methodology,
    OrgType,
    PeriodKind,
    PeriodStatus,
    ProjectStatus,
)

CreditTypeName = Literal["temporary", "long_term"]
ReportChainStatus = Literal["submitted", "approved", "rejected"]


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


class PublicOrg(BaseModel):
    name: str
    org_type: OrgType


class ChainRef(BaseModel):
    """Where an indexed contract event lives on-chain."""

    chain_id: int
    contract_address: str
    tx_hash: str
    log_index: int
    block_number: int
    block_hash: str
    explorer_url: str | None


class ProjectRegistration(ChainRef):
    boundary_hash: str  # SHA-256 of the RFC 8785 GeoJSON of the full-precision boundary
    start_year: int


class PublicProjectSummary(BaseModel):
    id: uuid.UUID
    name: str
    org: PublicOrg
    state: str
    ecosystem: Ecosystem
    methodology: Methodology
    status: ProjectStatus
    area_ha: float
    start_date: date
    centroid: tuple[float, float]  # (lon, lat), 4 decimals (~11 m)
    registered_on_chain: bool


class PeriodSummary(BaseModel):
    id: uuid.UUID
    kind: PeriodKind
    status: PeriodStatus
    start_date: date
    end_date: date


class EvidenceStats(BaseModel):
    """Aggregate field-evidence statistics. Individual submissions are never public."""

    submissions_total: int
    checked: int
    rejected: int
    pending_checks: int
    median_trust_score: float | None  # over checked submissions


class MonthlyIndexValue(BaseModel):
    month: date  # first day of the month
    median_p50: float
    scenes: int


class SatelliteIndicators(BaseModel):
    index: Literal["ndvi"] = "ndvi"
    min_clear_fraction: float
    latest_date: date | None
    latest_p50: float | None
    latest_clear_fraction: float | None
    last_12_months: list[MonthlyIndexValue]
    # OLS slope of the monthly medians, in index units per year (null with < 3 months).
    trend_p50_per_year: float | None


class AlertCounts(BaseModel):
    total: int
    low: int
    medium: int
    high: int
    latest_observed_on: date | None


class OnChainReport(BaseModel):
    report_id: int
    period_start: int  # year
    period_end: int  # year
    report_hash: str  # 0x + SHA-256 of the RFC 8785 MRV record
    lccc: int
    tccc: int | None  # set on approval
    status: ReportChainStatus
    submitted: ChainRef
    decided: ChainRef | None


class BoundaryInfo(BaseModel):
    geometry: dict[str, Any]
    simplification: str
    tolerance_deg: float


class PublicProjectDetail(PublicProjectSummary):
    description: str
    boundary: BoundaryInfo
    periods: list[PeriodSummary]
    evidence: EvidenceStats
    satellite: SatelliteIndicators
    alerts: AlertCounts
    registration: ProjectRegistration | None
    reports: list[OnChainReport]
    vintages: list["VintageOut"]


class PublicTimeseriesPoint(BaseModel):
    date: date
    item_id: str  # public STAC scene id
    clear_fraction: float
    valid_pixels: int
    mean: float | None
    p10: float | None
    p50: float | None
    p90: float | None


class PublicTimeseries(BaseModel):
    project_id: uuid.UUID
    index: str
    min_clear_fraction: float
    points: list[PublicTimeseriesPoint]


class ProjectRef(BaseModel):
    id: uuid.UUID | None  # null when the on-chain project is not (yet) public here
    name: str | None
    project_key: str  # bytes32 on-chain id


class IssuanceOut(BaseModel):
    amount: int
    tx_hash: str
    block_number: int
    explorer_url: str | None


class VintageOut(BaseModel):
    token_id: int
    project: ProjectRef
    credit_type: CreditTypeName
    credit_unit: Literal["tCCC", "lCCC"]
    report_id: int
    issued: int
    retired: int
    replaced: int  # long-term credits burned to settle a reversal
    outstanding: int  # issued - retired - replaced
    expires_at: datetime | None
    expired: bool
    issuances: list[IssuanceOut]


class CertificateVerification(BaseModel):
    """Everything needed to check a retirement against the chain without trusting AquaCreds:
    look up `tx_hash` on any explorer/RPC and compare the CreditsRetired log at `log_index`.
    `payload_sha256` is the SHA-256 of the RFC 8785 canonical JSON of `fields`."""

    certificate_url: str
    event: Literal["CreditsRetired"] = "CreditsRetired"
    contract: Literal["AquaCredit"] = "AquaCredit"
    chain: ChainRef
    fields: dict[str, Any]
    payload_sha256: str


class RetirementOut(BaseModel):
    retirement_id: int
    token_id: int
    amount: int
    project: ProjectRef | None
    credit_type: CreditTypeName | None
    beneficiary: str
    purpose: str
    tx_hash: str
    block_number: int
    # From the relayer's confirmation record when available; the indexer stores no block timestamp.
    block_time: datetime | None
    indexed_at: datetime


class RetirementDetail(RetirementOut):
    verification: CertificateVerification
    qr_png_url: str
    qr_svg_url: str


class VerifyHashRequest(BaseModel):
    sha256_hex: str = Field(description="64 hex characters, optional 0x prefix")

    @field_validator("sha256_hex")
    @classmethod
    def _normalise(cls, value: str) -> str:
        return normalise_sha256(value)


def normalise_sha256(value: str) -> str:
    text = value.strip().lower().removeprefix("0x")
    if len(text) != 64 or any(c not in "0123456789abcdef" for c in text):
        raise ValueError("sha256_hex must be 64 hexadecimal characters")
    return text


class HashMatch(BaseModel):
    report_id: int
    project: ProjectRef
    period_start: int
    period_end: int
    status: ReportChainStatus
    chain: ChainRef


class VerifyHashOut(BaseModel):
    sha256_hex: str
    found: bool
    matches: list[HashMatch]


PublicProjectDetail.model_rebuild()
