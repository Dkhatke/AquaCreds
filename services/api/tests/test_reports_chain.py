"""Approved MRV report → `aqua.reports.anchor` core → Anvil: register, submitReport (registrar),
approveReport (verifier); then the verify endpoint compares the stored, DB and on-chain hashes."""

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from chain_testlib import Deployment, deploy_suite, mine
from conftest import FakeStorage
from reports_testlib import request_and_build, setup_project
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from aqua_chain.relayer import Relayer
from aqua_chain.runtime import build_relayer, indexer_config
from aqua_core.db import session_factory
from aqua_core.models import ChainTransaction, ChainTxStatus, MRVReport
from aqua_core.settings import Settings
from aqua_worker.tasks.chain import index_events_core
from aqua_worker.tasks.reports import AnchorError, anchor_report_core


@pytest.fixture
async def deployment(anvil_url: str) -> Deployment:
    return await deploy_suite(anvil_url)


@pytest.fixture
def settings_overrides(anvil_url: str, deployment: Deployment) -> dict[str, Any]:
    return deployment.settings(anvil_url)


@pytest.fixture
async def sessionmaker(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(database_url)
    yield session_factory(engine)
    await engine.dispose()


@pytest.fixture
async def relayer(
    database_url: str,
    settings_overrides: dict[str, Any],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[Relayer]:
    settings = Settings(_env_file=None, database_url=database_url, **settings_overrides)
    r = build_relayer(settings, sessionmaker)
    yield r
    await r.client.close()


async def _anchor(relayer: Relayer, anvil_url: str, report_id: uuid.UUID) -> dict[str, Any]:
    steps = []
    for _ in range(10):
        state = await anchor_report_core(relayer, report_id)
        steps.append(state.get("step"))
        if state["state"] == "anchored":
            state["steps"] = steps
            return state
        await mine(anvil_url, relayer.policy.confirmations)
    raise AssertionError(f"not anchored after 10 rounds: {steps}")


async def test_approved_report_is_anchored_and_verifiable(
    client: Any,
    make_actor: Any,
    sql: Any,
    storage: FakeStorage,
    enqueued: list[tuple[str, tuple[str, ...], str]],
    sessionmaker: async_sessionmaker[AsyncSession],
    relayer: Relayer,
    anvil_url: str,
    deployment: Deployment,
) -> None:
    s = await setup_project(make_actor, sql)
    await request_and_build(s, storage, enqueued, sessionmaker)
    rep = (await s.admin.get(f"/periods/{s.period_id}/reports")).json()[0]
    report_id = uuid.UUID(rep["id"])

    with pytest.raises(AnchorError):  # not approved yet
        await anchor_report_core(relayer, report_id)

    r = await s.verifier.post(f"/reports/{rep['id']}/review", json={"decision": "approve"})
    assert r.status_code == 200, r.text
    assert enqueued[-1] == ("aqua.reports.anchor", (rep["id"],), "chain")

    state = await _anchor(relayer, anvil_url, report_id)
    assert {"register_project", "submit_report", "approve_report"} <= set(state["steps"])
    assert state["chain_report_id"] == 1

    async with sessionmaker() as session:
        report = await session.get(MRVReport, report_id)
        assert report is not None
        assert report.anchored_at is not None
        assert report.submit_tx_id
        assert report.approve_tx_id
        submit_tx = await session.get(ChainTransaction, report.submit_tx_id)
        approve_tx = await session.get(ChainTransaction, report.approve_tx_id)
    assert submit_tx is not None
    assert approve_tx is not None
    assert submit_tx.status is approve_tx.status is ChainTxStatus.CONFIRMED
    assert submit_tx.from_address != approve_tx.from_address  # registrar ≠ verifier key
    assert submit_tx.args[3] == rep["lccc_units"]

    onchain = await relayer.client.contract("AquaRegistry").functions.getReport(1).call()
    assert onchain[3].hex() == rep["report_hash"]  # reportHash
    assert onchain[6] == 2  # ReportStatus.Approved

    # Already anchored: idempotent
    assert (await anchor_report_core(relayer, report_id))["state"] == "anchored"

    detail = (await s.admin.get(f"/reports/{rep['id']}")).json()
    assert detail["chain_report_id"] == 1
    assert detail["submit_tx_id"] == str(submit_tx.id)

    v = (await s.verifier.get(f"/reports/{rep['id']}/verify")).json()
    assert v["onchain_source"] == "receipt"
    assert v["onchain_matches"] is True
    assert v["ok"] is True

    await mine(anvil_url, 5)
    await index_events_core(
        sessionmaker,
        relayer.client,
        indexer_config(Settings(_env_file=None, **deployment.settings(anvil_url))),
    )
    v = (await s.verifier.get(f"/reports/{rep['id']}/verify")).json()
    assert v["onchain_source"] == "indexer"
    assert v["onchain_matches"] is True

    # Tampering the DB hash is detected against both the stored JSON and the chain
    await sql("UPDATE mrv_reports SET report_hash=:h WHERE id=:id", h="0" * 64, id=report_id)
    v = (await s.verifier.get(f"/reports/{rep['id']}/verify")).json()
    assert v["stored_matches"] is False
    assert v["onchain_matches"] is False
    assert v["ok"] is False
