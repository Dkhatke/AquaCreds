"""Forward funding (AquaForwardFund) end to end on real PostGIS + Anvil: buyer request (buyer's own
key), relayed offers from custodial org wallets via the API, award, delivery, refund, indexing,
and the read API's escrow arithmetic checked against the contract's views."""

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from api_testlib import project_payload
from chain_testlib import (
    Deployment,
    call_as,
    deploy_forward_fund,
    deploy_suite,
    mine,
    rpc,
    signers,
    w3_for,
)
from eth_account import Account
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from aqua_chain import actions, artifacts
from aqua_chain.forward_fund import DISCLAIMER, EVENTS
from aqua_chain.relayer import Relayer
from aqua_chain.runtime import build_relayer, indexer_config
from aqua_core.db import session_factory
from aqua_core.models import ChainEvent, ChainTransaction, OrgWallet
from aqua_core.settings import Settings
from aqua_worker.tasks.chain import (
    approve_report_core,
    index_events_core,
    issue_credits_core,
    register_project_core,
    send_core,
    submit_report_core,
)

USDC = 10**6  # mUSDC has 6 decimals
DAY = 24 * 3600
TEN_YEARS = 10 * 365 * DAY


@pytest.fixture
async def deployment(anvil_url: str) -> Deployment:
    return await deploy_forward_fund(anvil_url, await deploy_suite(anvil_url))


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


class Env:
    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)


async def _confirm(relayer: Relayer, anvil_url: str, tx_id: Any) -> dict[str, Any]:
    first = await send_core(relayer, uuid.UUID(str(tx_id)))
    if first["status"] != "submitted":
        return first
    await mine(anvil_url, relayer.policy.confirmations)
    return await send_core(relayer, uuid.UUID(str(tx_id)))


async def _index(relayer: Relayer, sessionmaker: Any, settings: Settings) -> dict[str, Any]:
    return await index_events_core(sessionmaker, relayer.client, indexer_config(settings))


def _square(lon: float) -> dict[str, Any]:
    lat, d = 20.70, 0.02
    ring = [[lon, lat], [lon + d, lat], [lon + d, lat + d], [lon, lat + d], [lon, lat]]
    return {"type": "Polygon", "coordinates": [ring]}


async def _project(admin: Any, org_id: str, sql: Any, lon: float) -> str:
    r = await admin.post("/projects", json=project_payload(org_id, boundary=_square(lon)))
    assert r.status_code == 201, r.text
    await sql("UPDATE projects SET status='submitted' WHERE id=:id", id=r.json()["id"])
    return str(r.json()["id"])


async def _approve_capacity(
    relayer: Relayer, anvil_url: str, project_id: str, lccc: int, period_end: int
) -> int:
    sub = await submit_report_core(
        relayer,
        project_id=uuid.UUID(project_id),
        period_end=period_end,
        report_hash=uuid.uuid4().hex * 2,
        lccc=lccc,
        evidence_uri="urn:aquacreds:evidence:ff",
    )
    sub = await _confirm(relayer, anvil_url, sub["id"])
    assert sub["status"] == "confirmed", sub
    async with relayer.sessionmaker() as session:
        tx = await session.get(ChainTransaction, uuid.UUID(sub["id"]))
        assert tx is not None
        report_id = int(actions.event_value(tx, "ReportSubmitted", "reportId"))
    ok = await approve_report_core(
        relayer, report_id=report_id, entity_type="project", entity_id=project_id
    )
    assert (await _confirm(relayer, anvil_url, ok["id"]))["status"] == "confirmed"
    return report_id


async def _chain_now(w3: Any) -> int:
    return int((await w3.eth.get_block("latest"))["timestamp"])


async def _warp(anvil_url: str, seconds: int) -> None:
    await rpc(anvil_url, "evm_increaseTime", [seconds])
    await mine(anvil_url, 1)


@pytest.fixture
async def env(
    make_actor: Any,
    sql: Any,
    relayer: Relayer,
    anvil_url: str,
    deployment: Deployment,
    sessionmaker: Any,
    chain_settings: Settings,
) -> AsyncIterator[Env]:
    admin = await make_actor("NGO Admin")
    org = (await admin.post("/orgs", json={"name": "Sundarban Trust", "org_type": "ngo"})).json()
    project_a = await _project(admin, org["id"], sql, 86.85)
    project_b = await _project(admin, org["id"], sql, 86.90)
    for pid in (project_a, project_b):
        reg = await register_project_core(relayer, uuid.UUID(pid))
        assert (await _confirm(relayer, anvil_url, reg["id"]))["status"] == "confirmed"
    async with sessionmaker() as session:
        wallet = await session.get(OrgWallet, uuid.UUID(org["id"]))
    assert wallet is not None
    # custodial org wallets need gas; on Amoy the platform tops them up
    await rpc(anvil_url, "anvil_setBalance", [wallet.address, hex(10**20)])

    w3 = w3_for(anvil_url)
    buyer = Account.from_key(signers().derive(5).key)
    deployer = Account.from_key(signers().derive(0).key)
    fund = w3.eth.contract(address=deployment.forward_fund, abi=artifacts.abi("AquaForwardFund"))  # type: ignore[arg-type]
    usdc = w3.eth.contract(address=deployment.usdc, abi=artifacts.abi("MockUSDC"))  # type: ignore[arg-type]
    credit = w3.eth.contract(address=deployment.credit, abi=artifacts.abi("AquaCredit"))  # type: ignore[arg-type]
    await call_as(w3, deployer, usdc.functions.mint(buyer.address, 10_000 * USDC))
    yield Env(
        admin=admin,
        org_id=org["id"],
        a=project_a,
        b=project_b,
        wallet=wallet,
        w3=w3,
        buyer=buyer,
        fund=fund,
        usdc=usdc,
        credit=credit,
        index=lambda: _index(relayer, sessionmaker, chain_settings),
    )
    await w3.provider.disconnect()  # type: ignore[attr-defined]


async def _create_request(e: Env, quantity: int, max_price: int, upfront_bps: int) -> int:
    now = await _chain_now(e.w3)
    await call_as(e.w3, e.buyer, e.usdc.functions.approve(e.fund.address, quantity * max_price))
    await call_as(
        e.w3,
        e.buyer,
        e.fund.functions.createRequest(quantity, max_price, now + DAY, now + 30 * DAY, upfront_bps),
    )
    return int(await e.fund.functions.requestCount().call())


async def _offer(e: Env, project: str, **body: Any) -> Any:
    return await e.admin.post(f"/projects/{project}/forward-fund/offers", json=body)


# ---------------------------------------------------------------- the full auction


async def test_forward_fund_auction_end_to_end(
    env: Env,
    relayer: Relayer,
    anvil_url: str,
    enqueued: list[Any],
    make_actor: Any,
    sessionmaker: Any,
    sql: Any,
) -> None:
    e = env
    rid = await _create_request(e, quantity=80, max_price=10 * USDC, upfront_bps=2000)
    await e.index()

    # --- open request, with an explicit disclaimer and the upfront share flagged as buyer risk
    listing = await e.admin.get("/forward-fund/requests", params={"status": "open"})
    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert body["disclaimer"] == DISCLAIMER
    assert "not a guarantee of credits" in body["disclaimer"]
    (item,) = body["items"]
    assert item["request_id"] == rid
    assert item["escrow_deposited"] == 800 * USDC == item["escrow_left"]
    assert item["upfront"] == {
        "bps": 2000,
        "share_pct": 20.0,
        "amount_paid": 0,
        "buyer_risk": True,
        "refundable": False,
        "note": item["upfront"]["note"],
    }
    assert (await e.admin.get("/forward-fund/requests", params={"status": "awarded"})).json()[
        "items"
    ] == []

    # --- eligibility: no verified removal capacity yet → refused before anything is relayed
    r = await _offer(e, e.a, request_id=rid, price_per_unit=8 * USDC, quantity=60)
    assert r.status_code == 409
    assert r.json()["detail"] == "no verified removal capacity yet"
    assert enqueued == []

    report_a = await _approve_capacity(relayer, anvil_url, e.a, lccc=120, period_end=2029)
    await _approve_capacity(relayer, anvil_url, e.b, lccc=40, period_end=2029)
    await e.index()

    # --- role checks: member (non-admin) 403, outsider 404, registry admin 403
    member = await make_actor("Field Member")
    added = await e.admin.post(
        f"/orgs/{e.org_id}/members", json={"email": member.email, "role": "member"}
    )
    assert added.status_code == 201, added.text
    outsider = await make_actor("Outsider")
    payload = {"request_id": rid, "price_per_unit": 8 * USDC, "quantity": 60}
    assert (
        await member.post(f"/projects/{e.a}/forward-fund/offers", json=payload)
    ).status_code == 403
    assert (
        await outsider.post(f"/projects/{e.a}/forward-fund/offers", json=payload)
    ).status_code == 404
    assert (await outsider.get(f"/projects/{e.a}/forward-fund")).status_code == 404
    assert (await member.get(f"/projects/{e.a}/forward-fund")).status_code == 200

    # --- eligibility: capacity exceeded; pre-checks against the indexed request
    over = await _offer(e, e.b, request_id=rid, price_per_unit=9 * USDC, quantity=41)
    assert over.status_code == 409
    assert "exceeds the project's available removal capacity 40" in over.json()["detail"]
    too_dear = await _offer(e, e.a, request_id=rid, price_per_unit=11 * USDC, quantity=10)
    assert too_dear.status_code == 422
    assert (await _offer(e, e.a, request_id=999, price_per_unit=1, quantity=1)).status_code == 404

    # --- relayed offer, signed by the org's custodial wallet (the on-chain project owner)
    r = await _offer(e, e.a, request_id=rid, price_per_unit=8 * USDC, quantity=60)
    assert r.status_code == 202, r.text
    tx_a = r.json()
    assert tx_a["status"] == "queued"
    assert tx_a["tx_hash"] is None
    assert tx_a["from_address"] == e.wallet.address
    assert tx_a["function"] == "submitOffer"
    assert enqueued[-1] == ("aqua.chain.send", (tx_a["id"],), "chain")
    # a second relay for the same request is refused while the first is in flight
    assert (
        await _offer(e, e.a, request_id=rid, price_per_unit=7 * USDC, quantity=5)
    ).status_code == 409

    done = await _confirm(relayer, anvil_url, tx_a["id"])
    assert done["status"] == "confirmed", done
    receipt = await e.w3.eth.get_transaction_receipt(done["tx_hash"])
    assert receipt["status"] == 1
    assert receipt["from"] == e.wallet.address

    r = await _offer(e, e.b, request_id=rid, price_per_unit=9 * USDC, quantity=30)
    assert r.status_code == 202, r.text
    assert (await _confirm(relayer, anvil_url, r.json()["id"]))["status"] == "confirmed"
    await e.index()

    # duplicate offer on the same request (now indexed) → 409 before relaying
    dup = await _offer(e, e.a, request_id=rid, price_per_unit=7 * USDC, quantity=5)
    assert dup.status_code == 409
    assert "already has active offer" in dup.json()["detail"]

    # --- withdraw B's offer (relayed), then B offers again
    detail = (await e.admin.get(f"/forward-fund/requests/{rid}")).json()
    offer_b = next(o for o in detail["offers"] if o["project_id"] == e.b)
    assert offer_b["status"] == "active"
    w = await e.admin.client.delete(
        f"/projects/{e.b}/forward-fund/offers/{offer_b['offer_id']}", headers=e.admin.headers
    )
    assert w.status_code == 202, w.text
    assert (
        await member.client.delete(
            f"/projects/{e.b}/forward-fund/offers/{offer_b['offer_id']}", headers=member.headers
        )
    ).status_code == 403
    assert (await _confirm(relayer, anvil_url, w.json()["id"]))["status"] == "confirmed"
    await e.index()
    gone = await e.admin.client.delete(
        f"/projects/{e.b}/forward-fund/offers/{offer_b['offer_id']}", headers=e.admin.headers
    )
    assert gone.status_code == 409  # already withdrawn
    r = await _offer(e, e.b, request_id=rid, price_per_unit=9 * USDC, quantity=30)
    assert r.status_code == 202, r.text
    assert (await _confirm(relayer, anvil_url, r.json()["id"]))["status"] == "confirmed"

    # --- award after the offer window (anyone may call; here the buyer, with its own key)
    await _warp(anvil_url, DAY + 1)
    await call_as(e.w3, e.buyer, e.fund.functions.award(rid))
    await e.index()

    detail = (await e.admin.get(f"/forward-fund/requests/{rid}")).json()
    assert detail["status"] == "awarded"
    assert detail["awarded_quantity"] == 80 <= detail["quantity"]
    awards = {a["project_id"]: a for a in detail["awards"]}
    a_award, b_award = awards[e.a], awards[e.b]
    assert (a_award["quantity"], b_award["quantity"]) == (60, 20)  # cheapest first, B partial
    assert sum(a["quantity"] for a in detail["awards"]) <= detail["quantity"]
    assert a_award["upfront_paid"] == 60 * 8 * USDC * 2000 // 10_000
    assert b_award["upfront_paid"] == 20 * 9 * USDC * 2000 // 10_000
    assert all(a["upfront_buyer_risk"] is True for a in detail["awards"])
    assert detail["upfront"]["amount_paid"] == a_award["upfront_paid"] + b_award["upfront_paid"]
    assert detail["unused_escrow_refunded"] == 800 * USDC - (480 + 180) * USDC
    statuses = sorted(o["status"] for o in detail["offers"])
    assert statuses == ["awarded", "awarded", "withdrawn"]
    assert detail["disclaimer"] == DISCLAIMER

    # --- deliver 30 LongTerm credits of project A (org wallet's own key: not an API action)
    now = await _chain_now(e.w3)
    issued = await issue_credits_core(
        relayer,
        report_id=report_a,
        credit_type="long_term",
        org_id=uuid.UUID(e.org_id),
        amount=30,
        expires_at=now + TEN_YEARS,
        entity_type="project",
        entity_id=e.a,
    )
    issued = await _confirm(relayer, anvil_url, issued["id"])
    assert issued["status"] == "confirmed", issued
    async with sessionmaker() as session:
        issue_tx = await session.get(ChainTransaction, uuid.UUID(issued["id"]))
        token_id = int(actions.event_value(issue_tx, "CreditsIssued", "tokenId"))
    owner = Account.from_key(signers().derive(e.wallet.derivation_index).key)
    await call_as(e.w3, owner, e.credit.functions.setApprovalForAll(e.fund.address, True))
    await call_as(e.w3, owner, e.fund.functions.deliver(a_award["award_id"], token_id, 30))
    await e.index()

    detail = (await e.admin.get(f"/forward-fund/requests/{rid}", params={"onchain": True})).json()
    a_award = next(a for a in detail["awards"] if a["project_id"] == e.a)
    assert a_award["delivered_quantity"] == 30
    assert a_award["released"] == (480 - 96) * USDC * 30 // 60
    assert detail["delivered_quantity"] == 30
    check = detail["onchain"]
    assert check["consistent"] is True, check["mismatches"]
    assert check["escrow"] == detail["escrow_left"]
    for award in detail["awards"]:
        assert check["refundable"][str(award["award_id"])] == award["refundable_after_deadline"]

    # --- project view: offers, awards, capacity netted by open commitments
    view = (await e.admin.get(f"/projects/{e.a}/forward-fund")).json()
    assert view["disclaimer"] == DISCLAIMER
    assert view["capacity"]["total"] == 120
    assert view["capacity"]["committed"] == 30  # 60 awarded − 30 delivered
    assert view["capacity"]["available"] == 90
    assert [o["status"] for o in view["offers"]] == ["awarded"]
    assert len(view["awards"]) == 1

    # --- after the deadline: refund A's undelivered escrow; the amount equals our prediction
    predicted = a_award["refundable_after_deadline"]
    await _warp(anvil_url, 30 * DAY)
    await call_as(e.w3, e.buyer, e.fund.functions.refundUndelivered(a_award["award_id"]))
    await e.index()
    async with sessionmaker() as session:
        refund_event = await session.scalar(
            select(ChainEvent).where(ChainEvent.event == "UndeliveredRefunded")
        )
    assert refund_event is not None
    assert int(refund_event.args["refund"]) == predicted
    detail = (await e.admin.get(f"/forward-fund/requests/{rid}", params={"onchain": True})).json()
    assert detail["onchain"]["consistent"] is True
    assert detail["status"] == "awarded"  # B is still open
    await call_as(e.w3, e.buyer, e.fund.functions.refundUndelivered(b_award["award_id"]))
    await e.index()
    detail = (await e.admin.get(f"/forward-fund/requests/{rid}", params={"onchain": True})).json()
    assert detail["status"] == "settled"
    assert detail["escrow_left"] == 0 == detail["onchain"]["escrow"]
    assert detail["refundable_after_deadline"] == 0
    settled = (await e.admin.get("/forward-fund/requests", params={"status": "settled"})).json()
    assert [i["request_id"] for i in settled["items"]] == [rid]
    # upfront shares were not refunded: buyer got back unused + undelivered only
    refunded = detail["unused_escrow_refunded"] + detail["refunded_undelivered"]
    assert int(await e.fund.functions.claimable(e.buyer.address).call()) == refunded

    # --- buyer withdraws with its own key; every fund event name landed in chain_events
    await call_as(e.w3, e.buyer, e.fund.functions.withdraw())
    await e.index()
    async with sessionmaker() as session:
        names = set(
            await session.scalars(
                select(ChainEvent.event).where(ChainEvent.contract == "AquaForwardFund")
            )
        )
    assert set(EVENTS) <= names  # (plus AccessControl RoleGranted from the constructor)

    # audit chain still valid, with the relayed actions recorded
    registry = await make_actor("Registry Admin")
    await sql("UPDATE users SET platform_role='registry_admin' WHERE id=:id", id=registry.id)
    verify = await registry.get("/audit/verify")
    assert verify.json()["ok"] is True


async def test_offer_requires_onchain_registration(make_actor: Any, sql: Any) -> None:
    admin = await make_actor("NGO Admin")
    org = (await admin.post("/orgs", json={"name": "Kutch Trust", "org_type": "ngo"})).json()
    pid = await _project(admin, org["id"], sql, 86.95)
    r = await admin.post(
        f"/projects/{pid}/forward-fund/offers",
        json={"request_id": 1, "price_per_unit": 1, "quantity": 1},
    )
    assert r.status_code == 409
    assert "not registered on-chain" in r.json()["detail"]
    assert (await admin.get(f"/forward-fund/requests/{12345}")).status_code == 404
    unknown = await admin.client.delete(
        f"/projects/{pid}/forward-fund/offers/1", headers=admin.headers
    )
    assert unknown.status_code == 409  # not registered on-chain yet
    view = (await admin.get(f"/projects/{pid}/forward-fund")).json()
    assert view["capacity"]["total"] == 0
    assert view["offers"] == [] == view["awards"]
