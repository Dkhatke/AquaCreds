"""Merge the parallel Phase 5b (MRV reports) and hardening (user MFA) migration branches.

Revision ID: 0007_merge_reports_mfa
Revises: 0006_mrv_reports, 0006_user_mfa
Create Date: 2026-10-20
"""

from collections.abc import Sequence

revision: str = "0007_merge_reports_mfa"
down_revision: str | Sequence[str] | None = ("0006_mrv_reports", "0006_user_mfa")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
