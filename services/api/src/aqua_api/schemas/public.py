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
    # The approved MRV report in this registry whose hash and project match (null if none).
    mrv_report_id: uuid.UUID | None
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
    mrv_reports: list["PublicReport"]  # approved MRV reports only
    vintages: list["VintageOut"]


# Public time-series indices: the optical indices, plus two Sentinel-1 per-pass P50 aggregates.
PublicSarIndex = Literal["rvi", "vh_db"]
PublicTimeseriesIndex = Literal[
    "ndvi", "evi", "mvi", "ndwi", "mndwi", "ndmi", "cmri", PublicSarIndex
]


class PublicTimeseriesPoint(BaseModel):
    """Sentinel-2: one clear scene. Sentinel-1 (`rvi`, `vh_db`): one radar pass, P50 only (mean and
    percentiles are null), `item_id` is the acquisition key and `valid_fraction` replaces
    `clear_fraction`."""

    date: date
    item_id: str  # public STAC scene id / Sentinel-1 acquisition key
    clear_fraction: float | None
    valid_fraction: float | None = None
    valid_pixels: int
    mean: float | None
    p10: float | None
    p50: float | None
    p90: float | None


class PublicTimeseries(BaseModel):
    project_id: uuid.UUID
    index: str
    sensor: Literal["sentinel-2-l2a", "sentinel-1-rtc"] = "sentinel-2-l2a"
    note: str  # radar and optical values are not comparable
    min_clear_fraction: float  # applied to valid_fraction for Sentinel-1
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
    """`source="chain"`: a ReportSubmitted event carries the hash (`anchored` is true).
    `source="registry_db"`: an approved MRV report in this registry has the hash but no indexed
    ReportSubmitted event matches it yet (`anchored` says whether the relayer has confirmed the
    submitReport transaction; `chain` is then null)."""

    source: Literal["chain", "registry_db"]
    anchored: bool
    report_id: int | None  # on-chain AquaRegistry reportId
    mrv_report_id: uuid.UUID | None
    project: ProjectRef
    period_start: int
    period_end: int
    status: ReportChainStatus
    chain: ChainRef | None


class VerifyHashOut(BaseModel):
    sha256_hex: str
    found: bool
    matches: list[HashMatch]


class PublicTx(BaseModel):
    chain_id: int
    tx_hash: str
    block_number: int | None
    explorer_url: str | None


class ReportAnchoring(BaseModel):
    """On-chain anchoring of an approved MRV report, from the relayer's confirmed transactions.

    Monitoring-period reports are anchored by submitReport + approveReport (`hash_anchored` once
    submitReport is confirmed). Baseline-period reports only lead to the project registration, so
    their hash is never submitted on-chain (`hash_anchored` stays false)."""

    hash_anchored: bool
    chain_report_id: int | None  # AquaRegistry reportId
    submit_tx: PublicTx | None
    approve_tx: PublicTx | None
    completed_at: datetime | None


class ReportDownloadLinks(BaseModel):
    """Stable registry URLs. `pdf_url` redirects to a short-lived presigned download of the PDF;
    `record_url` serves the public variant of the hashed JSON record (personal and site-precise
    fields removed). The full canonical JSON and the CSV export are not public."""

    pdf_url: str
    record_url: str


class PublicReport(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    period_id: uuid.UUID
    period_kind: PeriodKind
    version: int
    status: Literal["approved"] = "approved"
    period_start_year: int
    period_end_year: int
    generated_at: datetime
    decided_at: datetime | None
    report_hash: str  # hex SHA-256 of the RFC 8785 canonical record (no 0x)
    lccc_units: int
    tccc_units: int
    anchoring: ReportAnchoring
    downloads: ReportDownloadLinks


class ReportTraceability(BaseModel):
    """Read from the stored canonical record after checking its SHA-256 against `report_hash`."""

    record_schema: str
    status_label: str
    methodology_profile: dict[str, Any]
    software: dict[str, str]  # package → version used for the calculation
    record_sha256_verified: Literal[True] = True


class PublicReportDetail(PublicReport):
    project: ProjectRef
    project_methodology: Methodology
    tree_stock_tco2e: float
    tccc_tco2e: float
    traceability: ReportTraceability


class PublicRecordVariant(BaseModel):
    variant: Literal["aquacreds.mrv_report.public/1"] = "aquacreds.mrv_report.public/1"
    report_id: uuid.UUID
    # SHA-256 of the *full* canonical record. It cannot be recomputed from this variant; the full
    # record is available to the project's organisation, verifiers and regulators.
    full_record_sha256: str
    redacted: list[str]  # paths removed from the full record, e.g. "plots[].lat"
    record: dict[str, Any]


PublicProjectDetail.model_rebuild()
