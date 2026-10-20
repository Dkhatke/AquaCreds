"""Domain-level transaction builders: they translate platform records into contract calls and queue
them with the right signer. Separation of duties is fixed here: the registrar registers projects
and submits reports, the verifier approves/rejects, the issuer mints."""

import uuid
from typing import Any

from geoalchemy2.shape import to_shape
from shapely.geometry import mapping
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aqua_chain.encoding import geojson_hash, hex_to_bytes32, project_id_bytes32, to_hex
from aqua_chain.relayer import Relayer
from aqua_chain.wallets import ensure_org_wallet
from aqua_core.models import (
    ChainSigner,
    ChainTransaction,
    ChainTxPurpose,
    ChainTxStatus,
    Project,
)

ACTIVE_STATUSES = (ChainTxStatus.QUEUED, ChainTxStatus.SUBMITTED, ChainTxStatus.CONFIRMED)
CREDIT_TYPES = {"temporary": 0, "long_term": 1}
METADATA_URI_PREFIX = "urn:aquacreds:project:"


class DuplicateChainActionError(RuntimeError):
    def __init__(self, existing: ChainTransaction) -> None:
        super().__init__(f"{existing.purpose} already {existing.status} as {existing.id}")
        self.existing = existing


async def active_transaction(
    session: AsyncSession, purpose: ChainTxPurpose, entity_type: str, entity_id: str
) -> ChainTransaction | None:
    found: ChainTransaction | None = await session.scalar(
        select(ChainTransaction)
        .where(
            ChainTransaction.purpose == purpose,
            ChainTransaction.entity_type == entity_type,
            ChainTransaction.entity_id == entity_id,
            ChainTransaction.status.in_(ACTIVE_STATUSES),
        )
        .order_by(ChainTransaction.created_at.desc())
        .limit(1)
    )
    return found


def project_boundary_hash(project: Project) -> bytes:
    return geojson_hash(mapping(to_shape(project.boundary)))


async def queue_register_project(
    session: AsyncSession, relayer: Relayer, project: Project, requested_by: uuid.UUID | None = None
) -> ChainTransaction:
    """registerProject(projectId, owner=org custodial wallet, boundaryHash, startYear, uri)."""
    existing = await active_transaction(
        session, ChainTxPurpose.REGISTER_PROJECT, "project", str(project.id)
    )
    if existing is not None:
        raise DuplicateChainActionError(existing)
    wallet = await ensure_org_wallet(session, relayer.signers, project.org_id)
    return await relayer.queue(
        session,
        purpose=ChainTxPurpose.REGISTER_PROJECT,
        entity_type="project",
        entity_id=str(project.id),
        signer=ChainSigner.REGISTRAR,
        contract="AquaRegistry",
        function="registerProject",
        args=[
            to_hex(project_id_bytes32(project.id)),
            wallet.address,
            to_hex(project_boundary_hash(project)),
            project.start_date.year,
            f"{METADATA_URI_PREFIX}{project.id}",
        ],
        requested_by=requested_by,
    )


async def queue_submit_report(
    session: AsyncSession,
    relayer: Relayer,
    *,
    project_id: uuid.UUID,
    period_end: int,
    report_hash: str,
    lccc: int,
    evidence_uri: str,
    entity_type: str = "project",
    entity_id: str | None = None,
    requested_by: uuid.UUID | None = None,
) -> ChainTransaction:
    """submitReport(projectId, periodEnd, reportHash, lccc, evidenceURI). `lccc` is whole tCO2e
    from the carbon engine (negative = reversal)."""
    return await relayer.queue(
        session,
        purpose=ChainTxPurpose.SUBMIT_REPORT,
        entity_type=entity_type,
        entity_id=entity_id or str(project_id),
        signer=ChainSigner.REGISTRAR,
        contract="AquaRegistry",
        function="submitReport",
        args=[
            to_hex(project_id_bytes32(project_id)),
            period_end,
            to_hex(hex_to_bytes32(report_hash)),
            lccc,
            evidence_uri,
        ],
        requested_by=requested_by,
    )


async def queue_approve_report(
    session: AsyncSession,
    relayer: Relayer,
    *,
    report_id: int,
    entity_type: str,
    entity_id: str,
    requested_by: uuid.UUID | None = None,
) -> ChainTransaction:
    return await relayer.queue(
        session,
        purpose=ChainTxPurpose.APPROVE_REPORT,
        entity_type=entity_type,
        entity_id=entity_id,
        signer=ChainSigner.VERIFIER,
        contract="AquaRegistry",
        function="approveReport",
        args=[report_id],
        requested_by=requested_by,
    )


async def queue_reject_report(
    session: AsyncSession,
    relayer: Relayer,
    *,
    report_id: int,
    reason_hash: str,
    entity_type: str,
    entity_id: str,
    requested_by: uuid.UUID | None = None,
) -> ChainTransaction:
    return await relayer.queue(
        session,
        purpose=ChainTxPurpose.REJECT_REPORT,
        entity_type=entity_type,
        entity_id=entity_id,
        signer=ChainSigner.VERIFIER,
        contract="AquaRegistry",
        function="rejectReport",
        args=[report_id, to_hex(hex_to_bytes32(reason_hash))],
        requested_by=requested_by,
    )


async def queue_issue_credits(
    session: AsyncSession,
    relayer: Relayer,
    *,
    report_id: int,
    credit_type: str,
    org_id: uuid.UUID,
    amount: int,
    expires_at: int,
    entity_type: str,
    entity_id: str,
    requested_by: uuid.UUID | None = None,
) -> ChainTransaction:
    """issue(reportId, creditType, to=org custodial wallet, amount, expiresAt). The contract caps
    the amount at the verifier-approved quantity."""
    if credit_type not in CREDIT_TYPES:
        raise ValueError(f"credit_type must be one of {sorted(CREDIT_TYPES)}")
    if amount <= 0:
        raise ValueError("amount must be positive")
    wallet = await ensure_org_wallet(session, relayer.signers, org_id)
    return await relayer.queue(
        session,
        purpose=ChainTxPurpose.ISSUE_CREDITS,
        entity_type=entity_type,
        entity_id=entity_id,
        signer=ChainSigner.ISSUER,
        contract="AquaCredit",
        function="issue",
        args=[report_id, CREDIT_TYPES[credit_type], wallet.address, amount, expires_at],
        requested_by=requested_by,
    )


def event_value(tx: ChainTransaction, event: str, arg: str) -> Any:
    """A value from a decoded event in a confirmed transaction's receipt (e.g. the reportId)."""
    for item in tx.result.get("events", []):
        if item.get("event") == event and arg in item.get("args", {}):
            return item["args"][arg]
    raise LookupError(f"{event}.{arg} not found in chain tx {tx.id}")
