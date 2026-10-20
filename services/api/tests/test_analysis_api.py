"""Satellite analysis API: triggers (enqueue + run record), authorisation, time series, alerts."""

import json
import uuid
from datetime import date, datetime
from typing import Any

import pytest
from api_testlib import insert_sar_observation, project_payload


class Setup:
    def __init__(self, admin: Any, member: Any, outsider: Any, project_id: str) -> None:
        self.admin, self.member, self.outsider, self.project_id = (
            admin,
            member,
            outsider,
            project_id,
        )


@pytest.fixture
async def setup(make_actor: Any, sql: Any) -> Setup:
    admin = await make_actor("NGO Admin")
    org = (await admin.post("/orgs", json={"name": "Godavari Mangroves", "org_type": "ngo"})).json()
    member = await make_actor("Member")
    await admin.post(f"/orgs/{org['id']}/members", json={"email": member.email, "role": "member"})
    outsider = await make_actor("Outsider")
    project_id = (await admin.post("/projects", json=project_payload(org["id"]))).json()["id"]
    await sql("UPDATE projects SET status='submitted' WHERE id=:id", id=project_id)
    return Setup(admin, member, outsider, project_id)


async def _promote(sql: Any, actor: Any, role: str) -> None:
    await sql("UPDATE users SET platform_role=:r WHERE id=:id", r=role, id=actor.id)


async def test_backfill_creates_queued_run_and_enqueues_geo_task(
    setup: Setup, enqueued: list[Any], sql: Any
) -> None:
    r = await setup.admin.post(f"/projects/{setup.project_id}/analysis/backfill")
    assert r.status_code == 202, r.text
    run = r.json()
    assert (run["kind"], run["status"]) == ("backfill", "queued")
    assert run["requested_by"] == str(setup.admin.id)
    assert enqueued == [("aqua.geo.backfill_project", (setup.project_id, "", run["id"]), "geo")]

    detail = await setup.member.get(f"/analysis-runs/{run['id']}")
    assert detail.status_code == 200
    assert detail.json()["id"] == run["id"]
    listed = (await setup.admin.get(f"/projects/{setup.project_id}/analysis-runs")).json()
    assert [x["id"] for x in listed] == [run["id"]]

    # a second backfill while one is queued is refused
    again = await setup.admin.post(f"/projects/{setup.project_id}/analysis/backfill")
    assert again.status_code == 409
    actions = [r.action for r in await sql("SELECT action FROM audit_log ORDER BY id")]
    assert actions.count("analysis.backfill_requested") == 1


async def test_backfill_with_start_date_and_validation(setup: Setup, enqueued: list[Any]) -> None:
    r = await setup.admin.post(
        f"/projects/{setup.project_id}/analysis/backfill", json={"start_date": "2020-01-01"}
    )
    assert r.status_code == 202, r.text
    assert r.json()["params"] == {"start": "2020-01-01"}
    assert enqueued[0][1][1] == "2020-01-01"
    future = await setup.admin.post(
        f"/projects/{setup.project_id}/analysis/backfill", json={"start_date": "2999-01-01"}
    )
    assert future.status_code == 422


async def test_trigger_authorisation(setup: Setup, sql: Any, make_actor: Any) -> None:
    url = f"/projects/{setup.project_id}/analysis/backfill"
    assert (await setup.member.post(url)).status_code == 403
    assert (await setup.outsider.post(url)).status_code == 404  # invisible project
    verifier = await make_actor("Verifier")
    await _promote(sql, verifier, "verifier")
    assert (await verifier.post(url)).status_code == 403  # can read, cannot trigger
    registry = await make_actor("Registry")
    await _promote(sql, registry, "registry_admin")
    assert (await registry.post(url)).status_code == 202
    assert (
        await setup.admin.post(f"/projects/{uuid.uuid4()}/analysis/backfill")
    ).status_code == 404


async def test_draft_projects_cannot_be_analysed(setup: Setup, sql: Any) -> None:
    await sql("UPDATE projects SET status='draft' WHERE id=:id", id=setup.project_id)
    r = await setup.admin.post(f"/projects/{setup.project_id}/analysis/backfill")
    assert r.status_code == 409


async def test_composite_request(setup: Setup, enqueued: list[Any]) -> None:
    url = f"/projects/{setup.project_id}/analysis/composite"
    r = await setup.admin.post(url, json={"season_start_year": 2024})
    assert r.status_code == 202, r.text
    run = r.json()
    assert run["kind"] == "composite"
    assert enqueued == [("aqua.geo.build_composite", (setup.project_id, "2024", run["id"]), "geo")]
    assert (await setup.admin.post(url, json={"season_start_year": 2099})).status_code == 422
    assert (await setup.admin.post(url, json={"season_start_year": 2010})).status_code == 422
    assert (await setup.member.post(url, json={"season_start_year": 2024})).status_code == 403


async def test_enqueue_failure_marks_run_failed(client: Any, setup: Setup, sql: Any) -> None:
    transport: Any = client._transport
    app = transport.app

    def broken(*_: Any, **__: Any) -> None:
        raise ConnectionError("broker down")

    app.state.enqueue = broken
    r = await setup.admin.post(f"/projects/{setup.project_id}/analysis/backfill")
    assert r.status_code == 503
    rows = await sql("SELECT status, error FROM analysis_runs")
    assert [(x.status, x.error) for x in rows] == [("failed", "could not enqueue the job")]


async def test_run_visibility(setup: Setup) -> None:
    run_id = (await setup.admin.post(f"/projects/{setup.project_id}/analysis/backfill")).json()[
        "id"
    ]
    assert (await setup.outsider.get(f"/analysis-runs/{run_id}")).status_code == 404
    assert (await setup.admin.get(f"/analysis-runs/{uuid.uuid4()}")).status_code == 404
    assert (
        await setup.outsider.get(f"/projects/{setup.project_id}/analysis-runs")
    ).status_code == 404


async def _observation(
    sql: Any, project_id: str, day: str, clear: float, ndvi: float | None
) -> None:
    ndvi_stats: dict[str, float] = {"valid_pixels": 0}
    if ndvi is not None:
        ndvi_stats = {"valid_pixels": 100, "mean": ndvi, "p10": ndvi - 0.05, "p50": ndvi}
        ndvi_stats["p90"] = ndvi + 0.05
    stats = json.dumps({"ndvi": ndvi_stats})
    await sql(
        "INSERT INTO scene_observations (id, project_id, item_id, acquired_at, acquired_on, "
        "cloud_cover, clear_fraction, clear_pixels, polygon_pixels, index_stats, provider) "
        "VALUES (:id, :p, :item, :at, :day, 12.5, :clear, "
        ":clear_px, 100, CAST(:stats AS jsonb), 'fake')",
        id=uuid.uuid4(),
        p=project_id,
        item=f"S2_{day}",
        day=date.fromisoformat(day),
        clear=clear,
        clear_px=round(100 * clear),
        at=datetime.fromisoformat(f"{day}T05:00:00+00:00"),
        stats=stats,
    )


async def test_timeseries_points_with_filters(setup: Setup, sql: Any) -> None:
    pid = setup.project_id
    await _observation(sql, pid, "2024-01-05", 0.95, 0.75)
    await _observation(sql, pid, "2024-01-10", 0.10, None)  # cloudy
    await _observation(sql, pid, "2024-02-05", 0.90, 0.72)

    r = await setup.member.get(f"/projects/{pid}/timeseries", params={"index": "ndvi"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["index"] == "ndvi"
    points = body["points"]
    assert [p["date"] for p in points] == ["2024-01-05", "2024-01-10", "2024-02-05"]
    assert points[0]["clear_fraction"] == 0.95
    assert points[0]["p50"] == 0.75
    assert points[1]["p50"] is None
    assert points[1]["valid_pixels"] == 0

    clear_only = await setup.member.get(f"/projects/{pid}/timeseries", params={"min_clear": 0.6})
    assert len(clear_only.json()["points"]) == 2
    window = await setup.member.get(
        f"/projects/{pid}/timeseries", params={"from": "2024-01-06", "to": "2024-01-31"}
    )
    assert [p["date"] for p in window.json()["points"]] == ["2024-01-10"]
    other = await setup.member.get(f"/projects/{pid}/timeseries", params={"index": "mvi"})
    assert other.json()["points"][0]["mean"] is None  # index not stored for this row
    bad = await setup.member.get(f"/projects/{pid}/timeseries", params={"index": "bogus"})
    assert bad.status_code == 422
    assert (await setup.outsider.get(f"/projects/{pid}/timeseries")).status_code == 404


async def test_timeseries_serves_sentinel1_indices(setup: Setup, sql: Any) -> None:
    pid = setup.project_id
    await _observation(sql, pid, "2024-07-01", 0.95, 0.75)  # optical, never in a SAR series
    await insert_sar_observation(sql, pid, "2024-07-05", vh_db=-15.2, rvi=0.61)
    await insert_sar_observation(sql, pid, "2024-07-17", vh_db=-16.0, valid_fraction=0.4)
    await insert_sar_observation(sql, pid, "2024-08-10", vh_db=-14.8, relative_orbit=12)

    r = await setup.member.get(f"/projects/{pid}/timeseries", params={"index": "vh_db"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["index"], body["sensor"]) == ("vh_db", "sentinel-1-rtc")
    assert "dB" in body["units"]
    assert "not compar" in body["note"]
    points = body["points"]
    assert [p["date"] for p in points] == ["2024-07-05", "2024-07-17", "2024-08-10"]
    first = points[0]
    assert (first["item_id"], first["p50"], first["valid_fraction"]) == (
        "S1A_20240705_120000",
        -15.2,
        1.0,
    )
    assert (first["clear_fraction"], first["cloud_cover"]) == (None, None)
    assert (first["orbit_state"], first["relative_orbit"]) == ("ascending", 85)
    assert points[2]["relative_orbit"] == 12

    valid_only = await setup.member.get(
        f"/projects/{pid}/timeseries", params={"index": "vh_db", "min_clear": 0.6}
    )
    assert [p["date"] for p in valid_only.json()["points"]] == ["2024-07-05", "2024-08-10"]
    window = await setup.member.get(
        f"/projects/{pid}/timeseries",
        params={"index": "rvi", "from": "2024-07-01", "to": "2024-07-31"},
    )
    assert [p["p50"] for p in window.json()["points"]] == [0.61, 0.6]
    ratio = await setup.member.get(
        f"/projects/{pid}/timeseries", params={"index": "vh_vv_ratio_db"}
    )
    assert {p["p50"] for p in ratio.json()["points"]} == {-7.0}
    vv = (await setup.member.get(f"/projects/{pid}/timeseries", params={"index": "vv_db"})).json()
    assert vv["points"][0]["p50"] == pytest.approx(-8.2)

    optical = (await setup.member.get(f"/projects/{pid}/timeseries")).json()
    assert optical["sensor"] == "sentinel-2-l2a"
    assert [p["date"] for p in optical["points"]] == ["2024-07-01"]
    assert optical["points"][0]["valid_fraction"] is None
    assert (await setup.outsider.get(f"/projects/{pid}/timeseries?index=rvi")).status_code == 404


async def _alert(sql: Any, project_id: str, day: str = "2024-01-20") -> str:
    alert_id = uuid.uuid4()
    await sql(
        "INSERT INTO alerts (id, project_id, kind, metric, observed_on, observed_value, expected, "
        "severity, message) VALUES (:id, :p, 'vegetation_loss', 'ndvi_p50', :day, 0.4, "
        "CAST('{\"p10\": 0.7}' AS jsonb), 'high', 'NDVI below envelope')",
        id=alert_id,
        p=project_id,
        day=date.fromisoformat(day),
    )
    return str(alert_id)


async def test_alerts_listing_and_acknowledgement(setup: Setup, sql: Any, make_actor: Any) -> None:
    pid = setup.project_id
    first = await _alert(sql, pid, "2024-01-20")
    second = await _alert(sql, pid, "2024-02-20")

    listed = (await setup.member.get(f"/projects/{pid}/alerts")).json()
    assert [a["id"] for a in listed] == [second, first]  # newest first
    assert listed[0]["severity"] == "high"
    assert listed[0]["expected"] == {"p10": 0.7}
    assert (await setup.outsider.get(f"/projects/{pid}/alerts")).status_code == 404

    assert (await setup.member.post(f"/alerts/{first}/acknowledge")).status_code == 403
    assert (await setup.outsider.post(f"/alerts/{first}/acknowledge")).status_code == 404
    assert (await setup.admin.post(f"/alerts/{uuid.uuid4()}/acknowledge")).status_code == 404
    ack = await setup.admin.post(f"/alerts/{first}/acknowledge")
    assert ack.status_code == 200, ack.text
    assert ack.json()["acknowledged_by"] == str(setup.admin.id)
    assert ack.json()["acknowledged_at"] is not None
    assert (await setup.admin.post(f"/alerts/{first}/acknowledge")).status_code == 409

    verifier = await make_actor("Verifier")
    await _promote(sql, verifier, "verifier")
    assert (await verifier.post(f"/alerts/{second}/acknowledge")).status_code == 200

    open_only = await setup.member.get(f"/projects/{pid}/alerts", params={"unacknowledged": True})
    assert open_only.json() == []
    actions = [r.action for r in await sql("SELECT action FROM audit_log ORDER BY id")]
    assert actions.count("alert.acknowledged") == 2
