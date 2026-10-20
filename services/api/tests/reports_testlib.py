"""Helpers for MRV report tests: a submitted project with a measured monitoring period, and a
verifier independent of the project's organisation. Plot data is illustrative test data."""

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from api_testlib import BHITARKANIKA, project_payload, square
from conftest import FakeStorage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aqua_worker.tasks.reports import build_report_core

GENERATED_AT = datetime(2029, 12, 20, 6, 30, tzinfo=UTC)

# code → (stratum, lon, lat, trees)
PLOTS: dict[str, tuple[str, float, float, list[dict[str, Any]]]] = {
    "D01": (
        "dense",
        86.852,
        20.702,
        [
            {"species": "Avicennia marina", "dbh_cm": 6.2, "height_m": 4.1, "alive": True},
            {"species": "Avicennia marina", "dbh_cm": 5.4, "height_m": 3.8, "alive": True},
            {"species": "Rhizophora mucronata", "dbh_cm": 4.9, "height_m": 3.5, "alive": True},
            {"species": "Avicennia marina", "dbh_cm": 2.1, "height_m": None, "alive": False},
        ],
    ),
    "D02": (
        "dense",
        86.854,
        20.704,
        [
            {"species": "Avicennia marina", "dbh_cm": 5.9, "height_m": 4.0, "alive": True},
            {"species": "Rhizophora mucronata", "dbh_cm": 5.1, "height_m": 3.6, "alive": True},
            {"species": "Avicennia marina", "dbh_cm": 6.6, "height_m": 4.4, "alive": True},
        ],
    ),
    "S01": (
        "sparse",
        86.862,
        20.712,
        [
            {"species": "Avicennia marina", "dbh_cm": 3.4, "height_m": 2.6, "alive": True},
            {"species": "Excoecaria agallocha", "dbh_cm": 3.9, "height_m": 2.9, "alive": True},
        ],
    ),
    "S02": (
        "sparse",
        86.864,
        20.714,
        [
            {"species": "Avicennia marina", "dbh_cm": 3.1, "height_m": 2.4, "alive": True},
            {"species": "Avicennia marina", "dbh_cm": 3.6, "height_m": 2.7, "alive": True},
        ],
    ),
}
STRATA_AREA = {"dense": 250.0, "sparse": 120.0}
PLOT_AREA_M2 = 100.0


@dataclass
class ReportSetup:
    admin: Any
    verifier: Any
    org_id: str
    project_id: str
    period_id: str
    plot_ids: dict[str, str]


async def make_verifier(make_actor: Any, sql: Any, name: str = "Verifier") -> Any:
    verifier = await make_actor(name)
    await sql("UPDATE users SET platform_role='verifier' WHERE id=:id", id=verifier.id)
    return verifier


async def setup_project(
    make_actor: Any,
    sql: Any,
    *,
    kind: str = "monitoring",
    end_date: str = "2029-12-31",
    plots: dict[str, tuple[str, float, float, list[dict[str, Any]]]] | None = None,
    project_status: str = "submitted",
    measured_on: str = "2029-11-15",
    lon_offset: float = 0.0,
) -> ReportSetup:
    admin = await make_actor("NGO Admin")
    org = (
        await admin.post("/orgs", json={"name": "Kendrapara Mangrove Trust", "org_type": "ngo"})
    ).json()
    lon, lat = BHITARKANIKA
    payload = project_payload(org["id"], boundary=square(lon + lon_offset, lat))
    r = await admin.post("/projects", json=payload)
    assert r.status_code == 201, r.text
    project_id = r.json()["id"]
    await sql("UPDATE projects SET status=:s WHERE id=:id", s=project_status, id=project_id)
    r = await admin.post(
        f"/projects/{project_id}/periods",
        json={"kind": kind, "start_date": "2026-06-01", "end_date": end_date},
    )
    assert r.status_code == 201, r.text
    period_id = r.json()["id"]
    plot_ids = {}
    for code, (stratum, plot_lon, plot_lat, trees) in (PLOTS if plots is None else plots).items():
        r = await admin.post(
            f"/projects/{project_id}/plots",
            json={
                "code": code,
                "stratum": stratum,
                "lon": plot_lon + lon_offset,
                "lat": plot_lat,
                "area_m2": PLOT_AREA_M2,
                "marker_id": f"TAG-{code}",
            },
        )
        assert r.status_code == 201, r.text
        plot_ids[code] = r.json()["id"]
        r = await admin.post(
            f"/periods/{period_id}/plots/{plot_ids[code]}/measurements",
            json={"measured_on": measured_on, "trees": trees},
        )
        assert r.status_code == 201, r.text
    verifier = await make_verifier(make_actor, sql)
    return ReportSetup(admin, verifier, org["id"], project_id, period_id, plot_ids)


async def request_and_build(
    s: ReportSetup,
    storage: FakeStorage,
    enqueued: list[tuple[str, tuple[str, ...], str]],
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    body: dict[str, Any] | None = None,
    now: datetime = GENERATED_AT,
) -> dict[str, Any]:
    """POST the report request, then run the worker task the API enqueued."""
    r = await s.admin.post(
        f"/periods/{s.period_id}/reports",
        json={"strata_area_ha": STRATA_AREA} if body is None else body,
    )
    assert r.status_code == 202, r.text
    name, args, queue = enqueued[-1]
    assert (name, queue) == ("aqua.reports.build_report", "reports")
    period_id, actor_id, options = args
    result = await build_report_core(
        sessionmaker,
        storage,
        uuid.UUID(period_id),
        uuid.UUID(actor_id),
        json.loads(options),
        now=now,
    )
    return result
