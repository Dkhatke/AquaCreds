"""field evidence

Revision ID: 0003_field_evidence
Revises: 0002_identity_projects
Create Date: 2026-10-19 17:57:39.597162
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geometry
from sqlalchemy.dialects import postgresql

revision: str = "0003_field_evidence"
down_revision: str | None = "0002_identity_projects"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "monitoring_periods",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "baseline",
                "monitoring",
                name="period_kind",
                native_enum=False,
                create_constraint=True,
                length=40,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "open",
                "analysing",
                "under_review",
                "changes_requested",
                "approved",
                "rejected",
                name="period_status",
                native_enum=False,
                create_constraint=True,
                length=40,
            ),
            nullable=False,
        ),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "end_date > start_date", name=op.f("ck_monitoring_periods_period_dates")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_monitoring_periods_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_monitoring_periods_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_monitoring_periods")),
    )
    op.create_index(
        op.f("ix_monitoring_periods_project_id"), "monitoring_periods", ["project_id"], unique=False
    )
    op.create_geospatial_table(
        "plots",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=20), nullable=False),
        sa.Column("stratum", sa.String(length=40), nullable=False),
        sa.Column(
            "centre",
            Geometry(
                geometry_type="POINT",
                srid=4326,
                dimension=2,
                spatial_index=False,
                from_text="ST_GeomFromEWKT",
                name="geometry",
                nullable=False,
            ),
            nullable=False,
        ),
        sa.Column("area_m2", sa.Double(), nullable=False),
        sa.Column("marker_id", sa.String(length=40), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("area_m2 > 0", name=op.f("ck_plots_plot_area_positive")),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_plots_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_plots")),
        sa.UniqueConstraint("project_id", "code", name="uq_plot_code"),
    )
    op.create_geospatial_index(
        "idx_plots_centre",
        "plots",
        ["centre"],
        unique=False,
        postgresql_using="gist",
        postgresql_ops={},
    )
    op.create_index(op.f("ix_plots_project_id"), "plots", ["project_id"], unique=False)
    op.create_table(
        "capture_nonces",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("period_id", sa.Uuid(), nullable=False),
        sa.Column("nonce_hash", sa.String(length=64), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["period_id"],
            ["monitoring_periods.id"],
            name=op.f("fk_capture_nonces_period_id_monitoring_periods"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_capture_nonces_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_capture_nonces")),
        sa.UniqueConstraint("nonce_hash", name=op.f("uq_capture_nonces_nonce_hash")),
    )
    op.create_index(
        op.f("ix_capture_nonces_period_id"), "capture_nonces", ["period_id"], unique=False
    )
    op.create_table(
        "plot_measurements",
        sa.Column("plot_id", sa.Uuid(), nullable=False),
        sa.Column("period_id", sa.Uuid(), nullable=False),
        sa.Column("measured_by", sa.Uuid(), nullable=False),
        sa.Column("measured_on", sa.Date(), nullable=False),
        sa.Column("trees", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["measured_by"],
            ["users.id"],
            name=op.f("fk_plot_measurements_measured_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["period_id"],
            ["monitoring_periods.id"],
            name=op.f("fk_plot_measurements_period_id_monitoring_periods"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["plot_id"],
            ["plots.id"],
            name=op.f("fk_plot_measurements_plot_id_plots"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_plot_measurements")),
        sa.UniqueConstraint("plot_id", "period_id", name="uq_plot_period"),
    )
    op.create_index(
        op.f("ix_plot_measurements_period_id"), "plot_measurements", ["period_id"], unique=False
    )
    op.create_geospatial_table(
        "field_submissions",
        sa.Column("period_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("submitted_by", sa.Uuid(), nullable=False),
        sa.Column("nonce_id", sa.Uuid(), nullable=True),
        sa.Column("plot_id", sa.Uuid(), nullable=True),
        sa.Column(
            "kind",
            sa.Enum(
                "plot_centre",
                "photo_point",
                "general",
                name="submission_kind",
                native_enum=False,
                create_constraint=True,
                length=40,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "pending_checks",
                "checked",
                "rejected",
                name="submission_status",
                native_enum=False,
                create_constraint=True,
                length=40,
            ),
            nullable=False,
        ),
        sa.Column("object_key", sa.String(length=512), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("phash", sa.String(length=16), nullable=True),
        sa.Column("content_type", sa.String(length=50), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column(
            "location",
            Geometry(
                geometry_type="POINT",
                srid=4326,
                dimension=2,
                spatial_index=False,
                from_text="ST_GeomFromEWKT",
                name="geometry",
                nullable=False,
            ),
            nullable=False,
        ),
        sa.Column("gps_accuracy_m", sa.Double(), nullable=False),
        sa.Column("heading_deg", sa.Double(), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("device_model", sa.String(length=120), nullable=False),
        sa.Column("app_version", sa.String(length=40), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("exif", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("checks", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("trust_score", sa.Integer(), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "gps_accuracy_m >= 0", name=op.f("ck_field_submissions_accuracy_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["nonce_id"],
            ["capture_nonces.id"],
            name=op.f("fk_field_submissions_nonce_id_capture_nonces"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["period_id"],
            ["monitoring_periods.id"],
            name=op.f("fk_field_submissions_period_id_monitoring_periods"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["plot_id"],
            ["plots.id"],
            name=op.f("fk_field_submissions_plot_id_plots"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_field_submissions_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["submitted_by"],
            ["users.id"],
            name=op.f("fk_field_submissions_submitted_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_field_submissions")),
        sa.UniqueConstraint("object_key", name=op.f("uq_field_submissions_object_key")),
    )
    op.create_geospatial_index(
        "idx_field_submissions_location",
        "field_submissions",
        ["location"],
        unique=False,
        postgresql_using="gist",
        postgresql_ops={},
    )
    op.create_index(
        op.f("ix_field_submissions_period_id"), "field_submissions", ["period_id"], unique=False
    )
    op.create_index("ix_field_submissions_phash", "field_submissions", ["phash"], unique=False)
    op.create_index(
        op.f("ix_field_submissions_project_id"), "field_submissions", ["project_id"], unique=False
    )
    op.create_index(
        op.f("ix_field_submissions_sha256"), "field_submissions", ["sha256"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_field_submissions_sha256"), table_name="field_submissions")
    op.drop_index(op.f("ix_field_submissions_project_id"), table_name="field_submissions")
    op.drop_index("ix_field_submissions_phash", table_name="field_submissions")
    op.drop_index(op.f("ix_field_submissions_period_id"), table_name="field_submissions")
    op.drop_geospatial_index(
        "idx_field_submissions_location",
        table_name="field_submissions",
        postgresql_using="gist",
        column_name="location",
    )
    op.drop_geospatial_table("field_submissions")
    op.drop_index(op.f("ix_plot_measurements_period_id"), table_name="plot_measurements")
    op.drop_table("plot_measurements")
    op.drop_index(op.f("ix_capture_nonces_period_id"), table_name="capture_nonces")
    op.drop_table("capture_nonces")
    op.drop_index(op.f("ix_plots_project_id"), table_name="plots")
    op.drop_geospatial_index(
        "idx_plots_centre", table_name="plots", postgresql_using="gist", column_name="centre"
    )
    op.drop_geospatial_table("plots")
    op.drop_index(op.f("ix_monitoring_periods_project_id"), table_name="monitoring_periods")
    op.drop_table("monitoring_periods")
