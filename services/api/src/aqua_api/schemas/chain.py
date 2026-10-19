"""Schemas for blockchain transactions, indexed events and relayer status."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from aqua_core.models import ChainSigner, ChainTxPurpose, ChainTxStatus


class ChainTxOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    chain_id: int
    purpose: ChainTxPurpose
    status: ChainTxStatus
    entity_type: str
    entity_id: str
    signer: ChainSigner
    from_address: str
    to_address: str
    contract: str
    function: str
    args: list[Any]
    nonce: int | None
    tx_hash: str | None
    block_number: int | None
    gas_used: int | None
    error: str | None
    error_name: str | None
    result: dict[str, Any]
    attempts: int
    created_at: datetime
    submitted_at: datetime | None
    confirmed_at: datetime | None


class ChainEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    contract: str
    address: str
    event: str
    block_number: int
    tx_hash: str
    log_index: int
    args: dict[str, Any]


class ProjectChainOut(BaseModel):
    project_id: uuid.UUID
    project_key: str  # bytes32 id used on-chain (keccak256 of the UUID string)
    registered: bool  # a ProjectRegistered event has been indexed
    transactions: list[ChainTxOut]
    events: list[ChainEventOut]


class SignerStatus(BaseModel):
    role: ChainSigner
    address: str
    balance_wei: int | None


class ChainStatusOut(BaseModel):
    configured: bool
    connected: bool
    expected_chain_id: int
    chain_id: int | None
    latest_block: int | None
    error: str | None
    contracts: dict[str, str]
    signers: list[SignerStatus]
    indexer_cursors: dict[str, int]
