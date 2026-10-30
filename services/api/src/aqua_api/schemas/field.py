import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from aqua_core.models import NonceMode, PeriodKind, PeriodStatus, SubmissionKind, SubmissionStatus


class PeriodCreate(BaseModel):
    kind: PeriodKind
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def _order(self) -> "PeriodCreate":
        if self.end_date <= self.start_date:
            raise ValueError("end_date must be after start_date")
        return self


class PeriodOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    project_id: uuid.UUID
    kind: PeriodKind
    status: PeriodStatus
    start_date: date
    end_date: date
    created_at: datetime


class NonceOut(BaseModel):
    """A single-use capture nonce. `server_time` lets the app estimate its clock offset (the
    worker compares the app's `captured_at` against `issued_at`/`expires_at`)."""

    nonce: str
    expires_at: datetime
    issued_at: datetime
    server_time: datetime
    mode: NonceMode = NonceMode.LIVE


class NonceBatchRequest(BaseModel):
    count: int = Field(default=10, ge=1, le=20)


class NonceBatchItem(BaseModel):
    nonce: str
    expires_at: datetime


class NonceBatchOut(BaseModel):
    """Pre-issued nonces for offline capture: each single-use, bound to the caller and the
    period, valid from `issued_at` to `expires_at`. Photos taken with one get a lower-weight
    `capture_nonce` check (`nonce_mode: batch`): they prove only capture after `issued_at`."""

    mode: Literal[NonceMode.BATCH] = NonceMode.BATCH
    period_id: uuid.UUID
    issued_at: datetime
    expires_at: datetime
    server_time: datetime
    nonces: list[NonceBatchItem]


class PhotoUploadRequest(BaseModel):
    content_type: str = Field(pattern=r"^image/(jpeg|png)$")


class SubmissionCreate(BaseModel):
    object_key: str = Field(max_length=512)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    nonce: str = Field(min_length=20, max_length=100)
    kind: SubmissionKind
    plot_code: str | None = Field(default=None, max_length=20)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    gps_accuracy_m: float = Field(ge=0, le=500)
    heading_deg: float | None = Field(default=None, ge=0, lt=360)
    captured_at: AwareDatetime
    device_model: str = Field(default="", max_length=120)
    app_version: str = Field(default="", max_length=40)

    @model_validator(mode="after")
    def _plot(self) -> "SubmissionCreate":
        if self.kind == SubmissionKind.PLOT_CENTRE and not self.plot_code:
            raise ValueError("plot_code is required for plot_centre photos")
        return self


class SubmissionOut(BaseModel):
    id: uuid.UUID
    period_id: uuid.UUID
    kind: SubmissionKind
    status: SubmissionStatus
    plot_id: uuid.UUID | None
    sha256: str
    lat: float
    lon: float
    gps_accuracy_m: float
    captured_at: datetime
    received_at: datetime
    trust_score: int | None
    checks: list[dict[str, Any]] | None
    exif: dict[str, Any] | None


class PlotCreate(BaseModel):
    code: str = Field(pattern=r"^[A-Za-z0-9-]{1,20}$")
    stratum: str = Field(min_length=1, max_length=40)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    area_m2: float = Field(gt=0, le=10_000)
    marker_id: str = Field(min_length=1, max_length=40)


class PlotOut(BaseModel):
    id: uuid.UUID
    code: str
    stratum: str
    lat: float
    lon: float
    area_m2: float
    marker_id: str


class TreeRecord(BaseModel):
    species: str = Field(min_length=3, max_length=80)
    dbh_cm: float = Field(gt=0, le=300)
    height_m: float | None = Field(default=None, gt=0, le=60)
    alive: bool = True


class MeasurementCreate(BaseModel):
    measured_on: date
    trees: list[TreeRecord] = Field(max_length=2000)
    notes: str = Field(default="", max_length=2000)


class MeasurementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    plot_id: uuid.UUID
    period_id: uuid.UUID
    measured_by: uuid.UUID
    measured_on: date
    trees: list[dict[str, Any]]
    notes: str
    created_at: datetime


# ---------------------------------------------------------------- evidence points (map)

EvidencePrecision = Literal["snapped", "raw"]
SnapRule = Literal["plot_centre", "grid", "raw"]


class PointGeometry(BaseModel):
    type: Literal["Point"] = "Point"
    coordinates: tuple[float, float]  # [lon, lat]


class EvidencePointProperties(BaseModel):
    """Only what the map needs. `id` and `captured_at` are present for `precision=raw` only;
    snapped points carry no identifier, so they cannot be joined back to a raw location."""

    snap: SnapRule
    status: SubmissionStatus
    trust_score: int | None
    kind: SubmissionKind
    period_id: uuid.UUID
    plot_code: str | None = None
    id: uuid.UUID | None = None
    captured_at: datetime | None = None
    gps_accuracy_m: float | None = None


class EvidencePointFeature(BaseModel):
    type: Literal["Feature"] = "Feature"
    geometry: PointGeometry
    properties: EvidencePointProperties


class EvidencePoints(BaseModel):
    """GeoJSON FeatureCollection of a project's field photos for the map.

    `precision=snapped` (default, any project viewer): a plot photo sits on its plot centre, any
    other photo on the centre of a `grid_deg` grid cell; never the photo's own GPS fix.
    `precision=raw` (organisation admins and field agents of the project, and staff): the
    app-reported GPS fix."""

    type: Literal["FeatureCollection"] = "FeatureCollection"
    precision: EvidencePrecision
    grid_deg: float
    features: list[EvidencePointFeature]
    total: int
    truncated: bool
