"""MRV reports: one hashed, versioned record per monitoring period, plus the verifier decision and
the on-chain anchoring references.

The canonical JSON (RFC 8785), PDF and CSV live in private object storage; the row keeps their keys,
the SHA-256 of the canonical JSON (`report_hash`, anchored via `AquaRegistry.submitReport`) and the
headline units copied from the carbon engine result, so listings never need to read the files."""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from aqua_core.db import Base
from aqua_core.models._types import Timestamps, UUIDPk, str_enum
from aqua_core.models.enums import ReportStatus


class MRVReport(UUIDPk, Timestamps, Base):
    __tablename__ = "mrv_reports"
    __table_args__ = (
        sa.UniqueConstraint("period_id", "version", name="uq_mrv_report_period_version"),
        sa.CheckConstraint("version >= 1", name="mrv_report_version_positive"),
        sa.CheckConstraint("period_end_year > period_start_year", name="mrv_report_years"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    period_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("monitoring_periods.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int]
    status: Mapped[ReportStatus] = mapped_column(
        str_enum(ReportStatus, "report_status"), default=ReportStatus.DRAFT, index=True
    )

    # Hex SHA-256 of the canonical JSON (64 chars, no 0x).
    report_hash: Mapped[str] = mapped_column(sa.String(64), unique=True)
    # Signed whole lCCC units as anchored on-chain (negative = units to replace after a reversal).
    lccc_units: Mapped[int] = mapped_column(sa.BigInteger)
    tccc_units: Mapped[int] = mapped_column(sa.BigInteger)  # floor(tCCC tCO2e since project start)
    # Verification period t1 → t2 (years) and the values the next report builds on.
    period_start_year: Mapped[int]
    period_end_year: Mapped[int]
    tree_stock_tco2e: Mapped[float] = mapped_column(sa.Double)
    tccc_tco2e: Mapped[float] = mapped_column(sa.Double)

    json_key: Mapped[str] = mapped_column(sa.String(512), unique=True)
    pdf_key: Mapped[str] = mapped_column(sa.String(512), unique=True)
    csv_key: Mapped[str] = mapped_column(sa.String(512), unique=True)

    generated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    generated_by: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("users.id", ondelete="RESTRICT"))

    verifier_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("users.id", ondelete="RESTRICT")
    )
    decided_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    decision_comment: Mapped[str | None] = mapped_column(sa.Text)

    # On-chain anchoring (filled by the `aqua.reports.anchor` worker task after approval).
    submit_tx_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("chain_transactions.id", ondelete="SET NULL")
    )
    approve_tx_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("chain_transactions.id", ondelete="SET NULL")
    )
    chain_report_id: Mapped[int | None] = mapped_column(sa.BigInteger)  # AquaRegistry reportId
    anchored_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    anchor_error: Mapped[str | None] = mapped_column(sa.Text)
