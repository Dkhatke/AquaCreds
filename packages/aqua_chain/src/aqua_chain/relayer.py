"""Transaction relayer: queue → sign → broadcast → confirm, with database-coordinated nonces.

Lifecycle (`ChainTxStatus`):

    queued ──estimate reverts / node rejects──▶ failed      (no nonce consumed, no hash invented)
       │
       └─sign + persist (write-ahead) ─▶ submitted ──receipt.status=1, N confirmations──▶ confirmed
                                            └──────receipt.status=0, N confirmations──▶ reverted

Nonce safety: allocation runs under a per-(chain, sender) Postgres advisory lock and takes
max(node pending nonce, highest nonce the DB holds for that sender + 1). The signed transaction
(nonce, hash, raw bytes) is committed *before* broadcast, so a lost RPC response is retried by
re-broadcasting the same bytes and can never produce a second transaction with a fresh nonce. A
partial unique index on (chain_id, from_address, nonce) backs this up in the database.

Custodial organisation wallets: a transaction queued with `org_wallet=` is signed by that
organisation's HD-derived wallet (e.g. a forward-fund offer, where the org wallet is the on-chain
project owner). Its `signer` is `org_wallet` and `from_address` is the org wallet; the key is
derived from its `OrgWallet` row at submit time. Platform-role transactions never fall back to an
org wallet key, and org-wallet transactions never use a platform-role key.
"""

from dataclasses import dataclass
from typing import Any

from eth_account.signers.local import LocalAccount
from eth_typing import HexStr
from eth_utils.crypto import keccak
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from web3.exceptions import (
    ContractLogicError,
    ProviderConnectionError,
    TransactionNotFound,
    Web3RPCError,
)
from web3.types import Nonce, TxParams, Wei

from aqua_chain import artifacts
from aqua_chain.client import GWEI, ChainClient
from aqua_chain.encoding import from_json_args
from aqua_chain.errors import ChainError, decode_revert
from aqua_chain.events import EventDecoder
from aqua_chain.wallets import Signers
from aqua_core import audit
from aqua_core.logging import get_logger
from aqua_core.models import (
    ChainSigner,
    ChainTransaction,
    ChainTxPurpose,
    ChainTxStatus,
    OrgWallet,
)
from aqua_core.models._types import utcnow
from aqua_core.models.chain import NONCE_HOLDING_STATUSES
from aqua_core.settings import Settings

log = get_logger("aqua_chain.relayer")

ALLOWED_TRANSITIONS: dict[ChainTxStatus, frozenset[ChainTxStatus]] = {
    ChainTxStatus.QUEUED: frozenset({ChainTxStatus.SUBMITTED, ChainTxStatus.FAILED}),
    ChainTxStatus.SUBMITTED: frozenset(
        {ChainTxStatus.CONFIRMED, ChainTxStatus.REVERTED, ChainTxStatus.FAILED}
    ),
    ChainTxStatus.CONFIRMED: frozenset(),
    ChainTxStatus.FAILED: frozenset(),
    ChainTxStatus.REVERTED: frozenset(),
}
TERMINAL_STATUSES = frozenset(s for s, nxt in ALLOWED_TRANSITIONS.items() if not nxt)
# Errors from the node meaning "this exact transaction is already in the pool".
_ALREADY_KNOWN = ("already known", "known transaction", "already imported")
_TRANSIENT_RPC = (OSError, TimeoutError, ProviderConnectionError)


class InvalidTransitionError(ChainError):
    pass


def transition(tx: ChainTransaction, new: ChainTxStatus) -> None:
    if new not in ALLOWED_TRANSITIONS[tx.status]:
        raise InvalidTransitionError(f"chain tx {tx.id}: {tx.status} → {new} is not allowed")
    tx.status = new


def nonce_lock_key(chain_id: int, address: str) -> int:
    """Signed 64-bit advisory-lock key for one sender on one chain."""
    digest = keccak(text=f"aqua-nonce:{chain_id}:{address.lower()}")[:8]
    return int.from_bytes(digest, "big", signed=True)


@dataclass(frozen=True)
class RelayerPolicy:
    confirmations: int
    gas_limit_cap: int
    gas_price_cap_wei: int
    priority_fee_wei: int
    gas_multiplier: float = 1.2

    @classmethod
    def from_settings(cls, settings: Settings) -> "RelayerPolicy":
        return cls(
            confirmations=settings.chain_confirmations,
            gas_limit_cap=settings.chain_gas_limit_cap,
            gas_price_cap_wei=int(settings.chain_gas_price_cap_gwei * GWEI),
            priority_fee_wei=int(settings.chain_priority_fee_gwei * GWEI),
        )


@dataclass
class Relayer:
    sessionmaker: async_sessionmaker[AsyncSession]
    client: ChainClient
    signers: Signers
    policy: RelayerPolicy

    # ------------------------------------------------------------ queueing (no RPC needed)

    async def queue(
        self,
        session: AsyncSession,
        *,
        purpose: ChainTxPurpose,
        entity_type: str,
        entity_id: str,
        signer: ChainSigner,
        contract: str,
        function: str,
        args: list[Any],
        requested_by: Any = None,
        org_wallet: OrgWallet | None = None,
    ) -> ChainTransaction:
        """Create a `queued` transaction in the caller's session (caller commits). With
        `org_wallet`, the transaction is signed by that custodial organisation wallet."""
        from_json_args(artifacts.function_abi(contract, function)["inputs"], args)  # validate
        if (org_wallet is not None) != (signer == ChainSigner.ORG_WALLET):
            raise ChainError("signer 'org_wallet' requires org_wallet=, and only it may use one")
        if org_wallet is not None:
            derived = self.signers.derive(org_wallet.derivation_index).address
            if derived.lower() != org_wallet.address.lower():
                raise ChainError(
                    f"org wallet {org_wallet.address} does not match derivation index "
                    f"{org_wallet.derivation_index}"
                )
        tx = ChainTransaction(
            chain_id=self.client.chain_id,
            purpose=purpose,
            entity_type=entity_type,
            entity_id=entity_id,
            signer=signer,
            from_address=org_wallet.address if org_wallet else self.signers.address(signer),
            to_address=self.client.address_of(contract),
            contract=contract,
            function=function,
            args=args,
            status=ChainTxStatus.QUEUED,
            requested_by=requested_by,
            result={},
            attempts=0,
        )
        session.add(tx)
        await session.flush()
        return tx

    # ------------------------------------------------------------ processing

    async def process(self, tx_id: Any) -> ChainTxStatus:
        """Submit if queued, then check confirmations. Returns the resulting status."""
        status = await self.submit(tx_id)
        if status is ChainTxStatus.SUBMITTED:
            status = await self.confirm(tx_id)
        return status

    async def submit(self, tx_id: Any) -> ChainTxStatus:
        async with self.sessionmaker() as session:
            tx = await session.get(ChainTransaction, tx_id, with_for_update=True)
            if tx is None:
                raise LookupError(f"chain transaction {tx_id} not found")
            if tx.status is not ChainTxStatus.QUEUED:
                return tx.status
            account, problem = await self._signing_account(session, tx)
            if problem or account is None:
                await self._finish(session, tx, ChainTxStatus.FAILED, error=problem)
                await session.commit()
                return ChainTxStatus.FAILED

            contract = self.client.contract(tx.contract)
            inputs = artifacts.function_abi(tx.contract, tx.function)["inputs"]
            fn = contract.get_function_by_name(tx.function)(*from_json_args(inputs, tx.args))
            tx.attempts += 1
            try:
                estimate = await fn.estimate_gas({"from": account.address})
            except ContractLogicError as exc:
                decoded = decode_revert(exc.data)
                await self._finish(
                    session,
                    tx,
                    ChainTxStatus.FAILED,
                    error=(decoded.describe() if decoded else str(exc))
                    + " (rejected during gas estimation; not broadcast)",
                    error_name=decoded.name if decoded else None,
                )
                await session.commit()
                return ChainTxStatus.FAILED
            if estimate > self.policy.gas_limit_cap:
                await self._finish(
                    session,
                    tx,
                    ChainTxStatus.FAILED,
                    error=f"gas estimate {estimate} exceeds the cap {self.policy.gas_limit_cap}",
                )
                await session.commit()
                return ChainTxStatus.FAILED
            gas_limit = min(int(estimate * self.policy.gas_multiplier), self.policy.gas_limit_cap)
            fees = await self.client.fee_quote(
                cap_wei=self.policy.gas_price_cap_wei, priority_wei=self.policy.priority_fee_wei
            )  # GasPriceTooHighError → rollback; tx stays queued and is retried later

            await session.execute(
                text("SELECT pg_advisory_xact_lock(:k)"),
                {"k": nonce_lock_key(tx.chain_id, tx.from_address)},
            )
            nonce = await self._next_nonce(session, tx)
            params: TxParams = {
                "from": account.address,
                "chainId": tx.chain_id,
                "nonce": Nonce(nonce),
                "gas": gas_limit,
                "maxFeePerGas": Wei(fees.max_fee_per_gas),
                "maxPriorityFeePerGas": Wei(fees.max_priority_fee_per_gas),
                "value": Wei(0),
            }
            built = await fn.build_transaction(params)
            signed = account.sign_transaction(built)
            tx.nonce = nonce
            tx.tx_hash = signed.hash.to_0x_hex()
            raw = signed.raw_transaction.to_0x_hex()
            tx.raw_tx = raw
            tx.gas_limit = gas_limit
            tx.max_fee_per_gas = fees.max_fee_per_gas
            tx.submitted_at = utcnow()
            transition(tx, ChainTxStatus.SUBMITTED)
            await self._audit(session, tx)
            await session.commit()  # write-ahead: persisted before it leaves the process

        return await self._broadcast(tx_id, raw)

    async def confirm(self, tx_id: Any) -> ChainTxStatus:
        w3 = self.client.w3
        async with self.sessionmaker() as session:
            tx = await session.get(ChainTransaction, tx_id, with_for_update=True)
            if tx is None:
                raise LookupError(f"chain transaction {tx_id} not found")
            if tx.status is not ChainTxStatus.SUBMITTED or tx.tx_hash is None:
                return tx.status
            try:
                receipt = await w3.eth.get_transaction_receipt(tx.tx_hash)  # type: ignore[arg-type]
            except TransactionNotFound:
                await self._ensure_in_pool(session, tx)
                await session.commit()
                return tx.status

            mined_in = int(receipt["blockNumber"])
            tx.block_number = mined_in
            tx.block_hash = receipt["blockHash"].to_0x_hex()
            tx.gas_used = int(receipt["gasUsed"])
            if await self.client.latest_block() - mined_in + 1 < self.policy.confirmations:
                await session.commit()
                return tx.status

            if int(receipt["status"]) == 1:
                decoder = EventDecoder(self.client)
                events = [d for d in (decoder.decode(lg) for lg in receipt["logs"]) if d]
                tx.result = {"events": [{"event": e.event, "args": e.args} for e in events]}
                tx.confirmed_at = utcnow()
                await self._finish(session, tx, ChainTxStatus.CONFIRMED)
            else:
                decoded = await self._replay_revert(tx, mined_in)
                await self._finish(
                    session,
                    tx,
                    ChainTxStatus.REVERTED,
                    error=decoded.describe()
                    if decoded
                    else "reverted on-chain (reason unavailable)",
                    error_name=decoded.name if decoded else None,
                )
            await session.commit()
            return tx.status

    # ------------------------------------------------------------ internals

    async def _signing_account(
        self, session: AsyncSession, tx: ChainTransaction
    ) -> tuple[LocalAccount | None, str | None]:
        """The key that signs `tx`: its platform role's, or (when `from_address` is a custodial
        organisation wallet) that wallet's. Returns (None, problem) on a configuration mismatch."""
        if tx.chain_id != self.client.chain_id:
            return (
                None,
                f"queued for chain {tx.chain_id}, relayer is on chain {self.client.chain_id}",
            )
        if self.client.address_of(tx.contract).lower() != tx.to_address.lower():
            return None, f"{tx.contract} address changed since the transaction was queued"
        if tx.signer != ChainSigner.ORG_WALLET:
            role_account = self.signers.account(tx.signer)
            if role_account.address.lower() == tx.from_address.lower():
                return role_account, None
            return None, f"{tx.signer} key no longer derives {tx.from_address}"
        wallet = await session.scalar(
            select(OrgWallet).where(func.lower(OrgWallet.address) == tx.from_address.lower())
        )
        if wallet is None:
            return None, f"no org wallet {tx.from_address}"
        account = self.signers.derive(wallet.derivation_index)
        if account.address.lower() == tx.from_address.lower():
            return account, None
        return (
            None,
            f"org wallet index {wallet.derivation_index} no longer derives {tx.from_address}",
        )

    async def _next_nonce(self, session: AsyncSession, tx: ChainTransaction) -> int:
        chain_nonce = await self.client.pending_nonce(tx.from_address)
        db_max = await session.scalar(
            select(func.max(ChainTransaction.nonce)).where(
                ChainTransaction.chain_id == tx.chain_id,
                func.lower(ChainTransaction.from_address) == tx.from_address.lower(),
                ChainTransaction.status.in_(NONCE_HOLDING_STATUSES),
            )
        )
        return chain_nonce if db_max is None else max(chain_nonce, int(db_max) + 1)

    async def _broadcast(self, tx_id: Any, raw: str) -> ChainTxStatus:
        try:
            await self.client.w3.eth.send_raw_transaction(HexStr(raw))
        except Web3RPCError as exc:
            if any(marker in str(exc).lower() for marker in _ALREADY_KNOWN):
                return ChainTxStatus.SUBMITTED
            return await self._rejected(tx_id, f"rejected by the node: {exc}")
        except _TRANSIENT_RPC as exc:
            # Unknown outcome: keep `submitted`; confirm() re-broadcasts the same signed bytes.
            log.warning("chain.broadcast_unknown", tx_id=str(tx_id), error=str(exc)[:200])
        return ChainTxStatus.SUBMITTED

    async def _rejected(self, tx_id: Any, error: str) -> ChainTxStatus:
        async with self.sessionmaker() as session:
            tx = await session.get(ChainTransaction, tx_id, with_for_update=True)
            if tx is None:
                raise LookupError(f"chain transaction {tx_id} not found")
            if tx.status is ChainTxStatus.SUBMITTED and not await self._known(tx):
                # Never reached the pool: its nonce is free again (FAILED does not hold a nonce).
                await self._finish(session, tx, ChainTxStatus.FAILED, error=error)
                await session.commit()
            return tx.status

    async def _known(self, tx: ChainTransaction) -> bool:
        try:
            await self.client.w3.eth.get_transaction(tx.tx_hash)  # type: ignore[arg-type]
            return True
        except TransactionNotFound:
            return False

    async def _ensure_in_pool(self, session: AsyncSession, tx: ChainTransaction) -> None:
        """Not mined yet: if the node has forgotten it (or never got it), re-broadcast."""
        if await self._known(tx):
            return
        try:
            await self.client.w3.eth.send_raw_transaction(tx.raw_tx)  # type: ignore[arg-type]
            log.info("chain.rebroadcast", tx_id=str(tx.id), tx_hash=tx.tx_hash)
        except Web3RPCError as exc:
            message = str(exc).lower()
            if any(marker in message for marker in _ALREADY_KNOWN):
                return
            if "nonce too low" in message:
                await self._finish(
                    session,
                    tx,
                    ChainTxStatus.FAILED,
                    error="nonce was consumed by a different transaction; this one was dropped",
                )
                return
            raise

    async def _replay_revert(self, tx: ChainTransaction, block: int) -> Any:
        """Best effort: re-run the call against the block's state to recover the revert data."""
        w3 = self.client.w3
        try:
            original = await w3.eth.get_transaction(tx.tx_hash)  # type: ignore[arg-type]
            await w3.eth.call(
                {
                    "from": original["from"],
                    "to": original["to"],
                    "data": original["input"],
                    "gas": original["gas"],
                },
                block_identifier=block,
            )
        except ContractLogicError as exc:
            return decode_revert(exc.data)
        except Exception as exc:  # replay is diagnostic only
            log.warning("chain.replay_failed", tx_id=str(tx.id), error=str(exc)[:200])
        return None

    async def _finish(
        self,
        session: AsyncSession,
        tx: ChainTransaction,
        status: ChainTxStatus,
        *,
        error: str | None = None,
        error_name: str | None = None,
    ) -> None:
        transition(tx, status)
        tx.error = error
        tx.error_name = error_name
        await self._audit(session, tx)
        log.info(
            "chain.tx_status",
            tx_id=str(tx.id),
            status=status.value,
            function=tx.function,
            tx_hash=tx.tx_hash,
            error=error,
        )

    async def _audit(self, session: AsyncSession, tx: ChainTransaction) -> None:
        await audit.record(
            session,
            actor_id=None,
            action=f"chain.tx.{tx.status.value}",
            entity_type=tx.entity_type,
            entity_id=tx.entity_id,
            data={
                "chain_tx_id": str(tx.id),
                "function": f"{tx.contract}.{tx.function}",
                "from": tx.from_address,
                "nonce": tx.nonce,
                "tx_hash": tx.tx_hash,
                "block_number": tx.block_number,
                "error": tx.error,
            },
        )
