"""Chain integration: API → relayer → Anvil → indexer, on real PostGIS (testcontainers) and a real
Anvil node (Foundry image). Contracts are freshly deployed per test from the committed artifacts."""

import asyncio
import hashlib
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from api_testlib import project_payload
from chain_testlib import Deployment, call_as, deploy_suite, mine, rpc, signers, w3_for
from eth_account import Account
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from aqua_chain import actions, artifacts
from aqua_chain.client import ChainClient
from aqua_chain.encoding import project_id_bytes32, to_hex
from aqua_chain.errors import ChainIdMismatchError, GasPriceTooHighError
from aqua_chain.indexer import INDEXER_LOCK_KEY, IndexerConfig
from aqua_chain.relayer import Relayer
from aqua_chain.runtime import build_relayer, indexer_config
from aqua_core.db import session_factory
from aqua_core.models import (
    ChainEvent,
    ChainSigner,
    ChainTransaction,
    ChainTxPurpose,
    ChainTxStatus,
    OrgWallet,
    Project,
)
from aqua_core.settings import Settings
from aqua_worker.tasks.chain import (
    approve_report_core,
    index_events_core,
    issue_credits_core,
    register_project_core,
    send_core,
    submit_report_core,
)

TEN_YEARS = 10 * 365 * 24 * 3600


@pytest.fixture
async def deployment(anvil_url: str) -> Deployment:
    return await deploy_suite(anvil_url)


@pytest.fixture
def settings_overrides(anvil_url: str, deployment: Deployment) -> dict[str, Any]:
    return deployment.settings(anvil_url)


@pytest.fixture
def chain_settings(database_url: str, settings_overrides: dict[str, Any]) -> Settings:
    return Settings(_env_file=None, database_url=database_url, **settings_overrides)


@pytest.fixture
async def sessionmaker(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(database_url)
    yield session_factory(engine)
    await engine.dispose()


@pytest.fixture
async def relayer(
    chain_settings: Settings, sessionmaker: async_sessionmaker[AsyncSession]
) -> AsyncIterator[Relayer]:
    r = build_relayer(chain_settings, sessionmaker)
    yield r
    await r.client.close()


class Setup:
    def __init__(self, admin: Any, registry: Any, org_id: str, project_id: str) -> None:
        self.admin, self.registry, self.org_id, self.project_id = (
            admin,
            registry,
            org_id,
            project_id,
        )


async def _project(admin: Any, org_id: str, sql: Any, lon: float = 86.85) -> str:
    payload = project_payload(org_id, boundary=_square(lon))
    r = await admin.post("/projects", json=payload)
    assert r.status_code == 201, r.text
    await sql("UPDATE projects SET status='submitted' WHERE id=:id", id=r.json()["id"])
    return str(r.json()["id"])


def _square(lon: float) -> dict[str, Any]:
    lat, d = 20.70, 0.02
    ring = [[lon, lat], [lon + d, lat], [lon + d, lat + d], [lon, lat + d], [lon, lat]]
    return {"type": "Polygon", "coordinates": [ring]}


@pytest.fixture
async def setup(make_actor: Any, sql: Any) -> Setup:
    admin = await make_actor("NGO Admin")
    org = (
        await admin.post("/orgs", json={"name": "Odisha Coastal Trust", "org_type": "ngo"})
    ).json()
    registry = await make_actor("Registry Admin")
    await sql("UPDATE users SET platform_role='registry_admin' WHERE id=:id", id=registry.id)
    return Setup(admin, registry, org["id"], await _project(admin, org["id"], sql))


async def _confirm(relayer: Relayer, anvil_url: str, tx_id: Any) -> dict[str, Any]:
    """Process, mine enough blocks for the confirmation policy, process again."""
    first = await send_core(relayer, tx_id)
    if first["status"] != ChainTxStatus.SUBMITTED.value:
        return first
    await mine(anvil_url, relayer.policy.confirmations)
    return await send_core(relayer, tx_id)


async def _tx(sessionmaker: async_sessionmaker[AsyncSession], tx_id: Any) -> ChainTransaction:
    async with sessionmaker() as session:
        tx = await session.get(ChainTransaction, uuid.UUID(str(tx_id)))
        assert tx is not None
        return tx


# ---------------------------------------------------------------- full flow


async def test_register_submit_approve_issue_and_index(
    setup: Setup,
    relayer: Relayer,
    anvil_url: str,
    deployment: Deployment,
    sessionmaker: async_sessionmaker[AsyncSession],
    chain_settings: Settings,
    enqueued: list[Any],
    make_actor: Any,
) -> None:
    # 1. registry admin anchors the project via the API → queued + enqueued on the chain queue
    r = await setup.registry.post(f"/projects/{setup.project_id}/chain/register")
    assert r.status_code == 202, r.text
    reg_tx = r.json()
    assert reg_tx["status"] == "queued"
    assert reg_tx["signer"] == "registrar"
    assert reg_tx["tx_hash"] is None  # nothing invented before broadcast
    assert enqueued == [("aqua.chain.send", (reg_tx["id"],), "chain")]

    # duplicate request is refused while the first is active
    dup = await setup.registry.post(f"/projects/{setup.project_id}/chain/register")
    assert dup.status_code == 409

    # 2. worker: submit → (not enough confirmations yet) → mine → confirmed
    first = await send_core(relayer, uuid.UUID(reg_tx["id"]))
    assert first["status"] == "submitted"
    assert first["tx_hash"].startswith("0x")
    await mine(anvil_url, 1)
    assert (await send_core(relayer, uuid.UUID(reg_tx["id"])))["status"] == "confirmed"

    tx = await _tx(sessionmaker, reg_tx["id"])
    assert tx.block_number
    assert tx.gas_used
    assert tx.nonce is not None
    assert tx.result["events"][0]["event"] == "ProjectRegistered"

    # on-chain state: owner = the org's HD-derived custodial wallet
    async with sessionmaker() as session:
        wallet = await session.get(OrgWallet, uuid.UUID(setup.org_id))
    assert wallet is not None
    assert wallet.derivation_index == chain_settings.chain_org_index_offset
    assert wallet.address == signers().derive(wallet.derivation_index).address
    registry = relayer.client.contract("AquaRegistry")
    key = project_id_bytes32(setup.project_id)
    onchain = await registry.functions.getProject(key).call()
    assert onchain[0] == wallet.address
    assert onchain[2] == 2026  # start year

    # 3. registrar submits a report (worker task core)
    report_hash = hashlib.sha256(b"monitoring report v1").hexdigest()
    submitted = await submit_report_core(
        relayer,
        project_id=uuid.UUID(setup.project_id),
        period_end=2029,
        report_hash=report_hash,
        lccc=120,
        evidence_uri="urn:aquacreds:evidence:1",
    )
    submitted = await _confirm(relayer, anvil_url, uuid.UUID(submitted["id"]))
    assert submitted["status"] == "confirmed", submitted
    report_id = actions.event_value(
        await _tx(sessionmaker, submitted["id"]), "ReportSubmitted", "reportId"
    )
    assert report_id == 1

    # 4. a *different* key (verifier) approves
    approved = await approve_report_core(
        relayer, report_id=report_id, entity_type="project", entity_id=setup.project_id
    )
    approved = await _confirm(relayer, anvil_url, uuid.UUID(approved["id"]))
    assert approved["status"] == "confirmed", approved
    approve_tx = await _tx(sessionmaker, approved["id"])
    submit_tx = await _tx(sessionmaker, submitted["id"])
    assert approve_tx.from_address != submit_tx.from_address
    report = await registry.functions.getReport(report_id).call()
    assert report[6] == 2  # ReportStatus.Approved
    assert report[8] == approve_tx.from_address  # verifier

    # 5. issuer mints long-term credits to the org wallet, capped by the approved lCCC
    issued = await issue_credits_core(
        relayer,
        report_id=report_id,
        credit_type="long_term",
        org_id=uuid.UUID(setup.org_id),
        amount=100,
        expires_at=int(time.time()) + TEN_YEARS,
        entity_type="project",
        entity_id=setup.project_id,
    )
    issued = await _confirm(relayer, anvil_url, uuid.UUID(issued["id"]))
    assert issued["status"] == "confirmed", issued
    token_id = actions.event_value(
        await _tx(sessionmaker, issued["id"]), "CreditsIssued", "tokenId"
    )
    credit = relayer.client.contract("AquaCredit")
    assert token_id == await credit.functions.tokenIdFor(report_id, 1).call()
    assert await credit.functions.balanceOf(wallet.address, token_id).call() == 100

    # over-issuance beyond the approved 120 is refused before broadcast
    over = await issue_credits_core(
        relayer,
        report_id=report_id,
        credit_type="long_term",
        org_id=uuid.UUID(setup.org_id),
        amount=21,
        expires_at=int(time.time()) + TEN_YEARS,
        entity_type="project",
        entity_id=setup.project_id,
    )
    assert over["status"] == "failed"
    assert over["error_name"] == "ExceedsApproved"
    assert over["tx_hash"] is None

    # 6. indexer: decode + store, then API views
    result = await index_events_core(sessionmaker, relayer.client, indexer_config(chain_settings))
    assert result["events"]["AquaRegistry"] >= 3
    view = await setup.admin.get(f"/projects/{setup.project_id}/chain")
    assert view.status_code == 200, view.text
    body = view.json()
    assert body["registered"] is True
    assert body["project_key"] == to_hex(key)
    assert [t["purpose"] for t in body["transactions"]] == [
        "register_project",
        "submit_report",
        "approve_report",
        "issue_credits",
        "issue_credits",
    ]
    names = [e["event"] for e in body["events"]]
    assert names == ["ProjectRegistered", "ReportSubmitted", "ReportApproved", "CreditsIssued"]
    issued_event = body["events"][-1]["args"]
    assert issued_event["amount"] == 100
    assert issued_event["to"] == wallet.address

    # transaction detail: registry admin and org members yes, outsiders no
    assert (await setup.registry.get(f"/chain/transactions/{reg_tx['id']}")).json()[
        "status"
    ] == "confirmed"
    assert (await setup.admin.get(f"/chain/transactions/{reg_tx['id']}")).status_code == 200
    outsider = await make_actor("Outsider")
    assert (await outsider.get(f"/chain/transactions/{reg_tx['id']}")).status_code == 404
    assert (await outsider.get(f"/projects/{setup.project_id}/chain")).status_code == 404
    assert (await setup.admin.get(f"/chain/transactions/{uuid.uuid4()}")).status_code == 404

    # relayer status
    status_r = await setup.registry.get("/chain/status")
    assert status_r.status_code == 200
    status_body = status_r.json()
    assert status_body["connected"] is True
    assert status_body["configured"] is True
    assert status_body["chain_id"] == 31337
    assert status_body["error"] is None
    assert {s["role"] for s in status_body["signers"]} == {"registrar", "verifier", "issuer"}
    assert all(s["balance_wei"] > 0 for s in status_body["signers"])
    assert status_body["contracts"]["AquaRegistry"] == deployment.registry
    assert status_body["indexer_cursors"]["AquaRegistry"] >= deployment.block
    assert (await setup.admin.get("/chain/status")).status_code == 403

    # audit trail stays a valid hash chain and contains the relayer transitions
    verify = await setup.registry.get("/audit/verify")
    assert verify.json()["ok"] is True


async def test_register_task_reuses_active_transaction(
    setup: Setup, relayer: Relayer, anvil_url: str
) -> None:
    first = await register_project_core(relayer, uuid.UUID(setup.project_id))
    assert first["status"] == "submitted"
    again = await register_project_core(relayer, uuid.UUID(setup.project_id))
    assert again["id"] == first["id"]  # no second transaction for the same registration
    with pytest.raises(LookupError):
        await register_project_core(relayer, uuid.uuid4())


# ---------------------------------------------------------------- separation of duties


async def test_self_approval_is_rejected_and_surfaced_as_failed(
    setup: Setup,
    relayer: Relayer,
    anvil_url: str,
    deployment: Deployment,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    # Misconfiguration on-chain: the registrar also holds VERIFIER_ROLE. The contract's
    # SelfApproval check must still stop it approving its own report.
    w3 = w3_for(anvil_url)
    deployer = Account.from_key(signers().derive(0).key)
    reg = w3.eth.contract(address=deployment.registry, abi=artifacts.abi("AquaRegistry"))  # type: ignore[arg-type]
    verifier_role = await reg.functions.VERIFIER_ROLE().call()
    await call_as(
        w3,
        deployer,
        reg.functions.grantRole(verifier_role, relayer.signers.address(ChainSigner.REGISTRAR)),
    )
    await w3.provider.disconnect()  # type: ignore[attr-defined]

    reg_result = await register_project_core(relayer, uuid.UUID(setup.project_id))
    await _confirm(relayer, anvil_url, uuid.UUID(reg_result["id"]))
    sub = await submit_report_core(
        relayer,
        project_id=uuid.UUID(setup.project_id),
        period_end=2029,
        report_hash="0x" + "ab" * 32,
        lccc=10,
        evidence_uri="urn:e",
    )
    await _confirm(relayer, anvil_url, uuid.UUID(sub["id"]))

    async with sessionmaker() as session:
        tx = await relayer.queue(
            session,
            purpose=ChainTxPurpose.APPROVE_REPORT,
            entity_type="project",
            entity_id=setup.project_id,
            signer=ChainSigner.REGISTRAR,  # same key that submitted
            contract="AquaRegistry",
            function="approveReport",
            args=[1],
        )
        await session.commit()
    result = await send_core(relayer, tx.id)
    assert result["status"] == "failed"
    assert result["error_name"] == "SelfApproval"
    assert "SelfApproval()" in result["error"]
    assert result["tx_hash"] is None
    failed = await _tx(sessionmaker, tx.id)
    assert failed.nonce is None  # no nonce consumed
    # Processing a terminal transaction again is a no-op
    assert (await send_core(relayer, tx.id))["status"] == "failed"

    # the verifier (a different key) can approve it
    ok = await approve_report_core(
        relayer, report_id=1, entity_type="project", entity_id=setup.project_id
    )
    assert (await _confirm(relayer, anvil_url, uuid.UUID(ok["id"])))["status"] == "confirmed"


# ---------------------------------------------------------------- nonces


async def test_parallel_sends_never_reuse_a_nonce(
    setup: Setup,
    relayer: Relayer,
    anvil_url: str,
    sql: Any,
    sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = []
    for i in range(4):
        pid = await _project(setup.admin, setup.org_id, sql, lon=86.0 + i * 0.1)
        async with sessionmaker() as session:
            project = await session.get(Project, uuid.UUID(pid))
            tx = await actions.queue_register_project(session, relayer, project)
            await session.commit()
            ids.append(tx.id)

    # Simulate a lagging node that keeps reporting the same pending nonce: only the database
    # coordination can keep the nonces unique.
    stale = await relayer.client.pending_nonce(relayer.signers.address(ChainSigner.REGISTRAR))

    async def lagging(_address: str) -> int:
        return stale

    monkeypatch.setattr(relayer.client, "pending_nonce", lagging)
    statuses = await asyncio.gather(*(relayer.submit(i) for i in ids))
    assert set(statuses) == {ChainTxStatus.SUBMITTED}
    txs = [await _tx(sessionmaker, i) for i in ids]
    nonces = sorted(t.nonce for t in txs if t.nonce is not None)
    assert nonces == list(range(stale, stale + 4))
    await mine(anvil_url, 2)
    for i in ids:
        assert await relayer.confirm(i) is ChainTxStatus.CONFIRMED


async def test_dropped_transaction_is_rebroadcast_with_same_bytes(
    setup: Setup, relayer: Relayer, anvil_url: str, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await rpc(anvil_url, "evm_setAutomine", [False])
    try:
        async with sessionmaker() as session:
            project = await session.get(Project, uuid.UUID(setup.project_id))
            tx = await actions.queue_register_project(session, relayer, project)
            await session.commit()
        assert await relayer.submit(tx.id) is ChainTxStatus.SUBMITTED
        sent = await _tx(sessionmaker, tx.id)
        await rpc(anvil_url, "anvil_dropTransaction", [sent.tx_hash])
        # not mined, unknown to the node → confirm() re-broadcasts the stored signed bytes
        assert await relayer.confirm(tx.id) is ChainTxStatus.SUBMITTED
        await mine(anvil_url, 2)
        assert await relayer.confirm(tx.id) is ChainTxStatus.CONFIRMED
        assert (await _tx(sessionmaker, tx.id)).tx_hash == sent.tx_hash
    finally:
        await rpc(anvil_url, "evm_setAutomine", [True])


async def test_onchain_revert_is_recorded_with_decoded_reason(
    setup: Setup, relayer: Relayer, anvil_url: str, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """Two registrations of the same project both pass estimation (neither is mined yet); the
    second reverts on-chain and is surfaced as `reverted` with the custom error name."""
    await rpc(anvil_url, "evm_setAutomine", [False])
    try:
        async with sessionmaker() as session:
            project = await session.get(Project, uuid.UUID(setup.project_id))
            first = await actions.queue_register_project(session, relayer, project)
            second = await relayer.queue(
                session,
                purpose=ChainTxPurpose.OTHER,
                entity_type="project",
                entity_id=setup.project_id,
                signer=ChainSigner.REGISTRAR,
                contract="AquaRegistry",
                function="registerProject",
                args=first.args,
            )
            await session.commit()
        assert await relayer.submit(first.id) is ChainTxStatus.SUBMITTED
        assert await relayer.submit(second.id) is ChainTxStatus.SUBMITTED
        await mine(anvil_url, 3)
    finally:
        await rpc(anvil_url, "evm_setAutomine", [True])
    assert await relayer.confirm(first.id) is ChainTxStatus.CONFIRMED
    assert await relayer.confirm(second.id) is ChainTxStatus.REVERTED
    reverted = await _tx(sessionmaker, second.id)
    assert reverted.error_name == "ProjectExists"
    assert reverted.tx_hash
    assert reverted.block_number
    assert reverted.gas_used


async def test_gas_price_above_cap_keeps_transaction_queued(
    setup: Setup,
    database_url: str,
    settings_overrides: dict[str, Any],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    settings = Settings(
        _env_file=None,
        database_url=database_url,
        **{**settings_overrides, "chain_gas_price_cap_gwei": 0.000001},
    )
    relayer = build_relayer(settings, sessionmaker)
    try:
        async with sessionmaker() as session:
            project = await session.get(Project, uuid.UUID(setup.project_id))
            tx = await actions.queue_register_project(session, relayer, project)
            await session.commit()
        with pytest.raises(GasPriceTooHighError, match="exceeds the cap"):
            await relayer.submit(tx.id)
        assert (await _tx(sessionmaker, tx.id)).status is ChainTxStatus.QUEUED
    finally:
        await relayer.client.close()


async def test_relayer_refuses_transactions_queued_for_another_setup(
    setup: Setup, relayer: Relayer, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    async with sessionmaker() as session:
        project = await session.get(Project, uuid.UUID(setup.project_id))
        tx = await actions.queue_register_project(session, relayer, project)
        await session.flush()
        await session.execute(
            update(ChainTransaction)
            .where(ChainTransaction.id == tx.id)
            .values(from_address="0x" + "00" * 20)
        )
        await session.commit()
    assert await relayer.submit(tx.id) is ChainTxStatus.FAILED
    assert "no longer derives" in ((await _tx(sessionmaker, tx.id)).error or "")
    with pytest.raises(LookupError):
        await relayer.submit(uuid.uuid4())
    with pytest.raises(LookupError):
        await relayer.confirm(uuid.uuid4())


# ---------------------------------------------------------------- indexer


async def test_indexer_is_idempotent_and_drops_orphaned_events(
    setup: Setup,
    relayer: Relayer,
    anvil_url: str,
    chain_settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    reg = await register_project_core(relayer, uuid.UUID(setup.project_id))
    await _confirm(relayer, anvil_url, uuid.UUID(reg["id"]))
    config = indexer_config(chain_settings)  # batch of 5 blocks → several ranges

    async def snapshot() -> list[tuple[str, str, int]]:
        async with sessionmaker() as session:
            rows = await session.scalars(select(ChainEvent).order_by(ChainEvent.id))
            return [(e.event, e.tx_hash, e.log_index) for e in rows]

    await index_events_core(sessionmaker, relayer.client, config)
    first = await snapshot()
    names = [e for e, _, _ in first]
    assert names.count("ProjectRegistered") == 1
    assert "RoleGranted" in names  # AccessControl events from deployment are indexed too
    await index_events_core(sessionmaker, relayer.client, config)
    assert await snapshot() == first  # re-run: no duplicates

    # An event from a block that was reorged away (within the re-scan window) is removed.
    async with sessionmaker() as session:
        latest = await relayer.client.latest_block()
        session.add(
            ChainEvent(
                chain_id=31337,
                contract="AquaRegistry",
                address=relayer.client.address_of("AquaRegistry"),
                event="ProjectRegistered",
                block_number=latest,
                block_hash="0x" + "ee" * 32,
                tx_hash="0x" + "dd" * 32,
                log_index=0,
                project_key="0x" + "cc" * 32,
                args={},
            )
        )
        await session.commit()
    result = await index_events_core(sessionmaker, relayer.client, config)
    assert result["removed"] == 1
    assert await snapshot() == first


async def test_indexer_bounded_batches_and_lock(
    setup: Setup,
    relayer: Relayer,
    anvil_url: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    deployment: Deployment,
) -> None:
    await mine(anvil_url, 12)
    cfg = IndexerConfig(deploy_block=deployment.block, batch_blocks=2, reorg_depth=0, max_batches=1)
    first = await index_events_core(sessionmaker, relayer.client, cfg)
    assert first["cursors"]["AquaRegistry"] == deployment.block + 1  # only one 2-block range
    second = await index_events_core(sessionmaker, relayer.client, cfg)
    assert second["cursors"]["AquaRegistry"] == deployment.block + 3

    # A concurrent run (lock held elsewhere) is skipped, not doubled.
    async with sessionmaker() as holder:
        await holder.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": INDEXER_LOCK_KEY})
        skipped = await index_events_core(sessionmaker, relayer.client, cfg)
        assert skipped["skipped"] is True
        await holder.rollback()


async def test_wrong_chain_id_is_refused(chain_settings: Settings) -> None:
    client = ChainClient.from_settings(chain_settings.model_copy(update={"chain_id": 80002}))
    try:
        with pytest.raises(ChainIdMismatchError):
            await client.check()
    finally:
        await client.close()


# ---------------------------------------------------------------- API guards


async def test_register_endpoint_guards(setup: Setup, sql: Any) -> None:
    # only registry admins
    assert (
        await setup.admin.post(f"/projects/{setup.project_id}/chain/register")
    ).status_code == 403
    # unknown project
    assert (
        await setup.registry.post(f"/projects/{uuid.uuid4()}/chain/register")
    ).status_code == 404
    # must be submitted or later
    await sql("UPDATE projects SET status='draft' WHERE id=:id", id=setup.project_id)
    r = await setup.registry.post(f"/projects/{setup.project_id}/chain/register")
    assert r.status_code == 409
    assert "submitted" in r.json()["detail"]
