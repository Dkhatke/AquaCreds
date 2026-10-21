"""Celery tasks for the blockchain relayer and indexer (queue `chain`).

The `chain` queue must run with concurrency 1 so transactions from one signer leave in nonce
order. Nonce *safety* does not depend on it: allocation is serialised by a Postgres advisory lock
in `aqua_chain.relayer`, so a misconfigured concurrency can delay but never duplicate a nonce.

Pattern: async core functions take their dependencies (relayer / client / sessionmaker) so tests
drive them directly against Anvil; the Celery wrappers only wire settings and retries.

Indexer schedule: Celery beat runs `aqua.chain.index_events` every CHAIN_INDEX_INTERVAL_S (30 s)
when the chain is configured (see `aqua_worker.celery_app.beat_schedule`). By hand:
    celery -A aqua_worker.celery_app call aqua.chain.index_events --queue chain

Gas top-ups: beat runs `aqua.chain.top_up_org_wallets` every CHAIN_TOP_UP_INTERVAL_S (5 min): org
wallets with queued / waiting-for-gas transactions below CHAIN_ORG_WALLET_MIN_BALANCE get
CHAIN_ORG_WALLET_TOP_UP wei from the funder account, within daily caps (`aqua_chain.gas`).
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Any

from celery import shared_task
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aqua_chain import actions
from aqua_chain.client import ChainClient
from aqua_chain.errors import RetryableChainError
from aqua_chain.gas import TopUpPolicy, summary, top_up_org_wallets
from aqua_chain.indexer import IndexerConfig, index_events
from aqua_chain.relayer import Relayer
from aqua_chain.runtime import build_relayer, indexer_config
from aqua_core import audit
from aqua_core.db import create_engine, session_factory
from aqua_core.logging import get_logger
from aqua_core.models import ChainTransaction, ChainTxStatus, Project
from aqua_core.settings import get_settings

CONFIRM_POLL_S = 15
CONFIRM_MAX_POLLS = 240  # ~1 h at 15 s; Amoy usually confirms within a minute
_TRANSIENT = (ConnectionError, TimeoutError, OSError, RetryableChainError)


class AwaitingConfirmationError(Exception):
    """The transaction is broadcast but does not have enough confirmations yet."""


# ---------------------------------------------------------------- core (dependency-injected)


def _tx_summary(tx: ChainTransaction) -> dict[str, Any]:
    return {
        "id": str(tx.id),
        "status": tx.status.value,
        "tx_hash": tx.tx_hash,
        "error": tx.error,
        "error_name": tx.error_name,
    }


async def _queue_and_process(
    relayer: Relayer,
    build: Callable[[AsyncSession], Awaitable[ChainTransaction]],
) -> dict[str, Any]:
    async with relayer.sessionmaker() as session:
        tx = await build(session)
        await audit.record(
            session,
            actor_id=None,
            action=f"chain.{tx.purpose.value}.queued",
            entity_type=tx.entity_type,
            entity_id=tx.entity_id,
            data={"chain_tx_id": str(tx.id)},
        )
        await session.commit()
        tx_id = tx.id
    await relayer.process(tx_id)
    return await transaction_summary(relayer.sessionmaker, tx_id)


async def transaction_summary(
    sessionmaker: async_sessionmaker[AsyncSession], tx_id: uuid.UUID
) -> dict[str, Any]:
    async with sessionmaker() as session:
        tx = await session.get(ChainTransaction, tx_id)
        if tx is None:
            raise LookupError(f"chain transaction {tx_id} not found")
        return _tx_summary(tx)


async def register_project_core(relayer: Relayer, project_id: uuid.UUID) -> dict[str, Any]:
    async def build(session: AsyncSession) -> ChainTransaction:
        project = await session.get(Project, project_id)
        if project is None:
            raise LookupError(f"project {project_id} not found")
        try:
            return await actions.queue_register_project(session, relayer, project)
        except actions.DuplicateChainActionError as dup:
            return dup.existing

    return await _queue_and_process(relayer, build)


async def submit_report_core(
    relayer: Relayer,
    *,
    project_id: uuid.UUID,
    period_end: int,
    report_hash: str,
    lccc: int,
    evidence_uri: str,
    entity_type: str = "project",
    entity_id: str | None = None,
) -> dict[str, Any]:
    async def build(session: AsyncSession) -> ChainTransaction:
        return await actions.queue_submit_report(
            session,
            relayer,
            project_id=project_id,
            period_end=period_end,
            report_hash=report_hash,
            lccc=lccc,
            evidence_uri=evidence_uri,
            entity_type=entity_type,
            entity_id=entity_id,
        )

    return await _queue_and_process(relayer, build)


async def approve_report_core(
    relayer: Relayer, *, report_id: int, entity_type: str, entity_id: str
) -> dict[str, Any]:
    async def build(session: AsyncSession) -> ChainTransaction:
        return await actions.queue_approve_report(
            session, relayer, report_id=report_id, entity_type=entity_type, entity_id=entity_id
        )

    return await _queue_and_process(relayer, build)


async def issue_credits_core(
    relayer: Relayer,
    *,
    report_id: int,
    credit_type: str,
    org_id: uuid.UUID,
    amount: int,
    expires_at: int,
    entity_type: str,
    entity_id: str,
) -> dict[str, Any]:
    async def build(session: AsyncSession) -> ChainTransaction:
        return await actions.queue_issue_credits(
            session,
            relayer,
            report_id=report_id,
            credit_type=credit_type,
            org_id=org_id,
            amount=amount,
            expires_at=expires_at,
            entity_type=entity_type,
            entity_id=entity_id,
        )

    return await _queue_and_process(relayer, build)


async def send_core(relayer: Relayer, tx_id: uuid.UUID) -> dict[str, Any]:
    await relayer.process(tx_id)
    return await transaction_summary(relayer.sessionmaker, tx_id)


async def index_events_core(
    sessionmaker: async_sessionmaker[AsyncSession], client: ChainClient, config: IndexerConfig
) -> dict[str, Any]:
    await client.check()
    return asdict(await index_events(sessionmaker, client, config))


async def top_up_org_wallets_core(relayer: Relayer, policy: TopUpPolicy) -> dict[str, Any]:
    return summary(await top_up_org_wallets(relayer, policy))


# ---------------------------------------------------------------- Celery wiring


async def _with_relayer(run: Callable[[Relayer], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    relayer = build_relayer(settings, session_factory(engine))
    try:
        await relayer.client.check()
        return await run(relayer)
    finally:
        await relayer.client.close()
        await engine.dispose()


def _run_or_wait(run: Callable[[Relayer], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
    """Run, and if the transaction is still `submitted`, poll again later via `aqua.chain.send`
    (re-running the build step would queue a duplicate)."""
    result = asyncio.run(_with_relayer(run))
    if result["status"] == ChainTxStatus.SUBMITTED.value:
        send.apply_async(args=[result["id"]], countdown=CONFIRM_POLL_S, queue="chain")
    return result


_TASK_OPTIONS: dict[str, Any] = {
    "autoretry_for": _TRANSIENT,
    "retry_backoff": True,
    "retry_backoff_max": 300,
    "max_retries": 8,
}


@shared_task(name="aqua.chain.register_project", **_TASK_OPTIONS)
def register_project(project_id: str) -> dict[str, Any]:
    pid = uuid.UUID(project_id)
    return _run_or_wait(lambda r: register_project_core(r, pid))


@shared_task(name="aqua.chain.submit_report", **_TASK_OPTIONS)
def submit_report(
    project_id: str,
    period_end: int | str,
    report_hash: str,
    lccc: int | str,
    evidence_uri: str,
    entity_type: str = "project",
    entity_id: str | None = None,
) -> dict[str, Any]:
    return _run_or_wait(
        lambda r: submit_report_core(
            r,
            project_id=uuid.UUID(project_id),
            period_end=int(period_end),
            report_hash=report_hash,
            lccc=int(lccc),
            evidence_uri=evidence_uri,
            entity_type=entity_type,
            entity_id=entity_id,
        ),
    )


@shared_task(name="aqua.chain.approve_report", **_TASK_OPTIONS)
def approve_report(report_id: int | str, entity_type: str, entity_id: str) -> dict[str, Any]:
    return _run_or_wait(
        lambda r: approve_report_core(
            r, report_id=int(report_id), entity_type=entity_type, entity_id=entity_id
        ),
    )


@shared_task(name="aqua.chain.issue_credits", **_TASK_OPTIONS)
def issue_credits(
    report_id: int | str,
    credit_type: str,
    org_id: str,
    amount: int | str,
    expires_at: int | str,
    entity_type: str,
    entity_id: str,
) -> dict[str, Any]:
    return _run_or_wait(
        lambda r: issue_credits_core(
            r,
            report_id=int(report_id),
            credit_type=credit_type,
            org_id=uuid.UUID(org_id),
            amount=int(amount),
            expires_at=int(expires_at),
            entity_type=entity_type,
            entity_id=entity_id,
        ),
    )


@shared_task(
    name="aqua.chain.send",
    bind=True,
    autoretry_for=_TRANSIENT,
    retry_backoff=True,
    retry_backoff_max=300,
    max_retries=CONFIRM_MAX_POLLS,
)
def send(self: Any, tx_id: str) -> dict[str, Any]:
    """Generic: submit a queued transaction and/or wait for its confirmations."""
    tid = uuid.UUID(tx_id)
    result = asyncio.run(_with_relayer(lambda r: send_core(r, tid)))
    if result["status"] == ChainTxStatus.SUBMITTED.value:
        raise self.retry(exc=AwaitingConfirmationError(tx_id), countdown=CONFIRM_POLL_S)
    return result


async def _index() -> dict[str, Any]:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    client = ChainClient.from_settings(settings)
    try:
        return await index_events_core(session_factory(engine), client, indexer_config(settings))
    finally:
        await client.close()
        await engine.dispose()


@shared_task(name="aqua.chain.index_events", **_TASK_OPTIONS)
def index_events_task() -> dict[str, Any]:
    if not get_settings().chain_configured:
        get_logger("aqua_worker.chain").info("chain.index.skipped", reason="chain not configured")
        return {"skipped": "chain not configured"}
    return asyncio.run(_index())


@shared_task(name="aqua.chain.top_up_org_wallets", **_TASK_OPTIONS)
def top_up_org_wallets_task() -> dict[str, Any]:
    settings = get_settings()
    if not settings.chain_configured:
        get_logger("aqua_worker.chain").info("chain.top_up.skipped", reason="chain not configured")
        return {"skipped": "chain not configured"}
    policy = TopUpPolicy.from_settings(settings)
    result = asyncio.run(_with_relayer(lambda r: top_up_org_wallets_core(r, policy)))
    # Top-ups and resubmitted transactions still need their confirmations.
    for tx_id in [*result["top_ups"], *result["resubmitted"]]:
        send.apply_async(args=[tx_id], countdown=CONFIRM_POLL_S, queue="chain")
    return result
