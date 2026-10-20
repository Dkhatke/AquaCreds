"""Approved MRV reports in the public registry: visibility (approved only), report fields, anchoring
links, the redacted public record, the PDF redirect, privacy, and verify-hash sources."""

import json
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import httpx
import pytest
from conftest import FakeStorage
from reports_testlib import (
    GENERATED_AT,
    PLOTS,
    ReportSetup,
    request_and_build,
    setup_project,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from aqua_api.services.public_reports import public_record
from aqua_chain.encoding import project_id_bytes32, to_hex
from aqua_core.db import session_factory

CHAIN = 31337
REGISTRY = "0x" + "11" * 20
SECRET_NOTE = "Call Sunita Devi on +91 98765 43210 about the boundary dispute"
CHANGES_COMMENT = "Plot D02 tags unreadable, re-measure"
APPROVE_COMMENT = "Reviewed with the field coordinator Mr Rao"


@pytest.fixture
async def sessionmaker(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(database_url)
    yield session_factory(engine)
    await engine.dispose()


def _body() -> dict[str, Any]:
    return {"strata_area_ha": {"dense": 250.0, "sparse": 120.0}, "notes": SECRET_NOTE}


async def _review(s: ReportSetup, report_id: str, decision: str, comment: str) -> None:
    r = await s.verifier.post(
        f"/reports/{report_id}/review", json={"decision": decision, "comment": comment}
    )
    assert r.status_code == 200, r.text


async def _private(s: ReportSetup, report_id: str) -> dict[str, Any]:
    r = await s.admin.get(f"/reports/{report_id}")
    assert r.status_code == 200, r.text
    detail: dict[str, Any] = r.json()
    return detail


async def _hidden_everywhere(client: httpx.AsyncClient, s: ReportSetup, report: Any) -> None:
    detail = (await client.get(f"/public/projects/{s.project_id}")).json()
    assert detail["mrv_reports"] == []
    for suffix in ("", "/record.json", "/report.pdf"):
        assert (await client.get(f"/public/reports/{report['id']}{suffix}")).status_code == 404
    hit = await client.get("/public/verify-hash", params={"sha256_hex": report["report_hash"]})
    assert hit.json()["found"] is False


async def _approved_world(
    client: httpx.AsyncClient,
    make_actor: Any,
    sql: Any,
    storage: FakeStorage,
    enqueued: Any,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> tuple[ReportSetup, dict[str, Any], dict[str, Any]]:
    """v1 → changes requested (stays private); v2 → approved. Returns (setup, v1, v2)."""
    s = await setup_project(make_actor, sql)
    built = await request_and_build(s, storage, enqueued, sessionmaker, body=_body())
    assert built["status"] == "built", built
    v1 = await _private(s, built["report_id"])
    await _hidden_everywhere(client, s, v1)  # under review
    await _review(s, v1["id"], "request_changes", CHANGES_COMMENT)
    await _hidden_everywhere(client, s, v1)  # changes requested
    later = GENERATED_AT + timedelta(days=1)
    built = await request_and_build(s, storage, enqueued, sessionmaker, body=_body(), now=later)
    assert built["status"] == "built", built
    v2 = await _private(s, built["report_id"])
    await _review(s, v2["id"], "approve", APPROVE_COMMENT)
    return s, v1, await _private(s, v2["id"])


async def _confirmed_tx(sql: Any, purpose: str, tx_hash: str, block: int) -> uuid.UUID:
    tx_id = uuid.uuid4()
    await sql(
        "INSERT INTO chain_transactions (id, chain_id, purpose, entity_type, entity_id, signer, "
        "from_address, to_address, contract, function, args, status, tx_hash, block_number, "
        "result, attempts) VALUES (:id, :c, :p, 'mrv_report', 'x', 'registrar', :a, :a, "
        "'AquaRegistry', 'f', CAST('[]' AS jsonb), 'confirmed', :tx, :b, CAST('{}' AS jsonb), 1)",
        id=tx_id,
        c=CHAIN,
        p=purpose,
        a=REGISTRY,
        tx=tx_hash,
        b=block,
    )
    return tx_id


async def _chain_event(
    sql: Any, event: str, args: dict[str, Any], project_key: str, block: int
) -> str:
    tx_hash = "0x" + f"{block:064x}"
    await sql(
        "INSERT INTO chain_events (chain_id, contract, address, event, block_number, block_hash, "
        "tx_hash, log_index, project_key, args) VALUES (:c, 'AquaRegistry', :addr, :ev, :b, :bh, "
        ":tx, 0, :pk, CAST(:args AS jsonb))",
        c=CHAIN,
        addr=REGISTRY,
        ev=event,
        b=block,
        bh="0x" + "ab" * 32,
        tx=tx_hash,
        pk=project_key,
        args=json.dumps(args),
    )
    return tx_hash


# ---------------------------------------------------------------- visibility and fields


async def test_only_approved_reports_are_public(
    client: httpx.AsyncClient,
    make_actor: Any,
    sql: Any,
    storage: FakeStorage,
    enqueued: Any,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    s, v1, v2 = await _approved_world(client, make_actor, sql, storage, enqueued, sessionmaker)
    # The changes-requested version stays invisible after a later version is approved.
    for suffix in ("", "/record.json", "/report.pdf"):
        assert (await client.get(f"/public/reports/{v1['id']}{suffix}")).status_code == 404
    v1_hit = await client.get("/public/verify-hash", params={"sha256_hex": v1["report_hash"]})
    assert v1_hit.json()["found"] is False
    assert (await client.get(f"/public/reports/{uuid.uuid4()}")).status_code == 404

    detail = (await client.get(f"/public/projects/{s.project_id}")).json()
    (pub,) = detail["mrv_reports"]
    assert pub["id"] == v2["id"]
    assert pub["status"] == "approved"
    assert pub["version"] == 2
    assert pub["period_id"] == s.period_id
    assert pub["period_kind"] == "monitoring"
    for field in (
        "report_hash",
        "lccc_units",
        "tccc_units",
        "period_start_year",
        "period_end_year",
    ):
        assert pub[field] == v2[field], field
    assert pub["generated_at"] == v2["generated_at"]
    assert pub["decided_at"] == v2["decided_at"]
    assert pub["anchoring"] == {
        "hash_anchored": False,
        "chain_report_id": None,
        "submit_tx": None,
        "approve_tx": None,
        "completed_at": None,
    }
    assert pub["downloads"]["pdf_url"].endswith(f"/public/reports/{v2['id']}/report.pdf")
    assert pub["downloads"]["record_url"].endswith(f"/public/reports/{v2['id']}/record.json")
    assert [p["status"] for p in detail["periods"]] == ["approved"]

    r = await client.get(f"/public/reports/{v2['id']}")
    assert r.status_code == 200, r.text
    one = r.json()
    assert {k: one[k] for k in pub} == pub
    assert one["project"] == {
        "id": s.project_id,
        "name": detail["name"],
        "project_key": to_hex(project_id_bytes32(s.project_id)),
    }
    assert one["project_methodology"] == "ccts_bm_fr05_001"
    assert one["tree_stock_tco2e"] > 0
    assert one["tccc_tco2e"] >= one["tccc_units"]
    full = json.loads(storage.objects[f"{_prefix(s, 2)}/report.json"][0])
    trace = one["traceability"]
    assert trace["record_schema"] == "aquacreds.mrv_report/1"
    assert trace["methodology_profile"] == full["methodology"]
    assert trace["methodology_profile"]["id"] == "ccts_bm_fr05_001"
    assert trace["software"] == full["software"]
    assert set(trace["software"]) == {"aqua_carbon", "aqua_mrv"}
    assert trace["status_label"] == full["status"]
    assert trace["record_sha256_verified"] is True


def _prefix(s: ReportSetup, version: int) -> str:
    return f"projects/{s.project_id}/periods/{s.period_id}/reports/v{version}"


async def test_rejected_report_shows_only_as_period_status(
    client: httpx.AsyncClient,
    make_actor: Any,
    sql: Any,
    storage: FakeStorage,
    enqueued: Any,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    s = await setup_project(make_actor, sql, project_status="monitoring")
    built = await request_and_build(s, storage, enqueued, sessionmaker, body=_body())
    report = await _private(s, built["report_id"])
    await _review(s, report["id"], "reject", "Allometry inputs implausible")
    await _hidden_everywhere(client, s, report)
    detail = (await client.get(f"/public/projects/{s.project_id}")).json()
    assert [p["status"] for p in detail["periods"]] == ["rejected"]
    assert "Allometry inputs implausible" not in json.dumps(detail)


async def test_hidden_project_hides_its_approved_report(
    client: httpx.AsyncClient,
    make_actor: Any,
    sql: Any,
    storage: FakeStorage,
    enqueued: Any,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    s, _, v2 = await _approved_world(client, make_actor, sql, storage, enqueued, sessionmaker)
    await sql("UPDATE projects SET status='draft' WHERE id=:id", id=s.project_id)
    assert (await client.get(f"/public/reports/{v2['id']}")).status_code == 404
    hit = await client.get("/public/verify-hash", params={"sha256_hex": v2["report_hash"]})
    assert hit.json()["found"] is False


# ---------------------------------------------------------------- downloads and privacy


async def test_public_record_is_redacted_and_only_the_pdf_is_presigned(
    client: httpx.AsyncClient,
    make_actor: Any,
    sql: Any,
    storage: FakeStorage,
    enqueued: Any,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    s, _, v2 = await _approved_world(client, make_actor, sql, storage, enqueued, sessionmaker)
    prefix = _prefix(s, 2)
    full = json.loads(storage.objects[f"{prefix}/report.json"][0])
    assert full["notes"] == SECRET_NOTE  # the private record does hold these fields
    assert full["plots"][0]["lat"] is not None
    assert full["project"]["boundary"]

    r = await client.get(f"/public/reports/{v2['id']}/record.json")
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "public, max-age=60"
    body = r.json()
    assert body["variant"] == "aquacreds.mrv_report.public/1"
    assert body["full_record_sha256"] == v2["report_hash"]
    assert set(body["redacted"]) == {"notes", "project.boundary", "plots[].lat", "plots[].lon"}
    record = body["record"]
    assert "notes" not in record
    assert "boundary" not in record["project"]
    assert [p["code"] for p in record["plots"]] == sorted(PLOTS)
    for plot in record["plots"]:
        assert not {"lat", "lon"} & plot.keys()
        assert plot["trees"]
    # Everything else of the calculation is kept verbatim.
    for key in ("result", "trace", "wood_density", "methodology", "software", "inputs"):
        assert record[key] == full[key], key

    pdf = await client.get(f"/public/reports/{v2['id']}/report.pdf")
    assert pdf.status_code == 307
    assert pdf.headers["cache-control"] == "no-store"
    location = pdf.headers["location"]
    assert location == storage.presign_get(f"{prefix}/report.pdf", f"mrv-report-{v2['id']}-v2.pdf")
    for private_key in (f"{prefix}/report.json", f"{prefix}/report-csv.zip"):
        assert private_key not in location
    for missing in ("report.json", "report-csv.zip", "report.csv", "record.csv"):
        assert (await client.get(f"/public/reports/{v2['id']}/{missing}")).status_code in (
            404,
            405,
        )


async def test_privacy_of_report_payloads(
    client: httpx.AsyncClient,
    make_actor: Any,
    sql: Any,
    storage: FakeStorage,
    enqueued: Any,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    s, v1, v2 = await _approved_world(client, make_actor, sql, storage, enqueued, sessionmaker)
    prefix = _prefix(s, 2)
    full = json.loads(storage.objects[f"{prefix}/report.json"][0])
    boundary_text = json.dumps(full["project"]["boundary"]["coordinates"], separators=(",", ":"))
    forbidden = [
        s.admin.email,
        s.verifier.email,
        "NGO Admin",
        str(s.admin.id),
        str(s.verifier.id),
        SECRET_NOTE,
        "Sunita",
        CHANGES_COMMENT,
        APPROVE_COMMENT,
        f"{prefix}/report.json",
        f"{prefix}/report-csv.zip",
        f"{prefix}/report.pdf",
        "report-csv",
        "presign",
        "signature",
        "TAG-D01",  # plot marker ids
        "generated_by",
        "verifier_id",
        "decision_comment",
        "anchor_error",
        "json_key",
        "csv_key",
        "pdf_key",
        v1["id"],  # the changes-requested version
        v1["report_hash"],
    ]
    # Exact plot centres (lon, lat) from the measurement data.
    for _, lon, lat, _ in PLOTS.values():
        forbidden += [f"{lon}", f"{lat}"]
    urls = [
        f"/public/projects/{s.project_id}",
        f"/public/reports/{v2['id']}",
        f"/public/reports/{v2['id']}/record.json",
        f"/public/verify-hash?sha256_hex={v2['report_hash']}",
    ]
    for url in urls:
        r = await client.get(url)
        assert r.status_code == 200, (url, r.text)
        for needle in forbidden:
            assert needle not in r.text, (url, needle)
    record_text = (await client.get(f"/public/reports/{v2['id']}/record.json")).text
    # The full-precision boundary is not in the public record.
    assert boundary_text not in record_text
    assert '"boundary"' not in record_text


async def test_tampered_record_is_an_error_not_a_guess(
    client: httpx.AsyncClient,
    make_actor: Any,
    sql: Any,
    storage: FakeStorage,
    enqueued: Any,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    s, _, v2 = await _approved_world(client, make_actor, sql, storage, enqueued, sessionmaker)
    key = f"{_prefix(s, 2)}/report.json"
    data, content_type = storage.objects[key]
    storage.objects[key] = (data.replace(b'"schema"', b' "schema"', 1), content_type)
    for suffix in ("", "/record.json"):
        r = await client.get(f"/public/reports/{v2['id']}{suffix}")
        assert r.status_code == 502
        assert "does not match" in r.json()["detail"]
    del storage.objects[key]
    r = await client.get(f"/public/reports/{v2['id']}")
    assert r.status_code == 502
    assert "unavailable" in r.json()["detail"]


def test_public_record_drops_unknown_fields() -> None:
    record = {
        "schema": "aquacreds.mrv_report/1",
        "notes": "private",
        "operator_email": "someone@example.org",
        "project": {"id": "p", "name": "P", "boundary": {"type": "Polygon"}, "owner": "x"},
        "plots": [
            {"code": "A", "lat": 1.0, "lon": 2.0, "trees": [{"n": 1, "tag_photo": "k.jpg"}]},
            "not-a-plot",
        ],
        "result": {"lccc": 1.0},
    }
    variant, redacted = public_record(record)
    assert variant == {
        "schema": "aquacreds.mrv_report/1",
        "project": {"id": "p", "name": "P"},
        "plots": [{"code": "A", "trees": [{"n": 1}]}],
        "result": {"lccc": 1.0},
    }
    assert redacted == [
        "notes",
        "operator_email",
        "plots[].lat",
        "plots[].lon",
        "plots[].trees[].tag_photo",
        "project.boundary",
        "project.owner",
    ]


# ---------------------------------------------------------------- anchoring and verify-hash


async def test_anchoring_links_and_verify_hash_sources(
    client: httpx.AsyncClient,
    make_actor: Any,
    sql: Any,
    storage: FakeStorage,
    enqueued: Any,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    s, v1, v2 = await _approved_world(client, make_actor, sql, storage, enqueued, sessionmaker)
    digest = v2["report_hash"]
    pkey = to_hex(project_id_bytes32(s.project_id))

    # 1. Approved, not yet anchored: matched from the registry database only.
    body = (await client.post("/public/verify-hash", json={"sha256_hex": digest})).json()
    assert body["found"] is True
    (match,) = body["matches"]
    assert match["source"] == "registry_db"
    assert match["anchored"] is False
    assert match["chain"] is None
    assert match["report_id"] is None
    assert match["mrv_report_id"] == v2["id"]
    assert match["status"] == "approved"
    assert match["project"] == {
        "id": s.project_id,
        "name": match["project"]["name"],
        "project_key": pkey,
    }
    assert (match["period_start"], match["period_end"]) == (
        v2["period_start_year"],
        v2["period_end_year"],
    )

    # 2. The relayer confirmed submitReport + approveReport; the indexer has not caught up.
    submit_hash, approve_hash = "0x" + "a1" * 32, "0x" + "a2" * 32
    submit_id = await _confirmed_tx(sql, "submit_report", submit_hash, 500)
    approve_id = await _confirmed_tx(sql, "approve_report", approve_hash, 501)
    await sql(
        "UPDATE mrv_reports SET submit_tx_id=:s, approve_tx_id=:a, chain_report_id=7, "
        "anchored_at=now() WHERE id=:id",
        s=submit_id,
        a=approve_id,
        id=v2["id"],
    )
    (match,) = (await client.get("/public/verify-hash", params={"sha256_hex": digest})).json()[
        "matches"
    ]
    assert (match["source"], match["anchored"], match["report_id"]) == ("registry_db", True, 7)
    pub = (await client.get(f"/public/reports/{v2['id']}")).json()
    anchoring = pub["anchoring"]
    assert anchoring["hash_anchored"] is True
    assert anchoring["chain_report_id"] == 7
    assert anchoring["submit_tx"] == {
        "chain_id": CHAIN,
        "tx_hash": submit_hash,
        "block_number": 500,
        "explorer_url": None,
    }
    assert anchoring["approve_tx"]["tx_hash"] == approve_hash
    assert anchoring["completed_at"] is not None

    # 3. Indexed: the chain match replaces the database one and links the MRV report id.
    submitted = {
        "reportId": 7,
        "projectId": pkey,
        "periodStart": v2["period_start_year"],
        "periodEnd": v2["period_end_year"],
        "reportHash": "0x" + digest,
        "lccc": v2["lccc_units"],
        "evidenceURI": f"urn:aquacreds:mrv-report:{v2['id']}",
    }
    await _chain_event(sql, "ReportSubmitted", submitted, pkey, 500)
    await _chain_event(
        sql,
        "ReportApproved",
        {"reportId": 7, "projectId": pkey, "lccc": v2["lccc_units"], "tccc": v2["tccc_units"]},
        pkey,
        501,
    )
    # A chain report carrying the hash of the unapproved v1 is never linked to it.
    await _chain_event(
        sql,
        "ReportSubmitted",
        {**submitted, "reportId": 8, "reportHash": "0x" + v1["report_hash"]},
        pkey,
        502,
    )
    body = (await client.get("/public/verify-hash", params={"sha256_hex": digest})).json()
    (match,) = body["matches"]
    assert (match["source"], match["anchored"]) == ("chain", True)
    assert (match["report_id"], match["mrv_report_id"]) == (7, v2["id"])
    assert match["status"] == "approved"
    assert match["chain"]["block_number"] == 500

    detail = (await client.get(f"/public/projects/{s.project_id}")).json()
    by_id = {r["report_id"]: r for r in detail["reports"]}
    assert by_id[7]["mrv_report_id"] == v2["id"]
    assert by_id[8]["mrv_report_id"] is None
    (v1_match,) = (
        await client.get("/public/verify-hash", params={"sha256_hex": v1["report_hash"]})
    ).json()["matches"]
    assert (v1_match["source"], v1_match["mrv_report_id"]) == ("chain", None)
