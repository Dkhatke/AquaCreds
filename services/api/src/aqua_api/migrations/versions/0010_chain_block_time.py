"""Chain follow-ups: event block timestamps, gas top-ups of custodial org wallets.

- `chain_events.block_time` (nullable; the indexer fills it, and backfills older rows);
- `chain_signer` += 'funder' (the account paying gas top-ups);
- `chain_tx_purpose` += 'gas_top_up';
- `chain_tx_status` += 'waiting_for_gas' (org-wallet tx parked until its wallet is funded).

Revision ID: 0010_chain_block_time
Revises: 0009_sar_observations
Create Date: 2026-10-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_chain_block_time"
down_revision: str | Sequence[str] | None = "0009_sar_observations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SIGNERS_OLD = ("registrar", "verifier", "issuer", "org_wallet")
SIGNERS_NEW = (*SIGNERS_OLD, "funder")
PURPOSES_OLD = (
    "register_project",
    "submit_report",
    "approve_report",
    "reject_report",
    "issue_credits",
    "other",
    "forward_fund_offer",
    "forward_fund_withdraw",
)
PURPOSES_NEW = (*PURPOSES_OLD, "gas_top_up")
STATUSES_OLD = ("queued", "submitted", "confirmed", "failed", "reverted")
STATUSES_NEW = (*STATUSES_OLD, "waiting_for_gas")


def _check(column: str, name: str, values: tuple[str, ...]) -> None:
    # `name` is the bare constraint name: the metadata naming convention adds the
    # `ck_chain_transactions_` prefix, as it did when 0004_chain created these constraints.
    op.drop_constraint(name, "chain_transactions", type_="check")
    allowed = ", ".join(f"'{v}'" for v in values)
    op.create_check_constraint(name, "chain_transactions", f"{column} IN ({allowed})")


def upgrade() -> None:
    op.add_column(
        "chain_events", sa.Column("block_time", sa.DateTime(timezone=True), nullable=True)
    )
    _check("signer", "chain_signer", SIGNERS_NEW)
    _check("purpose", "chain_tx_purpose", PURPOSES_NEW)
    _check("status", "chain_tx_status", STATUSES_NEW)


def downgrade() -> None:
    # Lossy by necessity: parked transactions go back to `queued` (nothing was signed for them);
    # finished gas top-ups keep their rows but are relabelled 'other', signed by 'registrar'.
    op.execute("UPDATE chain_transactions SET status = 'queued' WHERE status = 'waiting_for_gas'")
    op.execute(
        "UPDATE chain_transactions SET signer = 'registrar', purpose = 'other' "
        "WHERE signer = 'funder' OR purpose = 'gas_top_up'"
    )
    _check("status", "chain_tx_status", STATUSES_OLD)
    _check("purpose", "chain_tx_purpose", PURPOSES_OLD)
    _check("signer", "chain_signer", SIGNERS_OLD)
    op.drop_column("chain_events", "block_time")
