"""Public transparency registry: visibility, privacy, boundary simplification, vintage maths,
retirement certificates + QR codes, hash verification, pagination and caching."""

import hashlib
import json
import struct
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pytest
import rfc8785
import segno
from api_testlib import insert_sar_observation, project_payload, square
from shapely.geometry import shape

from aqua_chain.encoding import project_id_bytes32, to_hex

CHAIN = 31337
REGISTRY = "0x" + "11" * 20
CREDIT = "0x" + "22" * 20
PHOTO_LON, PHOTO_LAT = 86.861234, 20.712345
PHOTO_KEY = "field/secret-org/photo-abc123.jpg"
TENURE_KEY = "tenure/secret-org/forest-agreement.pdf"


def key_of(project_id: str) -> str:
    return to_hex(project_id_bytes32(project_id))


class World:
    def __init__(self, admin: Any, agent: Any, org_id: str, projects: dict[str, str]) -> None:
        self.admin, self.agent, self.org_id, self.projects = admin, agent, org_id, projects
        self.next_block = 100


async def _event(
    sql: Any,
    world: World,
    event: str,
    args: dict[str, Any],
    *,
    project_key: str | None = None,
    contract: str = "AquaCredit",
    chain_id: int = CHAIN,
) -> str:
    world.next_block += 1
    tx_hash = "0x" + hashlib.sha256(f"{event}{world.next_block}".encode()).hexdigest()
    await sql(
        "INSERT INTO chain_events (chain_id, contract, address, event, block_number, block_hash, "
        "tx_hash, log_index, project_key, args) VALUES (:c, :contract, :addr, :ev, :b, :bh, :tx, "
        "0, :pk, CAST(:args AS jsonb))",
        c=chain_id,
        contract=contract,
        addr=REGISTRY if contract == "AquaRegistry" else CREDIT,
        ev=event,
        b=world.next_block,
        bh="0x" + "ab" * 32,
        tx=tx_hash,
        pk=project_key,
        args=json.dumps(args),
    )
    return tx_hash


async def _register(sql: Any, world: World, project_id: str) -> str:
    return await _event(
        sql,
        world,
        "ProjectRegistered",
        {
            "projectId": key_of(project_id),
            "owner": "0x" + "33" * 20,
            "boundaryHash": "0x" + "44" * 32,
            "startYear": 2026,
            "metadataURI": f"urn:aquacreds:project:{project_id}",
        },
        project_key=key_of(project_id),
        contract="AquaRegistry",
    )


@pytest.fixture
async def world(make_actor: Any, sql: Any) -> World:
    admin = await make_actor("Priya Admin")
    org = (await admin.post("/orgs", json={"name": "Godavari Mangroves", "org_type": "ngo"})).json()
    agent = await make_actor("Ravi Fieldagent")
    await admin.post(f"/orgs/{org['id']}/members", json={"email": agent.email, "role": "member"})
    projects: dict[str, str] = {}
    spots = {
        "registered": (86.85, 20.70, "registered"),
        "monitoring": (86.90, 20.75, "monitoring"),
        "submitted": (86.95, 20.80, "submitted"),
        "draft": (87.00, 20.85, "draft"),
        "rejected": (87.05, 20.90, "rejected"),
    }
    for label, (lon, lat, status) in spots.items():
        r = await admin.post(
            "/projects",
            json=project_payload(org["id"], name=f"Project {label}", boundary=square(lon, lat)),
        )
        assert r.status_code == 201, r.text
        projects[label] = r.json()["id"]
        await sql("UPDATE projects SET status=:s WHERE id=:id", s=status, id=projects[label])
    await sql(
        "UPDATE projects SET state='Andhra Pradesh', ecosystem='mangrove' WHERE id=:id",
        id=projects["monitoring"],
    )
    return World(admin, agent, org["id"], projects)


async def _seed_private_data(sql: Any, world: World) -> None:
    """Field evidence and a tenure document that must never leak through the public API."""
    pid = world.projects["registered"]
    period_id = uuid.uuid4()
    await sql(
        "INSERT INTO monitoring_periods (id, project_id, kind, status, start_date, end_date, "
        "created_by) VALUES (:id, :p, 'monitoring', 'open', '2026-06-01', '2027-05-31', :u)",
        id=period_id,
        p=pid,
        u=world.admin.id,
    )
    for i, (status, trust) in enumerate(
        [
            ("checked", 80),
            ("checked", 90),
            ("checked", 60),
            ("rejected", 10),
            ("pending_checks", None),
        ]
    ):
        await sql(
            "INSERT INTO field_submissions (id, period_id, project_id, submitted_by, kind, status, "
            "object_key, sha256, content_type, size_bytes, location, gps_accuracy_m, captured_at, "
            "device_model, app_version, trust_score) VALUES (:id, :per, :p, :u, 'general', :st, "
            ":key, :sha, 'image/jpeg', 1000, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), 4.0, "
            "now(), 'Pixel 9', '1.0', :trust)",
            id=uuid.uuid4(),
            per=period_id,
            p=pid,
            u=world.agent.id,
            st=status,
            key=f"{PHOTO_KEY}.{i}",
            sha=hashlib.sha256(str(i).encode()).hexdigest(),
            lon=PHOTO_LON,
            lat=PHOTO_LAT,
            trust=trust,
        )
    await sql(
        "INSERT INTO tenure_documents (id, project_id, kind, object_key, filename, content_type, "
        "size_bytes, sha256, uploaded_by) VALUES (:id, :p, 'forest_dept_agreement', :key, "
        "'agreement-signed.pdf', 'application/pdf', 10, :sha, :u)",
        id=uuid.uuid4(),
        p=pid,
        key=TENURE_KEY,
        sha="ee" * 32,
        u=world.admin.id,
    )


async def _observation(sql: Any, project_id: str, day: date, clear: float, p50: float) -> None:
    stats = {
        "ndvi": {"valid_pixels": 100, "mean": p50, "p10": p50 - 0.05, "p50": p50, "p90": p50 + 0.05}
    }
    await sql(
        "INSERT INTO scene_observations (id, project_id, item_id, acquired_at, acquired_on, "
        "cloud_cover, clear_fraction, clear_pixels, polygon_pixels, index_stats, provider) "
        "VALUES (:id, :p, :item, :at, :day, 10, :clear, 90, 100, CAST(:stats AS jsonb), 'fake')",
        id=uuid.uuid4(),
        p=project_id,
        item=f"S2_{day.isoformat()}",
        at=datetime(day.year, day.month, day.day, 5, tzinfo=UTC),
        day=day,
        clear=clear,
        stats=json.dumps(stats),
    )


async def _seed_credits(sql: Any, world: World) -> dict[str, Any]:
    """Report 1 (registered project): submitted, approved, two issuances of tCCC (token 2) and
    one of lCCC (token 3); two retirements of token 2 and a reversal replacement on token 3."""
    pid = world.projects["registered"]
    pkey = key_of(pid)
    await _register(sql, world, pid)
    report_hash = hashlib.sha256(b"mrv record").hexdigest()
    await _event(
        sql,
        world,
        "ReportSubmitted",
        {
            "reportId": 1,
            "projectId": pkey,
            "periodStart": 2026,
            "periodEnd": 2029,
            "reportHash": "0x" + report_hash,
            "lccc": 120,
            "evidenceURI": "urn:aquacreds:report:1",
        },
        project_key=pkey,
        contract="AquaRegistry",
    )
    await _event(
        sql,
        world,
        "ReportApproved",
        {"reportId": 1, "projectId": pkey, "verifier": "0x" + "55" * 20, "lccc": 120, "tccc": 300},
        project_key=pkey,
        contract="AquaRegistry",
    )
    expiry = int((datetime.now(UTC) + timedelta(days=365 * 5)).timestamp())
    issued = {"projectId": pkey, "reportId": 1, "to": "0x" + "66" * 20, "expiresAt": expiry}
    tx1 = await _event(
        sql,
        world,
        "CreditsIssued",
        {**issued, "tokenId": 2, "creditType": 0, "amount": 200},
        project_key=pkey,
    )
    tx2 = await _event(
        sql,
        world,
        "CreditsIssued",
        {**issued, "tokenId": 2, "creditType": 0, "amount": 100},
        project_key=pkey,
    )
    await _event(
        sql,
        world,
        "CreditsIssued",
        {**issued, "tokenId": 3, "creditType": 1, "amount": 120},
        project_key=pkey,
    )
    holder = "0x" + "77" * 20
    retire_tx = await _event(
        sql,
        world,
        "CreditsRetired",
        {
            "retirementId": 1,
            "tokenId": 2,
            "holder": holder,
            "amount": 40,
            "beneficiary": "Acme Steel Ltd",
            "purpose": "FY2027 scope 1 offset",
        },
    )
    await _event(
        sql,
        world,
        "CreditsRetired",
        {
            "retirementId": 2,
            "tokenId": 2,
            "holder": holder,
            "amount": 10,
            "beneficiary": "B",
            "purpose": "P",
        },
    )
    await _event(
        sql,
        world,
        "ReversalReplaced",
        {"projectId": pkey, "tokenId": 3, "holder": holder, "amount": 20},
        project_key=pkey,
    )
    # Same ids on another chain must be ignored.
    await _event(
        sql,
        world,
        "CreditsIssued",
        {**issued, "tokenId": 2, "creditType": 0, "amount": 9999},
        project_key=pkey,
        chain_id=80002,
    )
    return {
        "report_hash": report_hash,
        "issuance_txs": [tx1, tx2],
        "retire_tx": retire_tx,
        "holder": holder,
        "expiry": expiry,
    }


# ---------------------------------------------------------------- projects


async def test_public_endpoints_need_no_login_and_hide_draft_and_rejected(
    world: World, client: httpx.AsyncClient
) -> None:
    r = await client.get("/public/projects")
    assert r.status_code == 200, r.text
    body = r.json()
    names = {p["name"] for p in body["items"]}
    assert names == {"Project registered", "Project monitoring", "Project submitted"}
    assert body["total"] == 3
    for hidden in ("draft", "rejected"):
        pid = world.projects[hidden]
        assert (await client.get(f"/public/projects/{pid}")).status_code == 404
        assert (await client.get(f"/public/projects/{pid}/timeseries")).status_code == 404
    assert (await client.get("/public/projects", params={"status": "draft"})).status_code == 422
    assert (await client.get(f"/public/projects/{uuid.uuid4()}")).status_code == 404


async def test_project_filters_and_pagination(world: World, client: httpx.AsyncClient) -> None:
    by_state = (await client.get("/public/projects", params={"state": "andhra pradesh"})).json()
    assert [p["name"] for p in by_state["items"]] == ["Project monitoring"]
    by_status = (await client.get("/public/projects", params={"status": "submitted"})).json()
    assert [p["name"] for p in by_status["items"]] == ["Project submitted"]
    eco = (await client.get("/public/projects", params={"ecosystem": "seagrass"})).json()
    assert eco["total"] == 0

    seen = []
    for offset in (0, 2):
        page = (await client.get("/public/projects", params={"limit": 2, "offset": offset})).json()
        assert (page["total"], page["limit"], page["offset"]) == (3, 2, offset)
        seen += [p["id"] for p in page["items"]]
    assert len(seen) == 3
    assert len(set(seen)) == 3
    assert (await client.get("/public/projects", params={"limit": 0})).status_code == 422
    assert (await client.get("/public/projects", params={"limit": 101})).status_code == 422


async def test_project_detail_aggregates(world: World, client: httpx.AsyncClient, sql: Any) -> None:
    await _seed_private_data(sql, world)
    pid = world.projects["registered"]
    today = datetime.now(UTC).date()

    def months_back(k: int) -> date:
        index = today.year * 12 + today.month - 1 - k
        return date(index // 12, index % 12 + 1, 10)

    month_starts = [months_back(k) for k in (3, 2, 1)]
    for i, day in enumerate(month_starts):
        await _observation(sql, pid, day, 0.9, 0.60 + 0.02 * i)
    await _observation(sql, pid, month_starts[-1] + timedelta(days=5), 0.1, 0.10)  # cloudy: ignored
    await _observation(sql, pid, today - timedelta(days=800), 0.9, 0.5)  # outside 12 months
    await sql(
        "INSERT INTO alerts (id, project_id, kind, metric, observed_on, observed_value, expected, "
        "severity, message) VALUES (:id, :p, 'vegetation_loss', 'ndvi_p50', :d, 0.4, "
        "CAST('{}' AS jsonb), 'high', 'NDVI below envelope')",
        id=uuid.uuid4(),
        p=pid,
        d=today,
    )

    r = await client.get(f"/public/projects/{pid}")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["org"] == {"name": "Godavari Mangroves", "org_type": "ngo"}
    assert d["status"] == "registered"
    assert d["registration"] is None
    assert d["registered_on_chain"] is False
    assert d["evidence"] == {
        "submissions_total": 5,
        "checked": 3,
        "rejected": 1,
        "pending_checks": 1,
        "median_trust_score": 80.0,
    }
    assert [p["kind"] for p in d["periods"]] == ["monitoring"]
    assert "created_by" not in d["periods"][0]
    sat = d["satellite"]
    assert [m["median_p50"] for m in sat["last_12_months"]] == [0.6, 0.62, 0.64]
    assert sat["latest_p50"] == 0.64
    assert sat["trend_p50_per_year"] == pytest.approx(0.24, abs=0.02)
    assert d["alerts"]["total"] == 1
    assert d["alerts"]["high"] == 1
    assert d["reports"] == []
    assert d["vintages"] == []
    assert d["mrv_reports"] == []

    series = (await client.get(f"/public/projects/{pid}/timeseries")).json()
    assert len(series["points"]) == 4  # the cloudy scene is filtered (min_clear 0.5)
    assert series["min_clear_fraction"] == 0.5
    assert (
        await client.get(f"/public/projects/{pid}/timeseries", params={"min_clear": 0.1})
    ).status_code == 422  # cannot lower the public clear-sky floor
    evi = (await client.get(f"/public/projects/{pid}/timeseries", params={"index": "evi"})).json()
    assert evi["points"] == []


async def test_public_timeseries_exposes_sar_p50_aggregates_only(
    world: World, client: httpx.AsyncClient, sql: Any
) -> None:
    pid = world.projects["registered"]
    await insert_sar_observation(sql, pid, "2024-07-05", vh_db=-15.2, rvi=0.61)
    await insert_sar_observation(sql, pid, "2024-07-17", vh_db=-16.0, valid_fraction=0.3)

    r = await client.get(f"/public/projects/{pid}/timeseries", params={"index": "rvi"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["sensor"], body["min_clear_fraction"]) == ("sentinel-1-rtc", 0.5)
    assert "not compar" in body["note"]
    assert body["points"] == [
        {
            "date": "2024-07-05",
            "item_id": "S1A_20240705_120000",
            "clear_fraction": None,
            "valid_fraction": 1.0,
            "valid_pixels": 360,
            "mean": None,
            "p10": None,
            "p50": 0.61,
            "p90": None,
        }
    ]  # the mostly-invalid pass is below the public floor
    vh = (await client.get(f"/public/projects/{pid}/timeseries", params={"index": "vh_db"})).json()
    assert [p["p50"] for p in vh["points"]] == [-15.2]
    for index in ("vv_db", "vh_vv_ratio_db"):  # authenticated API only
        bad = await client.get(f"/public/projects/{pid}/timeseries", params={"index": index})
        assert bad.status_code == 422
    optical = (await client.get(f"/public/projects/{pid}/timeseries")).json()
    assert (optical["sensor"], optical["points"]) == ("sentinel-2-l2a", [])
    hidden = world.projects["draft"]
    await insert_sar_observation(sql, hidden, "2024-07-05")
    assert (
        await client.get(f"/public/projects/{hidden}/timeseries", params={"index": "rvi"})
    ).status_code == 404


async def test_boundary_is_simplified_and_valid(
    world: World, client: httpx.AsyncClient, sql: Any
) -> None:
    # A square traced with a 1 m zig-zag every ~2 m: thousands of vertices.
    lon, lat, size, n = 87.20, 21.00, 0.02, 400
    edge = []
    for i in range(n):
        t = size * i / n
        wiggle = 0.00001 if i % 2 else 0.0
        edge.append([lon + t, lat - wiggle])
    for i in range(n):
        t = size * i / n
        edge.append([lon + size + (0.00001 if i % 2 else 0.0), lat + t])
    edge += [[lon + size, lat + size], [lon, lat + size], [lon, lat]]
    dense = {"type": "Polygon", "coordinates": [edge]}
    r = await world.admin.post(
        "/projects", json=project_payload(world.org_id, name="Dense", boundary=dense)
    )
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    await sql("UPDATE projects SET status='submitted' WHERE id=:id", id=pid)

    boundary = (await client.get(f"/public/projects/{pid}")).json()["boundary"]
    assert boundary["simplification"] == "ST_SimplifyPreserveTopology"
    assert boundary["tolerance_deg"] == 0.0001
    geom = shape(boundary["geometry"])
    assert boundary["geometry"]["type"] == "MultiPolygon"
    assert geom.is_valid
    vertices = sum(len(p.exterior.coords) for p in geom.geoms)
    assert vertices < 20 < len(edge)
    assert geom.area == pytest.approx(shape(dense).area, rel=0.01)


async def test_privacy_no_personal_data_anywhere(
    world: World, client: httpx.AsyncClient, sql: Any
) -> None:
    await _seed_private_data(sql, world)
    seeded = await _seed_credits(sql, world)
    pid = world.projects["registered"]
    urls = [
        "/public/projects",
        f"/public/projects/{pid}",
        f"/public/projects/{pid}/timeseries",
        "/public/vintages",
        "/public/vintages/2",
        "/public/retirements",
        "/public/retirements/1",
        f"/public/verify-hash?sha256_hex={seeded['report_hash']}",
    ]
    forbidden = [
        world.admin.email,
        world.agent.email,
        "Priya Admin",
        "Ravi Fieldagent",
        str(world.admin.id),
        str(world.agent.id),
        PHOTO_KEY,
        TENURE_KEY,
        "agreement-signed.pdf",
        "ee" * 32,  # tenure document hash: not published by this API
        str(PHOTO_LON),
        str(PHOTO_LAT),
        "86.8612",  # even rounded photo coordinates
        "Pixel 9",
        "presign",
        "signature",
        seeded["holder"],  # buyer wallet address
        "0x" + "66" * 20,  # org custodial wallet
        "tenure_type",
        "created_by",
        "submitted_by",
        "audit",
    ]
    for url in urls:
        r = await client.get(url)
        assert r.status_code == 200, (url, r.text)
        text = r.text
        for needle in forbidden:
            assert needle not in text, (url, needle)
    detail = (await client.get(f"/public/projects/{pid}")).json()
    assert not {"email", "full_name", "object_key", "location", "documents"} & detail.keys()


# ---------------------------------------------------------------- chain-derived records


async def test_registration_reports_and_vintage_totals(
    world: World, client: httpx.AsyncClient, sql: Any
) -> None:
    seeded = await _seed_credits(sql, world)
    pid = world.projects["registered"]

    d = (await client.get(f"/public/projects/{pid}")).json()
    assert d["registered_on_chain"] is True
    assert d["registration"]["boundary_hash"] == "0x" + "44" * 32
    assert d["registration"]["start_year"] == 2026
    (report,) = d["reports"]
    assert report["status"] == "approved"
    assert (report["lccc"], report["tccc"]) == (120, 300)
    assert report["report_hash"] == "0x" + seeded["report_hash"]
    assert {v["token_id"] for v in d["vintages"]} == {2, 3}
    listed = (await client.get("/public/projects")).json()["items"]
    assert {p["name"]: p["registered_on_chain"] for p in listed}["Project registered"] is True

    vintages = (await client.get("/public/vintages")).json()
    assert vintages["total"] == 2
    t_ccc, l_ccc = vintages["items"]
    assert (t_ccc["token_id"], t_ccc["credit_type"], t_ccc["credit_unit"]) == (
        2,
        "temporary",
        "tCCC",
    )
    assert (t_ccc["issued"], t_ccc["retired"], t_ccc["replaced"]) == (300, 50, 0)
    assert t_ccc["outstanding"] == t_ccc["issued"] - t_ccc["retired"] == 250
    assert [i["tx_hash"] for i in t_ccc["issuances"]] == seeded["issuance_txs"]
    assert t_ccc["project"] == {"id": pid, "name": "Project registered", "project_key": key_of(pid)}
    assert t_ccc["report_id"] == 1
    assert t_ccc["expired"] is False
    assert datetime.fromisoformat(t_ccc["expires_at"]).timestamp() == seeded["expiry"]
    assert (l_ccc["credit_unit"], l_ccc["issued"], l_ccc["replaced"]) == ("lCCC", 120, 20)
    assert l_ccc["outstanding"] == 100
    page = (await client.get("/public/vintages", params={"limit": 1, "offset": 1})).json()
    assert [v["token_id"] for v in page["items"]] == [3]
    assert (await client.get("/public/vintages", params={"project_id": str(uuid.uuid4())})).json()[
        "total"
    ] == 0

    one = await client.get("/public/vintages/2")
    assert one.status_code == 200
    assert one.json() == t_ccc
    assert (await client.get("/public/vintages/99")).status_code == 404


async def test_retirement_certificate_and_qr(
    world: World, client: httpx.AsyncClient, sql: Any
) -> None:
    seeded = await _seed_credits(sql, world)
    listed = (await client.get("/public/retirements")).json()
    assert listed["total"] == 2
    assert [r["retirement_id"] for r in listed["items"]] == [1, 2]
    paged = (await client.get("/public/retirements", params={"limit": 1, "offset": 1})).json()
    assert [r["retirement_id"] for r in paged["items"]] == [2]

    r = await client.get("/public/retirements/1")
    assert r.status_code == 200, r.text
    cert = r.json()
    assert (cert["amount"], cert["token_id"], cert["credit_type"]) == (40, 2, "temporary")
    assert cert["beneficiary"] == "Acme Steel Ltd"
    assert cert["purpose"] == "FY2027 scope 1 offset"
    assert cert["tx_hash"] == seeded["retire_tx"]
    assert cert["project"]["name"] == "Project registered"
    assert cert["block_time"] is None  # buyer-signed tx: no relayer record
    verification = cert["verification"]
    assert verification["certificate_url"] == "http://localhost:3000/registry/retirements/1"
    assert verification["chain"]["tx_hash"] == seeded["retire_tx"]
    assert (
        verification["payload_sha256"]
        == hashlib.sha256(rfc8785.dumps(verification["fields"])).hexdigest()
    )
    assert verification["fields"]["amount"] == 40
    assert cert["qr_png_url"].endswith("/public/retirements/1/qr.png")
    assert cert["qr_svg_url"].endswith("/public/retirements/1/qr.svg")
    assert (await client.get("/public/retirements/42")).status_code == 404

    png = await client.get("/public/retirements/1/qr.png")
    assert png.status_code == 200
    assert png.headers["content-type"] == "image/png"
    assert png.headers["cache-control"] == "public, max-age=86400"
    assert png.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert png.content[12:16] == b"IHDR"
    width, height = struct.unpack(">II", png.content[16:24])
    expected = segno.make(verification["certificate_url"], error="m", micro=False)
    assert (width, height) == expected.symbol_size(scale=8, border=4)
    svg = await client.get("/public/retirements/1/qr.svg")
    assert svg.headers["content-type"].startswith("image/svg+xml")
    assert b"<svg" in svg.content
    assert (await client.get("/public/retirements/42/qr.png")).status_code == 404
    assert (await client.get("/public/retirements/1/qr.gif")).status_code == 422


@pytest.mark.parametrize("settings_overrides", [{"public_base_url": "https://registry.example/"}])
async def test_certificate_url_uses_configured_base(
    world: World, client: httpx.AsyncClient, sql: Any
) -> None:
    await _seed_credits(sql, world)
    cert = (await client.get("/public/retirements/1")).json()
    assert (
        cert["verification"]["certificate_url"] == "https://registry.example/registry/retirements/1"
    )


async def test_block_time_from_relayer_record(
    world: World, client: httpx.AsyncClient, sql: Any
) -> None:
    seeded = await _seed_credits(sql, world)
    confirmed = datetime(2027, 1, 2, 3, 4, 5, tzinfo=UTC)
    await sql(
        "INSERT INTO chain_transactions (id, chain_id, purpose, entity_type, entity_id, signer, "
        "from_address, to_address, contract, function, args, status, tx_hash, confirmed_at, "
        "result, attempts) VALUES (:id, 31337, 'other', 'retirement', '1', 'issuer', :a, :a, "
        "'AquaCredit', 'retire', CAST('[]' AS jsonb), 'confirmed', :tx, :at, "
        "CAST('{}' AS jsonb), 1)",
        id=uuid.uuid4(),
        a=CREDIT,
        tx=seeded["retire_tx"],
        at=confirmed,
    )
    cert = (await client.get("/public/retirements/1")).json()
    assert datetime.fromisoformat(cert["block_time"]) == confirmed


async def test_verify_hash(world: World, client: httpx.AsyncClient, sql: Any) -> None:
    seeded = await _seed_credits(sql, world)
    digest = seeded["report_hash"]
    pid = world.projects["registered"]

    hit = await client.post("/public/verify-hash", json={"sha256_hex": "0x" + digest.upper()})
    assert hit.status_code == 200, hit.text
    body = hit.json()
    assert body["found"] is True
    assert body["sha256_hex"] == digest
    (match,) = body["matches"]
    assert (match["report_id"], match["status"]) == (1, "approved")
    assert (match["source"], match["anchored"]) == ("chain", True)
    assert match["mrv_report_id"] is None  # no MRV report in this registry has the hash
    assert match["project"]["id"] == pid
    assert (match["period_start"], match["period_end"]) == (2026, 2029)
    again = await client.get("/public/verify-hash", params={"sha256_hex": digest})
    assert again.json() == body  # GET and POST agree; idempotent

    miss = await client.post("/public/verify-hash", json={"sha256_hex": "ab" * 32})
    assert miss.json() == {"sha256_hex": "ab" * 32, "found": False, "matches": []}
    assert (await client.post("/public/verify-hash", json={"sha256_hex": "xyz"})).status_code == 422
    assert (await client.get("/public/verify-hash", params={"sha256_hex": "12"})).status_code == 422


async def test_cache_headers_and_etag(world: World, client: httpx.AsyncClient) -> None:
    r = await client.get("/public/projects")
    assert r.headers["cache-control"] == "public, max-age=60"
    etag = r.headers["etag"]
    assert etag.startswith('W/"')
    cached = await client.get("/public/projects", headers={"If-None-Match": etag})
    assert cached.status_code == 304
    assert cached.content == b""
    other = await client.get("/public/projects", headers={"If-None-Match": 'W/"other"'})
    assert other.status_code == 200


async def test_unregistered_on_chain_project_has_null_ref(
    world: World, client: httpx.AsyncClient, sql: Any
) -> None:
    """Events for a project key this deployment cannot resolve still show, without a project."""
    stranger = "0x" + "99" * 32
    await _event(
        sql,
        world,
        "CreditsIssued",
        {
            "tokenId": 8,
            "reportId": 4,
            "projectId": stranger,
            "creditType": 1,
            "to": "0x" + "88" * 20,
            "amount": 5,
            "expiresAt": 0,
        },
        project_key=stranger,
    )
    v = (await client.get("/public/vintages/8")).json()
    assert v["project"] == {"id": None, "name": None, "project_key": stranger}
    assert v["expires_at"] is None
    assert v["expired"] is False
    # A hidden (draft) project registered on-chain is not named either.
    draft = world.projects["draft"]
    await _register(sql, world, draft)
    await _event(
        sql,
        world,
        "CreditsIssued",
        {
            "tokenId": 10,
            "reportId": 5,
            "projectId": key_of(draft),
            "creditType": 0,
            "to": "0x" + "88" * 20,
            "amount": 5,
            "expiresAt": 0,
        },
        project_key=key_of(draft),
    )
    assert (await client.get("/public/vintages/10")).json()["project"]["name"] is None
