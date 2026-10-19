"""Projects (with PostGIS boundary) and tenure/authorisation documents."""

import uuid
from datetime import date, datetime

import sqlalchemy as sa
from geoalchemy2 import Geometry
from geoalchemy2.elements import WKBElement
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aqua_core.db import Base
from aqua_core.models._types import Timestamps, UUIDPk, str_enum
from aqua_core.models.enums import DocumentKind, Ecosystem, Methodology, ProjectStatus, TenureType


class Project(UUIDPk, Timestamps, Base):
    __tablename__ = "projects"
    __table_args__ = (
        sa.CheckConstraint(
            "planned_mangrove_share_pct BETWEEN 0 AND 100", name="mangrove_share_range"
        ),
        sa.CheckConstraint("soil_disturbance_pct BETWEEN 0 AND 100", name="soil_disturbance_range"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("organizations.id", ondelete="RESTRICT"), index=True
    )
    created_by: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("users.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(sa.String(200))
    description: Mapped[str] = mapped_column(sa.Text, default="")
    state: Mapped[str] = mapped_column(sa.String(80))  # Indian state / UT
    ecosystem: Mapped[Ecosystem] = mapped_column(str_enum(Ecosystem, "ecosystem"))
    methodology: Mapped[Methodology] = mapped_column(str_enum(Methodology, "methodology"))
    status: Mapped[ProjectStatus] = mapped_column(
        str_enum(ProjectStatus, "project_status"), default=ProjectStatus.DRAFT, index=True
    )
    tenure_type: Mapped[TenureType] = mapped_column(str_enum(TenureType, "tenure_type"))

    boundary: Mapped[WKBElement] = mapped_column(
        Geometry("MULTIPOLYGON", srid=4326, spatial_index=True)
    )
    area_ha: Mapped[float] = mapped_column(sa.Double)

    start_date: Mapped[date]
    planned_mangrove_share_pct: Mapped[float] = mapped_column(sa.Double)
    soil_disturbance_pct: Mapped[float] = mapped_column(sa.Double)
    submitted_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    documents: Mapped[list["TenureDocument"]] = relationship(
        back_populates="project", lazy="selectin", cascade="all, delete-orphan"
    )


class TenureDocument(UUIDPk, Base):
    """Tenure/authorisation evidence. Files live in private object storage; only metadata and the
    SHA-256 are stored here (the hash is later anchored on-chain)."""

    __tablename__ = "tenure_documents"
    __table_args__ = (sa.UniqueConstraint("project_id", "sha256", name="uq_tenure_doc_hash"),)

    project_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[DocumentKind] = mapped_column(str_enum(DocumentKind, "document_kind"))
    object_key: Mapped[str] = mapped_column(sa.String(512), unique=True)
    filename: Mapped[str] = mapped_column(sa.String(255))
    content_type: Mapped[str] = mapped_column(sa.String(100))
    size_bytes: Mapped[int] = mapped_column(sa.BigInteger)
    sha256: Mapped[str] = mapped_column(sa.String(64))
    uploaded_by: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )

    project: Mapped[Project] = relationship(back_populates="documents")
