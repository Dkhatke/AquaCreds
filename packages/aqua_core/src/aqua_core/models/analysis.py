"""Satellite analysis: runs (backfill / nightly / composite), per-scene Sentinel-2 and per-pass
Sentinel-1 time series, alerts."""

import uuid
from datetime import date, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from aqua_core.db import Base
from aqua_core.models._types import Timestamps, UUIDPk, str_enum
from aqua_core.models.enums import (
    AlertKind,
    AlertSeverity,
    AnalysisKind,
    AnalysisStatus,
    SarTrigger,
)


class AnalysisRun(UUIDPk, Timestamps, Base):
    """One execution of a geo pipeline job, with its parameters, progress and outputs (e.g. COG
    object keys, scene counts, zonal summaries). Provenance for every derived number."""

    __tablename__ = "analysis_runs"

    project_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[AnalysisKind] = mapped_column(str_enum(AnalysisKind, "analysis_kind"))
    status: Mapped[AnalysisStatus] = mapped_column(
        str_enum(AnalysisStatus, "analysis_status"), default=AnalysisStatus.QUEUED
    )
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(sa.Text)
    outputs: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class SceneObservation(UUIDPk, Base):
    """Zonal statistics of one Sentinel-2 scene over a project polygon (the time-series table)."""

    __tablename__ = "scene_observations"
    __table_args__ = (
        sa.UniqueConstraint("project_id", "item_id", name="uq_scene_observation_item"),
        sa.Index("ix_scene_observations_project_acquired", "project_id", "acquired_on"),
        sa.CheckConstraint("clear_fraction BETWEEN 0 AND 1", name="clear_fraction_range"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("projects.id", ondelete="CASCADE"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("analysis_runs.id", ondelete="SET NULL")
    )
    item_id: Mapped[str] = mapped_column(sa.String(120))  # STAC item id
    acquired_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    acquired_on: Mapped[date]
    cloud_cover: Mapped[float | None] = mapped_column(sa.Double)  # whole-tile eo:cloud_cover (%)
    clear_fraction: Mapped[float] = mapped_column(sa.Double)  # clear pixels / polygon pixels
    clear_pixels: Mapped[int]
    polygon_pixels: Mapped[int]
    # {"ndvi": {"mean", "p10", "p50", "p90", "valid_pixels"}, "evi": {...}, ...}
    index_stats: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    provider: Mapped[str] = mapped_column(sa.String(40))
    processing_baseline: Mapped[str | None] = mapped_column(sa.String(10))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )


class SarObservation(UUIDPk, Base):
    """Per-polygon backscatter statistics of one Sentinel-1 RTC pass (all slices of one absolute
    orbit), see `aqua_geo.sentinel1`. Values are terrain-corrected gamma0; `*_db` = 10*log10.

    SAR metrics are NOT comparable with the optical indices in `scene_observations`: they measure
    radar backscatter (structure, moisture, inundation), not reflectance."""

    __tablename__ = "sar_observations"
    __table_args__ = (
        sa.UniqueConstraint("project_id", "acquisition_key", name="uq_sar_observation_acquisition"),
        sa.Index("ix_sar_observations_project_acquired", "project_id", "acquired_on"),
        sa.CheckConstraint("valid_fraction BETWEEN 0 AND 1", name="valid_fraction_range"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("projects.id", ondelete="CASCADE"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("analysis_runs.id", ondelete="SET NULL")
    )
    # platform + UTC date + time of the pass, e.g. "S1A_20240726_054932" (provider-independent)
    acquisition_key: Mapped[str] = mapped_column(sa.String(64))
    item_ids: Mapped[list[str]] = mapped_column(ARRAY(sa.String(120)))  # STAC slices fused
    acquired_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    acquired_on: Mapped[date]
    platform: Mapped[str] = mapped_column(sa.String(10))
    orbit_state: Mapped[str | None] = mapped_column(sa.String(12))
    relative_orbit: Mapped[int | None]
    trigger: Mapped[SarTrigger] = mapped_column(str_enum(SarTrigger, "sar_trigger"))
    # the cloudy Sentinel-2 item that triggered a `cloud_gap` acquisition
    trigger_item_id: Mapped[str | None] = mapped_column(sa.String(120))
    provider: Mapped[str] = mapped_column(sa.String(40))
    polygon_pixels: Mapped[int]
    valid_pixels: Mapped[int]
    valid_fraction: Mapped[float] = mapped_column(sa.Double)  # valid pixels / polygon pixels
    # {"vh_db": {"mean", "p10", "p50", "p90", "valid_pixels"}, "vv_db", "vh_vv_ratio",
    #  "vh_vv_db", "rvi"}
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )


class Alert(UUIDPk, Base):
    """Loss/degradation alert raised against the project's own historical envelope."""

    __tablename__ = "alerts"
    __table_args__ = (
        sa.UniqueConstraint(
            "project_id", "kind", "metric", "observed_on", name="uq_alert_observation"
        ),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("analysis_runs.id", ondelete="SET NULL")
    )
    kind: Mapped[AlertKind] = mapped_column(str_enum(AlertKind, "alert_kind"))
    metric: Mapped[str] = mapped_column(sa.String(40))  # e.g. "ndvi_p50"
    observed_on: Mapped[date]
    observed_value: Mapped[float] = mapped_column(sa.Double)
    # {"month", "p10", "p50", "p90", "n", "margin", "threshold", "observations": [...]}
    expected: Mapped[dict[str, Any]] = mapped_column(JSONB)
    severity: Mapped[AlertSeverity] = mapped_column(str_enum(AlertSeverity, "alert_severity"))
    message: Mapped[str] = mapped_column(sa.Text)
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
