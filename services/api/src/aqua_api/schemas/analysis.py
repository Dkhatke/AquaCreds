import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from aqua_core.models import AlertKind, AlertSeverity, AnalysisKind, AnalysisStatus

TimeseriesIndex = Literal["ndvi", "evi", "mvi", "ndwi", "mndwi", "ndmi", "cmri"]


class BackfillRequest(BaseModel):
    # Sentinel-2 L2A over India is available from about 2017.
    start_date: date | None = Field(default=None, ge=date(2015, 6, 23))


class CompositeRequest(BaseModel):
    """Dry season starting in November of `season_start_year` (e.g. 2025 → Nov 2025–Mar 2026)."""

    season_start_year: int = Field(ge=2016, le=2100)


class AnalysisRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    project_id: uuid.UUID
    kind: AnalysisKind
    status: AnalysisStatus
    params: dict[str, Any]
    requested_by: uuid.UUID | None
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    outputs: dict[str, Any]
    created_at: datetime


class TimeseriesPoint(BaseModel):
    date: date
    acquired_at: datetime
    item_id: str
    clear_fraction: float
    cloud_cover: float | None
    valid_pixels: int
    mean: float | None
    p10: float | None
    p50: float | None
    p90: float | None


class TimeseriesOut(BaseModel):
    project_id: uuid.UUID
    index: TimeseriesIndex
    min_clear_fraction: float
    points: list[TimeseriesPoint]


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    project_id: uuid.UUID
    run_id: uuid.UUID | None
    kind: AlertKind
    metric: str
    observed_on: date
    observed_value: float
    expected: dict[str, Any]
    severity: AlertSeverity
    message: str
    acknowledged_by: uuid.UUID | None
    acknowledged_at: datetime | None
    created_at: datetime
