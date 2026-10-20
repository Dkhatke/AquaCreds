import uuid
from datetime import date, datetime
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

from aqua_core.models import AlertKind, AlertSeverity, AnalysisKind, AnalysisStatus

OpticalIndex = Literal["ndvi", "evi", "mvi", "ndwi", "mndwi", "ndmi", "cmri"]
# Sentinel-1 RTC backscatter metrics (terrain-corrected gamma0): VH and VV in dB, the VH/VV ratio in
# dB (VH dB − VV dB) and the dual-pol radar vegetation index RVI = 4·VH/(VV+VH) (linear).
SarIndex = Literal["vh_db", "vv_db", "vh_vv_ratio_db", "rvi"]
TimeseriesIndex = Literal[OpticalIndex, SarIndex]
SAR_INDICES: frozenset[str] = frozenset(get_args(SarIndex))
# API index name → key in `sar_observations.stats` (aqua_geo.sentinel1.SAR_METRICS)
SAR_STATS_KEY: dict[str, str] = {
    "vh_db": "vh_db",
    "vv_db": "vv_db",
    "vh_vv_ratio_db": "vh_vv_db",
    "rvi": "rvi",
}
Sensor = Literal["sentinel-2-l2a", "sentinel-1-rtc"]
SAR_UNITS: dict[str, str] = {
    "vh_db": "dB (10·log10 gamma0)",
    "vv_db": "dB (10·log10 gamma0)",
    "vh_vv_ratio_db": "dB (VH dB − VV dB)",
    "rvi": "unitless, 0–2 (4·VH/(VV+VH), linear gamma0)",
}
NOT_COMPARABLE = (
    "Sentinel-1 radar backscatter (structure, moisture, inundation) and Sentinel-2 optical indices "
    "(reflectance) measure different things on different scales: do not compare or mix their values."
)


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
    """One Sentinel-2 scene or one Sentinel-1 pass. For Sentinel-1, `item_id` is the acquisition
    key, `clear_fraction`/`cloud_cover` are null and `valid_fraction` is the share of polygon
    pixels with valid backscatter; for Sentinel-2 the SAR-only fields are null."""

    date: date
    acquired_at: datetime
    item_id: str
    clear_fraction: float | None
    cloud_cover: float | None
    valid_fraction: float | None = None
    orbit_state: str | None = None
    relative_orbit: int | None = None
    valid_pixels: int
    mean: float | None
    p10: float | None
    p50: float | None
    p90: float | None


class TimeseriesOut(BaseModel):
    project_id: uuid.UUID
    index: TimeseriesIndex
    sensor: Sensor
    units: str
    note: str  # SAR and optical indices are not comparable
    min_clear_fraction: float  # applied to valid_fraction for Sentinel-1 indices
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


class ClassificationRequest(BaseModel):
    """Dry season starting in November of `season_start_year` (e.g. 2020 → Nov 2020–Mar 2021)."""

    season_start_year: int = Field(ge=2016, le=2100)


class ClassificationOut(BaseModel):
    """One ecosystem-classifier result. Areas: classified 10 m pixels x pixel area / UTM areal
    scale, pixel centres inside the boundary. Context for MRV only: credits come from plots."""

    model_config = ConfigDict(from_attributes=True, protected_namespaces=())
    id: uuid.UUID
    project_id: uuid.UUID
    run_id: uuid.UUID | None
    season_start_year: int
    model_version: str
    model_sha256: str
    features_sha256: str
    provider: str
    scenes: list[dict[str, Any]]
    dem_items: list[str]
    epsg: int
    pixel_area_m2: float
    areal_scale: float
    polygon_pixels: int
    classified_pixels: int
    unclassified_pixels: int
    classes: list[str]  # class index order (values of the classes COG)
    class_areas_ha: dict[str, float]
    mangrove_ha: float
    mangrove_mean_confidence: float | None
    low_confidence_threshold: float
    low_confidence_share: float
    classes_cog_sha256: str
    probability_cog_sha256: str
    created_at: datetime


class ClassificationDownloads(BaseModel):
    classes_url: str  # uint8 class index COG (nodata 255; class names in the band tags)
    probability_url: str  # float32 highest-class probability COG (NaN no-data)


class ClassificationDetail(ClassificationOut):
    downloads: ClassificationDownloads
