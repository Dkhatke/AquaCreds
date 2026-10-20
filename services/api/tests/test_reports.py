"""MRV reports: API request → worker build (real PostGIS, FakeStorage) → verifier review → verify."""

import hashlib
import json
import uuid
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from typing import Any

import pytest
from conftest import FakeStorage
from reports_testlib import (
    GENERATED_AT,
    PLOT_AREA_M2,
    PLOTS,
    STRATA_AREA,
    make_verifier,
    request_and_build,
    setup_project,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from aqua_api.routers.reports import project_transition
from aqua_core.db import session_factory
from aqua_core.models import PeriodKind, ProjectStatus
from aqua_mrv.models import (
    EvidenceSummary,
    PeriodInfo,
    PlotRecord,
    ProjectInfo,
    ReportInputs,
    SatelliteSummary,
    TreeRecord,
)
from aqua_mrv.report import build_report
from aqua_worker.tasks.reports import ReportBuildError, resolve_strata

Enqueued = list[tuple[str, tuple[str, ...], str]]


@pytest.fixture
async def sessionmaker(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(database_url)
    yield session_factory(engine)
    await engine.dispose()


async def _add_evidence(sql: Any, s: Any) -> None:
    """One checked photo (trust 85), one rejected, and two Sentinel-2 scenes (one cloudy)."""
    for status, score in (("checked", 85), ("rejected", 0)):
        await sql(
            """
            INSERT INTO field_submissions (id, period_id, project_id, submitted_by, kind, status,
                object_key, sha256, content_type, size_bytes, location, gps_accuracy_m,
                captured_at, device_model, app_version, trust_score)
            VALUES (gen_random_uuid(), :period, :project, :user, 'general', :status,
                :key, :sha, 'image/jpeg', 1000, ST_SetSRID(ST_MakePoint(86.86, 20.71), 4326),
                4.0, now(), 'Phone', '0.1.0', :score)
            """,
            period=s.period_id,
            project=s.project_id,
            user=s.admin.id,
            status=status,
            key=f"photos/{uuid.uuid4()}.jpg",
            sha=hashlib.sha256(status.encode()).hexdigest(),
            score=score,
        )
    for item, day, clear, p50 in (
        ("S2A_1", "2028-01-10", 0.9, 0.71),
        ("S2B_2", "2028-02-10", 0.2, 0.3),
    ):
        await sql(
            """
            INSERT INTO scene_observations (id, project_id, item_id, acquired_at, acquired_on,
                cloud_cover, clear_fraction, clear_pixels, polygon_pixels, index_stats, provider)
            VALUES (gen_random_uuid(), :project, :item, :at, :day,
                10.0, :clear, 900, 1000, CAST(:stats AS jsonb), 'planetary_computer')
            """,
            project=s.project_id,
            item=item,
            day=date.fromisoformat(day),
            at=datetime.fromisoformat(day + "T05:00:00+00:00"),
            clear=clear,
            stats=json.dumps({"ndvi": {"p50": p50, "mean": p50}}),
        )


def _expected_inputs(record: dict[str, Any], area_ha: float) -> ReportInputs:
    """The same report built independently from the test data (not from the DB)."""
    return ReportInputs(
        project=ProjectInfo(
            id=record["project"]["id"],
            name="Bhitarkanika Fringe Restoration",
            state="Odisha",
            area_ha=area_ha,
            start_year=2026,
            boundary=record["project"]["boundary"],
        ),
        period=PeriodInfo(kind="verification", start_year=2026, end_year=2029),
        strata_area_ha=STRATA_AREA,
        plots=tuple(
            PlotRecord(
                code=code,
                stratum=stratum,
                area_m2=PLOT_AREA_M2,
                lat=lat,
                lon=lon,
                trees=tuple(TreeRecord(**t) for t in trees),
            )
            for code, (stratum, lon, lat, trees) in sorted(PLOTS.items())
        ),
        evidence=EvidenceSummary(
            submissions_total=2,
            submissions_accepted=1,
            rejected=1,
            trust_score_distribution={"0-24": 0, "25-49": 0, "50-74": 0, "75-100": 1},
        ),
        satellite=SatelliteSummary(
            stats={
                "window": ["2026-06-01", "2029-12-31"],
                "scenes_total": 2,
                "clear_scenes": 1,
                "clear_fraction_threshold": 0.5,
                "ndvi_p50_median": 0.71,
                "first_acquired": "2028-01-10",
                "last_acquired": "2028-02-10",
            },
            provenance={"providers": "planetary_computer"},
        ),
        generated_at=GENERATED_AT.isoformat(),
    )


async def test_report_build_golden_hash_and_full_approval(
    make_actor: Any, sql: Any, storage: FakeStorage, enqueued: Enqueued, sessionmaker: Any
) -> None:
    s = await setup_project(make_actor, sql)
    await _add_evidence(sql, s)

    result = await request_and_build(s, storage, enqueued, sessionmaker)
    assert result["status"] == "built", result
    reports = (await s.admin.get(f"/periods/{s.period_id}/reports")).json()
    assert len(reports) == 1
    rep = reports[0]
    assert rep["status"] == "under_review"
    assert rep["version"] == 1
    assert (rep["period_start_year"], rep["period_end_year"]) == (2026, 2029)
    periods = (await s.admin.get(f"/projects/{s.project_id}/periods")).json()
    assert periods[0]["status"] == "under_review"

    # Stored artefacts under the documented prefix
    prefix = f"projects/{s.project_id}/periods/{s.period_id}/reports/v1/"
    assert sorted(k for k in storage.objects if k.startswith(prefix)) == [
        prefix + "report-csv.zip",
        prefix + "report.json",
        prefix + "report.pdf",
    ]
    canonical = storage.objects[prefix + "report.json"][0]
    assert hashlib.sha256(canonical).hexdigest() == rep["report_hash"]
    assert storage.objects[prefix + "report.pdf"][0].startswith(b"%PDF")

    # Golden: aqua_mrv computed independently from the test data gives the same hash and units
    record = json.loads(canonical)
    area_ha = (await sql("SELECT area_ha FROM projects WHERE id=:id", id=s.project_id))[0][0]
    expected = build_report(_expected_inputs(record, area_ha))
    assert expected.report_hash == rep["report_hash"]
    assert expected.lccc_units == rep["lccc_units"]
    assert rep["tccc_units"] == int(expected.result.tccc // 1)
    assert rep["lccc_units"] > 0

    # Detail with presigned downloads
    detail = (await s.admin.get(f"/reports/{rep['id']}")).json()
    assert detail["downloads"]["pdf_url"].startswith(f"http://storage.test/{prefix}report.pdf")
    assert set(detail["downloads"]) == {"json_url", "pdf_url", "csv_url"}

    # Verifier approves
    r = await s.verifier.post(
        f"/reports/{rep['id']}/review", json={"decision": "approve", "comment": "Plots check out"}
    )
    assert r.status_code == 200, r.text
    approved = r.json()
    assert approved["status"] == "approved"
    assert approved["verifier_id"] == str(s.verifier.id)
    assert approved["decision_comment"] == "Plots check out"
    assert enqueued[-1] == ("aqua.reports.anchor", (rep["id"],), "chain")
    project = (await s.admin.get(f"/projects/{s.project_id}")).json()
    assert project["status"] == "monitoring"  # first approved monitoring report
    periods = (await s.admin.get(f"/projects/{s.project_id}/periods")).json()
    assert periods[0]["status"] == "approved"

    # Every state change is audited
    actions = [
        row[0]
        for row in await sql(
            "SELECT action FROM audit_log WHERE action LIKE 'report.%' OR action LIKE '%status_changed' ORDER BY id"
        )
    ]
    assert actions == [
        "report.requested",
        "report.generated",
        "report.approved",
        "period.status_changed",
        "project.status_changed",
    ]

    # A decided report cannot be reviewed again
    again = await s.verifier.post(
        f"/reports/{rep['id']}/review", json={"decision": "reject", "comment": "x"}
    )
    assert again.status_code == 409

    # Verify: intact, then tampered
    v = (await s.verifier.get(f"/reports/{rep['id']}/verify")).json()
    assert v["ok"] is True
    assert v["stored_matches"] is True
    assert v["onchain_hash"] is None
    assert v["onchain_matches"] is None
    tampered = canonical.replace(b'"lccc_issuable_units":', b'"lccc_issuable_units":1')
    assert tampered != canonical
    storage.put(prefix + "report.json", tampered, "application/json")
    v = (await s.verifier.get(f"/reports/{rep['id']}/verify")).json()
    assert v["ok"] is False
    assert v["stored_matches"] is False
    assert v["stored_hash"] == hashlib.sha256(tampered).hexdigest()
    del storage.objects[prefix + "report.json"]
    v = (await s.verifier.get(f"/reports/{rep['id']}/verify")).json()
    assert v["stored_hash"] is None
    assert v["ok"] is False


async def test_review_requires_independent_verifier(
    make_actor: Any, sql: Any, storage: FakeStorage, enqueued: Enqueued, sessionmaker: Any
) -> None:
    s = await setup_project(make_actor, sql)
    await request_and_build(s, storage, enqueued, sessionmaker)
    report_id = (await s.admin.get(f"/periods/{s.period_id}/reports")).json()[0]["id"]
    approve = {"decision": "approve", "comment": ""}

    # Org admin (not a verifier) cannot review
    assert (await s.admin.post(f"/reports/{report_id}/review", json=approve)).status_code == 403
    # Registry admins are not verifiers (separation of duties)
    registry = await make_actor("Registry")
    await sql("UPDATE users SET platform_role='registry_admin' WHERE id=:id", id=registry.id)
    assert (await registry.post(f"/reports/{report_id}/review", json=approve)).status_code == 403
    # A verifier who is a member of the project's organisation has a conflict of interest
    insider = await make_verifier(make_actor, sql, "Insider Verifier")
    r = await s.admin.post(
        f"/orgs/{s.org_id}/members", json={"email": insider.email, "role": "member"}
    )
    assert r.status_code == 201, r.text
    r = await insider.post(f"/reports/{report_id}/review", json=approve)
    assert r.status_code == 403
    assert "Conflict of interest" in r.json()["detail"]
    # Unknown report
    assert (
        await s.verifier.post(f"/reports/{uuid.uuid4()}/review", json=approve)
    ).status_code == 404
    # Comment is mandatory for request_changes / reject
    r = await s.verifier.post(f"/reports/{report_id}/review", json={"decision": "reject"})
    assert r.status_code == 422
    # Outsiders cannot even see the report
    outsider = await make_actor("Outsider")
    assert (await outsider.get(f"/reports/{report_id}")).status_code == 404
    assert (await outsider.get(f"/reports/{report_id}/verify")).status_code == 404
    assert (await outsider.get(f"/periods/{s.period_id}/reports")).status_code == 404
    assert (await outsider.get(f"/periods/{uuid.uuid4()}/reports")).status_code == 404
    # Nothing changed
    rep = (await s.admin.get(f"/reports/{report_id}")).json()
    assert rep["status"] == "under_review"
    assert not any(name == "aqua.reports.anchor" for name, _, _ in enqueued)


async def test_request_changes_then_new_version(
    make_actor: Any, sql: Any, storage: FakeStorage, enqueued: Enqueued, sessionmaker: Any
) -> None:
    s = await setup_project(make_actor, sql)
    await request_and_build(s, storage, enqueued, sessionmaker)
    v1 = (await s.admin.get(f"/periods/{s.period_id}/reports")).json()[0]
    r = await s.verifier.post(
        f"/reports/{v1['id']}/review",
        json={"decision": "request_changes", "comment": "Re-measure plot S02"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "changes_requested"
    periods = (await s.admin.get(f"/projects/{s.project_id}/periods")).json()
    assert periods[0]["status"] == "changes_requested"
    project = (await s.admin.get(f"/projects/{s.project_id}")).json()
    assert project["status"] == "submitted"  # monitoring report: project unchanged

    result = await request_and_build(
        s, storage, enqueued, sessionmaker, now=GENERATED_AT + timedelta(days=3)
    )
    assert result["status"] == "built"
    assert result["version"] == 2
    reports = (await s.admin.get(f"/periods/{s.period_id}/reports")).json()
    assert [x["version"] for x in reports] == [2, 1]
    assert reports[0]["report_hash"] != v1["report_hash"]  # generated_at is part of the record
    assert reports[0]["status"] == "under_review"

    r = await s.verifier.post(
        f"/reports/{reports[0]['id']}/review", json={"decision": "reject", "comment": "Fabricated"}
    )
    assert r.json()["status"] == "rejected"
    periods = (await s.admin.get(f"/projects/{s.project_id}/periods")).json()
    assert periods[0]["status"] == "rejected"
    # A rejected period cannot produce another report
    r = await s.admin.post(f"/periods/{s.period_id}/reports", json={"strata_area_ha": STRATA_AREA})
    assert r.status_code == 409


async def test_baseline_report_decides_registration(
    make_actor: Any, sql: Any, storage: FakeStorage, enqueued: Enqueued, sessionmaker: Any
) -> None:
    s = await setup_project(make_actor, sql, kind="baseline", project_status="baseline_ready")
    await request_and_build(s, storage, enqueued, sessionmaker)
    rep = (await s.admin.get(f"/periods/{s.period_id}/reports")).json()[0]
    r = await s.verifier.post(f"/reports/{rep['id']}/review", json={"decision": "approve"})
    assert r.status_code == 200, r.text
    project = (await s.admin.get(f"/projects/{s.project_id}")).json()
    assert project["status"] == "registered"
    assert enqueued[-1] == ("aqua.reports.anchor", (rep["id"],), "chain")


@pytest.mark.parametrize(
    ("kind", "decision", "current", "expected"),
    [
        (PeriodKind.BASELINE, "approve", ProjectStatus.SUBMITTED, ProjectStatus.REGISTERED),
        (PeriodKind.BASELINE, "approve", ProjectStatus.CHANGES_REQUESTED, ProjectStatus.REGISTERED),
        (
            PeriodKind.BASELINE,
            "request_changes",
            ProjectStatus.BASELINE_READY,
            ProjectStatus.CHANGES_REQUESTED,
        ),
        (PeriodKind.BASELINE, "reject", ProjectStatus.SUBMITTED, ProjectStatus.REJECTED),
        (PeriodKind.BASELINE, "reject", ProjectStatus.MONITORING, ProjectStatus.MONITORING),
        (PeriodKind.MONITORING, "approve", ProjectStatus.REGISTERED, ProjectStatus.MONITORING),
        (PeriodKind.MONITORING, "approve", ProjectStatus.MONITORING, ProjectStatus.MONITORING),
        (PeriodKind.MONITORING, "approve", ProjectStatus.SUBMITTED, ProjectStatus.MONITORING),
        (PeriodKind.MONITORING, "reject", ProjectStatus.MONITORING, ProjectStatus.MONITORING),
        (
            PeriodKind.MONITORING,
            "request_changes",
            ProjectStatus.REGISTERED,
            ProjectStatus.REGISTERED,
        ),
    ],
)
def test_project_transitions(
    kind: PeriodKind, decision: str, current: ProjectStatus, expected: ProjectStatus
) -> None:
    assert project_transition(kind, decision, current) is expected


async def test_report_request_rules(
    make_actor: Any, sql: Any, storage: FakeStorage, enqueued: Enqueued
) -> None:
    s = await setup_project(make_actor, sql)
    url = f"/periods/{s.period_id}/reports"
    # Field agents and verifiers cannot request reports
    agent = await make_actor("Agent")
    await s.admin.post(
        f"/orgs/{s.org_id}/members", json={"email": agent.email, "role": "field_agent"}
    )
    assert (await agent.post(url, json={"strata_area_ha": STRATA_AREA})).status_code == 403
    assert (await s.verifier.post(url, json={"strata_area_ha": STRATA_AREA})).status_code == 403
    assert (await (await make_actor("Stranger")).post(url)).status_code == 404
    assert (await s.admin.post(f"/periods/{uuid.uuid4()}/reports")).status_code == 404
    # Several strata need explicit areas, for exactly the measured strata, within the project area
    r = await s.admin.post(url)
    assert r.status_code == 422
    assert "dense" in r.json()["detail"]
    assert (await s.admin.post(url, json={"strata_area_ha": {"dense": 10.0}})).status_code == 422
    big = {"dense": 10_000.0, "sparse": 1.0}
    assert (await s.admin.post(url, json={"strata_area_ha": big})).status_code == 422
    bad = {"dense": -1.0, "sparse": 1.0}
    assert (await s.admin.post(url, json={"strata_area_ha": bad})).status_code == 422
    assert enqueued == []

    # Accepted → period analysing; a second request conflicts
    r = await s.admin.post(
        url, json={"strata_area_ha": STRATA_AREA, "planting": [{"year": 2026, "area_ha": 300}]}
    )
    assert r.status_code == 202, r.text
    assert r.json()["period_status"] == "analysing"
    name, args, queue = enqueued[-1]
    assert (name, queue, args[:2]) == (
        "aqua.reports.build_report",
        "reports",
        (s.period_id, str(s.admin.id)),
    )
    options = json.loads(args[2])
    assert options["strata_area_ha"] == STRATA_AREA
    assert options["planting"] == [{"year": 2026, "area_ha": 300.0}]
    assert options["restore_status"] == "open"
    assert (await s.admin.post(url, json={"strata_area_ha": STRATA_AREA})).status_code == 409


async def test_single_stratum_defaults_to_project_area_and_needs_measurements(
    make_actor: Any, sql: Any, storage: FakeStorage, enqueued: Enqueued, sessionmaker: Any
) -> None:
    one = {"D01": PLOTS["D01"], "D02": PLOTS["D02"]}
    s = await setup_project(make_actor, sql, plots=one)
    result = await request_and_build(s, storage, enqueued, sessionmaker, body={})
    assert result["status"] == "built", result
    area_ha = (await sql("SELECT area_ha FROM projects WHERE id=:id", id=s.project_id))[0][0]
    rep = (await s.admin.get(f"/periods/{s.period_id}/reports")).json()[0]
    record = json.loads(
        storage.objects[f"projects/{s.project_id}/periods/{s.period_id}/reports/v1/report.json"][0]
    )
    assert record["inputs"]["strata_area_ha"] == {"dense": area_ha}
    assert record["evidence"]["submissions_total"] == 0
    assert record["satellite"] is None
    assert rep["status"] == "under_review"

    empty = await setup_project(make_actor, sql, plots={}, lon_offset=0.05)
    r = await empty.admin.post(f"/periods/{empty.period_id}/reports")
    assert r.status_code == 409
    # Project not yet submitted
    await sql("UPDATE projects SET status='draft' WHERE id=:id", id=empty.project_id)
    assert (await empty.admin.post(f"/periods/{empty.period_id}/reports")).status_code == 409


async def test_build_failures_reopen_the_period(
    make_actor: Any, sql: Any, storage: FakeStorage, enqueued: Enqueued, sessionmaker: Any
) -> None:
    unknown = {
        "D01": (
            "dense",
            86.852,
            20.702,
            [{"species": "Quercus robur", "dbh_cm": 5.0, "alive": True}],
        ),
        "D02": PLOTS["D02"],
    }
    s = await setup_project(make_actor, sql, plots=unknown)
    result = await request_and_build(s, storage, enqueued, sessionmaker, body={})
    assert result["status"] == "failed"
    assert "Quercus" in result["error"]
    periods = (await s.admin.get(f"/projects/{s.project_id}/periods")).json()
    assert periods[0]["status"] == "open"
    assert (await s.admin.get(f"/periods/{s.period_id}/reports")).json() == []
    assert storage.objects == {}
    rows = await sql("SELECT data FROM audit_log WHERE action='report.build_failed'")
    assert "Quercus" in rows[0][0]["error"]

    # A period ending in the project start year cannot be a verification period (t2 must be > t1)
    short = await setup_project(
        make_actor,
        sql,
        end_date="2026-12-31",
        lon_offset=0.05,
        plots={"D02": PLOTS["D02"]},
        measured_on="2026-11-15",
    )
    result = await request_and_build(short, storage, enqueued, sessionmaker, body={})
    assert result["status"] == "failed"
    assert "must end after 2026" in result["error"]

    # The worker refuses a period that is not analysing
    from aqua_worker.tasks.reports import build_report_core

    again = await build_report_core(
        sessionmaker, storage, uuid.UUID(short.period_id), short.admin.id
    )
    assert again["status"] == "failed"
    with pytest.raises(LookupError):
        await build_report_core(sessionmaker, storage, uuid.uuid4(), short.admin.id)


async def test_enqueue_failure_restores_period(
    client: Any, make_actor: Any, sql: Any, enqueued: Enqueued
) -> None:
    s = await setup_project(make_actor, sql)
    app = client._transport.app

    def broken(*_: Any, **__: Any) -> None:
        raise ConnectionError("broker down")

    app.state.enqueue = broken
    r = await s.admin.post(f"/periods/{s.period_id}/reports", json={"strata_area_ha": STRATA_AREA})
    assert r.status_code == 503
    periods = (await s.admin.get(f"/projects/{s.project_id}/periods")).json()
    assert periods[0]["status"] == "open"


async def test_approval_survives_anchor_enqueue_failure(
    client: Any,
    make_actor: Any,
    sql: Any,
    storage: FakeStorage,
    enqueued: Enqueued,
    sessionmaker: Any,
) -> None:
    s = await setup_project(make_actor, sql)
    await request_and_build(s, storage, enqueued, sessionmaker)
    rep = (await s.admin.get(f"/periods/{s.period_id}/reports")).json()[0]

    def broken(*_: Any, **__: Any) -> None:
        raise ConnectionError("broker down")

    client._transport.app.state.enqueue = broken
    r = await s.verifier.post(f"/reports/{rep['id']}/review", json={"decision": "approve"})
    assert r.status_code == 200
    assert r.json()["status"] == "approved"
    assert r.json()["anchor_error"] == "could not enqueue on-chain anchoring"


def test_resolve_strata() -> None:
    assert resolve_strata({"a"}, None, 12.5) == {"a": 12.5}
    assert resolve_strata({"a", "b"}, {"a": 1, "b": 2}, 12.5) == {"a": 1.0, "b": 2.0}
    with pytest.raises(ReportBuildError):
        resolve_strata({"a", "b"}, None, 12.5)
    with pytest.raises(ReportBuildError):
        resolve_strata({"a", "b"}, {"a": 1}, 12.5)
