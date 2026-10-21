"""Blockchain integration state: relayer transactions, indexer cursors/events, custodial wallets.

The chain is the source of truth for registry state; these tables record what the platform sent
(and why) and a decoded, queryable copy of the contract events."""

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from aqua_core.db import Base
from aqua_core.models._types import Timestamps, UUIDPk, str_enum
from aqua_core.models.enums import ChainSigner, ChainTxPurpose, ChainTxStatus

# Statuses whose nonce is (or may be) consumed on-chain. A nonce can never be reused among these;
# a partial unique index enforces it in the database as a second line of defence.
NONCE_HOLDING_STATUSES = (
    ChainTxStatus.SUBMITTED,
    ChainTxStatus.CONFIRMED,
    ChainTxStatus.REVERTED,
)
# Not finished yet: queued, parked until its custodial wallet has gas, or broadcast.
PENDING_STATUSES = (
    ChainTxStatus.QUEUED,
    ChainTxStatus.WAITING_FOR_GAS,
    ChainTxStatus.SUBMITTED,
)
# `contract`/`function` of a plain native-token transfer (no contract call), e.g. a gas top-up.
NATIVE_CONTRACT = "native"
NATIVE_TRANSFER = "transfer"


class ChainTransaction(UUIDPk, Timestamps, Base):
    __tablename__ = "chain_transactions"
    __table_args__ = (
        sa.Index(
            "uq_chain_tx_sender_nonce",
            "chain_id",
            "from_address",
            "nonce",
            unique=True,
            postgresql_where=sa.text("status IN ('submitted', 'confirmed', 'reverted')"),
        ),
        sa.Index("ix_chain_tx_entity", "entity_type", "entity_id"),
    )

    chain_id: Mapped[int] = mapped_column(sa.BigInteger)
    purpose: Mapped[ChainTxPurpose] = mapped_column(str_enum(ChainTxPurpose, "chain_tx_purpose"))
    entity_type: Mapped[str] = mapped_column(sa.String(40))
    entity_id: Mapped[str] = mapped_column(sa.String(64))
    signer: Mapped[ChainSigner] = mapped_column(str_enum(ChainSigner, "chain_signer"))
    from_address: Mapped[str] = mapped_column(sa.String(42))
    to_address: Mapped[str] = mapped_column(sa.String(42))
    contract: Mapped[str] = mapped_column(sa.String(40))
    function: Mapped[str] = mapped_column(sa.String(80))
    args: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    status: Mapped[ChainTxStatus] = mapped_column(
        str_enum(ChainTxStatus, "chain_tx_status"), default=ChainTxStatus.QUEUED, index=True
    )
    nonce: Mapped[int | None] = mapped_column(sa.BigInteger)
    tx_hash: Mapped[str | None] = mapped_column(sa.String(66), unique=True)
    # Signed payload, persisted before broadcast so a lost RPC response can be re-broadcast with
    # the same nonce instead of signing a second, duplicate transaction.
    raw_tx: Mapped[str | None] = mapped_column(sa.Text)
    gas_limit: Mapped[int | None] = mapped_column(sa.BigInteger)
    max_fee_per_gas: Mapped[int | None] = mapped_column(sa.BigInteger)  # wei (capped by config)
    block_number: Mapped[int | None] = mapped_column(sa.BigInteger)
    block_hash: Mapped[str | None] = mapped_column(sa.String(66))
    gas_used: Mapped[int | None] = mapped_column(sa.BigInteger)
    error: Mapped[str | None] = mapped_column(sa.Text)
    error_name: Mapped[str | None] = mapped_column(sa.String(80))  # decoded custom error
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # decoded event values
    attempts: Mapped[int] = mapped_column(default=0)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    submitted_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class ChainCursor(Base):
    """Last block the indexer fully processed for a contract."""

    __tablename__ = "chain_cursors"

    chain_id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    address: Mapped[str] = mapped_column(sa.String(42), primary_key=True)
    contract: Mapped[str] = mapped_column(sa.String(40))
    last_block: Mapped[int] = mapped_column(sa.BigInteger)
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()
    )


class ChainEvent(Base):
    """A decoded contract log. (chain, tx hash, log index) is unique, so re-indexing is
    idempotent. `block_time` is the timestamp of the block (`block_hash`) the log was mined in."""

    __tablename__ = "chain_events"
    __table_args__ = (
        sa.UniqueConstraint("chain_id", "tx_hash", "log_index", name="uq_chain_events_log"),
        sa.Index("ix_chain_events_block", "chain_id", "address", "block_number"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(), primary_key=True)
    chain_id: Mapped[int] = mapped_column(sa.BigInteger)
    contract: Mapped[str] = mapped_column(sa.String(40))
    address: Mapped[str] = mapped_column(sa.String(42))
    event: Mapped[str] = mapped_column(sa.String(80), index=True)
    block_number: Mapped[int] = mapped_column(sa.BigInteger)
    block_hash: Mapped[str] = mapped_column(sa.String(66))
    tx_hash: Mapped[str] = mapped_column(sa.String(66), index=True)
    log_index: Mapped[int] = mapped_column(sa.Integer)
    block_time: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    # bytes32 project id (0x-hex) when the event carries one, for per-project lookups.
    project_key: Mapped[str | None] = mapped_column(sa.String(66), index=True)
    args: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )


class OrgWallet(Base):
    """Custodial address of an organisation: derived from the relayer mnemonic at
    `derivation_index`. Only the index and address are stored, never key material."""

    __tablename__ = "org_wallets"

    org_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("organizations.id", ondelete="RESTRICT"), primary_key=True
    )
    derivation_index: Mapped[int] = mapped_column(sa.Integer, unique=True)
    address: Mapped[str] = mapped_column(sa.String(42), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
