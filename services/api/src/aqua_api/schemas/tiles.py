"""Map-tile responses: TileJSON 3.0 and the classification legend."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TileJSON(BaseModel):
    """TileJSON 3.0.0 (https://github.com/mapbox/tilejson-spec/tree/master/3.0.0) for one raster
    layer of one classification. `tiles` carries signed URLs (valid until `expires_at`): MapLibre
    loads them without an Authorization header. Fetch a fresh TileJSON after expiry."""

    model_config = ConfigDict(protected_namespaces=())

    tilejson: Literal["3.0.0"] = "3.0.0"
    name: str
    description: str
    version: str = "1.0.0"
    attribution: str
    scheme: Literal["xyz"] = "xyz"
    tiles: list[str]
    minzoom: int = Field(ge=0, le=30)
    maxzoom: int = Field(ge=0, le=30)
    bounds: list[float] = Field(min_length=4, max_length=4)  # west, south, east, north (WGS84)
    center: list[float] = Field(min_length=3, max_length=3)  # lon, lat, zoom
    # AquaCreds extensions (TileJSON allows extra keys).
    layer: Literal["classification", "confidence"]
    project_id: uuid.UUID
    classification_id: uuid.UUID
    season_start_year: int
    model_version: str
    expires_at: datetime
    legend_url: str


class LegendClass(BaseModel):
    value: int  # pixel value in the classes COG
    class_name: str  # model class name, e.g. "mangrove"
    label: str
    color: str  # #RRGGBB
    area_ha: float | None  # classified area inside the project boundary


class LegendNoData(BaseModel):
    value: int
    label: str
    color: None = None  # rendered fully transparent


class LegendRampStop(BaseModel):
    value: float  # highest class probability
    color: str


class ConfidenceLegend(BaseModel):
    label: str
    min: float
    max: float
    stops: list[LegendRampStop]
    low_confidence_threshold: float


class ClassificationLegend(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    classification_id: uuid.UUID
    project_id: uuid.UUID
    season_start_year: int
    model_version: str
    attribution: str
    classes: list[LegendClass]
    nodata: LegendNoData
    confidence: ConfidenceLegend
    note: str
