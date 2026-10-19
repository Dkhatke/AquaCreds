"""Blockchain integration: enqueue anchoring transactions, inspect their status, relayer health.

Transactions are created here as `queued` rows (no RPC needed) and handed to the `chain` worker
queue by id; the worker signs, broadcasts and confirms them."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select

from aqua_api.deps import (
    CurrentUser,
    SessionDep,
    can_read_all_projects,
    org_role,
    require_platform_role,
)
from aqua_api.schemas.chain import (
    ChainEventOut,
    ChainStatusOut,
    ChainTxOut,
    ProjectChainOut,
    SignerStatus,
)
from aqua_api.services import audit
from aqua_chain import actions
from aqua_chain.client import ChainClient
from aqua_chain.encoding import project_id_bytes32, to_hex
from aqua_chain.errors import ChainError
from aqua_chain.indexer import events_for_project
from aqua_chain.relayer import Relayer
from aqua_chain.runtime import build_relayer
from aqua_chain.wallets import ChainNotConfiguredError, Signers
from aqua_core.models import (
    ChainCursor,
    ChainTransaction,
    PlatformRole,
    Project,
    ProjectStatus,
    User,
)
from aqua_core.settings import Settings

router = APIRouter(tags=["chain"])

RegistryAdmin = Annotated[User, Depends(require_platform_role(PlatformRole.REGISTRY_ADMIN))]
# "submitted or later" in the project lifecycle
REGISTRABLE_STATUSES = {
    ProjectStatus.SUBMITTED,
    ProjectStatus.BASELINE_READY,
    ProjectStatus.REGISTERED,
    ProjectStatus.MONITORING,
}


def _client(request: Request) -> ChainClient:
    factory = getattr(request.app.state, "chain_client_factory", None)
    settings: Settings = request.app.state.settings
    client: ChainClient = factory() if factory else ChainClient.from_settings(settings)
    return client


def _relayer(request: Request) -> Relayer:
    settings: Settings = request.app.state.settings
    try:
        return build_relayer(settings, request.app.state.sessionmaker, _client(request))
    except ChainNotConfiguredError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


async def _visible_project(session: SessionDep, project_id: uuid.UUID, user: User) -> Project:
    project = await session.get(Project, project_id)
    if project is None or (
        org_role(user, project.org_id) is None and not can_read_all_projects(user)
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return project


@router.post(
    "/projects/{project_id}/chain/register",
    response_model=ChainTxOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def register_project_on_chain(
    project_id: uuid.UUID, request: Request, user: RegistryAdmin, session: SessionDep
) -> ChainTransaction:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    if project.status not in REGISTRABLE_STATUSES:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Project is {project.status}; it must be submitted before on-chain registration",
        )
    relayer = _relayer(request)
    try:
        tx = await actions.queue_register_project(session, relayer, project, requested_by=user.id)
    except actions.DuplicateChainActionError as dup:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Registration already {dup.existing.status} (transaction {dup.existing.id})",
        ) from dup
    await audit.record(
        session,
        actor_id=user.id,
        action="chain.register_project.requested",
        entity_type="project",
        entity_id=project.id,
        data={"chain_tx_id": str(tx.id), "from": tx.from_address, "args": tx.args},
    )
    await session.commit()
    request.app.state.enqueue("aqua.chain.send", str(tx.id), queue="chain")
    return tx


@router.get("/chain/transactions/{tx_id}", response_model=ChainTxOut)
async def get_transaction(
    tx_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> ChainTransaction:
    tx = await session.get(ChainTransaction, tx_id)
    if tx is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Transaction not found")
    if not can_read_all_projects(user):
        project = (
            await session.get(Project, uuid.UUID(tx.entity_id))
            if tx.entity_type == "project"
            else None
        )
        if project is None or org_role(user, project.org_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Transaction not found")
    return tx


@router.get("/projects/{project_id}/chain", response_model=ProjectChainOut)
async def project_chain(
    project_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> ProjectChainOut:
    project = await _visible_project(session, project_id, user)
    key = to_hex(project_id_bytes32(project.id))
    txs = await session.scalars(
        select(ChainTransaction)
        .where(
            ChainTransaction.entity_type == "project",
            ChainTransaction.entity_id == str(project.id),
        )
        .order_by(ChainTransaction.created_at)
    )
    events = await events_for_project(session, key)
    return ProjectChainOut(
        project_id=project.id,
        project_key=key,
        registered=any(e.event == "ProjectRegistered" for e in events),
        transactions=[ChainTxOut.model_validate(t) for t in txs],
        events=[ChainEventOut.model_validate(e) for e in events],
    )


@router.get("/chain/status", response_model=ChainStatusOut)
async def chain_status(
    request: Request, user: RegistryAdmin, session: SessionDep
) -> ChainStatusOut:
    settings: Settings = request.app.state.settings
    client = _client(request)
    signers: dict[str, str] = {}
    if settings.relayer_mnemonic.get_secret_value().strip():
        signers = {
            role.value: addr for role, addr in Signers.from_settings(settings).addresses().items()
        }
    cursors = {
        c.contract: c.last_block
        for c in await session.scalars(
            select(ChainCursor).where(ChainCursor.chain_id == settings.chain_id)
        )
    }
    out = ChainStatusOut(
        configured=settings.chain_configured,
        connected=False,
        expected_chain_id=settings.chain_id,
        chain_id=None,
        latest_block=None,
        error=None,
        contracts=client.addresses,
        signers=[SignerStatus(role=r, address=a, balance_wei=None) for r, a in signers.items()],
        indexer_cursors=cursors,
    )
    try:
        out.chain_id = int(await client.w3.eth.chain_id)
        out.latest_block = await client.latest_block()
        out.connected = True
        for signer in out.signers:
            signer.balance_wei = await client.balance(signer.address)
        if out.chain_id != settings.chain_id:
            out.error = f"RPC is on chain {out.chain_id}, expected {settings.chain_id}"
    except (ChainError, OSError, TimeoutError, ValueError) as exc:
        out.error = f"{type(exc).__name__}: {str(exc)[:200]}"
    except Exception as exc:  # any provider failure is reported, not raised
        out.error = f"{type(exc).__name__}: {str(exc)[:200]}"
    finally:
        await client.close()
    return out
