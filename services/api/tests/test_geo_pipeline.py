"""Worker geo pipeline cores (backfill, nightly + alerts, composites) on real PostGIS with a fake,
network-free scene source."""

import io
import uuid
from collections.abc import AsyncIterator
from datetime import date
from typing import Any

import numpy as np
import pytest
import rasterio
from api_testlib import BHITARKANIKA, project_payload, square
from conftest import FakeStorage
from geo_fakes import POLYGON_PIXELS, FakeSceneSource, scene
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from aqua_core.db import session_factory
from aqua_core.models import AnalysisKind, AnalysisStatus
from aqua_worker.tasks import geo

HISTORY_YEARS = range(2019, 2024)


@pytest.fixture
async def sessionmaker(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(database_url)
    yield session_factory(engine)
    await engine.dispose()


async def _project(
    make_actor: Any, sql: Any, status: str = "submitted", at: tuple[float, float] = BHITARKANIKA
) -> tuple[Any, uuid.UUID]:
    admin = await make_actor("NGO Admin")
    org = (await admin.post("/orgs", json={"name": "Sundarbans Trust", "org_type": "ngo"})).json()
    r = await admin.post("/projects", json=project_payload(org["id"], boundary=square(*at)))
    assert r.status_code == 201, r.text
    project_id = uuid.UUID(r.json()["id"])
    await sql("UPDATE projects SET status=:s WHERE id=:id", s=status, id=project_id)
    return admin, project_id


def _history() -> list[Any]:
    """Healthy Januaries 2019-2023 (three clear scenes each) plus one cloudy scene per year."""
    scenes = []
    for year in HISTORY_YEARS:
        for day, ndvi in ((5, 0.74), (15, 0.75), (25, 0.76)):
            scenes.append(scene(f"{year}-01-{day:02d}", ndvi))
        scenes.append(scene(f"{year}-01-28", 0.75, cloudy=0.8))
    return scenes


async def test_backfill_ingests_every_scene_one_at_a_time(
    client: Any, make_actor: Any, sql: Any, sessionmaker: Any
) -> None:
    _, project_id = await _project(make_actor, sql)
    source = FakeSceneSource(_history())
    result = await geo.ingest_project(
        sessionmaker, source, project_id, start=date(2017, 1, 1), end=date(2024, 1, 1)
    )
    assert result.status == AnalysisStatus.SUCCEEDED
    assert result.outputs["scenes_found"] == 20
    assert result.outputs["ingested"] == 20
    assert all(len(ids) == 1 for ids in source.loads)  # bounded memory: one scene per read

    rows = await sql(
        "SELECT acquired_on, clear_fraction, polygon_pixels, index_stats, provider "
        "FROM scene_observations WHERE project_id=:p ORDER BY acquired_on",
        p=project_id,
    )
    assert len(rows) == 20
    first = rows[0]
    assert first.polygon_pixels == POLYGON_PIXELS
    assert first.clear_fraction == 1.0
    assert first.index_stats["ndvi"]["p50"] == pytest.approx(0.74, abs=0.01)
    assert set(first.index_stats) == {"ndvi", "evi", "mvi", "ndwi", "mndwi", "ndmi", "cmri"}
    assert first.provider == "fake"
    cloudy = next(r for r in rows if r.acquired_on == date(2019, 1, 28))
    assert cloudy.clear_fraction == pytest.approx(0.2, abs=0.01)

    run = (
        await sql("SELECT kind, status, outputs FROM analysis_runs WHERE id=:id", id=result.run_id)
    )[0]
    assert (run.kind, run.status) == ("backfill", "succeeded")
    actions = [r.action for r in await sql("SELECT action FROM audit_log ORDER BY id")]
    assert "analysis.succeeded" in actions


async def test_rerunning_backfill_does_not_duplicate_observations(
    client: Any, make_actor: Any, sql: Any, sessionmaker: Any
) -> None:
    _, project_id = await _project(make_actor, sql)
    source = FakeSceneSource(_history())
    await geo.ingest_project(
        sessionmaker, source, project_id, start=date(2017, 1, 1), end=date(2024, 1, 1)
    )
    loads_before = len(source.loads)
    again = await geo.ingest_project(
        sessionmaker, source, project_id, start=date(2017, 1, 1), end=date(2024, 1, 1)
    )
    assert again.outputs["already_ingested"] == 20
    assert again.outputs["ingested"] == 0
    assert len(source.loads) == loads_before  # skipped before any pixel was read
    count = await sql(
        "SELECT count(*) AS n FROM scene_observations WHERE project_id=:p", p=project_id
    )
    assert count[0].n == 20


async def test_failed_scenes_are_recorded_and_retried_next_time(
    client: Any, make_actor: Any, sql: Any, sessionmaker: Any
) -> None:
    _, project_id = await _project(make_actor, sql)
    good = scene("2024-01-05")
    bad = scene("2024-01-10", fail=True)
    source = FakeSceneSource([good, bad])
    first = await geo.ingest_project(
        sessionmaker, source, project_id, start=date(2024, 1, 1), end=date(2024, 2, 1)
    )
    assert first.status == AnalysisStatus.SUCCEEDED
    assert first.outputs["ingested"] == 1
    assert first.outputs["failed_count"] == 1
    assert first.outputs["failed"][0]["item_id"] == bad.item_id
    assert "simulated read timeout" in first.outputs["failed"][0]["error"]

    source.scenes = [good, scene("2024-01-10")]  # the provider recovered
    second = await geo.ingest_project(
        sessionmaker, source, project_id, start=date(2024, 1, 1), end=date(2024, 2, 1)
    )
    assert (second.outputs["already_ingested"], second.outputs["ingested"]) == (1, 1)


async def test_run_fails_when_every_scene_fails(
    client: Any, make_actor: Any, sql: Any, sessionmaker: Any
) -> None:
    _, project_id = await _project(make_actor, sql)
    source = FakeSceneSource([scene("2024-01-10", fail=True)])
    result = await geo.ingest_project(
        sessionmaker, source, project_id, start=date(2024, 1, 1), end=date(2024, 2, 1)
    )
    assert result.status == AnalysisStatus.FAILED
    run = (await sql("SELECT status, error FROM analysis_runs WHERE id=:id", id=result.run_id))[0]
    assert run.status == "failed"
    assert "every scene failed" in run.error


async def test_search_failure_marks_the_queued_run_failed(
    client: Any, make_actor: Any, sql: Any, sessionmaker: Any
) -> None:
    _, project_id = await _project(make_actor, sql)
    run_id = uuid.uuid4()
    await sql(
        "INSERT INTO analysis_runs (id, project_id, kind, status, params, outputs) "
        "VALUES (:id, :p, 'backfill', 'queued', '{}', '{}')",
        id=run_id,
        p=project_id,
    )
    source = FakeSceneSource(search_error=ConnectionError("STAC API unreachable"))
    with pytest.raises(ConnectionError):
        await geo.ingest_project(
            sessionmaker,
            source,
            project_id,
            start=date(2024, 1, 1),
            end=date(2024, 2, 1),
            run_id=run_id,
        )
    run = (
        await sql("SELECT status, error, started_at FROM analysis_runs WHERE id=:id", id=run_id)
    )[0]
    assert run.status == "failed"
    assert "STAC API unreachable" in run.error
    assert run.started_at is not None


async def test_unknown_project_or_mismatched_run_is_rejected(
    client: Any, make_actor: Any, sql: Any, sessionmaker: Any
) -> None:
    _, project_id = await _project(make_actor, sql)
    with pytest.raises(LookupError):
        await geo.ingest_project(
            sessionmaker,
            FakeSceneSource(),
            uuid.uuid4(),
            start=date(2024, 1, 1),
            end=date(2024, 2, 1),
        )
    first = await geo.ingest_project(
        sessionmaker, FakeSceneSource(), project_id, start=date(2024, 1, 1), end=date(2024, 2, 1)
    )
    with pytest.raises(ValueError, match="does not belong"):
        await geo.build_composite(
            sessionmaker, FakeSceneSource(), FakeStorage(), project_id, 2023, run_id=first.run_id
        )


async def test_nightly_ingests_new_scenes_and_raises_one_alert(
    client: Any, make_actor: Any, sql: Any, sessionmaker: Any
) -> None:
    _, project_id = await _project(make_actor, sql, status="monitoring")
    _, draft_id = await _project(make_actor, sql, status="draft", at=(86.95, 20.80))
    source = FakeSceneSource(_history())
    await geo.ingest_project(
        sessionmaker, source, project_id, start=date(2017, 1, 1), end=date(2023, 12, 31)
    )

    # January 2024: canopy loss on two clear scenes, with a cloudy scene in between.
    source.scenes += [
        scene("2024-01-05", 0.38),
        scene("2024-01-10", 0.80, cloudy=0.7),
        scene("2024-01-15", 0.36),
    ]
    summary = await geo.ingest_new_scenes(
        sessionmaker, source, today=date(2024, 1, 20), backfill_start=date(2017, 1, 1)
    )
    assert summary == {"projects": 1, "ingested": 3, "alerts": 1, "failed": []}
    # incremental search starts at the latest stored observation (not 2017)
    assert source.searches[-1] == (date(2023, 1, 28), date(2024, 1, 20))

    alerts = await sql(
        "SELECT observed_on, observed_value, severity, message, expected, run_id FROM alerts "
        "WHERE project_id=:p",
        p=project_id,
    )
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.observed_on == date(2024, 1, 15)
    assert alert.observed_value == pytest.approx(0.36, abs=0.01)
    assert alert.severity == "high"
    assert "2 consecutive clear observations" in alert.message
    assert alert.expected["n"] == 15
    run = (await sql("SELECT kind FROM analysis_runs WHERE id=:id", id=alert.run_id))[0]
    assert run.kind == "incremental"
    assert await sql("SELECT 1 FROM scene_observations WHERE project_id=:p", p=draft_id) == []

    # Re-running finds nothing new and raises no duplicate alert.
    again = await geo.ingest_new_scenes(
        sessionmaker, source, today=date(2024, 1, 21), backfill_start=date(2017, 1, 1)
    )
    assert (again["ingested"], again["alerts"]) == (0, 0)
    assert await geo.detect_alerts(sessionmaker, project_id, since=None) == []
    count = await sql("SELECT count(*) AS n FROM alerts WHERE project_id=:p", p=project_id)
    assert count[0].n == 1
    actions = [r.action for r in await sql("SELECT action FROM audit_log ORDER BY id")]
    assert actions.count("alert.raised") == 1


async def test_nightly_backfills_projects_without_observations_and_isolates_failures(
    client: Any, make_actor: Any, sql: Any, sessionmaker: Any
) -> None:
    _, project_id = await _project(make_actor, sql, status="registered")
    source = FakeSceneSource([scene("2024-01-05")])
    summary = await geo.ingest_new_scenes(
        sessionmaker, source, today=date(2024, 1, 20), backfill_start=date(2017, 1, 1)
    )
    assert summary["ingested"] == 1
    assert source.searches == [(date(2017, 1, 1), date(2024, 1, 20))]

    source.search_error = TimeoutError("STAC timeout")
    failed = await geo.ingest_new_scenes(
        sessionmaker, source, today=date(2024, 1, 21), backfill_start=date(2017, 1, 1)
    )
    assert failed["failed"][0]["project_id"] == str(project_id)
    assert "STAC timeout" in failed["failed"][0]["error"]


async def test_composite_is_written_as_cog_with_zonal_summary(
    client: Any, make_actor: Any, sql: Any, sessionmaker: Any
) -> None:
    _, project_id = await _project(make_actor, sql)
    season = [
        scene("2023-11-10", 0.70),
        scene("2023-12-10", 0.72),
        scene("2024-01-10", 0.74),
        scene("2024-02-10", 0.76, cloudy=0.5),
        scene("2024-03-10", 0.78),
        scene("2024-06-10", 0.10),  # monsoon: outside the dry season
    ]
    storage = FakeStorage()
    out = await geo.build_composite(
        sessionmaker, FakeSceneSource(season), storage, project_id, 2023
    )
    key = f"projects/{project_id}/composites/2023-24.tif"
    assert out["cog_key"] == key
    assert out["scene_count"] == 5
    data, content_type = storage.objects[key]
    assert "cloud-optimized" in content_type
    assert out["sha256"] == storage.describe(key).sha256  # type: ignore[union-attr]

    with rasterio.open(io.BytesIO(data)) as src:
        assert src.tags(ns="IMAGE_STRUCTURE")["LAYOUT"] == "COG"
        assert src.crs.to_epsg() == 32645
        bands = list(src.descriptions)
        assert bands[:2] == ["ndvi", "evi"]
        assert {"nir", "ndvi_p10", "ndvi_p90", "mvi_p50", "clear_count"} <= set(bands)
        ndvi = src.read(bands.index("ndvi") + 1)
        assert np.isnan(ndvi[:, -1]).all()  # outside the polygon
        assert np.nanmedian(ndvi) == pytest.approx(0.74, abs=0.01)
        clear_count = src.read(bands.index("clear_count") + 1)
        assert clear_count.max() == 5
        assert clear_count[-1, 0] == 5

    zonal = out["zonal"]
    assert zonal["ndvi"]["p50"] == pytest.approx(0.74, abs=0.01)
    assert zonal["ndvi"]["valid_pixels"] == POLYGON_PIXELS
    run = (
        await sql(
            "SELECT kind, status, params, outputs FROM analysis_runs WHERE id=:id",
            id=uuid.UUID(out["run_id"]),
        )
    )[0]
    assert (run.kind, run.status) == (AnalysisKind.COMPOSITE.value, "succeeded")
    assert run.params["season"] == "2023-24"
    assert run.outputs["cog_key"] == key


async def test_composite_without_scenes_fails_the_run(
    client: Any, make_actor: Any, sql: Any, sessionmaker: Any
) -> None:
    _, project_id = await _project(make_actor, sql)
    with pytest.raises(LookupError, match="no Sentinel-2 scenes"):
        await geo.build_composite(sessionmaker, FakeSceneSource(), FakeStorage(), project_id, 2020)
    run = (await sql("SELECT status, error FROM analysis_runs WHERE project_id=:p", p=project_id))[
        0
    ]
    assert run.status == "failed"
    assert "2020-21" in run.error


async def test_dask_backed_loads_give_the_same_results(
    client: Any, make_actor: Any, sql: Any, sessionmaker: Any
) -> None:
    """The real loader is lazy (dask, chunked); results must match the eager path."""
    _, project_id = await _project(make_actor, sql)
    season = [scene(f"2023-{m}-10", 0.7 + i / 100) for i, m in enumerate(("11", "12"))]
    season += [scene("2024-01-10", 0.72, cloudy=0.3), scene("2024-02-10", 0.73)]
    eager, lazy = FakeSceneSource(season), FakeSceneSource(season, chunked=True)
    a = await geo.build_composite(sessionmaker, eager, FakeStorage(), project_id, 2023)
    b = await geo.build_composite(sessionmaker, lazy, FakeStorage(), project_id, 2023)
    assert a["zonal"] == b["zonal"]
    assert a["sha256"] == b["sha256"]

    _, other = await _project(make_actor, sql, at=(86.95, 20.80))
    result = await geo.ingest_project(
        sessionmaker, lazy, other, start=date(2023, 11, 1), end=date(2024, 3, 31)
    )
    assert result.outputs["ingested"] == 4
    rows = await sql(
        "SELECT clear_fraction FROM scene_observations WHERE project_id=:p ORDER BY acquired_on",
        p=other,
    )
    assert [r.clear_fraction for r in rows] == [1.0, 1.0, pytest.approx(0.7, abs=0.01), 1.0]


def test_season_helpers() -> None:
    assert geo.season_label(2025) == "2025-26"
    assert geo.season_label(2099) == "2099-00"
    assert geo.season_window(2025) == (date(2025, 11, 1), date(2026, 3, 31))
