"""Field evidence: monitoring periods, capture nonces, photo submissions, sample plots."""

import uuid
from datetime import date, datetime
from typing import Any

import sqlalchemy as sa
from geoalchemy2 import Geometry
from geoalchemy2.elements import WKBElement
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from aqua_core.db import Base
from aqua_core.models._types import Timestamps, UUIDPk, str_enum
from aqua_core.models.enums import (
    NonceMode,
    PeriodKind,
    PeriodStatus,
    SubmissionKind,
    SubmissionStatus,
)


class MonitoringPeriod(UUIDPk, Timestamps, Base):
    __tablename__ = "monitoring_periods"
    __table_args__ = (sa.CheckConstraint("end_date > start_date", name="period_dates"),)

    project_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[PeriodKind] = mapped_column(str_enum(PeriodKind, "period_kind"))
    status: Mapped[PeriodStatus] = mapped_column(
        str_enum(PeriodStatus, "period_status"), default=PeriodStatus.OPEN
    )
    start_date: Mapped[date]
    end_date: Mapped[date]
    created_by: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("users.id", ondelete="RESTRICT"))


class CaptureNonce(UUIDPk, Base):
    """Single-use token fetched by the app right before a capture. Proves the photo was taken
    live (after the nonce was issued), by this user, for this project. `batch` nonces are
    pre-issued for offline capture with a longer window, so they prove less (see
    aqua_worker.evidence.check_nonce)."""

    __tablename__ = "capture_nonces"

    user_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"))
    period_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("monitoring_periods.id", ondelete="CASCADE"), index=True
    )
    nonce_hash: Mapped[str] = mapped_column(sa.String(64), unique=True)
    issued_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    mode: Mapped[NonceMode] = mapped_column(
        str_enum(NonceMode, "nonce_mode"), default=NonceMode.LIVE, server_default="live"
    )


class Plot(UUIDPk, Timestamps, Base):
    """Permanent sample plot (AR-TOOL14 inventory), physically marked in the field."""

    __tablename__ = "plots"
    __table_args__ = (
        sa.UniqueConstraint("project_id", "code", name="uq_plot_code"),
        sa.CheckConstraint("area_m2 > 0", name="plot_area_positive"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(sa.String(20))  # e.g. "P-07"
    stratum: Mapped[str] = mapped_column(sa.String(40))
    centre: Mapped[WKBElement] = mapped_column(Geometry("POINT", srid=4326, spatial_index=True))
    area_m2: Mapped[float] = mapped_column(sa.Double)
    marker_id: Mapped[str] = mapped_column(sa.String(40))  # ID on the physical stake/tag


class PlotMeasurement(UUIDPk, Base):
    __tablename__ = "plot_measurements"
    __table_args__ = (sa.UniqueConstraint("plot_id", "period_id", name="uq_plot_period"),)

    plot_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("plots.id", ondelete="CASCADE"))
    period_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("monitoring_periods.id", ondelete="CASCADE"), index=True
    )
    measured_by: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("users.id", ondelete="RESTRICT"))
    measured_on: Mapped[date]
    trees: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB
    )  # [{species, dbh_cm, height_m, alive}]
    notes: Mapped[str] = mapped_column(sa.Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )


class FieldSubmission(UUIDPk, Base):
    __tablename__ = "field_submissions"
    __table_args__ = (
        sa.CheckConstraint("gps_accuracy_m >= 0", name="accuracy_non_negative"),
        sa.Index("ix_field_submissions_phash", "phash"),
    )

    period_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("monitoring_periods.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    submitted_by: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("users.id", ondelete="RESTRICT"))
    nonce_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("capture_nonces.id", ondelete="SET NULL")
    )
    plot_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("plots.id", ondelete="SET NULL")
    )
    kind: Mapped[SubmissionKind] = mapped_column(str_enum(SubmissionKind, "submission_kind"))
    status: Mapped[SubmissionStatus] = mapped_column(
        str_enum(SubmissionStatus, "submission_status"), default=SubmissionStatus.PENDING_CHECKS
    )

    object_key: Mapped[str] = mapped_column(sa.String(512), unique=True)
    sha256: Mapped[str] = mapped_column(sa.String(64), index=True)
    phash: Mapped[str | None] = mapped_column(sa.String(16))
    content_type: Mapped[str] = mapped_column(sa.String(50))
    size_bytes: Mapped[int] = mapped_column(sa.BigInteger)

    # What the app reported at capture time
    location: Mapped[WKBElement] = mapped_column(Geometry("POINT", srid=4326, spatial_index=True))
    gps_accuracy_m: Mapped[float] = mapped_column(sa.Double)
    heading_deg: Mapped[float | None] = mapped_column(sa.Double)
    captured_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    device_model: Mapped[str] = mapped_column(sa.String(120), default="")
    app_version: Mapped[str] = mapped_column(sa.String(40), default="")
    received_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )

    # Filled by the worker
    exif: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    checks: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    trust_score: Mapped[int | None]
    checked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class IdempotencyRecord(Base):
    """The first successful response to a create request sent with an `Idempotency-Key` header,
    replayed for the same user + key (and the same request) for `IDEMPOTENCY_TTL_H` hours."""

    __tablename__ = "idempotency_records"

    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    key: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(sa.String(60))  # e.g. "submission.create"
    request_sha256: Mapped[str] = mapped_column(sa.String(64))  # method + path + canonical body
    status_code: Mapped[int] = mapped_column(sa.SmallInteger)
    response: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), index=True
    )
