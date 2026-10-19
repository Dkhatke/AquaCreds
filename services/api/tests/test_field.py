"""Field evidence end to end: API (periods, nonces, uploads, plots) → worker checks on real PostGIS."""

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from api_testlib import BHITARKANIKA, project_payload
from conftest import FakeStorage
from photo_factory import make_jpeg
from sqlalchemy.ext.asyncio import create_async_engine

from aqua_core.db import session_factory
from aqua_worker.tasks.evidence import run_checks

LON, LAT = BHITARKANIKA  # project square: 86.85-86.87 E, 20.70-20.72 N
INSIDE = (86.86, 20.71)
FAR_OUTSIDE = (86.95, 20.80)


class Field:
    """A submitted project with an open period, an admin and a field agent."""

    def __init__(self, admin: Any, agent: Any, project_id: str, period_id: str) -> None:
        self.admin, self.agent, self.project_id, self.period_id = (
            admin,
            agent,
            project_id,
            period_id,
        )


@pytest.fixture
async def field(make_actor: Any, sql: Any) -> Field:
    admin = await make_actor("NGO Admin")
    org = (
        await admin.post("/orgs", json={"name": "Odisha Coastal Trust", "org_type": "ngo"})
    ).json()
    agent = await make_actor("Field Agent")
    await admin.post(
        f"/orgs/{org['id']}/members", json={"email": agent.email, "role": "field_agent"}
    )
    project_id = (await admin.post("/projects", json=project_payload(org["id"]))).json()["id"]
    await sql("UPDATE projects SET status='submitted' WHERE id=:id", id=project_id)
    r = await admin.post(
        f"/projects/{project_id}/periods",
        json={"kind": "monitoring", "start_date": "2026-01-01", "end_date": "2026-12-31"},
    )
    assert r.status_code == 201, r.text
    return Field(admin, agent, project_id, r.json()["id"])


async def _submit(
    f: Field,
    storage: FakeStorage,
    photo: bytes,
    *,
    at: tuple[float, float] = INSIDE,
    captured_at: datetime | None = None,
    nonce: str | None = None,
    kind: str = "general",
    plot_code: str | None = None,
) -> Any:
    if nonce is None:
        nonce = (await f.agent.post(f"/periods/{f.period_id}/nonces")).json()["nonce"]
    ticket = (
        await f.agent.post(
            f"/periods/{f.period_id}/photos/upload-url", json={"content_type": "image/jpeg"}
        )
    ).json()
    storage.put(ticket["object_key"], photo, "image/jpeg")
    return await f.agent.post(
        f"/periods/{f.period_id}/submissions",
        json={
            "object_key": ticket["object_key"],
            "sha256": hashlib.sha256(photo).hexdigest(),
            "nonce": nonce,
            "kind": kind,
            "plot_code": plot_code,
            "lon": at[0],
            "lat": at[1],
            "gps_accuracy_m": 4.5,
            "captured_at": (captured_at or datetime.now(UTC)).isoformat(),
            "device_model": "FieldPhone 1",
            "app_version": "0.1.0",
        },
    )


async def _check(database_url: str, storage: FakeStorage, submission_id: str) -> dict[str, Any]:
    engine = create_async_engine(database_url)
    try:
        return await run_checks(
            session_factory(engine), storage, uuid.UUID(submission_id), 25 * 1024 * 1024
        )
    finally:
        await engine.dispose()


def _photo(seed: int, at: tuple[float, float] = INSIDE) -> bytes:
    now_ist = datetime.now(UTC) + timedelta(hours=5, minutes=30)
    return make_jpeg(
        seed=seed, lon=at[0], lat=at[1], when=now_ist.strftime("%Y:%m:%d %H:%M:%S"), offset="+05:30"
    )


async def test_genuine_capture_scores_full_trust(
    field: Field, storage: FakeStorage, enqueued: list[Any], database_url: str
) -> None:
    r = await _submit(field, storage, _photo(1))
    assert r.status_code == 202, r.text
    sub_id = r.json()["id"]
    assert r.json()["status"] == "pending_checks"
    assert enqueued == [("aqua.ml.check_submission", (sub_id,), "ml")]

    result = await _check(database_url, storage, sub_id)
    assert result == {"id": sub_id, "status": "checked", "trust_score": 100}
    detail = (await field.agent.get(f"/submissions/{sub_id}")).json()
    outcomes = {c["name"]: c["outcome"] for c in detail["checks"]}
    assert outcomes == {
        "exif_gps": "pass",
        "exif_time": "pass",
        "capture_nonce": "pass",
        "geofence": "pass",
        "duplicate_exact": "pass",
        "duplicate_visual": "pass",
        "photo_model": "skipped",
        "satellite_consistency": "skipped",
    }
    assert detail["exif"]["make"] == "TestCam"


async def test_reused_photo_is_rejected_as_duplicate(
    field: Field, storage: FakeStorage, database_url: str
) -> None:
    first = (await _submit(field, storage, _photo(2))).json()["id"]
    await _check(database_url, storage, first)
    again = (await _submit(field, storage, _photo(2))).json()["id"]  # same bytes, new upload
    result = await _check(database_url, storage, again)
    assert result["status"] == "rejected"
    assert result["trust_score"] == 0
    detail = (await field.agent.get(f"/submissions/{again}")).json()
    assert {c["name"]: c["outcome"] for c in detail["checks"]}["duplicate_exact"] == "fail"


async def test_photo_outside_boundary_is_rejected(
    field: Field, storage: FakeStorage, database_url: str
) -> None:
    sub = (await _submit(field, storage, _photo(3, FAR_OUTSIDE), at=FAR_OUTSIDE)).json()["id"]
    result = await _check(database_url, storage, sub)
    assert result["status"] == "rejected"
    geofence = next(
        c
        for c in (await field.agent.get(f"/submissions/{sub}")).json()["checks"]
        if c["name"] == "geofence"
    )
    assert geofence["outcome"] == "fail"
    assert "outside the project boundary" in geofence["reason"]


async def test_gallery_photo_taken_before_nonce_is_rejected(
    field: Field, storage: FakeStorage, database_url: str
) -> None:
    sub = (
        await _submit(field, storage, _photo(4), captured_at=datetime.now(UTC) - timedelta(hours=3))
    ).json()["id"]
    result = await _check(database_url, storage, sub)
    assert result["status"] == "rejected"


async def test_nonce_cannot_be_reused_or_borrowed(
    field: Field, storage: FakeStorage, make_actor: Any
) -> None:
    nonce = (await field.agent.post(f"/periods/{field.period_id}/nonces")).json()["nonce"]
    assert (await _submit(field, storage, _photo(5), nonce=nonce)).status_code == 202
    reuse = await _submit(field, storage, _photo(6), nonce=nonce)
    assert reuse.status_code == 409

    admin_nonce = (await field.admin.post(f"/periods/{field.period_id}/nonces")).json()["nonce"]
    borrowed = await _submit(
        field, storage, _photo(7), nonce=admin_nonce
    )  # agent uses admin's nonce
    assert borrowed.status_code == 422


async def test_hash_mismatch_and_foreign_keys_refused(field: Field, storage: FakeStorage) -> None:
    nonce = (await field.agent.post(f"/periods/{field.period_id}/nonces")).json()["nonce"]
    ticket = (
        await field.agent.post(
            f"/periods/{field.period_id}/photos/upload-url", json={"content_type": "image/jpeg"}
        )
    ).json()
    storage.put(ticket["object_key"], _photo(8), "image/jpeg")
    body = {
        "object_key": ticket["object_key"],
        "sha256": "0" * 64,
        "nonce": nonce,
        "kind": "general",
        "lon": INSIDE[0],
        "lat": INSIDE[1],
        "gps_accuracy_m": 5,
        "captured_at": datetime.now(UTC).isoformat(),
    }
    r = await field.agent.post(f"/periods/{field.period_id}/submissions", json=body)
    assert r.status_code == 422
    assert "mismatch" in r.json()["detail"]
    body |= {"object_key": "projects/other/periods/x/photos/y.jpg"}
    assert (
        await field.agent.post(f"/periods/{field.period_id}/submissions", json=body)
    ).status_code == 422


async def test_roles_and_period_rules(field: Field, make_actor: Any) -> None:
    # field agents cannot open periods; only one open period at a time
    r = await field.agent.post(
        f"/projects/{field.project_id}/periods",
        json={"kind": "monitoring", "start_date": "2027-01-01", "end_date": "2027-12-31"},
    )
    assert r.status_code == 403
    r = await field.admin.post(
        f"/projects/{field.project_id}/periods",
        json={"kind": "monitoring", "start_date": "2027-01-01", "end_date": "2027-12-31"},
    )
    assert r.status_code == 409
    outsider = await make_actor("Outsider")
    assert (await outsider.post(f"/periods/{field.period_id}/nonces")).status_code == 404


async def test_plots_and_measurements(
    field: Field, storage: FakeStorage, database_url: str
) -> None:
    plot = await field.admin.post(
        f"/projects/{field.project_id}/plots",
        json={
            "code": "P-07",
            "stratum": "dense",
            "lon": 86.862,
            "lat": 20.712,
            "area_m2": 100,
            "marker_id": "BHK-07",
        },
    )
    assert plot.status_code == 201, plot.text
    plot_id = plot.json()["id"]
    outside = await field.admin.post(
        f"/projects/{field.project_id}/plots",
        json={
            "code": "P-99",
            "stratum": "dense",
            "lon": 86.95,
            "lat": 20.80,
            "area_m2": 100,
            "marker_id": "X",
        },
    )
    assert outside.status_code == 422

    trees = [
        {"species": "Avicennia marina", "dbh_cm": 9.5, "height_m": 6.2, "alive": True},
        {"species": "Rhizophora mucronata", "dbh_cm": 7.0, "alive": False},
    ]
    m = await field.agent.post(
        f"/periods/{field.period_id}/plots/{plot_id}/measurements",
        json={"measured_on": "2026-11-02", "trees": trees},
    )
    assert m.status_code == 201, m.text
    assert len(m.json()["trees"]) == 2
    dup = await field.agent.post(
        f"/periods/{field.period_id}/plots/{plot_id}/measurements",
        json={"measured_on": "2026-11-03", "trees": trees},
    )
    assert dup.status_code == 409
    wrong_date = await field.agent.post(
        f"/periods/{field.period_id}/plots/{plot_id}/measurements",
        json={"measured_on": "2025-11-03", "trees": trees},
    )
    assert wrong_date.status_code in (409, 422)

    # plot-centre photo must name an existing plot
    sub = await _submit(field, storage, _photo(9), kind="plot_centre", plot_code="P-07")
    assert sub.status_code == 202
    assert sub.json()["plot_id"] == plot_id
    missing = await _submit(field, storage, _photo(10), kind="plot_centre", plot_code="P-00")
    assert missing.status_code == 422
