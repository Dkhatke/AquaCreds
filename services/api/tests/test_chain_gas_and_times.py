"""Chain follow-ups on real PostGIS + Anvil: gas top-ups of custodial org wallets (waiting_for_gas,
funder transfers, per-wallet / global caps) and indexed block timestamps (public block_time for
retirements signed outside the relayer, backfill of older rows)."""

import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from api_testlib import project_payload
from chain_testlib import Deployment, call_as, deploy_suite, mine, rpc, signers, w3_for
from eth_account import Account
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from aqua_chain import actions
from aqua_chain.gas import TopUpPolicy
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
)
from aqua_core.settings import Settings
from aqua_worker.tasks.chain import (
    approve_report_core,
    index_events_core,
    issue_credits_core,
    register_project_core,
    send_core,
    submit_report_core,
    top_up_org_wallets_core,
)

POL = 10**18
POLICY = TopUpPolicy(
    min_balance=POL // 100,
    amount=2 * POL // 100,
    wallet_daily_cap=4 * POL // 100,
    global_daily_cap=POL,
)


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


async def _confirm(relayer: Relayer, anvil_url: str, tx_id: Any) -> dict[str, Any]:
    first = await send_core(relayer, uuid.UUID(str(tx_id)))
    if first["status"] != "submitted":
        return first
    await mine(anvil_url, relayer.policy.confirmations)
    return await send_core(relayer, uuid.UUID(str(tx_id)))


async def _tx(sessionmaker: async_sessionmaker[AsyncSession], tx_id: Any) -> ChainTransaction:
    async with sessionmaker() as session:
        tx = await session.get(ChainTransaction, uuid.UUID(str(tx_id)))
        assert tx is not None
        return tx


@pytest.fixture
async def registered(
    make_actor: Any, sql: Any, relayer: Relayer, anvil_url: str, sessionmaker: Any
) -> tuple[str, str, OrgWallet]:
    """A project registered on-chain under its org's custodial wallet: (org id, project, wallet)."""
    admin = await make_actor("NGO Admin")
    org = (await admin.post("/orgs", json={"name": "Godavari Trust", "org_type": "ngo"})).json()
    r = await admin.post("/projects", json=project_payload(org["id"]))
    assert r.status_code == 201, r.text
    pid = str(r.json()["id"])
    await sql("UPDATE projects SET status='submitted' WHERE id=:id", id=pid)
    reg = await register_project_core(relayer, uuid.UUID(pid))
    assert (await _confirm(relayer, anvil_url, reg["id"]))["status"] == "confirmed"
    async with sessionmaker() as session:
        wallet = await session.get(OrgWallet, uuid.UUID(org["id"]))
    assert wallet is not None
    return org["id"], pid, wallet


async def _queue_org_tx(relayer: Relayer, wallet: OrgWallet) -> uuid.UUID:
    """An org-wallet transaction that cannot revert (ERC-1155 approval of an operator)."""
    async with relayer.sessionmaker() as session:
        tx = await relayer.queue(
            session,
            purpose=ChainTxPurpose.OTHER,
            entity_type="org_wallet",
            entity_id=str(wallet.org_id),
            signer=ChainSigner.ORG_WALLET,
            contract="AquaCredit",
            function="setApprovalForAll",
            args=[signers().derive(7).address, True],
            org_wallet=wallet,
        )
        await session.commit()
        return tx.id


async def _set_balance(anvil_url: str, address: str, wei: int) -> None:
    await rpc(anvil_url, "anvil_setBalance", [address, hex(wei)])


# ---------------------------------------------------------------- gas top-ups


async def test_org_wallet_waits_for_gas_then_is_topped_up_within_caps(
    registered: tuple[str, str, OrgWallet],
    relayer: Relayer,
    anvil_url: str,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    _, _, wallet = registered
    funder = relayer.signers.address(ChainSigner.FUNDER)
    assert funder == signers().derive(4).address  # CHAIN_FUNDER_INDEX default
    await _set_balance(anvil_url, wallet.address, 0)

    # 1. not broadcast without gas: parked, no hash, no nonce
    first = await _queue_org_tx(relayer, wallet)
    assert await relayer.process(first) is ChainTxStatus.WAITING_FOR_GAS
    parked = await _tx(sessionmaker, first)
    assert (parked.tx_hash, parked.nonce, parked.raw_tx) == (None, None, None)
    assert "waiting for gas" in (parked.error or "")
    assert await relayer.submit(first) is ChainTxStatus.WAITING_FOR_GAS  # still parked

    # 2. top-up: one funder transfer, then the parked tx is resubmitted
    result = await top_up_org_wallets_core(relayer, POLICY)
    (check,) = result["wallets"]
    assert (check["address"], check["balance"], check["action"]) == (wallet.address, 0, "topped_up")
    assert result["resubmitted"] == [str(first)]
    assert result["still_waiting"] == []
    (top_up_id,) = result["top_ups"]
    top_up = await _tx(sessionmaker, top_up_id)
    assert (top_up.signer, top_up.purpose) == (ChainSigner.FUNDER, ChainTxPurpose.GAS_TOP_UP)
    assert (top_up.from_address, top_up.to_address) == (funder, wallet.address)
    assert (top_up.contract, top_up.function, top_up.args) == (
        "native",
        "transfer",
        [wallet.address, str(POLICY.amount)],
    )
    assert (await _confirm(relayer, anvil_url, top_up_id))["status"] == "confirmed"
    receipt = await relayer.client.w3.eth.get_transaction_receipt(top_up.tx_hash)  # type: ignore[arg-type]
    assert receipt["status"] == 1
    assert (await _confirm(relayer, anvil_url, first))["status"] == "confirmed"
    sent = await _tx(sessionmaker, first)
    assert sent.error is None
    assert 0 < await relayer.client.balance(wallet.address) < POLICY.amount

    # 3. a wallet with enough balance and nothing waiting is left alone
    second = await _queue_org_tx(relayer, wallet)
    await _set_balance(anvil_url, wallet.address, POL)
    again = await top_up_org_wallets_core(relayer, POLICY)
    assert [w["action"] for w in again["wallets"]] == ["sufficient"]
    assert again["top_ups"] == []
    assert (await _confirm(relayer, anvil_url, second))["status"] == "confirmed"

    # 4. second top-up reaches the per-wallet cap (2 × 0.02 = 0.04); a third is refused
    await _set_balance(anvil_url, wallet.address, 0)
    third = await _queue_org_tx(relayer, wallet)
    second_top_up = await top_up_org_wallets_core(relayer, POLICY)
    assert second_top_up["wallets"][0]["action"] == "topped_up"
    for tx_id in [*second_top_up["top_ups"], str(third)]:
        assert (await _confirm(relayer, anvil_url, tx_id))["status"] == "confirmed"
    await _set_balance(anvil_url, wallet.address, 0)
    fourth = await _queue_org_tx(relayer, wallet)
    assert await relayer.submit(fourth) is ChainTxStatus.WAITING_FOR_GAS
    capped = await top_up_org_wallets_core(relayer, POLICY)
    assert capped["wallets"][0]["action"] == "wallet_cap"
    assert capped["top_ups"] == []
    assert capped["still_waiting"] == [str(fourth)]
    assert (await _tx(sessionmaker, fourth)).status is ChainTxStatus.WAITING_FOR_GAS

    # 5. global cap applies across wallets
    tight = TopUpPolicy(
        min_balance=POLICY.min_balance,
        amount=POLICY.amount,
        wallet_daily_cap=POL,
        global_daily_cap=5 * POL // 100,  # 0.04 already spent; +0.02 would exceed 0.05
    )
    assert (await top_up_org_wallets_core(relayer, tight))["wallets"][0]["action"] == "global_cap"

    # 6. only rolling-24 h spend counts: age the earlier top-ups, then a top-up goes through
    async with sessionmaker() as session:
        await session.execute(
            update(ChainTransaction)
            .where(ChainTransaction.purpose == ChainTxPurpose.GAS_TOP_UP)
            .values(created_at=datetime(2020, 1, 1, tzinfo=UTC))
        )
        await session.commit()
    resumed = await top_up_org_wallets_core(relayer, POLICY)
    assert resumed["wallets"][0]["action"] == "topped_up"
    assert resumed["resubmitted"] == [str(fourth)]
    for tx_id in [*resumed["top_ups"], str(fourth)]:
        assert (await _confirm(relayer, anvil_url, tx_id))["status"] == "confirmed"

    # every top-up is audited and the audit chain is intact
    async with sessionmaker() as session:
        audited = await session.scalar(
            text("SELECT count(*) FROM audit_log WHERE action = 'chain.gas_top_up.queued'")
        )
    assert audited == 3


async def test_top_up_in_flight_lock_and_underfunded_funder(
    registered: tuple[str, str, OrgWallet],
    relayer: Relayer,
    anvil_url: str,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    _, _, wallet = registered
    funder = relayer.signers.address(ChainSigner.FUNDER)
    await _set_balance(anvil_url, wallet.address, 0)
    waiting = await _queue_org_tx(relayer, wallet)
    assert await relayer.submit(waiting) is ChainTxStatus.WAITING_FOR_GAS

    # a top-up still queued for this wallet: no second one
    async with sessionmaker() as session:
        pending = await relayer.queue_native_transfer(
            session,
            purpose=ChainTxPurpose.GAS_TOP_UP,
            entity_type="org_wallet",
            entity_id=str(wallet.org_id),
            signer=ChainSigner.FUNDER,
            to_address=wallet.address,
            value_wei=POLICY.amount,
        )
        await session.commit()
    result = await top_up_org_wallets_core(relayer, POLICY)
    assert result["wallets"][0]["action"] == "in_flight"
    assert result["top_ups"] == []

    # a concurrent run is skipped, never doubled
    async with sessionmaker() as holder:
        await holder.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": 0x41_51_47_53})
        assert (await top_up_org_wallets_core(relayer, POLICY))["skipped"] is True
        await holder.rollback()

    # an empty funder: the transfer fails with the node's reason, nothing is invented
    original = await relayer.client.balance(funder)
    await _set_balance(anvil_url, funder, 0)
    try:
        assert await relayer.process(pending.id) is ChainTxStatus.FAILED
        failed = await _tx(sessionmaker, pending.id)
        assert failed.tx_hash is None
        assert "not broadcast" in (failed.error or "")
    finally:
        await _set_balance(anvil_url, funder, original)
    assert (await _tx(sessionmaker, waiting)).status is ChainTxStatus.WAITING_FOR_GAS


async def test_native_transfer_guards(relayer: Relayer) -> None:
    async with relayer.sessionmaker() as session:
        with pytest.raises(Exception, match="never send native"):
            await relayer.queue_native_transfer(
                session,
                purpose=ChainTxPurpose.GAS_TOP_UP,
                entity_type="x",
                entity_id="y",
                signer=ChainSigner.ORG_WALLET,
                to_address=signers().derive(7).address,
                value_wei=1,
            )
        with pytest.raises(Exception, match="positive value"):
            await relayer.queue_native_transfer(
                session,
                purpose=ChainTxPurpose.GAS_TOP_UP,
                entity_type="x",
                entity_id="y",
                signer=ChainSigner.FUNDER,
                to_address=signers().derive(7).address,
                value_wei=0,
            )


# ---------------------------------------------------------------- block timestamps


async def test_indexer_stores_block_times_for_unrelayed_retirements_and_backfills(
    registered: tuple[str, str, OrgWallet],
    relayer: Relayer,
    anvil_url: str,
    chain_settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    client: Any,
) -> None:
    org_id, pid, wallet = registered
    sub = await submit_report_core(
        relayer,
        project_id=uuid.UUID(pid),
        period_end=2029,
        report_hash=uuid.uuid4().hex * 2,
        lccc=50,
        evidence_uri="urn:aquacreds:evidence:times",
    )
    sub = await _confirm(relayer, anvil_url, sub["id"])
    report_id = int(
        actions.event_value(await _tx(sessionmaker, sub["id"]), "ReportSubmitted", "reportId")
    )
    ok = await approve_report_core(
        relayer, report_id=report_id, entity_type="project", entity_id=pid
    )
    assert (await _confirm(relayer, anvil_url, ok["id"]))["status"] == "confirmed"
    issued = await issue_credits_core(
        relayer,
        report_id=report_id,
        credit_type="long_term",
        org_id=uuid.UUID(org_id),
        amount=20,
        expires_at=int(time.time()) + 10 * 365 * 86400,
        entity_type="project",
        entity_id=pid,
    )
    issued = await _confirm(relayer, anvil_url, issued["id"])
    token_id = int(
        actions.event_value(await _tx(sessionmaker, issued["id"]), "CreditsIssued", "tokenId")
    )

    # the org wallet retires with its own key: no relayer record exists for this transaction
    await _set_balance(anvil_url, wallet.address, POL)
    w3 = w3_for(anvil_url)
    owner = Account.from_key(signers().derive(wallet.derivation_index).key)
    credit = relayer.client.contract("AquaCredit")
    receipt = await call_as(
        w3, owner, credit.functions.retire(token_id, 5, "Example Buyer Ltd", "FY2029 offset")
    )
    await w3.provider.disconnect()  # type: ignore[attr-defined]
    block = await relayer.client.w3.eth.get_block(receipt["blockNumber"])
    mined_at = datetime.fromtimestamp(int(block["timestamp"]), UTC)

    config = indexer_config(chain_settings)
    await index_events_core(sessionmaker, relayer.client, config)
    async with sessionmaker() as session:
        events = list(await session.scalars(select(ChainEvent)))
        assert events
        assert all(e.block_time is not None for e in events)
        for e in events[:5]:
            b = await relayer.client.w3.eth.get_block(e.block_hash)  # type: ignore[arg-type]
            assert e.block_time == datetime.fromtimestamp(int(b["timestamp"]), UTC)
        assert (
            await session.scalar(
                select(ChainTransaction.id).where(
                    ChainTransaction.tx_hash == receipt["transactionHash"].to_0x_hex()
                )
            )
            is None
        )

    cert = (await client.get("/public/retirements/1")).json()
    assert datetime.fromisoformat(cert["block_time"]) == mined_at
    vintages = (await client.get("/public/vintages")).json()["items"]
    assert all(i["block_time"] is not None for v in vintages for i in v["issuances"])

    # rows indexed before timestamps were stored are backfilled; an orphaned block is skipped
    async with sessionmaker() as session:
        await session.execute(update(ChainEvent).values(block_time=None))
        session.add(
            ChainEvent(
                chain_id=31337,
                contract="AquaRegistry",
                address=relayer.client.address_of("AquaRegistry"),
                event="ProjectRegistered",
                block_number=1,  # long before the re-scan window
                block_hash="0x" + "ee" * 32,
                tx_hash="0x" + "dd" * 32,
                log_index=0,
                project_key="0x" + "cc" * 32,
                args={},
            )
        )
        await session.commit()
    result = await index_events_core(sessionmaker, relayer.client, indexer_config(chain_settings))
    assert result["block_times_backfilled"] > 0  # rows outside the re-scan window
    async with sessionmaker() as session:
        missing = list(
            await session.scalars(select(ChainEvent.tx_hash).where(ChainEvent.block_time.is_(None)))
        )
    assert missing == ["0x" + "dd" * 32]
    cert = (await client.get("/public/retirements/1")).json()
    assert datetime.fromisoformat(cert["block_time"]) == mined_at
