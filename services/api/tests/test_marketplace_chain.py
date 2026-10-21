"""Marketplace end to end on Anvil: register → submit → approve → issue (platform roles), then
list → buy → cancel → retire signed by the seller's and buyer's own keys (as MetaMask would),
indexed by the real indexer and read back through the public marketplace API + certificate PDF."""

import hashlib
import io
import re
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from api_testlib import project_payload, square
from chain_testlib import Deployment, call_as, deploy_suite, signers, w3_for
from eth_account import Account
from eth_account.signers.local import LocalAccount
from pypdf import PdfReader
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from aqua_api.services import marketplace as market
from aqua_chain import artifacts
from aqua_chain.client import ChainClient
from aqua_chain.encoding import project_id_bytes32
from aqua_chain.indexer import index_events
from aqua_chain.runtime import indexer_config
from aqua_core.db import session_factory
from aqua_core.models import ChainSigner
from aqua_core.settings import Settings

TEN_YEARS = 10 * 365 * 24 * 3600
PRICE = 2_500_000  # 2.5 mUSDC per tCO2e


@pytest.fixture
async def deployment(anvil_url: str) -> Deployment:
    return await deploy_suite(anvil_url)


@pytest.fixture
def settings_overrides(anvil_url: str, deployment: Deployment) -> dict[str, Any]:
    # No CHAIN_PAYMENT_TOKEN_DECIMALS: the API reads decimals() from the payment token.
    return deployment.settings(anvil_url)


@pytest.fixture
async def sessionmaker(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(database_url)
    yield session_factory(engine)
    await engine.dispose()


def _account(index: int) -> LocalAccount:
    return Account.from_key(signers().derive(index).key)


async def test_list_buy_retire_end_to_end(
    anvil_url: str,
    deployment: Deployment,
    settings_overrides: dict[str, Any],
    database_url: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    client: httpx.AsyncClient,
    make_actor: Any,
    sql: Any,
) -> None:
    market._TOKEN_CACHE.clear()
    admin = await make_actor("Chain Seller Admin")
    org = (await admin.post("/orgs", json={"name": "Mahanadi Delta", "org_type": "ngo"})).json()
    r = await admin.post("/projects", json=project_payload(org["id"], boundary=square(86.85, 20.7)))
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    await sql("UPDATE projects SET status='registered' WHERE id=:id", id=pid)

    w3 = w3_for(anvil_url)
    s = signers()
    deployer, seller, buyer = _account(0), _account(7), _account(8)
    registrar = s.account(ChainSigner.REGISTRAR)
    verifier = s.account(ChainSigner.VERIFIER)
    issuer = s.account(ChainSigner.ISSUER)

    def contract(name: str, address: str) -> Any:
        return w3.eth.contract(address=address, abi=artifacts.abi(name))  # type: ignore[arg-type]

    reg = contract("AquaRegistry", deployment.registry)
    credit = contract("AquaCredit", deployment.credit)
    mkt = contract("AquaMarketplace", deployment.marketplace)
    usdc = contract("MockUSDC", deployment.usdc)
    pkey = project_id_bytes32(pid)
    report_hash = hashlib.sha256(b"anvil mrv record").digest()
    try:
        await call_as(
            w3,
            registrar,
            reg.functions.registerProject(
                pkey, seller.address, b"\x44" * 32, 2026, f"urn:aquacreds:project:{pid}"
            ),
        )
        await call_as(
            w3, registrar, reg.functions.submitReport(pkey, 2029, report_hash, 30, "urn:x")
        )
        await call_as(w3, verifier, reg.functions.approveReport(1))
        expiry = int((await w3.eth.get_block("latest"))["timestamp"]) + TEN_YEARS
        await call_as(w3, issuer, credit.functions.issue(1, 1, seller.address, 30, expiry))
        token_id = 3  # tokenIdFor(report 1, LongTerm)

        # Seller lists from their own wallet; buyer pays from theirs.
        await call_as(w3, seller, credit.functions.setApprovalForAll(deployment.marketplace, True))
        await call_as(w3, seller, mkt.functions.list(token_id, 10, PRICE))  # listing 1
        await call_as(w3, seller, mkt.functions.list(token_id, 5, PRICE * 2))  # listing 2
        await call_as(w3, seller, mkt.functions.cancel(2))
        await call_as(w3, deployer, usdc.functions.mint(buyer.address, 100 * 10**6))
        await call_as(w3, buyer, usdc.functions.approve(deployment.marketplace, 100 * 10**6))
        await call_as(w3, buyer, mkt.functions.buy(1, 4, 4 * PRICE))
        retire = await call_as(
            w3, buyer, credit.functions.retire(token_id, 3, "Acme Steel Ltd", "FY2027 offset")
        )
        retire_tx = "0x" + bytes(retire["transactionHash"]).hex()
    finally:
        await w3.provider.disconnect()  # type: ignore[attr-defined]

    settings = Settings(_env_file=None, database_url=database_url, **settings_overrides)
    chain = ChainClient.from_settings(settings)
    try:
        result = await index_events(sessionmaker, chain, indexer_config(settings))
    finally:
        await chain.close()
    assert result.events["AquaMarketplace"] >= 4  # + OwnershipTransferred from the constructor
    rows = await sql(
        "SELECT event, count(*) FROM chain_events WHERE contract = 'AquaMarketplace' "
        "AND block_time IS NOT NULL AND event IN ('Listed', 'Cancelled', 'Purchased') GROUP BY 1"
    )
    assert dict(rows) == {"Listed": 2, "Cancelled": 1, "Purchased": 1}

    listings = (await client.get("/public/marketplace/listings")).json()
    assert listings["total"] == 1
    listing = listings["items"][0]
    assert listing["listing_id"] == 1
    assert (listing["amount_sold"], listing["remaining"]) == (4, 6)
    assert listing["price_per_unit"] == {"units": PRICE, "decimal": "2.500000"}
    assert listing["payment_token"]["decimals"] == 6  # read on-chain via decimals()
    assert listing["payment_token"]["address"].lower() == deployment.usdc.lower()
    assert listing["vintage"]["report_hash"] == "0x" + report_hash.hex()
    assert listing["project"]["id"] == pid
    assert listing["stewardship"]["score_version"].startswith("stewardship-")
    cancelled = (await client.get("/public/marketplace/listings/2")).json()
    assert cancelled["status"] == "cancelled"

    trades = (await client.get("/public/marketplace/trades")).json()["items"]
    assert len(trades) == 1
    trade = trades[0]
    assert trade["amount"] == 4
    assert trade["total_price"]["units"] == 4 * PRICE
    assert trade["fee"]["units"] == 4 * PRICE * 200 // 10_000  # the suite deploys a 2% fee
    assert trade["block_time"] is not None

    market_view = (await client.get(f"/public/marketplace/projects/{pid}/market")).json()
    assert market_view["volume_units"] == 4
    assert len(market_view["price_history"]) == 1

    vintage = (await client.get(f"/public/vintages/{token_id}")).json()
    assert vintage["retired"] == 3

    pdf = await client.get("/public/retirements/1/certificate.pdf")
    assert pdf.status_code == 200, pdf.text
    reader = PdfReader(io.BytesIO(pdf.content))
    text = re.sub(r"\s+", "", "".join(p.extract_text() for p in reader.pages))
    assert retire_tx in text
    assert "3lCCC" in text
    assert "0x" + report_hash.hex() in text
    again = await client.get("/public/retirements/1/certificate.pdf")
    assert again.content == pdf.content

    # Wallets are public on-chain, but the marketplace API never repeats them.
    for url in (
        "/public/marketplace/listings",
        "/public/marketplace/listings/1",
        "/public/marketplace/trades",
        f"/public/marketplace/projects/{pid}/market",
    ):
        body = (await client.get(url)).text.lower()
        for wallet in (seller.address, buyer.address):
            assert wallet.lower() not in body, (url, wallet)
            assert wallet.lower()[2:] not in body
    assert buyer.address.lower()[2:] not in text.lower()
    assert str(uuid.UUID(pid)) == pid
