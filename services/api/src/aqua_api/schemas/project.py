import uuid
from datetime import date, datetime
from typing import Any

from geojson_pydantic import MultiPolygon, Polygon
from pydantic import BaseModel, ConfigDict, Field

from aqua_core.models import DocumentKind, Ecosystem, Methodology, ProjectStatus, TenureType


class ProjectCreate(BaseModel):
    org_id: uuid.UUID
    name: str = Field(min_length=3, max_length=200)
    description: str = Field(default="", max_length=5000)
    state: str = Field(min_length=2, max_length=80, examples=["Odisha"])
    ecosystem: Ecosystem = Ecosystem.MANGROVE
    methodology: Methodology = Methodology.CCTS_BM_FR05_001
    tenure_type: TenureType
    boundary: Polygon | MultiPolygon = Field(description="GeoJSON geometry in WGS84 (lon, lat)")
    start_date: date
    planned_mangrove_share_pct: float = Field(ge=0, le=100)
    soil_disturbance_pct: float = Field(ge=0, le=100)


class ProjectUpdate(BaseModel):
    """Only allowed while the project is a draft or has changes requested."""

    name: str | None = Field(default=None, min_length=3, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    state: str | None = Field(default=None, min_length=2, max_length=80)
    tenure_type: TenureType | None = None
    boundary: Polygon | MultiPolygon | None = None
    start_date: date | None = None
    planned_mangrove_share_pct: float | None = Field(default=None, ge=0, le=100)
    soil_disturbance_pct: float | None = Field(default=None, ge=0, le=100)


class TenureDocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    kind: DocumentKind
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    created_at: datetime


class ProjectOut(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    description: str
    state: str
    ecosystem: Ecosystem
    methodology: Methodology
    status: ProjectStatus
    tenure_type: TenureType
    boundary: dict[str, Any]
    area_ha: float
    start_date: date
    planned_mangrove_share_pct: float
    soil_disturbance_pct: float
    submitted_at: datetime | None
    created_at: datetime
    documents: list[TenureDocumentOut]


class UploadRequest(BaseModel):
    kind: DocumentKind
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(pattern=r"^(application/pdf|image/(jpeg|png|webp))$")


class UploadTicket(BaseModel):
    object_key: str
    upload_url: str
    method: str = "PUT"
    headers: dict[str, str]
    expires_in: int


class DocumentConfirm(BaseModel):
    object_key: str = Field(max_length=512)
    kind: DocumentKind
    filename: str = Field(min_length=1, max_length=255)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$", description="Client-computed SHA-256 (hex)")


class SubmissionIssue(BaseModel):
    code: str
    message: str


class OverlapConflict(BaseModel):
    project_id: uuid.UUID
    name: str
    overlap_ha: float
