import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aqua_core.models import PeriodStatus, ReportStatus

ReviewDecision = Literal["approve", "request_changes", "reject"]


class PlantingCohortIn(BaseModel):
    year: int = Field(ge=1990, le=2100)
    area_ha: float = Field(gt=0)


class ReportRequest(BaseModel):
    """Inputs to the report that the database does not hold.

    `strata_area_ha`: hectares per stratum, one entry for every stratum that has plot measurements
    in the period. It may be omitted only when all measured plots are in a single stratum; that
    stratum then takes the whole project area."""

    strata_area_ha: dict[str, float] | None = None
    planting: list[PlantingCohortIn] = Field(default_factory=list, max_length=100)
    include_soc: bool = True
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _positive(self) -> "ReportRequest":
        if self.strata_area_ha is not None and any(v <= 0 for v in self.strata_area_ha.values()):
            raise ValueError("Stratum areas must be positive")
        return self


class ReportRequested(BaseModel):
    period_id: uuid.UUID
    period_status: PeriodStatus
    strata_area_ha: dict[str, float]


class ReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    project_id: uuid.UUID
    period_id: uuid.UUID
    version: int
    status: ReportStatus
    report_hash: str
    lccc_units: int
    tccc_units: int
    period_start_year: int
    period_end_year: int
    generated_at: datetime
    generated_by: uuid.UUID
    verifier_id: uuid.UUID | None
    decided_at: datetime | None
    decision_comment: str | None
    submit_tx_id: uuid.UUID | None
    approve_tx_id: uuid.UUID | None
    chain_report_id: int | None
    anchored_at: datetime | None
    anchor_error: str | None


class ReportDownloads(BaseModel):
    json_url: str
    pdf_url: str
    csv_url: str


class ReportDetail(ReportOut):
    downloads: ReportDownloads


class ReviewRequest(BaseModel):
    decision: ReviewDecision
    comment: str = Field(default="", max_length=5000)

    @model_validator(mode="after")
    def _comment_required(self) -> "ReviewRequest":
        if self.decision != "approve" and not self.comment.strip():
            raise ValueError("A comment is required when requesting changes or rejecting")
        return self


class ReportVerification(BaseModel):
    report_id: uuid.UUID
    report_hash: str  # as recorded in the database
    stored_hash: str | None  # SHA-256 of the stored canonical JSON (None if missing)
    stored_matches: bool
    onchain_hash: str | None  # reportHash of the ReportSubmitted event, if anchored
    onchain_source: Literal["indexer", "receipt"] | None
    onchain_matches: bool | None
    ok: bool
