"""Merge the parallel Phase 4 (geo time series) and Phase 6 (chain) migration branches.

Revision ID: 0005_merge_geo_chain
Revises: 0004_chain, 0004_geo_timeseries
Create Date: 2026-10-20
"""

from collections.abc import Sequence

revision: str = "0005_merge_geo_chain"
down_revision: str | Sequence[str] | None = ("0004_chain", "0004_geo_timeseries")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
