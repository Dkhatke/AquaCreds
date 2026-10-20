import asyncio
import importlib.util
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from api_testlib import project_payload
from sqlalchemy.exc import IntegrityError

import aqua_api


def _config(database_url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", "aqua_api:migrations")
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def test_models_and_migrations_are_in_sync(database_url: str) -> None:
    """Fails if a model changed without a matching Alembic migration."""
    command.check(_config(database_url))


def test_migrations_downgrade_and_upgrade_cleanly(database_url: str) -> None:
    cfg = _config(database_url)
    command.downgrade(cfg, "0001_enable_postgis")
    command.upgrade(cfg, "head")


def _migration_0009() -> Any:
    path = Path(aqua_api.__file__).parent / "migrations/versions/0009_sar_observations.py"
    spec = importlib.util.spec_from_file_location("migration_0009", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _legacy_obs(key: str, day: str, trigger: str, vh: float, **kw: Any) -> dict[str, Any]:
    """An `outputs.sentinel1.observations` entry as written by the worker before 0009."""
    return {
        "key": key,
        "item_ids": [f"{key}_rtc"],
        "acquired_at": f"{day}T23:30:00+00:00",
        "acquired_on": day,
        "platform": "S1A",
        "orbit_state": "descending",
        "relative_orbit": 19,
        "provider": "planetary-computer",
        "trigger": trigger,
        "polygon_pixels": 360,
        "valid_pixels": 342,
        "valid_fraction": 0.95,
        "stats": {"vh_db": {"valid_pixels": 342, "p50": vh}},
        **kw,
    }


async def _legacy_run(sql: Any, project_id: str, created_at: str, obs: list[Any]) -> uuid.UUID:
    run_id = uuid.uuid4()
    outputs = {"ingested": 0, "sentinel1": {"ingested": len(obs), "observations": obs}}
    await sql(
        "INSERT INTO analysis_runs (id, project_id, kind, status, params, outputs, created_at) "
        "VALUES (:id, :p, 'backfill', 'succeeded', CAST('{}' AS jsonb), CAST(:o AS jsonb), :c)",
        id=run_id,
        p=project_id,
        o=json.dumps(outputs),
        c=datetime.fromisoformat(created_at),
    )
    return run_id


async def test_0009_backfills_sar_observations_from_run_outputs_idempotently(
    database_url: str, make_actor: Any, sql: Any
) -> None:
    admin = await make_actor("NGO Admin")
    org = (await admin.post("/orgs", json={"name": "Coringa Trust", "org_type": "ngo"})).json()
    pid = (await admin.post("/projects", json=project_payload(org["id"]))).json()["id"]
    first = await _legacy_run(
        sql,
        pid,
        "2024-09-01T00:00:00+00:00",
        [
            _legacy_obs("S1A_20240705_233000", "2024-07-05", "monsoon", -15.1),
            _legacy_obs("S1A_20240112_233000", "2024-01-12", "low_clear:S2B_45QVC_20240110", -14),
        ],
    )
    await _legacy_run(
        sql,
        pid,
        "2024-10-01T00:00:00+00:00",
        [
            _legacy_obs("S1A_20240705_233000", "2024-07-05", "monsoon", -99.0),  # same pass again
            _legacy_obs("S1A_20240717_233000", "2024-07-17", "monsoon", -15.0, stats=None),
            _legacy_obs("S1A_20240729_233000", "2024-07-29", "monsoon", -15.0, valid_fraction=2),
            _legacy_obs("S1A_20240810_233000", "2024-08-10", "mystery", -15.0),
            "not an object",
        ],
    )
    await sql(  # an S2 run without Sentinel-1 outputs is ignored
        "INSERT INTO analysis_runs (id, project_id, kind, status, params, outputs) VALUES "
        "(:id, :p, 'incremental', 'succeeded', CAST('{}' AS jsonb), CAST('{}' AS jsonb))",
        id=uuid.uuid4(),
        p=pid,
    )
    await sql(
        "INSERT INTO alerts (id, project_id, kind, metric, observed_on, observed_value, expected, "
        "severity, message) VALUES (:id, :p, 'sar_backscatter_drop', 'vh_db_p50', '2024-07-05', "
        "-20, CAST('{}' AS jsonb), 'medium', 'heuristic')",
        id=uuid.uuid4(),
        p=pid,
    )

    cfg = _config(database_url)
    # alembic env.py runs its own event loop: call it from a thread
    await asyncio.to_thread(command.downgrade, cfg, "0008_forward_fund_signer")
    try:
        assert (await sql("SELECT count(*) AS n FROM alerts"))[0].n == 0  # SAR alerts removed
        with pytest.raises(IntegrityError, match="ck_alerts_alert_kind"):
            await sql(
                "INSERT INTO alerts (id, project_id, kind, metric, observed_on, observed_value, "
                "expected, severity, message) VALUES (:id, :p, 'sar_backscatter_drop', 'm', "
                "'2024-07-05', 0, CAST('{}' AS jsonb), 'medium', 'x')",
                id=uuid.uuid4(),
                p=pid,
            )
    finally:
        await asyncio.to_thread(command.upgrade, cfg, "head")

    query = "SELECT * FROM sar_observations ORDER BY acquired_on"
    rows = await sql(query)
    assert [r.acquisition_key for r in rows] == ["S1A_20240112_233000", "S1A_20240705_233000"]
    gap, monsoon = rows
    assert (gap.trigger, gap.trigger_item_id) == ("cloud_gap", "S2B_45QVC_20240110")
    assert (monsoon.trigger, monsoon.trigger_item_id) == ("monsoon", None)
    assert monsoon.run_id == first  # the earliest run wins
    assert monsoon.stats["vh_db"]["p50"] == -15.1
    assert monsoon.item_ids == ["S1A_20240705_233000_rtc"]
    assert (monsoon.relative_orbit, monsoon.orbit_state, monsoon.valid_fraction) == (
        19,
        "descending",
        0.95,
    )
    assert monsoon.acquired_on.isoformat() == "2024-07-05"  # UTC date of a 23:30 UTC pass

    # running the copy again changes nothing
    assert await sql(_migration_0009().BACKFILL_SQL) == 0
    again = await sql(query)
    assert [(r.id, r.acquisition_key) for r in again] == [(r.id, r.acquisition_key) for r in rows]
    # the run outputs are kept as the audit trail of the copy
    kept = await sql("SELECT outputs FROM analysis_runs WHERE id=:id", id=first)
    assert len(kept[0].outputs["sentinel1"]["observations"]) == 2
