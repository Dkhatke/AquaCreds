"""aqua.geo.reap_stale_runs on real PostGIS: stuck `running` runs → failed + audit entry."""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from api_testlib import project_payload
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from aqua_core.db import session_factory
from aqua_worker.tasks import geo

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
TIMEOUT = timedelta(hours=8)


@pytest.fixture
async def sessionmaker(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(database_url)
    yield session_factory(engine)
    await engine.dispose()


async def _run(
    sql: Any, project_id: str, status: str, started_hours_ago: float | None
) -> uuid.UUID:
    run_id = uuid.uuid4()
    started = NOW - timedelta(hours=started_hours_ago) if started_hours_ago is not None else None
    await sql(
        "INSERT INTO analysis_runs (id, project_id, kind, status, params, outputs, started_at,"
        " created_at, updated_at) VALUES (:id, :p, 'backfill', :s, '{}', '{\"ingested\": 7}', :st,"
        " :created, :created)",
        id=run_id,
        p=project_id,
        s=status,
        st=started,
        created=NOW - timedelta(hours=20),
    )
    return run_id


async def test_reaper_fails_only_stale_running_runs(
    make_actor: Any, sql: Any, sessionmaker: Any
) -> None:
    admin = await make_actor("NGO Admin")
    org = (await admin.post("/orgs", json={"name": "Reaper Trust", "org_type": "ngo"})).json()
    project = (await admin.post("/projects", json=project_payload(org["id"]))).json()

    stale = await _run(sql, project["id"], "running", 9)
    fresh = await _run(sql, project["id"], "running", 2)
    done = await _run(sql, project["id"], "succeeded", 30)
    never_started = await _run(sql, project["id"], "running", None)  # falls back to created_at

    result = await geo.reap_stale_runs(sessionmaker, stale_after=TIMEOUT, now=NOW)
    assert result["reaped"] == 2
    assert set(result["run_ids"]) == {str(stale), str(never_started)}

    rows = {
        r.id: r
        for r in await sql("SELECT id, status, error, finished_at, outputs FROM analysis_runs")
    }
    assert rows[stale].status == "failed"
    assert "beyond the Celery hard time limit" in rows[stale].error
    assert "480 min" in rows[stale].error
    assert rows[stale].finished_at == NOW
    assert rows[stale].outputs == {"ingested": 7}  # partial progress kept
    assert rows[fresh].status == "running"
    assert rows[done].status == "succeeded"

    audit = await sql("SELECT entity_id, data FROM audit_log WHERE action='analysis.reaped'")
    assert {a.entity_id for a in audit} == {str(stale), str(never_started)}
    assert all(a.data["stale_after_min"] == 480 and a.data["kind"] == "backfill" for a in audit)

    # Idempotent: a second pass finds nothing.
    assert (await geo.reap_stale_runs(sessionmaker, stale_after=TIMEOUT, now=NOW))["reaped"] == 0
