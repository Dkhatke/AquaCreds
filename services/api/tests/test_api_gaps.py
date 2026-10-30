"""API gaps from the web and mobile apps: public stats, evidence points for the map, offline
(batch) capture nonces, Idempotency-Key replays, measurement read-back, submission pagination."""

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from api_testlib import project_payload, square
from conftest import FakeStorage, Field
from test_field import INSIDE, _check, _photo, _submit

from aqua_api.services.public_registry import project_key

CHAIN = 31337  # Settings.chain_id default
PLOT = {
    "code": "P-01",
    "stratum": "dense",
    "lon": 86.862,
    "lat": 20.712,
    "area_m2": 100,
    "marker_id": "BHK-01",
}
TREES = [{"species": "Avicennia marina", "dbh_cm": 9.5, "height_m": 6.2, "alive": True}]


async def _chain_event(
    sql: Any, block: int, event: str, args: dict[str, Any], pk: str | None, when: datetime
) -> None:
    await sql(
        "INSERT INTO chain_events (chain_id, contract, address, event, block_number, block_hash, "
        "tx_hash, log_index, project_key, args, block_time) VALUES (:c, 'AquaCredit', :a, :ev, "
        ":b, :bh, :tx, 0, :pk, CAST(:args AS jsonb), :bt)",
        c=CHAIN,
        a="0x" + "22" * 20,
        ev=event,
        b=block,
        bh="0x" + "ab" * 32,
        tx="0x" + hashlib.sha256(f"{event}{block}".encode()).hexdigest(),
        pk=pk,
        args=json.dumps(args),
        bt=when,
    )


# ---------------------------------------------------------------- public stats


async def test_public_stats_empty(client: httpx.AsyncClient) -> None:
    r = await client.get("/public/stats")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["projects"] == 0
    assert body["area_ha"] == 0
    assert body["credits_total"] == {
        "vintages": 0,
        "issued": 0,
        "retired": 0,
        "replaced": 0,
        "outstanding": 0,
    }
    assert body["chain"] == {
        "chain_id": CHAIN,
        "last_indexed_block": None,
        "last_indexed_at": None,
        "last_event_block_time": None,
    }


async def test_public_stats_aggregates_public_projects_and_chain(
    client: httpx.AsyncClient, make_actor: Any, sql: Any
) -> None:
    admin = await make_actor("Stats Admin")
    org = (await admin.post("/orgs", json={"name": "Stats NGO", "org_type": "ngo"})).json()
    ids = []
    for i, status_ in enumerate(["registered", "monitoring", "submitted", "draft", "rejected"]):
        boundary = square(86.85 + 0.05 * i, 20.70, 0.01)
        r = await admin.post(
            "/projects",
            json=project_payload(org["id"], name=f"Stats project {i}", boundary=boundary),
        )
        assert r.status_code == 201, r.text
        ids.append(r.json()["id"])
        await sql("UPDATE projects SET status=:s WHERE id=:id", s=status_, id=ids[-1])
    areas = await sql("SELECT id, area_ha FROM projects")
    public_area = sum(a for pid, a in areas if str(pid) in ids[:3])

    when = datetime(2026, 10, 1, 12, tzinfo=UTC)
    key = project_key(uuid.UUID(ids[0]))
    await _chain_event(
        sql,
        10,
        "ProjectRegistered",
        {"metadataURI": f"urn:aquacreds:project:{ids[0]}"},
        key,
        when,
    )
    issued = {"tokenId": 1, "amount": 100, "creditType": 0, "expiresAt": 0, "reportId": 1}
    await _chain_event(sql, 11, "CreditsIssued", issued, key, when)
    long_term = {"tokenId": 2, "amount": 40, "creditType": 1, "expiresAt": 0, "reportId": 1}
    await _chain_event(sql, 12, "CreditsIssued", long_term, key, when)
    retired = {"tokenId": 1, "amount": 30, "retirementId": 1, "beneficiary": "A Buyer"}
    await _chain_event(sql, 13, "CreditsRetired", retired, None, when + timedelta(days=1))
    await sql(
        "INSERT INTO chain_cursors (chain_id, address, contract, last_block) VALUES "
        "(:c, :a, 'AquaCredit', 20), (:c, :b, 'AquaRegistry', 18)",
        c=CHAIN,
        a="0x" + "22" * 20,
        b="0x" + "11" * 20,
    )

    r = await client.get("/public/stats")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["projects"] == 3  # draft and rejected are never public
    assert body["area_ha"] == round(public_area, 2)
    assert body["projects_by_status"] == {"registered": 1, "monitoring": 1, "submitted": 1}
    assert (body["registered"], body["monitoring"], body["registered_on_chain"]) == (1, 1, 1)
    assert body["credits"]["temporary"] == {
        "vintages": 1,
        "issued": 100,
        "retired": 30,
        "replaced": 0,
        "outstanding": 70,
    }
    assert body["credits"]["long_term"]["issued"] == 40
    assert body["credits_total"]["issued"] == 140
    assert body["credits_total"]["outstanding"] == 110
    assert body["retirements"] == 1
    assert body["chain"]["last_indexed_block"] == 20
    assert body["chain"]["last_indexed_at"] is not None
    assert body["chain"]["last_event_block_time"].startswith("2026-10-02T12:00:00")
    # Aggregates only: no names, e-mails, beneficiaries, ids or coordinates.
    text = r.text
    for secret in (admin.email, "Stats NGO", "A Buyer", "Stats project", ids[0], "86.85"):
        assert secret not in text

    # Cacheable: public max-age + ETag, 304 on revalidation.
    assert r.headers["cache-control"] == "public, max-age=60"
    again = await client.get("/public/stats", headers={"If-None-Match": r.headers["etag"]})
    assert again.status_code == 304


# ---------------------------------------------------------------- evidence points


async def _member(field: Field, make_actor: Any, role: str = "member") -> Any:
    actor = await make_actor(f"Org {role}")
    org_id = (await field.admin.get(f"/projects/{field.project_id}")).json()["org_id"]
    r = await field.admin.post(f"/orgs/{org_id}/members", json={"email": actor.email, "role": role})
    assert r.status_code in (200, 201), r.text
    return actor


async def test_evidence_points_are_snapped_for_members_and_raw_for_the_field_team(
    field: Field, storage: FakeStorage, make_actor: Any, sql: Any, database_url: str
) -> None:
    plot = await field.admin.post(f"/projects/{field.project_id}/plots", json=PLOT)
    assert plot.status_code == 201, plot.text
    at_plot = (86.86213, 20.71187)  # a few metres from the plot centre
    loose = (86.85731, 20.70468)
    r1 = await _submit(field, storage, _photo(31, at_plot), at=at_plot, kind="plot_centre",
                       plot_code="P-01")  # fmt: skip
    r2 = await _submit(field, storage, _photo(32, loose), at=loose)
    assert r1.status_code == r2.status_code == 202
    await _check(database_url, storage, r2.json()["id"])

    member = await _member(field, make_actor)
    r = await member.get(f"/projects/{field.project_id}/evidence-points")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["type"] == "FeatureCollection"
    assert (body["precision"], body["grid_deg"], body["total"], body["truncated"]) == (
        "snapped",
        0.005,
        2,
        False,
    )
    by_snap = {f["properties"]["snap"]: f for f in body["features"]}
    assert by_snap["plot_centre"]["geometry"]["coordinates"] == [86.862, 20.712]
    assert by_snap["plot_centre"]["properties"]["plot_code"] == "P-01"
    # 86.85731 → cell [86.855, 86.860) → centre 86.8575; 20.70468 → [20.700, 20.705) → 20.7025
    assert by_snap["grid"]["geometry"]["coordinates"] == [86.8575, 20.7025]
    assert by_snap["grid"]["properties"]["status"] == "checked"
    assert by_snap["grid"]["properties"]["trust_score"] == 100
    # Privacy: no raw fix, submission id, capture time or accuracy anywhere in the response.
    for value in ("86.86213", "20.71187", "86.85731", "20.70468", r1.json()["id"], r2.json()["id"]):
        assert value not in r.text
    for feature in body["features"]:
        props = feature["properties"]
        assert props["id"] is None
        assert props["captured_at"] is None
        assert props["gps_accuracy_m"] is None

    # Plain members cannot ask for raw points; the field team and staff can.
    assert (
        await member.get(f"/projects/{field.project_id}/evidence-points?precision=raw")
    ).status_code == 403
    raw = await field.agent.get(f"/projects/{field.project_id}/evidence-points?precision=raw")
    assert raw.status_code == 200, raw.text
    coords = {tuple(f["geometry"]["coordinates"]) for f in raw.json()["features"]}
    assert coords == {at_plot, loose}
    assert {f["properties"]["id"] for f in raw.json()["features"]} == {
        r1.json()["id"],
        r2.json()["id"],
    }
    verifier = await make_actor("Verifier")
    await sql("UPDATE users SET platform_role='verifier' WHERE id=:id", id=verifier.id)
    staff = await verifier.get(f"/projects/{field.project_id}/evidence-points?precision=raw")
    assert staff.status_code == 200
    assert staff.json()["precision"] == "raw"

    # Period filter; outsiders see nothing.
    other = await member.get(
        f"/projects/{field.project_id}/evidence-points", params={"period_id": str(uuid.uuid4())}
    )
    assert other.json()["features"] == []
    outsider = await make_actor("Outsider")
    assert (await outsider.get(f"/projects/{field.project_id}/evidence-points")).status_code == 404
    # Unauthenticated: never.
    assert (
        await field.agent.client.get(f"/projects/{field.project_id}/evidence-points")
    ).status_code == 401


# ---------------------------------------------------------------- batch nonces


async def test_batch_nonces_offline_capture(
    field: Field, storage: FakeStorage, make_actor: Any, database_url: str, sql: Any
) -> None:
    r = await field.agent.post(f"/periods/{field.period_id}/nonces/batch", json={"count": 5})
    assert r.status_code == 201, r.text
    batch = r.json()
    assert batch["mode"] == "batch"
    assert len(batch["nonces"]) == len({n["nonce"] for n in batch["nonces"]}) == 5
    issued = datetime.fromisoformat(batch["issued_at"])
    expires = datetime.fromisoformat(batch["expires_at"])
    assert expires - issued == timedelta(hours=72)
    modes = await sql("SELECT DISTINCT mode FROM capture_nonces")
    assert modes == [("batch",)]

    # Offline: captured two days after the batch was issued (inside the window) → accepted,
    # but only a lower-weight warning: trust 90 instead of 100, recorded as nonce_mode batch.
    await sql(
        "UPDATE capture_nonces SET issued_at = issued_at - interval '2 days', "
        "expires_at = expires_at - interval '2 days'"
    )
    expires -= timedelta(days=2)
    first = batch["nonces"][0]["nonce"]
    sub = await _submit(field, storage, _photo(41), nonce=first)
    assert sub.status_code == 202, sub.text
    result = await _check(database_url, storage, sub.json()["id"])
    assert result["status"] == "checked"
    assert result["trust_score"] == 90
    detail = (await field.agent.get(f"/submissions/{sub.json()['id']}")).json()
    nonce_check = next(c for c in detail["checks"] if c["name"] == "capture_nonce")
    assert nonce_check["outcome"] == "warn"
    assert nonce_check["weight"] == 0.5
    assert nonce_check["details"]["nonce_mode"] == "batch"
    assert nonce_check["details"]["window_h"] == 72

    # Single use.
    again = await _submit(field, storage, _photo(42), nonce=first)
    assert again.status_code == 409

    # Bound to the agent: another field agent of the same org cannot use it.
    other_agent = await _member(field, make_actor, "field_agent")
    other = Field(field.admin, other_agent, field.project_id, field.period_id)
    stolen = await _submit(other, storage, _photo(43), nonce=batch["nonces"][1]["nonce"])
    assert stolen.status_code == 422

    # Expired: a capture after the window is a hard failure in the worker.
    late = await _submit(
        field,
        storage,
        _photo(44),
        nonce=batch["nonces"][2]["nonce"],
        captured_at=expires + timedelta(hours=1),
    )
    assert late.status_code == 202
    assert (await _check(database_url, storage, late.json()["id"]))["status"] == "rejected"


async def test_batch_nonce_limits_and_roles(
    field: Field, make_actor: Any, client: httpx.AsyncClient
) -> None:
    url = f"/periods/{field.period_id}/nonces/batch"
    assert (await field.agent.post(url, json={"count": 21})).status_code == 422
    assert (await field.agent.post(url, json={"count": 0})).status_code == 422
    assert (await field.agent.post(url, json={"count": 20})).status_code == 201
    assert (await field.agent.post(url, json={"count": 20})).status_code == 201
    capped = await field.agent.post(url, json={"count": 1})  # 40 outstanding is the limit
    assert capped.status_code == 409
    assert "40" in capped.json()["detail"]
    assert (await field.admin.post(url, json={"count": 1})).status_code == 201  # per agent
    member = await _member(field, make_actor)
    assert (await member.post(url, json={"count": 1})).status_code == 403
    outsider = await make_actor("Outsider")
    assert (await outsider.post(url, json={"count": 1})).status_code == 404

    live = (await field.agent.post(f"/periods/{field.period_id}/nonces")).json()
    assert live["mode"] == "live"
    issued = datetime.fromisoformat(live["issued_at"])
    assert datetime.fromisoformat(live["expires_at"]) - issued == timedelta(minutes=15)
    assert abs(datetime.fromisoformat(live["server_time"]) - issued) < timedelta(seconds=5)


# ---------------------------------------------------------------- idempotency


async def test_submission_idempotency_key_replays_the_original_response(
    field: Field, storage: FakeStorage, enqueued: list[Any], sql: Any, make_actor: Any
) -> None:
    nonce = (await field.agent.post(f"/periods/{field.period_id}/nonces")).json()["nonce"]
    ticket = (
        await field.agent.post(
            f"/periods/{field.period_id}/photos/upload-url", json={"content_type": "image/jpeg"}
        )
    ).json()
    photo = _photo(51)
    storage.put(ticket["object_key"], photo, "image/jpeg")
    body = {
        "object_key": ticket["object_key"],
        "sha256": hashlib.sha256(photo).hexdigest(),
        "nonce": nonce,
        "kind": "general",
        "lon": INSIDE[0],
        "lat": INSIDE[1],
        "gps_accuracy_m": 4.5,
        "captured_at": datetime.now(UTC).isoformat(),
    }
    url = f"/periods/{field.period_id}/submissions"
    key = str(uuid.uuid4())
    first = await field.agent.client.post(
        url, json=body, headers={**field.agent.headers, "Idempotency-Key": key}
    )
    assert first.status_code == 202, first.text
    assert "idempotent-replayed" not in first.headers

    # Lost response → retry with the same key: the original 202 and body, nothing re-done.
    replay = await field.agent.client.post(
        url, json=body, headers={**field.agent.headers, "Idempotency-Key": key}
    )
    assert replay.status_code == 202, replay.text
    assert replay.headers["idempotent-replayed"] == "true"
    assert replay.json() == first.json()
    assert len(enqueued) == 1
    assert (await sql("SELECT count(*) FROM field_submissions"))[0][0] == 1

    # Without the key the retry still gets the old 409.
    assert (await field.agent.post(url, json=body)).status_code == 409
    # Same key, different request → 422, never a silent replay of something else.
    changed = await field.agent.client.post(
        url,
        json={**body, "gps_accuracy_m": 9},
        headers={**field.agent.headers, "Idempotency-Key": key},
    )
    assert changed.status_code == 422
    assert changed.json()["detail"]["code"] == "idempotency_key_reused"
    # Keys are per user: another agent's identical key is not this agent's record.
    other = await _member(field, make_actor, "field_agent")
    foreign = await other.client.post(
        url, json=body, headers={**other.headers, "Idempotency-Key": key}
    )
    assert foreign.status_code == 422  # the nonce belongs to the first agent
    # Not a UUID → 422.
    bad = await field.agent.client.post(
        url, json=body, headers={**field.agent.headers, "Idempotency-Key": "x"}
    )
    assert bad.status_code == 422
    # After 24 h the key is forgotten: the request runs again (and meets the used nonce).
    await sql("UPDATE idempotency_records SET created_at = now() - interval '25 hours'")
    expired = await field.agent.client.post(
        url, json=body, headers={**field.agent.headers, "Idempotency-Key": key}
    )
    assert expired.status_code == 409


async def test_measurement_idempotency_and_read_back(
    field: Field, make_actor: Any, sql: Any
) -> None:
    plot_id = (await field.admin.post(f"/projects/{field.project_id}/plots", json=PLOT)).json()[
        "id"
    ]
    url = f"/periods/{field.period_id}/plots/{plot_id}/measurements"
    body = {"measured_on": "2026-11-02", "trees": TREES, "notes": "low tide"}
    key = {**field.agent.headers, "Idempotency-Key": str(uuid.uuid4())}

    assert (await field.agent.get(url)).json() == []
    first = await field.agent.client.post(url, json=body, headers=key)
    assert first.status_code == 201, first.text
    replay = await field.agent.client.post(url, json=body, headers=key)
    assert replay.status_code == 201
    assert replay.json() == first.json()
    assert replay.headers["idempotent-replayed"] == "true"
    assert (await field.agent.post(url, json=body)).status_code == 409  # no key: as before
    assert (await sql("SELECT count(*) FROM plot_measurements"))[0][0] == 1

    # Read-back: the app can confirm the record is its own.
    back = await field.agent.get(url)
    assert back.status_code == 200
    assert back.json() == [first.json()]
    assert back.json()[0]["measured_by"] == str(field.agent.id)
    listed = await field.agent.get(f"/periods/{field.period_id}/measurements")
    assert listed.json() == [first.json()]
    filtered = await field.agent.get(
        f"/periods/{field.period_id}/measurements", params={"plot_id": str(uuid.uuid4())}
    )
    assert filtered.json() == []
    member = await _member(field, make_actor)
    assert (await member.get(url)).status_code == 200
    assert (
        await field.agent.get(f"/periods/{field.period_id}/plots/{uuid.uuid4()}/measurements")
    ).status_code == 404
    outsider = await make_actor("Outsider")
    assert (await outsider.get(url)).status_code == 404


# ---------------------------------------------------------------- pagination


async def test_list_submissions_is_paginated_and_filterable(
    field: Field, storage: FakeStorage
) -> None:
    photos = [_photo(60 + i) for i in range(3)]
    ids = []
    for photo in photos:
        r = await _submit(field, storage, photo)
        assert r.status_code == 202
        ids.append(r.json()["id"])
    url = f"/periods/{field.period_id}/submissions"
    everything = await field.agent.get(url)
    assert everything.headers["x-total-count"] == "3"
    assert [s["id"] for s in everything.json()] == ids
    page = await field.agent.get(url, params={"limit": 2, "offset": 1})
    assert [s["id"] for s in page.json()] == ids[1:]
    assert page.headers["x-total-count"] == "3"
    one = await field.agent.get(url, params={"sha256": hashlib.sha256(photos[1]).hexdigest()})
    assert [s["id"] for s in one.json()] == [ids[1]]
    assert one.headers["x-total-count"] == "1"
    assert (await field.agent.get(url, params={"limit": 501})).status_code == 422
    assert (await field.agent.get(url, params={"sha256": "nothex"})).status_code == 422


async def test_concurrent_retries_with_one_key_create_one_measurement(
    field: Field, sql: Any
) -> None:
    import asyncio

    plot = await field.admin.post(f"/projects/{field.project_id}/plots", json=PLOT)
    url = f"/periods/{field.period_id}/plots/{plot.json()['id']}/measurements"
    body = {"measured_on": "2026-11-02", "trees": TREES}
    headers = {**field.agent.headers, "Idempotency-Key": str(uuid.uuid4())}
    responses = await asyncio.gather(
        *(field.agent.client.post(url, json=body, headers=headers) for _ in range(3))
    )
    assert [r.status_code for r in responses] == [201, 201, 201]
    assert len({r.json()["id"] for r in responses}) == 1
    assert (await sql("SELECT count(*) FROM plot_measurements"))[0][0] == 1
