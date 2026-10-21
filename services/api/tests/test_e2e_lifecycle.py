"""Full lifecycle, end to end, on a local stack: PostGIS (testcontainers) + Anvil + fake object
storage. One scenario through the HTTP API, the worker task cores and the contracts:

register users (staff with TOTP) → org + project in Bhitarkanika → tenure documents via presigned
uploads → submit → baseline period (plots, measurements, photo evidence, worker checks) → baseline
report → independent verifier approves → anchored (project registered on-chain) → monitoring
period → report → approve → anchored (submitReport + approveReport) → lCCC issued → indexer →
the buyer lists/buys/retires on the contracts directly → indexer → public registry (vintage,
retirement with block time, verify-hash `source: chain`) → stewardship score → audit chain valid.

Run alone with `uv run pytest -m e2e`. Plot data is illustrative test data, not field data."""

import hashlib
import json
import re
import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pyotp
import pytest
from api_testlib import project_payload
from chain_testlib import Deployment, call_as, deploy_suite, mine, rpc, signers, w3_for
from conftest import Actor, FakeStorage
from eth_account import Account
from photo_factory import make_jpeg
from reports_testlib import PLOT_AREA_M2, PLOTS, STRATA_AREA
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from aqua_api import mfa
from aqua_chain import actions
from aqua_chain.encoding import project_id_bytes32
from aqua_chain.relayer import Relayer
from aqua_chain.runtime import build_relayer, indexer_config
from aqua_core.db import session_factory
from aqua_core.models import ChainTransaction, ChainTxStatus, MRVReport, OrgWallet
from aqua_core.settings import Settings
from aqua_worker.tasks.chain import index_events_core, issue_credits_core, send_core
from aqua_worker.tasks.evidence import run_checks
from aqua_worker.tasks.reports import anchor_report_core, build_report_core

pytestmark = pytest.mark.e2e

PASSWORD = "correct-horse-battery"
T0 = 1_900_000_000.0  # pinned TOTP clock
TX_HASH = re.compile(r"^0x[0-9a-f]{64}$")
TEN_YEARS_S = 10 * 365 * 24 * 3600
PRICE_PER_UNIT = 2_000_000  # 2.00 mock USDC (6 decimals)
BUYER_INDEX = 9  # an Anvil dev account no platform signer uses
INSIDE = (86.858, 20.708)

# Baseline: the same plots, smaller trees (illustrative test data).
BASELINE_TREES = [
    {"species": "Avicennia marina", "dbh_cm": 2.4, "height_m": 1.8, "alive": True},
    {"species": "Rhizophora mucronata", "dbh_cm": 2.1, "height_m": 1.6, "alive": True},
]


@pytest.fixture
async def deployment(anvil_url: str) -> Deployment:
    return await deploy_suite(anvil_url)


@pytest.fixture
def settings_overrides(anvil_url: str, deployment: Deployment) -> dict[str, Any]:
    return {**deployment.settings(anvil_url), "mfa_required_for_staff": True}


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


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> dict[str, float]:
    state = {"now": T0}
    monkeypatch.setattr(mfa, "_now", lambda: state["now"])
    return state


# ---------------------------------------------------------------- helpers


async def register(client: httpx.AsyncClient, name: str) -> tuple[str, uuid.UUID]:
    email = f"{name.lower().replace(' ', '.')}@example.org"
    r = await client.post(
        "/auth/register", json={"email": email, "full_name": name, "password": PASSWORD}
    )
    assert r.status_code == 201, r.text
    return email, uuid.UUID(r.json()["id"])


async def login(client: httpx.AsyncClient, email: str, user_id: uuid.UUID) -> Actor:
    r = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    assert r.json().get("mfa_required") is not True
    return Actor(client, email, r.json()["access_token"], user_id)


async def staff(
    client: httpx.AsyncClient, sql: Any, clock: dict[str, float], name: str, role: str
) -> Actor:
    """A verifier / registry admin who enrols TOTP and signs in through the MFA challenge."""
    email, user_id = await register(client, name)
    await sql("UPDATE users SET platform_role=:r WHERE id=:id", r=role, id=user_id)
    plain = await login(client, email, user_id)
    # MFA_REQUIRED_FOR_STAFF: no staff privileges before enrolment
    assert (await plain.get("/audit/verify")).status_code == 403
    secret = (await plain.post("/auth/mfa/setup")).json()["secret"]
    enabled = await plain.post(
        "/auth/mfa/enable", json={"code": pyotp.TOTP(secret).at(int(clock["now"]))}
    )
    assert enabled.status_code == 200, enabled.text
    challenge = (
        await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    ).json()
    assert challenge["mfa_required"] is True
    clock["now"] += 30
    r = await client.post(
        "/auth/mfa/verify",
        json={
            "mfa_token": challenge["mfa_token"],
            "code": pyotp.TOTP(secret).at(int(clock["now"])),
        },
    )
    assert r.status_code == 200, r.text
    return Actor(client, email, r.json()["access_token"], user_id)


def synthetic_pdf(label: str) -> bytes:
    return f"%PDF-1.4\n% TEST FIXTURE, not a real permit: {label}\n%%EOF\n".encode()


async def attach_document(admin: Actor, storage: FakeStorage, project_id: str, kind: str) -> None:
    ticket = await admin.post(
        f"/projects/{project_id}/documents/upload-url",
        json={"kind": kind, "filename": f"{kind}.pdf", "content_type": "application/pdf"},
    )
    assert ticket.status_code == 200, ticket.text
    body = ticket.json()
    assert body["upload_url"].startswith("http://storage.test/")
    data = synthetic_pdf(kind)
    storage.put(body["object_key"], data, "application/pdf")  # the client's presigned PUT
    r = await admin.post(
        f"/projects/{project_id}/documents",
        json={
            "object_key": body["object_key"],
            "kind": kind,
            "filename": f"{kind}.pdf",
            "sha256": hashlib.sha256(data).hexdigest(),
        },
    )
    assert r.status_code == 201, r.text


async def photo_evidence(
    agent: Actor,
    storage: FakeStorage,
    sessionmaker: async_sessionmaker[AsyncSession],
    period_id: str,
    seed: int,
    plot_code: str,
    at: tuple[float, float],
) -> dict[str, Any]:
    """Nonce → presigned upload → submission → worker evidence checks."""
    nonce = (await agent.post(f"/periods/{period_id}/nonces")).json()["nonce"]
    ticket = (
        await agent.post(
            f"/periods/{period_id}/photos/upload-url", json={"content_type": "image/jpeg"}
        )
    ).json()
    now_ist = datetime.now(UTC) + timedelta(hours=5, minutes=30)
    photo = make_jpeg(
        seed=seed, lon=at[0], lat=at[1], when=now_ist.strftime("%Y:%m:%d %H:%M:%S"), offset="+05:30"
    )
    storage.put(ticket["object_key"], photo, "image/jpeg")
    r = await agent.post(
        f"/periods/{period_id}/submissions",
        json={
            "object_key": ticket["object_key"],
            "sha256": hashlib.sha256(photo).hexdigest(),
            "nonce": nonce,
            "kind": "plot_centre",
            "plot_code": plot_code,
            "lon": at[0],
            "lat": at[1],
            "gps_accuracy_m": 4.0,
            "captured_at": datetime.now(UTC).isoformat(),
            "device_model": "FieldPhone 1",
            "app_version": "0.1.0",
        },
    )
    assert r.status_code == 202, r.text
    return await run_checks(sessionmaker, storage, uuid.UUID(r.json()["id"]), 25 * 1024 * 1024)


async def build_report(
    admin: Actor,
    period_id: str,
    storage: FakeStorage,
    enqueued: list[tuple[str, tuple[str, ...], str]],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> dict[str, Any]:
    r = await admin.post(f"/periods/{period_id}/reports", json={"strata_area_ha": STRATA_AREA})
    assert r.status_code == 202, r.text
    name, (pid, actor_id, options), queue = enqueued[-1]
    assert (name, queue) == ("aqua.reports.build_report", "reports")
    built = await build_report_core(
        sessionmaker, storage, uuid.UUID(pid), uuid.UUID(actor_id), json.loads(options)
    )
    assert built["status"] == "built", built
    report: dict[str, Any] = (await admin.get(f"/periods/{period_id}/reports")).json()[0]
    assert report["status"] == "under_review"
    return report


async def anchor(relayer: Relayer, anvil_url: str, report_id: str) -> dict[str, Any]:
    steps = []
    for _ in range(12):
        state = await anchor_report_core(relayer, uuid.UUID(report_id))
        steps.append(state.get("step"))
        if state["state"] == "anchored":
            state["steps"] = steps
            return state
        await mine(anvil_url, relayer.policy.confirmations)
    raise AssertionError(f"not anchored: {steps}")


async def measure(
    agent: Actor, period_id: str, plot_ids: dict[str, str], day: str, trees: Any
) -> None:
    for code, plot_id in plot_ids.items():
        r = await agent.post(
            f"/periods/{period_id}/plots/{plot_id}/measurements",
            json={"measured_on": day, "trees": trees(code)},
        )
        assert r.status_code == 201, r.text


# ---------------------------------------------------------------- the scenario


async def test_full_lifecycle_register_to_retire(
    client: httpx.AsyncClient,
    sql: Any,
    storage: FakeStorage,
    enqueued: list[tuple[str, tuple[str, ...], str]],
    sessionmaker: async_sessionmaker[AsyncSession],
    relayer: Relayer,
    anvil_url: str,
    deployment: Deployment,
    chain_settings: Settings,
    clock: dict[str, float],
) -> None:
    started = time.monotonic()

    # 1. users: org admin, field agent, buyer; verifier + registry admin with TOTP
    admin = await login(client, *await register(client, "Org Admin"))
    agent = await login(client, *await register(client, "Field Agent"))
    buyer_user = await login(client, *await register(client, "Buyer"))
    verifier = await staff(client, sql, clock, "Independent Verifier", "verifier")
    registry = await staff(client, sql, clock, "Registry Admin", "registry_admin")

    # 2. org + project (Bhitarkanika), tenure documents, submit
    org = (
        await admin.post("/orgs", json={"name": "Kendrapara Test Trust", "org_type": "ngo"})
    ).json()
    r = await admin.post(
        f"/orgs/{org['id']}/members", json={"email": agent.email, "role": "field_agent"}
    )
    assert r.status_code == 201, r.text
    r = await admin.post("/projects", json=project_payload(org["id"]))
    assert r.status_code == 201, r.text
    project = r.json()
    pid = project["id"]
    assert project["status"] == "draft"
    blocked = await admin.post(f"/projects/{pid}/submit")
    assert blocked.status_code == 422  # tenure evidence missing
    for kind in ("forest_dept_agreement", "crz_clearance", "carbon_rights_agreement"):
        await attach_document(admin, storage, pid, kind)
    r = await admin.post(f"/projects/{pid}/submit")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "submitted"

    # 3. baseline period: plots, measurements (field agent), photo evidence + worker checks
    r = await admin.post(
        f"/projects/{pid}/periods",
        json={"kind": "baseline", "start_date": "2026-06-01", "end_date": "2027-12-31"},
    )
    assert r.status_code == 201, r.text
    baseline_id = r.json()["id"]
    plot_ids: dict[str, str] = {}
    for code, (stratum, lon, lat, _) in PLOTS.items():
        r = await admin.post(
            f"/projects/{pid}/plots",
            json={
                "code": code,
                "stratum": stratum,
                "lon": lon,
                "lat": lat,
                "area_m2": PLOT_AREA_M2,
                "marker_id": f"TAG-{code}",
            },
        )
        assert r.status_code == 201, r.text
        plot_ids[code] = r.json()["id"]
    await measure(agent, baseline_id, plot_ids, "2026-10-15", lambda _: BASELINE_TREES)
    for seed, code in enumerate(plot_ids, start=1):
        _, lon, lat, _ = PLOTS[code]
        checked = await photo_evidence(
            agent, storage, sessionmaker, baseline_id, seed, code, (lon, lat)
        )
        assert checked["status"] == "checked", checked
        assert checked["trust_score"] == 100

    # 4. baseline report → independent verifier approves → anchored → project registered
    baseline = await build_report(admin, baseline_id, storage, enqueued, sessionmaker)
    assert (
        await admin.post(f"/reports/{baseline['id']}/review", json={"decision": "approve"})
    ).status_code == 403
    r = await verifier.post(
        f"/reports/{baseline['id']}/review",
        json={"decision": "approve", "comment": "Plots verified"},
    )
    assert r.status_code == 200, r.text  # no conflict of interest: verifier is outside the org
    assert enqueued[-1] == ("aqua.reports.anchor", (baseline["id"],), "chain")
    state = await anchor(relayer, anvil_url, baseline["id"])
    assert "register_project" in state["steps"]
    assert (await admin.get(f"/projects/{pid}")).json()["status"] == "registered"
    async with sessionmaker() as session:
        wallet = await session.get(OrgWallet, uuid.UUID(org["id"]))
    assert wallet is not None
    registry_c = relayer.client.contract("AquaRegistry")
    onchain_project = await registry_c.functions.getProject(project_id_bytes32(pid)).call()
    assert onchain_project[0] == wallet.address

    # 5. monitoring period → report → approve → anchored (submitReport + approveReport)
    r = await admin.post(
        f"/projects/{pid}/periods",
        json={"kind": "monitoring", "start_date": "2028-01-01", "end_date": "2029-12-31"},
    )
    assert r.status_code == 201, r.text
    monitoring_id = r.json()["id"]
    await measure(agent, monitoring_id, plot_ids, "2029-11-15", lambda code: PLOTS[code][3])
    checked = await photo_evidence(agent, storage, sessionmaker, monitoring_id, 99, "D01", INSIDE)
    assert checked["status"] == "checked", checked
    monitoring = await build_report(admin, monitoring_id, storage, enqueued, sessionmaker)
    lccc = monitoring["lccc_units"]
    assert lccc > 0
    r = await verifier.post(f"/reports/{monitoring['id']}/review", json={"decision": "approve"})
    assert r.status_code == 200, r.text
    state = await anchor(relayer, anvil_url, monitoring["id"])
    assert {"submit_report", "approve_report"} <= set(state["steps"])
    chain_report_id = state["chain_report_id"]
    assert (await admin.get(f"/projects/{pid}")).json()["status"] == "monitoring"

    # report hash: recomputed SHA-256 of the stored JSON == DB == on-chain
    async with sessionmaker() as session:
        row = await session.get(MRVReport, uuid.UUID(monitoring["id"]))
        assert row is not None
        stored_json = storage.objects[row.json_key][0]
    recomputed = hashlib.sha256(stored_json).hexdigest()
    onchain_report = await registry_c.functions.getReport(chain_report_id).call()
    assert recomputed == row.report_hash == monitoring["report_hash"] == onchain_report[3].hex()
    assert onchain_report[6] == 2  # Approved
    assert onchain_report[4] == lccc  # approved lCCC on-chain

    # 6. issue lCCC to the org's custodial wallet, then index
    issued = await issue_credits_core(
        relayer,
        report_id=chain_report_id,
        credit_type="long_term",
        org_id=uuid.UUID(org["id"]),
        amount=lccc,
        expires_at=int(time.time()) + TEN_YEARS_S,
        entity_type="mrv_report",
        entity_id=monitoring["id"],
    )
    if issued["status"] == ChainTxStatus.SUBMITTED.value:
        await mine(anvil_url, relayer.policy.confirmations)
        issued = await send_core(relayer, uuid.UUID(issued["id"]))
    assert issued["status"] == "confirmed", issued
    async with sessionmaker() as session:
        issue_tx = await session.get(ChainTransaction, uuid.UUID(issued["id"]))
    assert issue_tx is not None
    token_id = int(actions.event_value(issue_tx, "CreditsIssued", "tokenId"))
    credit = relayer.client.contract("AquaCredit")
    assert await credit.functions.balanceOf(wallet.address, token_id).call() == lccc
    config = indexer_config(chain_settings)
    await mine(anvil_url, 5)
    await index_events_core(sessionmaker, relayer.client, config)

    # 7. marketplace: the org wallet lists; the buyer (own Anvil key) buys and retires
    w3 = w3_for(anvil_url)
    deployer = Account.from_key(signers().derive(0).key)
    seller = Account.from_key(signers().derive(wallet.derivation_index).key)
    buyer = Account.from_key(signers().derive(BUYER_INDEX).key)
    await rpc(anvil_url, "anvil_setBalance", [seller.address, hex(10**18)])
    market = relayer.client.contract("AquaMarketplace")
    usdc = relayer.client.contract("MockUSDC")
    bought, retired = min(lccc, 10), min(lccc, 4)
    total = bought * PRICE_PER_UNIT
    receipts = [
        await call_as(w3, seller, credit.functions.setApprovalForAll(deployment.marketplace, True)),
        await call_as(w3, seller, market.functions.list(token_id, lccc, PRICE_PER_UNIT)),
        await call_as(w3, deployer, usdc.functions.mint(buyer.address, total)),
        await call_as(w3, buyer, usdc.functions.approve(deployment.marketplace, total)),
        await call_as(w3, buyer, market.functions.buy(1, bought, total)),
    ]
    retire_receipt = await call_as(
        w3,
        buyer,
        credit.functions.retire(token_id, retired, "Example Buyer Ltd (test)", "FY2029 offset"),
    )
    receipts.append(retire_receipt)
    retire_block = await w3.eth.get_block(retire_receipt["blockNumber"])
    await w3.provider.disconnect()  # type: ignore[attr-defined]
    assert all(rc["status"] == 1 for rc in receipts)
    assert await credit.functions.balanceOf(buyer.address, token_id).call() == bought - retired
    await mine(anvil_url, 5)
    await index_events_core(sessionmaker, relayer.client, config)

    # 8. public registry (no login): vintage, retirement with block time, verify-hash
    vintages = (await client.get("/public/vintages", params={"project_id": pid})).json()["items"]
    assert len(vintages) == 1
    vintage = vintages[0]
    assert vintage["token_id"] == token_id
    assert vintage["credit_unit"] == "lCCC"
    assert (vintage["issued"], vintage["retired"]) == (lccc, retired)
    assert vintage["outstanding"] == vintage["issued"] - vintage["retired"] - vintage["replaced"]
    held = await credit.functions.balanceOf(wallet.address, token_id).call()
    held += await credit.functions.balanceOf(buyer.address, token_id).call()
    assert vintage["outstanding"] == held == lccc - retired
    assert vintage["issuances"][0]["tx_hash"] == issue_tx.tx_hash
    cert = (await client.get("/public/retirements/1")).json()
    assert (cert["amount"], cert["token_id"]) == (retired, token_id)
    assert cert["tx_hash"] == retire_receipt["transactionHash"].to_0x_hex()
    assert datetime.fromisoformat(cert["block_time"]) == datetime.fromtimestamp(
        int(retire_block["timestamp"]), UTC
    )
    v = (await client.post("/public/verify-hash", json={"sha256_hex": recomputed})).json()
    assert v["found"] is True
    assert [(m["source"], m["anchored"], m["report_id"]) for m in v["matches"]] == [
        ("chain", True, chain_report_id)
    ]
    verified = (await verifier.get(f"/reports/{monitoring['id']}/verify")).json()
    assert verified["ok"] is True
    assert verified["onchain_source"] == "indexer"

    # 9. every relayed transaction is real: confirmed with a receipt on Anvil
    async with sessionmaker() as session:
        txs = list(await session.scalars(select(ChainTransaction)))
    assert {t.purpose.value for t in txs} >= {
        "register_project",
        "submit_report",
        "approve_report",
        "issue_credits",
    }
    for tx in txs:
        assert tx.status is ChainTxStatus.CONFIRMED, (tx.purpose, tx.status, tx.error)
        assert tx.tx_hash is not None
        assert TX_HASH.match(tx.tx_hash)
        receipt = await relayer.client.w3.eth.get_transaction_receipt(tx.tx_hash)  # type: ignore[arg-type]
        assert receipt["status"] == 1
        assert receipt["blockNumber"] == tx.block_number

    # 10. stewardship score and audit-log integrity
    steward = await admin.get(f"/projects/{pid}/stewardship")
    assert steward.status_code == 200, steward.text
    assert steward.json()["score"] is not None
    assert (await buyer_user.get(f"/projects/{pid}/stewardship")).status_code == 404
    audit_ok = await registry.get("/audit/verify")
    assert audit_ok.status_code == 200, audit_ok.text
    assert audit_ok.json()["ok"] is True
    actions_logged = {
        e["action"] for e in (await registry.get("/audit", params={"limit": 1000})).json()
    }
    assert {
        "project.submitted",
        "project.document_attached",
        "report.generated",
        "report.approved",
        "report.anchored",
        "project.status_changed",
    } <= actions_logged

    assert time.monotonic() - started < 60
