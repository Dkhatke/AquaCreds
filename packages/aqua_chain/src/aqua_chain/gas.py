"""Native-gas top-ups for custodial organisation wallets.

Org wallets sign their own forward-fund actions, so they need native gas (POL on Amoy). One run of
`top_up_org_wallets`:

1. selects the org wallets that have work to send: a `queued` or `waiting_for_gas` org-wallet
   transaction on this chain. Idle wallets are never funded: every other platform action is
   signed by a platform role, so gas parked in an idle wallet would be faucet budget wasted;
2. for each, reads the native balance. A wallet below `min_balance`, or with a transaction
   `waiting_for_gas`, gets one transfer of `amount` from the dedicated funder account, unless a
   top-up to it is still in flight or a cap would be exceeded:
   - per wallet: sum of top-ups to it over the last 24 h (rolling) + amount <= `wallet_daily_cap`;
   - global: sum of all top-ups over the last 24 h + amount <= `global_daily_cap`.
   Caps count `queued`, `submitted` and `confirmed` top-ups (a failed or reverted transfer moved no
   value). Every top-up is a `ChainTransaction` (signer `funder`, purpose `gas_top_up`);
3. processes the new top-ups, then resubmits the wallets' `waiting_for_gas` transactions; the
   relayer re-checks each balance and either broadcasts or leaves it waiting.

Wei amounts are stored as decimal strings (transaction args, audit data): they exceed the
2^53 integer range of RFC 8785 / JavaScript numbers.

Nothing here invents a balance or a hash: an RPC failure propagates (the Celery task retries), an
underfunded funder makes the transfer fail with the node's reason, recorded on the transaction.
Runs are serialised by an advisory lock, so two overlapping runs cannot double-fund a wallet.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, text

from aqua_chain.relayer import Relayer
from aqua_core import audit
from aqua_core.logging import get_logger
from aqua_core.models import ChainSigner, ChainTransaction, ChainTxPurpose, ChainTxStatus, OrgWallet
from aqua_core.models._types import utcnow
from aqua_core.settings import Settings

log = get_logger("aqua_chain.gas")

TOP_UP_LOCK_KEY = 0x41_51_47_53  # "AQGS"
CAP_WINDOW = timedelta(hours=24)
# Top-ups that moved (or may still move) value, counted against the caps.
CAP_STATUSES = (ChainTxStatus.QUEUED, ChainTxStatus.SUBMITTED, ChainTxStatus.CONFIRMED)
IN_FLIGHT = (ChainTxStatus.QUEUED, ChainTxStatus.SUBMITTED)
NEEDS_GAS = (ChainTxStatus.QUEUED, ChainTxStatus.WAITING_FOR_GAS)


@dataclass(frozen=True)
class TopUpPolicy:
    min_balance: int  # wei
    amount: int  # wei per top-up
    wallet_daily_cap: int  # wei per wallet per rolling 24 h
    global_daily_cap: int  # wei across all wallets per rolling 24 h

    @classmethod
    def from_settings(cls, settings: Settings) -> "TopUpPolicy":
        return cls(
            min_balance=settings.chain_org_wallet_min_balance,
            amount=settings.chain_org_wallet_top_up,
            wallet_daily_cap=settings.chain_org_wallet_daily_cap,
            global_daily_cap=settings.chain_funder_daily_cap,
        )


@dataclass
class WalletCheck:
    org_id: str
    address: str
    balance: int
    action: str  # "topped_up" | "sufficient" | "in_flight" | "wallet_cap" | "global_cap"
    top_up_tx_id: str | None = None


@dataclass
class TopUpResult:
    funder: str
    wallets: list[WalletCheck] = field(default_factory=list)
    top_ups: list[str] = field(default_factory=list)  # ChainTransaction ids created this run
    resubmitted: list[str] = field(default_factory=list)  # waiting txs now broadcast
    still_waiting: list[str] = field(default_factory=list)
    skipped: bool = False  # another run holds the lock

    def to_send(self) -> list[str]:
        """Transactions the caller should keep polling for confirmations."""
        return [*self.top_ups, *self.resubmitted]


def _value(tx: ChainTransaction) -> int:
    return int(tx.args[1])


async def top_up_org_wallets(relayer: Relayer, policy: TopUpPolicy) -> TopUpResult:
    chain_id = relayer.client.chain_id
    funder = relayer.signers.address(ChainSigner.FUNDER)
    result = TopUpResult(funder=funder)
    async with relayer.sessionmaker() as session:
        locked = await session.scalar(
            text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": TOP_UP_LOCK_KEY}
        )
        if not locked:
            result.skipped = True
            return result
        pending = list(
            await session.scalars(
                select(ChainTransaction).where(
                    ChainTransaction.chain_id == chain_id,
                    ChainTransaction.signer == ChainSigner.ORG_WALLET,
                    ChainTransaction.status.in_(NEEDS_GAS),
                )
            )
        )
        waiting_by_wallet: dict[str, list[ChainTransaction]] = defaultdict(list)
        addresses: set[str] = set()
        for tx in pending:
            addresses.add(tx.from_address.lower())
            if tx.status is ChainTxStatus.WAITING_FOR_GAS:
                waiting_by_wallet[tx.from_address.lower()].append(tx)
        wallets = (
            list(
                await session.scalars(
                    select(OrgWallet)
                    .where(func.lower(OrgWallet.address).in_(addresses))
                    .order_by(OrgWallet.derivation_index)
                )
            )
            if addresses
            else []
        )
        recent = list(
            await session.scalars(
                select(ChainTransaction).where(
                    ChainTransaction.chain_id == chain_id,
                    ChainTransaction.purpose == ChainTxPurpose.GAS_TOP_UP,
                    ChainTransaction.status.in_(CAP_STATUSES),
                    ChainTransaction.created_at >= utcnow() - CAP_WINDOW,
                )
            )
        )
        spent_global = sum(_value(t) for t in recent)
        spent: dict[str, int] = defaultdict(int)
        in_flight: set[str] = set()
        for t in recent:
            spent[t.to_address.lower()] += _value(t)
            if t.status in IN_FLIGHT:
                in_flight.add(t.to_address.lower())

        for wallet in wallets:
            key = wallet.address.lower()
            balance = await relayer.client.balance(wallet.address)
            check = WalletCheck(
                org_id=str(wallet.org_id), address=wallet.address, balance=balance, action=""
            )
            result.wallets.append(check)
            if balance >= policy.min_balance and not waiting_by_wallet.get(key):
                check.action = "sufficient"
            elif key in in_flight:
                check.action = "in_flight"
            elif spent[key] + policy.amount > policy.wallet_daily_cap:
                check.action = "wallet_cap"
            elif spent_global + policy.amount > policy.global_daily_cap:
                check.action = "global_cap"
            else:
                tx = await relayer.queue_native_transfer(
                    session,
                    purpose=ChainTxPurpose.GAS_TOP_UP,
                    entity_type="org_wallet",
                    entity_id=str(wallet.org_id),
                    signer=ChainSigner.FUNDER,
                    to_address=wallet.address,
                    value_wei=policy.amount,
                )
                await audit.record(
                    session,
                    actor_id=None,
                    action="chain.gas_top_up.queued",
                    entity_type="org_wallet",
                    entity_id=str(wallet.org_id),
                    data={
                        "chain_tx_id": str(tx.id),
                        "to": wallet.address,
                        "value_wei": str(policy.amount),
                        "balance_wei": str(balance),
                        "min_balance_wei": str(policy.min_balance),
                    },
                )
                spent[key] += policy.amount
                spent_global += policy.amount
                check.action = "topped_up"
                check.top_up_tx_id = str(tx.id)
                result.top_ups.append(str(tx.id))
            if check.action in ("wallet_cap", "global_cap"):
                log.warning(
                    "chain.gas_top_up_capped",
                    wallet=wallet.address,
                    cap=check.action,
                    balance_wei=balance,
                    spent_wallet_wei=spent[key],
                    spent_global_wei=spent_global,
                )
        await session.commit()

    for tx_id in result.top_ups:
        await relayer.process(uuid.UUID(tx_id))
    for txs in waiting_by_wallet.values():
        for tx in txs:
            status = await relayer.submit(tx.id)
            bucket = (
                result.still_waiting
                if status is ChainTxStatus.WAITING_FOR_GAS
                else result.resubmitted
            )
            bucket.append(str(tx.id))
    log.info(
        "chain.gas_top_up_run",
        wallets=len(result.wallets),
        top_ups=len(result.top_ups),
        resubmitted=len(result.resubmitted),
        still_waiting=len(result.still_waiting),
    )
    return result


def summary(result: TopUpResult) -> dict[str, Any]:
    return {
        "funder": result.funder,
        "skipped": result.skipped,
        "wallets": [vars(w) for w in result.wallets],
        "top_ups": result.top_ups,
        "resubmitted": result.resubmitted,
        "still_waiting": result.still_waiting,
    }
