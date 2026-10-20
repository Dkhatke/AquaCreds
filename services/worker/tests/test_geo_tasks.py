"""Celery wiring for the geo tasks (no broker, no database, no network)."""

import os
import uuid
from datetime import UTC, date, datetime
from typing import Any

import pystac
import pytest
from celery.schedules import crontab
from geo_fakes import synthetic
from shapely.geometry import box

from aqua_core.models import AnalysisStatus
from aqua_core.settings import Settings
from aqua_worker.celery_app import app, beat_schedule
from aqua_worker.scenes import SceneRef, StacSceneSource
from aqua_worker.tasks import geo


def test_geo_tasks_registered_on_geo_queue() -> None:
    for name in (
        "aqua.geo.backfill_project",
        "aqua.geo.ingest_new_scenes",
        "aqua.geo.build_composite",
    ):
        assert name in app.tasks
    assert app.conf.task_routes["aqua.geo.*"] == {"queue": "geo"}


def test_nightly_beat_schedule_uses_configured_hour() -> None:
    entry = beat_schedule(Settings(_env_file=None, geo_nightly_hour_utc=21))["geo-nightly-ingest"]
    assert entry["task"] == "aqua.geo.ingest_new_scenes"
    assert entry["options"]["queue"] == "geo"
    assert entry["schedule"] == crontab(hour=21, minute=0)
    assert "geo-nightly-ingest" in app.conf.beat_schedule


CHAIN = {
    "relayer_mnemonic": " ".join(["abandon"] * 11 + ["about"]),  # BIP-39 test vector, no funds
    "chain_registry_address": "0x" + "11" * 20,
    "chain_credit_address": "0x" + "22" * 20,
}


def test_beat_schedule_without_chain_has_geo_jobs_only() -> None:
    schedule = beat_schedule(Settings(_env_file=None))
    assert set(schedule) == {"geo-nightly-ingest", "geo-reap-stale-runs", "ml-classify-dry-season"}
    reaper = schedule["geo-reap-stale-runs"]
    assert reaper["task"] == "aqua.geo.reap_stale_runs"
    assert reaper["schedule"] == crontab(minute=17)  # hourly
    # Not the geo queue: the single geo worker may be stuck in the very job being reaped.
    assert reaper["options"]["queue"] == "default"
    for entry in schedule.values():
        assert entry["task"] in app.tasks


def test_beat_schedule_indexes_chain_events_when_configured() -> None:
    schedule = beat_schedule(Settings(_env_file=None, chain_index_interval_s=45, **CHAIN))
    assert set(schedule) == {
        "geo-nightly-ingest",
        "geo-reap-stale-runs",
        "ml-classify-dry-season",
        "chain-index-events",
        "chain-top-up-org-wallets",
    }
    entry = schedule["chain-index-events"]
    assert entry["task"] == "aqua.chain.index_events"
    assert entry["task"] in app.tasks
    assert entry["schedule"] == 45
    assert entry["options"] == {"queue": "chain", "expires": 45}
    assert beat_schedule(Settings(_env_file=None, **CHAIN))["chain-index-events"]["schedule"] == 30
    top_up = schedule["chain-top-up-org-wallets"]
    assert top_up["task"] == "aqua.chain.top_up_org_wallets"
    assert top_up["task"] in app.tasks
    assert top_up["schedule"] == 300
    assert top_up["options"] == {"queue": "chain", "expires": 300}


def test_top_up_task_skips_when_chain_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    from aqua_worker.tasks import chain

    monkeypatch.setattr(chain, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(chain, "_with_relayer", lambda run: pytest.fail("must not touch the chain"))
    assert chain.top_up_org_wallets_task.run() == {"skipped": "chain not configured"}


def test_index_task_skips_when_chain_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    from aqua_worker.tasks import chain

    monkeypatch.setattr(chain, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(chain, "_index", lambda: pytest.fail("must not touch the chain"))
    assert chain.index_events_task.run() == {"skipped": "chain not configured"}


def test_stale_run_timeout_exceeds_every_geo_hard_time_limit() -> None:
    limit_s = max(
        app.tasks[name].time_limit
        for name in (
            "aqua.geo.backfill_project",
            "aqua.geo.ingest_new_scenes",
            "aqua.geo.build_composite",
        )
    )
    assert Settings(_env_file=None).geo_stale_run_timeout_min * 60 > limit_s


def test_reaper_wrapper_uses_configured_timeout(
    monkeypatch: pytest.MonkeyPatch, calls: list[Any]
) -> None:
    seen: dict[str, Any] = {}

    async def fake_reap(sm: Any, *, stale_after: Any, now: Any) -> dict[str, Any]:
        seen.update(sm=sm, stale_after=stale_after, now=now)
        return {"reaped": 0, "run_ids": []}

    monkeypatch.setattr(geo, "reap_stale_runs", fake_reap)
    monkeypatch.setattr(
        geo, "get_settings", lambda: Settings(_env_file=None, geo_stale_run_timeout_min=90)
    )
    assert geo.reap_stale_runs_task.run() == {"reaped": 0, "run_ids": []}
    assert seen["sm"] == "SESSIONMAKER"
    assert seen["stale_after"].total_seconds() == 90 * 60
    assert seen["now"].tzinfo is not None


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, tuple[Any, ...], dict[str, Any]]]:
    recorded: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    async def fake_with_db(job: Any) -> Any:
        return await job("SESSIONMAKER")

    def recorder(name: str, result: Any) -> Any:
        async def _core(*args: Any, **kwargs: Any) -> Any:
            recorded.append((name, args, kwargs))
            return result

        return _core

    run_id = uuid.uuid4()
    monkeypatch.setattr(geo, "_with_db", fake_with_db)
    monkeypatch.setattr(geo, "StacSceneSource", lambda **kw: "SOURCE")
    monkeypatch.setattr(geo, "StacSarSource", lambda: "SAR")
    monkeypatch.setattr(geo, "S3ObjectStorage", lambda settings: "STORAGE")
    monkeypatch.setattr(
        geo,
        "ingest_project",
        recorder("ingest", geo.IngestResult(run_id, AnalysisStatus.SUCCEEDED, {"ingested": 3}, [])),
    )
    monkeypatch.setattr(geo, "ingest_new_scenes", recorder("nightly", {"projects": 0}))
    monkeypatch.setattr(geo, "build_composite", recorder("composite", {"cog_key": "k"}))
    return recorded


def test_backfill_wrapper_parses_arguments(calls: list[Any]) -> None:
    pid, rid = uuid.uuid4(), uuid.uuid4()
    out = geo.backfill_project.run(str(pid), "2020-01-01", str(rid))
    assert out["status"] == "succeeded"
    assert out["ingested"] == 3
    _name, args, kwargs = calls[0]
    assert args == ("SESSIONMAKER", "SOURCE", pid)
    assert kwargs["start"] == date(2020, 1, 1)
    assert kwargs["run_id"] == rid
    assert kwargs["kind"].value == "backfill"

    assert (kwargs["sar_source"], kwargs["sar_policy"]) == ("SAR", geo.Sentinel1Policy())

    geo.backfill_project.run(str(pid), "", None)  # API sends "" for the default start
    assert calls[1][2]["start"] == date(2017, 1, 1)
    assert calls[1][2]["run_id"] is None


def test_nightly_and_composite_wrappers(calls: list[Any]) -> None:
    assert geo.ingest_new_scenes_task.run() == {"projects": 0}
    assert calls[0][2]["backfill_start"] == date(2017, 1, 1)
    assert calls[0][2]["sar_source"] == "SAR"
    pid = uuid.uuid4()
    assert geo.build_composite_task.run(str(pid), "2024", None) == {"cog_key": "k"}
    assert calls[1][1] == ("SESSIONMAKER", "SOURCE", "STORAGE", pid, 2024)


def test_stac_source_signs_right_before_loading(monkeypatch: pytest.MonkeyPatch) -> None:
    from aqua_geo import sentinel2 as s2

    item = pystac.Item(
        id="S2B_MSIL2A_20240110",
        geometry=None,
        bbox=None,
        datetime=datetime(2024, 1, 10, 5, tzinfo=UTC),
        properties={"eo:cloud_cover": 12.5, "s2:processing_baseline": "05.10"},
    )
    seen: dict[str, Any] = {}

    def fake_search(*args: Any, **kwargs: Any) -> list[pystac.Item]:
        seen["search"] = kwargs
        return [item]

    def fake_load(items: Any, geometry: Any, **kwargs: Any) -> Any:
        seen["load_items"] = items
        loaded = synthetic([])  # empty time axis is fine for wiring
        ds = loaded.ds.assign_attrs(epsg=32645)
        return ds

    signed: list[str] = []
    provider = s2.Provider(
        name="test",
        stac_url="http://stac.test",
        collection="s2",
        asset_names=s2.PLANETARY_COMPUTER.asset_names,
        sign=lambda it: signed.append(it.id) or it,
    )
    monkeypatch.setattr(s2, "search", fake_search)
    monkeypatch.setattr(s2, "load", fake_load)
    monkeypatch.setattr(s2, "polygon_mask", lambda template, geometry, epsg: "MASK")

    source = StacSceneSource(provider)
    refs = source.search(
        box(86.85, 20.70, 86.87, 20.72), date(2024, 1, 1), date(2024, 1, 31), max_cloud=50
    )
    assert seen["search"]["sign"] is False  # never sign at search time
    assert signed == []
    assert refs == [
        SceneRef("S2B_MSIL2A_20240110", datetime(2024, 1, 10, 5, tzinfo=UTC), 12.5, "05.10", "test")
    ]
    loaded = source.load(refs, box(86.85, 20.70, 86.87, 20.72))
    assert signed == ["S2B_MSIL2A_20240110"]
    assert loaded.epsg == 32645
    assert loaded.inside == "MASK"


def test_sentinel1_can_be_disabled_in_settings(
    calls: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(geo, "get_settings", lambda: Settings(_env_file=None, geo_s1_enabled=False))
    geo.backfill_project.run(str(uuid.uuid4()), None, None)
    assert (calls[0][2]["sar_source"], calls[0][2]["sar_policy"]) == (None, None)


@pytest.mark.skipif(
    os.environ.get("AQUA_NETWORK_TESTS") != "1",
    reason="network test: set AQUA_NETWORK_TESTS=1 (reads one scene from Planetary Computer)",
)
def test_live_single_scene_statistics() -> None:
    """End to end on real data: ~4 ha of Bhitarkanika, one dry-season scene."""
    area = box(86.86, 20.70, 86.862, 20.702)
    source = StacSceneSource()
    scenes = source.search(area, date(2024, 1, 1), date(2024, 1, 31), max_cloud=20)
    assert scenes
    stats = geo._scene_statistics(source, scenes[0], area)
    assert stats.polygon_pixels > 0
    assert 0.0 <= stats.clear_fraction <= 1.0
