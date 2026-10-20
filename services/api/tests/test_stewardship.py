"""Stewardship score endpoints against real PostGIS: inputs seeded with SQL (synthetic test values),
plus one report going through the real build → verifier approval workflow."""

import hashlib
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from conftest import FakeStorage
from reports_testlib import ReportSetup, request_and_build, setup_project
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from aqua_core.db import session_factory
from aqua_core.stewardship.loader import forward_funding_eligibility

Enqueued = list[tuple[str, tuple[str, ...], str]]
TODAY = datetime.now(UTC).date()
START = TODAY - timedelta(days=3 * 365)


@pytest.fixture
async def sessionmaker(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(database_url)
    yield session_factory(engine)
    await engine.dispose()


async def _period(sql: Any, s: ReportSetup, start: str, end: str) -> str:
    period_id = str(uuid.uuid4())
    await sql(
        "INSERT INTO monitoring_periods (id, project_id, kind, status, start_date, end_date, "
        "created_by) VALUES (:id, :p, 'monitoring', 'approved', :s, :e, :u)",
        id=period_id,
        p=s.project_id,
        s=date.fromisoformat(start),
        e=date.fromisoformat(end),
        u=s.admin.id,
    )
    return period_id


async def _report(
    sql: Any, s: ReportSetup, period_id: str, version: int, status: str, decided: str
) -> None:
    rid = uuid.uuid4()
    await sql(
        """
        INSERT INTO mrv_reports (id, project_id, period_id, version, status, report_hash,
            lccc_units, tccc_units, period_start_year, period_end_year, tree_stock_tco2e,
            tccc_tco2e, json_key, pdf_key, csv_key, generated_at, generated_by, verifier_id,
            decided_at)
        VALUES (:id, :p, :period, :v, :status, :hash, 10, 10, 2026, 2029, 100.0, 50.0,
            :j, :pdf, :csv, :decided, :admin, :verifier, :decided)
        """,
        id=rid,
        p=s.project_id,
        period=period_id,
        v=version,
        status=status,
        hash=hashlib.sha256(rid.bytes).hexdigest(),
        j=f"reports/{rid}.json",
        pdf=f"reports/{rid}.pdf",
        csv=f"reports/{rid}.csv",
        decided=datetime.fromisoformat(decided),
        admin=s.admin.id,
        verifier=s.verifier.id,
    )


async def _scene(sql: Any, project_id: str, day: date, clear: float, ndvi: float) -> None:
    await sql(
        """
        INSERT INTO scene_observations (id, project_id, item_id, acquired_at, acquired_on,
            cloud_cover, clear_fraction, clear_pixels, polygon_pixels, index_stats, provider)
        VALUES (gen_random_uuid(), :p, :item, :at, :day, 10.0, :clear, 900, 1000,
            CAST(:stats AS jsonb), 'planetary_computer')
        """,
        p=project_id,
        item=f"S2_{uuid.uuid4().hex[:8]}",
        day=day,
        at=datetime(day.year, day.month, day.day, 5, tzinfo=UTC),
        clear=clear,
        stats=json.dumps({"ndvi": {"p50": ndvi}, "mvi": {"p50": 2.0}}),
    )


async def _alert(
    sql: Any,
    project_id: str,
    severity: str,
    *,
    days_ago: int,
    acknowledged: bool = False,
    by: uuid.UUID | None = None,
) -> str:
    alert_id = str(uuid.uuid4())
    await sql(
        """
        INSERT INTO alerts (id, project_id, kind, metric, observed_on, observed_value, expected,
            severity, message, acknowledged_by, acknowledged_at)
        VALUES (:id, :p, 'vegetation_loss', 'ndvi_p50', :day, 0.3, CAST('{}' AS jsonb), :sev,
            'test', :by, :ack)
        """,
        id=alert_id,
        p=project_id,
        day=TODAY - timedelta(days=days_ago),
        sev=severity,
        by=by,
        ack=datetime.now(UTC) if acknowledged or by else None,
    )
    return alert_id


async def _submission(
    sql: Any, s: ReportSetup, period_id: str, status: str, trust: int | None, plot: str | None
) -> None:
    await sql(
        """
        INSERT INTO field_submissions (id, period_id, project_id, submitted_by, plot_id, kind,
            status, object_key, sha256, content_type, size_bytes, location, gps_accuracy_m,
            captured_at, device_model, app_version, trust_score)
        VALUES (gen_random_uuid(), :period, :p, :u, :plot, 'plot_centre', :status, :key, :sha,
            'image/jpeg', 1000, ST_SetSRID(ST_MakePoint(86.86, 20.71), 4326), 4.0, now(),
            'Phone', '0.1.0', :trust)
        """,
        period=period_id,
        p=s.project_id,
        u=s.admin.id,
        plot=plot,
        status=status,
        key=f"photos/{uuid.uuid4()}.jpg",
        sha=uuid.uuid4().hex * 2,
        trust=trust,
    )


def _component(body: dict[str, Any], name: str) -> dict[str, Any]:
    return next(c for c in body["components"] if c["name"] == name)


async def test_project_and_org_stewardship_from_seeded_data(make_actor: Any, sql: Any) -> None:
    s = await setup_project(make_actor, sql)
    await sql("UPDATE projects SET start_date=:d WHERE id=:id", d=START, id=s.project_id)
    # Continuity: P1 v1 changes requested → P1 v2 approved → P2 approved (streak 2, penalty 10).
    p2 = await _period(sql, s, "2030-01-01", "2031-12-31")
    await _report(sql, s, s.period_id, 1, "changes_requested", "2027-01-10T00:00:00+00:00")
    await _report(sql, s, s.period_id, 2, "approved", "2027-02-10T00:00:00+00:00")
    await _report(sql, s, p2, 1, "approved", "2028-03-10T00:00:00+00:00")
    # Survival: baseline NDVI p50 0.55 → latest 0.60 (cloudy low scenes are ignored).
    for i in range(4):
        await _scene(sql, s.project_id, START - timedelta(days=40 * (i + 1)), 0.9, 0.55)
        await _scene(sql, s.project_id, TODAY - timedelta(days=40 * (i + 1)), 0.9, 0.60)
    await _scene(sql, s.project_id, TODAY - timedelta(days=5), 0.1, 0.05)
    # Alerts: one unresolved high (counts), one high cleared by the independent verifier, one
    # medium, one high older than the latest 365-day window (neither counts).
    open_alert = await _alert(sql, s.project_id, "high", days_ago=10)
    await _alert(sql, s.project_id, "high", days_ago=12, by=s.verifier.id)
    await _alert(sql, s.project_id, "medium", days_ago=11)
    await _alert(sql, s.project_id, "high", days_ago=400)
    # Evidence (latest period P2): checked 80/90 on two of four plots, 70 without plot, 1 rejected.
    await _submission(sql, s, p2, "checked", 80, s.plot_ids["D01"])
    await _submission(sql, s, p2, "checked", 90, s.plot_ids["D02"])
    await _submission(sql, s, p2, "checked", 70, None)
    await _submission(sql, s, p2, "rejected", 0, None)
    await _submission(sql, s, p2, "pending_checks", None, None)
    await _submission(sql, s, s.period_id, "checked", 10, s.plot_ids["S01"])  # older period

    r = await s.admin.get(f"/projects/{s.project_id}/stewardship")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["score_version"] == "stewardship-v2"  # v2: area rule, NDVI fallback here
    assert not body["insufficient_data"]
    assert not body["renormalised"]

    cont = _component(body, "verification_continuity")
    assert cont["inputs"]["approved_streak"] == 2
    assert cont["inputs"]["changes_requested_in_window"] == 1
    assert [d["status"] for d in cont["inputs"]["decisions"]] == [
        "changes_requested",
        "approved",
        "approved",
    ]
    assert cont["score"] == 53.21

    surv = _component(body, "satellite_survival")
    assert surv["inputs"]["baseline_clear_scenes"] == 4
    assert surv["inputs"]["latest_scenes"] == 5
    assert surv["inputs"]["latest_clear_scenes"] == 4
    assert surv["inputs"]["ndvi_p50_delta"] == pytest.approx(0.05)
    assert surv["inputs"]["latest_mvi_p50_median"] == 2.0
    assert surv["inputs"]["unresolved_high_severity_alerts"] == 1
    assert surv["sub_scores"] == {"ndvi_change_score": 100.0, "alert_penalty": 20.0}
    assert surv["score"] == 80.0

    ev = _component(body, "evidence_quality")
    assert ev["inputs"]["period_id"] == p2
    assert ev["inputs"]["median_trust_score"] == 80
    assert ev["inputs"]["pending_submissions"] == 1
    assert ev["sub_scores"] == {"trust": 80.0, "acceptance": 75.0, "plot_coverage": 50.0}
    assert ev["score"] == 71.25

    assert body["score"] == pytest.approx(0.40 * 53.21 + 0.35 * 80 + 0.25 * 71.25, abs=0.01)

    r = await s.admin.get(f"/orgs/{s.org_id}/stewardship")
    assert r.status_code == 200, r.text
    org = r.json()
    assert org["score"] == body["score"]
    assert org["components"]["satellite_survival"] == 80.0
    assert org["params"]["weights"]["verification_continuity"] == 0.40
    ff = org["forward_funding"]
    assert ff["criteria_version"] == "forward-funding-v1"
    assert ff["eligible"] is False
    assert "fail: 1 unresolved high-severity alert(s) (maximum 0)" in ff["reasons"]

    # Staff can read; outsiders cannot even see the organisation/project exists.
    assert (await s.verifier.get(f"/orgs/{s.org_id}/stewardship")).status_code == 200
    assert (await s.verifier.get(f"/projects/{s.project_id}/stewardship")).status_code == 200
    outsider = await make_actor("Outsider")
    assert (await outsider.get(f"/orgs/{s.org_id}/stewardship")).status_code == 404
    assert (await outsider.get(f"/projects/{s.project_id}/stewardship")).status_code == 404
    assert (await outsider.get(f"/orgs/{uuid.uuid4()}/stewardship")).status_code == 404
    assert (await s.admin.client.get(f"/projects/{s.project_id}/stewardship")).status_code == 401

    # The org's own admin acknowledging its loss alert restores neither the score nor eligibility.
    r = await s.admin.post(f"/alerts/{open_alert}/acknowledge")
    assert r.status_code == 200, r.text
    org = (await s.admin.get(f"/orgs/{s.org_id}/stewardship")).json()
    assert org["components"]["satellite_survival"] == 80.0
    assert org["score"] == body["score"]
    assert org["forward_funding"]["eligible"] is False

    # An independent verifier's acknowledgement clears it: the org becomes eligible.
    await sql(
        "UPDATE alerts SET acknowledged_at=NULL, acknowledged_by=NULL WHERE id=:id", id=open_alert
    )
    r = await s.verifier.post(f"/alerts/{open_alert}/acknowledge")
    assert r.status_code == 200, r.text
    org = (await s.admin.get(f"/orgs/{s.org_id}/stewardship")).json()
    surv = _component(org["projects"][0], "satellite_survival")
    assert surv["inputs"]["unresolved_high_severity_alerts"] == 0
    assert surv["inputs"]["independently_cleared_high_severity_alerts"] == 2
    assert org["components"]["satellite_survival"] == 100.0
    assert org["forward_funding"]["eligible"] is True, org["forward_funding"]


async def test_only_independent_staff_acknowledgements_clear_alerts(
    make_actor: Any, sql: Any
) -> None:
    s = await setup_project(make_actor, sql)
    insider = await make_actor("Insider Verifier")
    await sql("UPDATE users SET platform_role='verifier' WHERE id=:id", id=insider.id)
    await sql(
        "INSERT INTO memberships (user_id, org_id, role) VALUES (:u, :o, 'member')",
        u=insider.id,
        o=s.org_id,
    )
    registry = await make_actor("Registry Admin")
    await sql("UPDATE users SET platform_role='registry_admin' WHERE id=:id", id=registry.id)
    p = s.project_id
    await _alert(sql, p, "high", days_ago=5)  # unacknowledged → counts
    await _alert(sql, p, "high", days_ago=6, by=s.admin.id)  # own org admin → counts
    await _alert(sql, p, "high", days_ago=7, by=insider.id)  # verifier inside the org → counts
    await _alert(sql, p, "high", days_ago=8, acknowledged=True)  # acknowledger unknown → counts
    await _alert(sql, p, "high", days_ago=9, by=registry.id)  # independent → cleared
    await _alert(sql, p, "high", days_ago=4, by=s.verifier.id)  # independent verifier → cleared
    await _alert(sql, p, "medium", days_ago=10)  # not high → ignored
    await _alert(sql, p, "high", days_ago=366)  # outside the latest window → ignored
    body = (await s.admin.get(f"/projects/{p}/stewardship")).json()
    inputs = _component(body, "satellite_survival")["inputs"]
    assert inputs["unresolved_high_severity_alerts"] == 4
    assert inputs["independently_cleared_high_severity_alerts"] == 2


async def test_insufficient_data_is_flagged_not_scored(
    make_actor: Any, sql: Any, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    s = await setup_project(make_actor, sql)
    body = (await s.admin.get(f"/projects/{s.project_id}/stewardship")).json()
    assert body["score"] is None
    assert body["insufficient_data"] is True
    assert all(c["insufficient_data"] and c["score"] is None for c in body["components"])
    assert any(
        "younger than 12 months" in n for n in _component(body, "satellite_survival")["notes"]
    )

    # Only evidence available → the total is the evidence score, renormalised and flagged.
    await _submission(sql, s, s.period_id, "checked", 60, s.plot_ids["D01"])
    body = (await s.admin.get(f"/projects/{s.project_id}/stewardship")).json()
    ev = _component(body, "evidence_quality")
    assert body["renormalised"] is True
    assert body["score"] == ev["score"] == pytest.approx(0.5 * 60 + 0.25 * 100 + 0.25 * 25)
    assert any("renormalised" in n for n in body["notes"])

    async with sessionmaker() as session:
        eligible, reasons = await forward_funding_eligibility(session, uuid.UUID(s.org_id))
    assert eligible is False
    assert "fail: verification history available" in reasons


async def test_draft_projects_are_excluded_from_the_org_score(make_actor: Any, sql: Any) -> None:
    s = await setup_project(make_actor, sql)
    await sql("UPDATE projects SET status='draft' WHERE id=:id", id=s.project_id)
    org = (await s.admin.get(f"/orgs/{s.org_id}/stewardship")).json()
    assert org["projects"] == []
    assert org["score"] is None
    assert org["insufficient_data"] is True
    assert org["notes"] == ["organisation has no submitted projects"]


async def test_real_approval_workflow_counts_towards_continuity(
    make_actor: Any,
    sql: Any,
    storage: FakeStorage,
    enqueued: Enqueued,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    s = await setup_project(make_actor, sql)
    result = await request_and_build(s, storage, enqueued, sessionmaker)
    r = await s.verifier.post(
        f"/reports/{result['report_id']}/review", json={"decision": "approve", "comment": "ok"}
    )
    assert r.status_code == 200, r.text
    cont = _component(
        (await s.admin.get(f"/projects/{s.project_id}/stewardship")).json(),
        "verification_continuity",
    )
    assert cont["inputs"]["approved_streak"] == 1
    assert cont["inputs"]["decisions"][0]["report_id"] == result["report_id"]
    assert cont["score"] == 39.35
