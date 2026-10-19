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
    entry = beat_schedule(21)["geo-nightly-ingest"]
    assert entry["task"] == "aqua.geo.ingest_new_scenes"
    assert entry["options"]["queue"] == "geo"
    assert entry["schedule"] == crontab(hour=21, minute=0)
    assert "geo-nightly-ingest" in app.conf.beat_schedule


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
    monkeypatch.setattr(geo, "StacSceneSource", lambda: "SOURCE")
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

    geo.backfill_project.run(str(pid), "", None)  # API sends "" for the default start
    assert calls[1][2]["start"] == date(2017, 1, 1)
    assert calls[1][2]["run_id"] is None


def test_nightly_and_composite_wrappers(calls: list[Any]) -> None:
    assert geo.ingest_new_scenes_task.run() == {"projects": 0}
    assert calls[0][2]["backfill_start"] == date(2017, 1, 1)
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
