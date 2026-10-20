"""Chain transactions signed by custodial org wallets: `org_wallet` signer, forward-fund purposes.

Revision ID: 0008_forward_fund_signer
Revises: 0007_merge_reports_mfa
Create Date: 2026-10-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008_forward_fund_signer"
down_revision: str | Sequence[str] | None = "0007_merge_reports_mfa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SIGNERS_OLD = ("registrar", "verifier", "issuer")
SIGNERS_NEW = (*SIGNERS_OLD, "org_wallet")
PURPOSES_OLD = (
    "register_project",
    "submit_report",
    "approve_report",
    "reject_report",
    "issue_credits",
    "other",
)
PURPOSES_NEW = (*PURPOSES_OLD, "forward_fund_offer", "forward_fund_withdraw")


def _check(column: str, name: str, values: tuple[str, ...]) -> None:
    op.drop_constraint(f"ck_chain_transactions_{name}", "chain_transactions", type_="check")
    allowed = ", ".join(f"'{v}'" for v in values)
    op.create_check_constraint(
        f"ck_chain_transactions_{name}", "chain_transactions", f"{column} IN ({allowed})"
    )


def upgrade() -> None:
    _check("signer", "chain_signer", SIGNERS_NEW)
    _check("purpose", "chain_tx_purpose", PURPOSES_NEW)
    # Offers relayed before this revision were recorded as signer 'registrar', purpose 'other'.
    op.execute(
        "UPDATE chain_transactions SET signer = 'org_wallet', purpose = CASE function "
        "WHEN 'submitOffer' THEN 'forward_fund_offer' ELSE 'forward_fund_withdraw' END "
        "WHERE contract = 'AquaForwardFund' AND function IN ('submitOffer', 'withdrawOffer')"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE chain_transactions SET signer = 'registrar', purpose = 'other' "
        "WHERE signer = 'org_wallet' OR purpose IN ('forward_fund_offer', 'forward_fund_withdraw')"
    )
    _check("signer", "chain_signer", SIGNERS_OLD)
    _check("purpose", "chain_tx_purpose", PURPOSES_OLD)
