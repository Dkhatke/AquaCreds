"""Public marketplace read API + retirement certificate PDFs, on indexed events seeded into
`chain_events` (the Anvil end-to-end flow is in test_marketplace_chain.py)."""

import hashlib
import io
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from api_testlib import project_payload, square
from pypdf import PdfReader

from aqua_api.schemas.marketplace import PaymentToken
from aqua_api.services import certificate as cert
from aqua_api.services import marketplace as market
from aqua_chain.encoding import project_id_bytes32, to_hex
from aqua_core.settings import Settings

CHAIN = 31337
REGISTRY = "0x" + "11" * 20
CREDIT = "0x" + "22" * 20
MARKET = "0x" + "AB" * 20  # mixed case on purpose: addresses are matched case-insensitively
OTHER_MARKET = "0x" + "cd" * 20
USDC = "0x" + "99" * 20
SELLER = "0x" + "5e" * 20
BUYER = "0x" + "b0" * 20
ORG_WALLET = "0x" + "66" * 20
BLOCK_TIME = datetime(2027, 3, 14, 9, 26, 53, tzinfo=UTC)


@pytest.fixture
def settings_overrides() -> dict[str, Any]:
    return {
        "chain_id": CHAIN,
        "chain_registry_address": REGISTRY,
        "chain_credit_address": CREDIT,
        "chain_marketplace_address": MARKET,
        "chain_usdc_address": USDC,
        "chain_payment_token_decimals": 6,
        "public_base_url": "https://registry.example.org",
    }


def key_of(project_id: str) -> str:
    return to_hex(project_id_bytes32(project_id))


class Seeder:
    def __init__(self, sql: Any) -> None:
        self.sql = sql
        self.block = 100

    async def event(
        self,
        event: str,
        args: dict[str, Any],
        *,
        contract: str = "AquaCredit",
        project_key: str | None = None,
        address: str | None = None,
        block_time: datetime | None = BLOCK_TIME,
    ) -> str:
        self.block += 1
        tx = "0x" + hashlib.sha256(f"{event}{self.block}".encode()).hexdigest()
        default = {"AquaRegistry": REGISTRY, "AquaCredit": CREDIT, "AquaMarketplace": MARKET}
        await self.sql(
            "INSERT INTO chain_events (chain_id, contract, address, event, block_number, "
            "block_hash, tx_hash, log_index, project_key, args, block_time) VALUES (:c, :contract, "
            ":addr, :ev, :b, :bh, :tx, 0, :pk, CAST(:args AS jsonb), :bt)",
            c=CHAIN,
            contract=contract,
            addr=address or default[contract],
            ev=event,
            b=self.block,
            bh="0x" + hashlib.sha256(str(self.block).encode()).hexdigest(),
            tx=tx,
            pk=project_key,
            args=json.dumps(args),
            bt=block_time,
        )
        return tx

    async def project(self, project_id: str, report_id: int, report_hash: str) -> str:
        pkey = key_of(project_id)
        await self.event(
            "ProjectRegistered",
            {
                "projectId": pkey,
                "owner": ORG_WALLET,
                "boundaryHash": "0x" + "44" * 32,
                "startYear": 2026,
                "metadataURI": f"urn:aquacreds:project:{project_id}",
            },
            contract="AquaRegistry",
            project_key=pkey,
        )
        await self.event(
            "ReportSubmitted",
            {
                "reportId": report_id,
                "projectId": pkey,
                "periodStart": 2026,
                "periodEnd": 2029,
                "reportHash": "0x" + report_hash,
                "lccc": 50,
                "evidenceURI": "urn:x",
            },
            contract="AquaRegistry",
            project_key=pkey,
        )
        return pkey

    async def issue(self, pkey: str, report_id: int, token_id: int, expires_at: int) -> None:
        await self.event(
            "CreditsIssued",
            {
                "tokenId": token_id,
                "reportId": report_id,
                "projectId": pkey,
                "creditType": token_id & 1,
                "to": ORG_WALLET,
                "amount": 50,
                "expiresAt": expires_at,
            },
            project_key=pkey,
        )

    async def listed(self, lid: int, token: int, amount: int, price: int, **kw: Any) -> str:
        return await self.event(
            "Listed",
            {
                "listingId": lid,
                "seller": SELLER,
                "tokenId": token,
                "amount": amount,
                "pricePerUnit": price,
            },
            contract="AquaMarketplace",
            **kw,
        )

    async def bought(self, lid: int, amount: int, price: int) -> str:
        total = amount * price
        return await self.event(
            "Purchased",
            {
                "listingId": lid,
                "buyer": BUYER,
                "amount": amount,
                "totalPrice": total,
                "fee": total * 200 // 10_000,
            },
            contract="AquaMarketplace",
        )


@pytest.fixture
async def world(make_actor: Any, sql: Any) -> dict[str, Any]:
    """Public project P (Odisha, lCCC token 3 + an expired tCCC token 2) and a draft project D
    (token 5). Listings: 1 active (4/10 sold), 2 sold out, 3 cancelled, 4 expired vintage,
    5 draft project's token, 6 on another marketplace address (ignored)."""
    admin = await make_actor("Meera Sellerowner")
    org = (await admin.post("/orgs", json={"name": "Kendrapara Trust", "org_type": "ngo"})).json()
    ids = {}
    for label, lon, status in (("public", 86.85, "registered"), ("draft", 86.95, "draft")):
        r = await admin.post(
            "/projects",
            json=project_payload(org["id"], name=f"Project {label}", boundary=square(lon, 20.7)),
        )
        assert r.status_code == 201, r.text
        ids[label] = r.json()["id"]
        await sql("UPDATE projects SET status=:s WHERE id=:id", s=status, id=ids[label])
    s = Seeder(sql)
    report_hash = hashlib.sha256(b"mrv record P").hexdigest()
    pkey = await s.project(ids["public"], 1, report_hash)
    dkey = await s.project(ids["draft"], 2, hashlib.sha256(b"draft").hexdigest())
    future = int((datetime.now(UTC) + timedelta(days=3650)).timestamp())
    past = int((datetime.now(UTC) - timedelta(days=1)).timestamp())
    await s.issue(pkey, 1, 3, future)
    await s.issue(pkey, 1, 2, past)
    await s.issue(dkey, 2, 5, future)
    txs = {"listed1": await s.listed(1, 3, 10, 3_000_000)}
    await s.listed(2, 3, 5, 2_000_000)
    await s.listed(3, 3, 5, 1_500_000)
    await s.listed(4, 2, 5, 1_000_000)
    await s.listed(5, 5, 5, 2_500_000)
    await s.listed(1, 3, 99, 1, address=OTHER_MARKET)
    txs["buy1"] = await s.bought(1, 4, 3_000_000)
    txs["buy2"] = await s.bought(2, 5, 2_000_000)
    txs["cancel3"] = await s.event("Cancelled", {"listingId": 3}, contract="AquaMarketplace")
    txs["retire"] = await s.event(
        "CreditsRetired",
        {
            "retirementId": 1,
            "tokenId": 3,
            "holder": BUYER,
            "amount": 4,
            "beneficiary": "Acme Steel <Ltd>",
            "purpose": "FY2027 scope 1 offset",
        },
    )
    await s.event(
        "CreditsRetired",
        {
            "retirementId": 2,
            "tokenId": 3,
            "holder": BUYER,
            "amount": 1,
            "beneficiary": "B",
            "purpose": "P",
        },
        block_time=None,
    )
    return {
        "admin": admin,
        "ids": ids,
        "pkey": pkey,
        "report_hash": report_hash,
        "txs": txs,
        "seeder": s,
    }


# ---------------------------------------------------------------- listings


async def test_active_listings_with_joins(world: dict[str, Any], client: httpx.AsyncClient) -> None:
    r = await client.get("/public/marketplace/listings")
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "public, max-age=60"
    body = r.json()
    assert body["total"] == 2  # 1 and 5: sold out, cancelled, expired and foreign are left out
    assert [x["listing_id"] for x in body["items"]] == [5, 1]  # newest first
    first = next(x for x in body["items"] if x["listing_id"] == 1)
    assert first["status"] == "active"
    assert (first["amount_listed"], first["amount_sold"], first["remaining"]) == (10, 4, 6)
    assert first["price_per_unit"] == {"units": 3_000_000, "decimal": "3.000000"}
    assert first["payment_token"] == {"address": USDC, "decimals": 6}
    assert first["vintage"]["token_id"] == 3
    assert first["vintage"]["credit_type"] == "long_term"
    assert first["vintage"]["report_hash"] == "0x" + world["report_hash"]
    assert first["project"]["id"] == world["ids"]["public"]
    assert first["project_facts"]["state"] == "Odisha"
    assert first["project_facts"]["registered_on_chain"] is True
    steward = first["stewardship"]
    assert steward["score_version"] == "stewardship-v2"
    assert set(steward) == {"score", "insufficient_data", "score_version"}
    assert first["listed"]["tx_hash"] == world["txs"]["listed1"]
    assert first["listed_at"].startswith("2027-03-14T09:26:53")
    assert first["closed"] is None
    draft = next(x for x in body["items"] if x["listing_id"] == 5)
    assert draft["project"]["id"] is None  # not public here: no facts, no score
    assert draft["project_facts"] is None
    assert draft["stewardship"] is None


class FakeHoldings:
    """Stands in for the on-chain reads (balanceOfBatch + isApprovedForAll); counts RPC rounds."""

    def __init__(self, balances: dict[int, int], approved: bool = True) -> None:
        self.balances, self.approved, self.calls = balances, approved, 0
        self.fail = False

    async def __call__(
        self, client: Any, market_address: str, pairs: list[tuple[str, int]], sellers: list[str]
    ) -> tuple[list[int], list[bool]]:
        self.calls += 1
        if self.fail:
            raise ConnectionError("rpc down")
        assert market_address == MARKET.lower()
        assert {s for s, _ in pairs} <= {SELLER.lower()}
        return [self.balances.get(t, 0) for _, t in pairs], [self.approved for _ in sellers]


async def test_listing_validity_unknown_when_rpc_fails(
    world: dict[str, Any], client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeHoldings({})
    fake.fail = True
    monkeypatch.setattr(market, "_read_holdings", fake)
    body = (await client.get("/public/marketplace/listings")).json()
    assert body["total"] == 2  # unknown is not "unavailable": still listed, clearly flagged
    for item in body["items"]:
        assert (item["available"], item["unavailable_reason"]) == (None, "unknown")
    expected = {2: "sold_out", 3: "cancelled", 4: "expired"}
    for lid, reason in expected.items():
        detail = (await client.get(f"/public/marketplace/listings/{lid}")).json()
        assert (detail["available"], detail["unavailable_reason"]) == (False, reason)
    # failures are not cached
    fake.fail = False
    fake.balances = {3: 6, 5: 5}
    one = (await client.get("/public/marketplace/listings/1")).json()
    assert (one["available"], one["unavailable_reason"]) == (True, None)


async def test_listing_validity_from_chain_and_cache(
    world: dict[str, Any], client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Listing 1 has 6 remaining (seller holds 6: fine); listing 5 has 5 (seller holds 2).
    fake = FakeHoldings({3: 6, 5: 2})
    monkeypatch.setattr(market, "_read_holdings", fake)
    body = (await client.get("/public/marketplace/listings")).json()
    assert [(x["listing_id"], x["available"]) for x in body["items"]] == [(1, True)]
    assert body["total"] == 1
    assert fake.calls == 1
    full = (
        await client.get("/public/marketplace/listings", params={"include_unavailable": True})
    ).json()
    assert {x["listing_id"]: x["unavailable_reason"] for x in full["items"]} == {
        1: None,
        5: "insufficient_balance",
    }
    market_view = (
        await client.get(f"/public/marketplace/projects/{world['ids']['public']}/market")
    ).json()
    assert [x["available"] for x in market_view["active_listings"]] == [True]
    assert fake.calls == 1  # all served from the ~60 s cache

    # Approval revoked: everything from that seller is unavailable (after the cache expires).
    market.clear_validity_cache()
    fake.approved = False
    full = (
        await client.get("/public/marketplace/listings", params={"include_unavailable": True})
    ).json()
    assert {x["unavailable_reason"] for x in full["items"]} == {"not_approved"}
    assert (await client.get("/public/marketplace/listings")).json()["total"] == 0
    assert fake.calls == 2


async def test_listing_validity_cache_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        _env_file=None,
        chain_marketplace_address=MARKET,
        chain_credit_address=CREDIT,
        marketplace_validity_cache_s=60,
    )
    fake = FakeHoldings({3: 10})
    now = [1000.0]
    listing = market.Listing(
        listed=type(
            "Ev", (), {"args": {"listingId": 1, "seller": SELLER, "tokenId": 3, "amount": 10}}
        )()  # type: ignore[arg-type]
    )
    ctx = market.Context({}, {}, {}, {})
    monkeypatch.setattr(market, "_read_holdings", fake)
    for t, calls in ((1000.0, 1), (1059.0, 1), (1061.0, 2)):
        now[0] = t
        out = await market.validity(
            settings, lambda: FakeClient(), [listing], ctx, clock=lambda: now[0]
        )
        assert out[1] == market.AVAILABLE
        assert fake.calls == calls


class FakeClient:
    async def close(self) -> None:
        return None


async def test_listing_filters_and_sorting(
    world: dict[str, Any], client: httpx.AsyncClient
) -> None:
    async def ids(**params: Any) -> list[int]:
        r = await client.get("/public/marketplace/listings", params=params)
        assert r.status_code == 200, r.text
        return [x["listing_id"] for x in r.json()["items"]]

    assert await ids(sort="price_asc") == [5, 1]
    assert await ids(sort="price_desc") == [1, 5]
    assert await ids(project_id=world["ids"]["public"]) == [1]
    assert await ids(state="odisha") == [1]
    assert await ids(state="Kerala") == []
    assert await ids(credit_type="long_term") == [5, 1]
    assert await ids(credit_type="temporary") == []  # token 2 is expired
    assert await ids(max_price=2_500_000) == [5]
    assert await ids(max_price=2_499_999) == []
    assert await ids(limit=1, offset=1) == [1]
    bad = await client.get("/public/marketplace/listings", params={"sort": "random"})
    assert bad.status_code == 422


async def test_listing_detail_any_status(world: dict[str, Any], client: httpx.AsyncClient) -> None:
    sold = (await client.get("/public/marketplace/listings/2")).json()
    assert sold["status"] == "sold_out"
    assert sold["remaining"] == 0
    assert sold["closed"]["tx_hash"] == world["txs"]["buy2"]
    cancelled = (await client.get("/public/marketplace/listings/3")).json()
    assert cancelled["status"] == "cancelled"
    assert cancelled["closed"]["tx_hash"] == world["txs"]["cancel3"]
    expired = (await client.get("/public/marketplace/listings/4")).json()
    assert expired["status"] == "active"
    assert expired["vintage"]["expired"] is True
    assert (await client.get("/public/marketplace/listings/99")).status_code == 404


async def test_trades_and_project_market(world: dict[str, Any], client: httpx.AsyncClient) -> None:
    trades = (await client.get("/public/marketplace/trades")).json()
    assert trades["total"] == 2
    newest, oldest = trades["items"]
    assert newest["listing_id"] == 2
    assert oldest["listing_id"] == 1
    assert oldest["amount"] == 4
    assert oldest["total_price"] == {"units": 12_000_000, "decimal": "12.000000"}
    assert oldest["fee"] == {"units": 240_000, "decimal": "0.240000"}
    assert oldest["chain"]["tx_hash"] == world["txs"]["buy1"]
    assert oldest["credit_type"] == "long_term"
    other = await client.get(
        "/public/marketplace/trades", params={"project_id": world["ids"]["draft"]}
    )
    assert other.json()["total"] == 0

    pid = world["ids"]["public"]
    m = (await client.get(f"/public/marketplace/projects/{pid}/market")).json()
    assert m["project"]["id"] == pid
    assert [x["listing_id"] for x in m["active_listings"]] == [1]
    assert [t["listing_id"] for t in m["trades"]] == [2, 1]
    assert [p["price_per_unit"]["units"] for p in m["price_history"]] == [3_000_000, 2_000_000]
    assert m["volume_units"] == 9
    assert m["last_price"]["decimal"] == "2.000000"
    hidden = await client.get(f"/public/marketplace/projects/{world['ids']['draft']}/market")
    assert hidden.status_code == 404


async def test_marketplace_privacy(world: dict[str, Any], client: httpx.AsyncClient) -> None:
    pid = world["ids"]["public"]
    urls = [
        "/public/marketplace/listings",
        "/public/marketplace/listings/1",
        "/public/marketplace/listings/2",
        "/public/marketplace/trades",
        f"/public/marketplace/projects/{pid}/market",
    ]
    forbidden = [
        SELLER,
        BUYER,
        ORG_WALLET,
        world["admin"].email,
        "Meera Sellerowner",
        str(world["admin"].id),
        "seller",
        "buyer",
        "tenure",
        "email",
    ]
    for url in urls:
        r = await client.get(url)
        assert r.status_code == 200, (url, r.text)
        for needle in forbidden:
            assert needle.lower() not in r.text.lower(), (url, needle)


# ---------------------------------------------------------------- certificates


def _pdf_text(pdf: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf))
    return re.sub(r"\s+", "", "".join(page.extract_text() for page in reader.pages))


async def test_certificate_pdf(world: dict[str, Any], client: httpx.AsyncClient) -> None:
    r = await client.get("/public/retirements/1/certificate.pdf")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["cache-control"] == "public, max-age=3600"
    assert "inline" in r.headers["content-disposition"]
    text = _pdf_text(r.content)
    detail = (await client.get("/public/retirements/1")).json()
    assert detail["certificate_pdf_url"].endswith("/public/retirements/1/certificate.pdf")
    for needle in (
        world["txs"]["retire"],
        "4lCCC",
        "AcmeSteel<Ltd>",  # escaped in HTML, rendered literally
        "FY2027scope1offset",
        "0x" + world["report_hash"],
        detail["verification"]["payload_sha256"],
        "https://registry.example.org/registry/retirements/1",
        "2027-03-1409:26:53UTC",
        "Estimated—pendingaccreditedverification",
        "Projectpublic",
        "IndiaCCTSBMFR05.001",
        "Chainid31337",
    ):
        assert needle in text, needle
    assert BUYER not in text
    assert world["admin"].email not in text
    reader = PdfReader(io.BytesIO(r.content))
    assert reader.metadata is not None
    assert "20270314092653" in str(reader.metadata.get("/CreationDate"))
    assert len(reader.pages[0].images) == 1  # the QR code

    again = await client.get("/public/retirements/1/certificate.pdf")
    assert again.content == r.content  # deterministic bytes
    assert again.headers["etag"] == r.headers["etag"]
    cached = await client.get(
        "/public/retirements/1/certificate.pdf", headers={"If-None-Match": r.headers["etag"]}
    )
    assert cached.status_code == 304
    assert cached.content == b""


async def test_certificate_waits_for_block_time_and_404(
    world: dict[str, Any], client: httpx.AsyncClient
) -> None:
    pending = await client.get("/public/retirements/2/certificate.pdf")
    assert pending.status_code == 503
    assert pending.headers["retry-after"] == "60"
    assert (await client.get("/public/retirements/9/certificate.pdf")).status_code == 404


def _data(**overrides: Any) -> cert.CertificateData:
    values: dict[str, Any] = {
        "retirement_id": 7,
        "amount": 12,
        "credit_unit": "lCCC",
        "credit_type": "long_term",
        "token_id": 3,
        "project_name": "P",
        "project_key": "0x" + "12" * 32,
        "methodology": "India CCTS BM FR05.001",
        "report_id": 1,
        "report_hash": "0x" + "34" * 32,
        "beneficiary": "B",
        "purpose": "P",
        "chain_id": 80002,
        "contract_address": CREDIT,
        "tx_hash": "0x" + "56" * 32,
        "log_index": 2,
        "block_number": 10,
        "block_hash": "0x" + "78" * 32,
        "block_time": BLOCK_TIME,
        "explorer_url": "https://amoy.polygonscan.com/tx/0x" + "56" * 32,
        "certificate_url": "https://r.example.org/registry/retirements/7",
        "payload_sha256": "9a" * 32,
    }
    values.update(overrides)
    return cert.CertificateData(**values)


def test_certificate_is_a_pure_function_of_its_data() -> None:
    a = cert.to_pdf(cert.to_html(_data()))
    assert a == cert.to_pdf(cert.to_html(_data()))
    changed = cert.to_html(_data(amount=13))
    assert cert.etag(changed) != cert.etag(cert.to_html(_data()))
    assert "polygonscan" in _pdf_text(a)


def test_certificate_never_fetches_remote_resources() -> None:
    html = cert.to_html(_data()).replace(
        "</body>", '<img src="http://169.254.169.254/latest/meta-data/"></body>'
    )
    assert cert.to_pdf(html).startswith(b"%PDF")  # the remote image is refused, not fetched


def test_status_label_matches_mrv_report() -> None:
    from aqua_mrv.report import STATUS_LABEL

    assert cert.STATUS_LABEL == STATUS_LABEL


# ---------------------------------------------------------------- payment token


def test_format_units() -> None:
    assert market.format_units(12_500_000, 6) == "12.500000"
    assert market.format_units(1, 6) == "0.000001"
    assert market.format_units(-5, 2) == "-0.05"
    assert market.format_units(42, 0) == "42"


async def test_payment_token_unconfigured_and_rpc_failure() -> None:
    def no_client() -> Any:
        raise AssertionError("must not build a client")

    with pytest.raises(market.MarketplaceUnavailableError, match="not configured"):
        await market.payment_token(Settings(_env_file=None), no_client)

    class Broken:
        closed = False

        async def check(self) -> int:
            raise ConnectionError("down")

        async def close(self) -> None:
            Broken.closed = True

    settings = Settings(_env_file=None, chain_marketplace_address=MARKET)
    with pytest.raises(market.MarketplaceUnavailableError, match="ConnectionError"):
        await market.payment_token(settings, lambda: Broken())  # type: ignore[arg-type,return-value]
    assert Broken.closed
    fixed = Settings(
        _env_file=None, chain_marketplace_address=MARKET, chain_payment_token_decimals=2
    )
    token = await market.payment_token(fixed, no_client)
    assert token == PaymentToken(address=None, decimals=2)


def test_validity_cache_prunes_expired_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(market, "_CACHE_PRUNE_AT", 2)
    for i in range(3):
        market._BALANCE_CACHE[(1, "c", "m", f"s{i}", 1)] = (float(i), 5)
    market._prune(now=1.5)  # entries expiring at 0 and 1 are gone
    assert list(market._BALANCE_CACHE) == [(1, "c", "m", "s2", 1)]
