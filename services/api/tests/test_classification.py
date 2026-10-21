"""Ecosystem classifier on real PostGIS: the `aqua.ml.classify_project` core with fake STAC/DEM
sources, a synthetic ONNX model and in-memory storage; the API; and the downstream consumers
(mangrove-area alerts, MRV satellite summary, stewardship-v2 survival)."""

import io
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
import rasterio
from api_testlib import project_payload
from classify_fakes import (
    CLASSES,
    POLYGON_PIXELS,
    SIZE,
    FakeDemSource,
    FakeStackSource,
    MemoryStorage,
    season,
    synthetic_model_files,
)
from conftest import FakeStorage
from reports_testlib import request_and_build, setup_project
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from aqua_core.db import session_factory
from aqua_geo.classify import utm_areal_scale
from aqua_worker.alerts import AreaLossRule
from aqua_worker.model_store import ModelManifest, load_model
from aqua_worker.tasks import ml

Enqueued = list[tuple[str, tuple[str, ...], str]]


@pytest.fixture
async def sessionmaker(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(database_url)
    yield session_factory(engine)
    await engine.dispose()


@pytest.fixture
def model_files(tmp_path: Path) -> tuple[Path, ModelManifest]:
    return tmp_path / "model", synthetic_model_files(tmp_path / "model")


async def _project(make_actor: Any, sql: Any) -> tuple[Any, uuid.UUID]:
    admin = await make_actor("NGO Admin")
    org = (await admin.post("/orgs", json={"name": "Kutch Mangroves", "org_type": "ngo"})).json()
    r = await admin.post("/projects", json=project_payload(org["id"]))
    assert r.status_code == 201, r.text
    pid = uuid.UUID(r.json()["id"])
    await sql("UPDATE projects SET status='submitted' WHERE id=:id", id=pid)
    return admin, pid


async def _classify(
    sessionmaker: Any,
    storage: Any,
    model_files: tuple[Path, ModelManifest],
    project_id: uuid.UUID,
    year: int,
    *,
    veg_cols: int = 6,
    source: FakeStackSource | None = None,
    dem: FakeDemSource | None = None,
    run_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    directory, manifest = model_files
    return await ml.classify_project(
        sessionmaker,
        source or FakeStackSource(season(year, veg_cols=veg_cols, n=5)),
        dem or FakeDemSource(),
        storage,
        lambda: load_model(directory, manifest),
        project_id,
        year,
        run_id=run_id,
        manifest=manifest,
        tile=6,
        low_confidence_threshold=0.6,
        loss_rule=AreaLossRule(10.0, 0.01),
    )


def _expected_ha(pixels: int) -> float:
    lon, lat = 86.86, 20.71  # centroid of the default project square
    return round(pixels * 100.0 / utm_areal_scale(32645, lon, lat) / 10_000, 4)


async def test_classify_project_stores_areas_cogs_and_provenance(
    client: Any, make_actor: Any, sql: Any, sessionmaker: Any, model_files: Any
) -> None:
    _, pid = await _project(make_actor, sql)
    storage = MemoryStorage()
    source = FakeStackSource(season(2020, n=13))  # more days than the 12 the model was trained on
    out = await _classify(sessionmaker, storage, model_files, pid, 2020, source=source)
    _, manifest = model_files

    assert source.searches == [(date(2020, 11, 1), date(2021, 3, 31), 80.0)]
    assert out["scene_count"] == 12
    assert out["scene_days"] == 12
    mangrove_px = SIZE * 8  # 6 vegetated + 2 NDVI-0 tie columns
    assert out["mangrove_ha"] == _expected_ha(mangrove_px)
    assert out["class_areas_ha"]["water"] == _expected_ha(POLYGON_PIXELS - mangrove_px)
    assert out["unclassified_pixels"] == 0
    assert out["low_confidence_share"] == round(SIZE * 2 / POLYGON_PIXELS, 6)
    assert out["model_sha256"] == manifest.sha256["model.onnx"]
    assert out["loss_check"]["previous_classification_id"] is None

    row = (
        await sql(
            "SELECT season_start_year, model_version, model_sha256, features_sha256, classes, "
            "mangrove_ha, mangrove_mean_confidence, epsg, pixel_area_m2, areal_scale, "
            "classes_cog_key, classes_cog_sha256, scenes, dem_items, run_id "
            "FROM classifications WHERE project_id=:p",
            p=pid,
        )
    )[0]
    assert row.season_start_year == 2020
    assert row.model_version == "ecosystem_classifier_v1"
    assert row.features_sha256 == manifest.sha256["features.json"]
    assert row.classes == list(CLASSES)
    assert row.mangrove_ha == out["mangrove_ha"]
    assert 0.85 < row.mangrove_mean_confidence < 0.9
    assert (row.epsg, row.pixel_area_m2) == (32645, 100.0)
    assert len(row.scenes) == 12
    assert row.dem_items
    assert str(row.run_id) == out["run_id"]

    data, content_type = storage.objects[row.classes_cog_key]
    assert "cloud-optimized" in content_type
    with rasterio.open(io.BytesIO(data)) as src:
        classes = src.read(1)
        assert src.nodata == 255
        assert src.tags()["class_2"] == "mangrove"
    assert int((classes == 2).sum()) == mangrove_px
    assert (classes[:, -2:] == 255).all()  # outside the polygon
    run = await sql("SELECT kind, status, outputs FROM analysis_runs WHERE id=:id", id=row.run_id)
    assert (run[0].kind, run[0].status) == ("classification", "succeeded")
    audit = await sql("SELECT action FROM audit_log WHERE entity_id=:id", id=str(row.run_id))
    assert [a.action for a in audit] == ["analysis.succeeded"]


async def test_mangrove_area_loss_alert_against_previous_season(
    client: Any, make_actor: Any, sql: Any, sessionmaker: Any, model_files: Any
) -> None:
    _, pid = await _project(make_actor, sql)
    storage = MemoryStorage()
    await _classify(sessionmaker, storage, model_files, pid, 2020, veg_cols=10)
    same = await _classify(sessionmaker, storage, model_files, pid, 2021, veg_cols=10)
    assert same["loss_check"]["alert_id"] is None
    loss = await _classify(sessionmaker, storage, model_files, pid, 2022, veg_cols=4)
    assert loss["loss_check"]["previous_mangrove_ha"] == same["mangrove_ha"]
    alert_id = loss["loss_check"]["alert_id"]
    assert alert_id
    rows = await sql(
        "SELECT kind, metric, observed_on, severity, observed_value, expected FROM alerts "
        "WHERE project_id=:p",
        p=pid,
    )
    assert len(rows) == 1
    a = rows[0]
    assert (a.kind, a.metric, a.observed_on) == (
        "mangrove_area_loss",
        "mangrove_ha",
        date(2023, 3, 31),
    )
    assert a.severity == "high"  # (12 − 6) / 12 columns = 50 % ≥ 2 × 10 %
    assert a.observed_value == loss["mangrove_ha"]
    assert a.expected["previous_classification_id"] == same["classification_id"]
    # Re-running the season adds a classification but never a second alert.
    again = await _classify(sessionmaker, storage, model_files, pid, 2022, veg_cols=4)
    assert again["loss_check"]["alert_id"] is None
    assert len(await sql("SELECT id FROM alerts WHERE project_id=:p", p=pid)) == 1
    assert len(await sql("SELECT id FROM classifications WHERE project_id=:p", p=pid)) == 4


async def test_failures_are_recorded_and_never_become_numbers(
    client: Any, make_actor: Any, sql: Any, sessionmaker: Any, model_files: Any, tmp_path: Path
) -> None:
    _, pid = await _project(make_actor, sql)
    storage = MemoryStorage()
    with pytest.raises(LookupError, match="no Sentinel-2 scenes"):
        await _classify(sessionmaker, storage, model_files, pid, 2020, source=FakeStackSource([]))
    cloudy = FakeStackSource(season(2020, n=5, cloudy_rows=SIZE))
    with pytest.raises(ValueError, match="no pixel of the project could be classified"):
        await _classify(sessionmaker, storage, model_files, pid, 2020, source=cloudy)
    broken = (tmp_path / "broken", model_files[1])
    broken[0].mkdir()
    with pytest.raises(Exception, match="missing"):
        await _classify(sessionmaker, storage, broken, pid, 2020)
    runs = await sql(
        "SELECT status, error FROM analysis_runs WHERE project_id=:p ORDER BY created_at", p=pid
    )
    assert [r.status for r in runs] == ["failed"] * 3
    assert "fewer than 3 clear looks" in runs[1].error
    assert await sql("SELECT id FROM classifications") == []
    assert storage.objects == {}


async def test_open_sea_gets_the_training_fill_and_seasons_to_classify(
    client: Any, make_actor: Any, sql: Any, sessionmaker: Any, model_files: Any
) -> None:
    _, pid = await _project(make_actor, sql)
    out = await _classify(
        sessionmaker, MemoryStorage(), model_files, pid, 2020, dem=FakeDemSource(elevation=None)
    )
    assert out["classified_pixels"] == POLYGON_PIXELS
    assert out["dem_items"] == []
    assert await ml.seasons_to_classify(sessionmaker, 2020, "ecosystem_classifier_v1") == []
    assert await ml.seasons_to_classify(sessionmaker, 2021, "ecosystem_classifier_v1") == [pid]


# ---------------------------------------------------------------- API


async def test_api_trigger_list_and_detail(
    client: Any,
    make_actor: Any,
    sql: Any,
    sessionmaker: Any,
    model_files: Any,
    enqueued: Enqueued,
    storage: FakeStorage,
) -> None:
    admin, pid = await _project(make_actor, sql)
    outsider = await make_actor("Outsider")
    url = f"/projects/{pid}/classifications"
    assert (await outsider.post(url, json={"season_start_year": 2020})).status_code == 404
    future = datetime.now(UTC).year
    r = await admin.post(url, json={"season_start_year": future})
    assert r.status_code == 422
    assert "not ended" in r.text
    r = await admin.post(url, json={"season_start_year": 2020})
    assert r.status_code == 202, r.text
    run = r.json()
    assert run["kind"] == "classification"
    assert run["status"] == "queued"
    assert enqueued == [("aqua.ml.classify_project", (str(pid), "2020", run["id"]), "ml")]
    assert (await admin.post(url, json={"season_start_year": 2020})).status_code == 409
    assert (await admin.post(url, json={"season_start_year": 2019})).status_code == 202

    # the worker picks up the queued run
    await _classify(sessionmaker, storage, model_files, pid, 2020, run_id=uuid.UUID(run["id"]))
    done = (await admin.get(f"/analysis-runs/{run['id']}")).json()
    assert done["status"] == "succeeded"
    assert done["params"]["model_version"]

    listing = (await admin.get(url)).json()
    assert len(listing) == 1
    item = listing[0]
    assert item["season_start_year"] == 2020
    assert item["classes"] == list(CLASSES)
    assert item["run_id"] == run["id"]
    assert (await admin.get(url, params={"season_start_year": 2019})).json() == []
    assert (await outsider.get(url)).status_code == 404

    detail = (await admin.get(f"/classifications/{item['id']}")).json()
    assert detail["mangrove_ha"] == item["mangrove_ha"]
    assert detail["downloads"]["classes_url"].startswith(
        f"http://storage.test/projects/{pid}/classifications/2020-21/"
    )
    assert "max-probability.tif" in detail["downloads"]["probability_url"]
    assert (await outsider.get(f"/classifications/{item['id']}")).status_code == 404
    assert (await admin.get(f"/classifications/{uuid.uuid4()}")).status_code == 404


# ---------------------------------------------------------------- downstream


async def test_mrv_report_quotes_the_latest_classification_as_context(
    make_actor: Any,
    sql: Any,
    storage: FakeStorage,
    enqueued: Enqueued,
    sessionmaker: Any,
    model_files: Any,
) -> None:
    s = await setup_project(make_actor, sql)  # period ends 2029-12-31
    pid = uuid.UUID(s.project_id)
    old = await _classify(sessionmaker, storage, model_files, pid, 2027)
    latest = await _classify(sessionmaker, storage, model_files, pid, 2028, veg_cols=7)
    await _classify(
        sessionmaker, storage, model_files, pid, 2029
    )  # ends Mar 2030: after the period
    result = await request_and_build(s, storage, enqueued, sessionmaker)
    assert result["status"] == "built", result
    record = json.loads(
        storage.objects[f"projects/{pid}/periods/{s.period_id}/reports/v1/report.json"][0]
    )
    sat = record["satellite"]
    c = sat["stats"]["classification"]
    assert c["label"].startswith("context only: credits come from field plots")
    assert c["season_start_year"] == 2028
    assert c["mangrove_ha"] == latest["mangrove_ha"]
    assert c["model_version"] == "ecosystem_classifier_v1"
    assert c["model_sha256"] == model_files[1].sha256["model.onnx"]
    assert c["model_heldout_metrics"]["macro_f1"] == 0.6378
    assert c["mangrove_mean_confidence"] == latest["mangrove_mean_confidence"]
    assert sat["provenance"]["classification_id"] == latest["classification_id"]
    assert old["classification_id"] != latest["classification_id"]
    # Credits do not depend on it: same lCCC as a report without any classification.
    assert result["lccc_units"] > 0


async def test_stewardship_v2_scores_survival_from_classified_area(
    make_actor: Any, sql: Any, sessionmaker: Any, model_files: Any
) -> None:
    s = await setup_project(make_actor, sql)
    pid = uuid.UUID(s.project_id)
    today = datetime.now(UTC).date()
    start = date(today.year - 3, 6, 1)
    await sql("UPDATE projects SET start_date=:d WHERE id=:id", d=start, id=pid)
    storage = MemoryStorage()
    base_year = start.year - 1  # season ending 31 Mar before the June start
    latest_year = ml.last_completed_season(today)
    await _classify(sessionmaker, storage, model_files, pid, base_year, veg_cols=10)
    await _classify(sessionmaker, storage, model_files, pid, latest_year, veg_cols=9)
    r = await s.admin.get(f"/projects/{pid}/stewardship")
    assert r.status_code == 200, r.text
    surv = next(c for c in r.json()["components"] if c["name"] == "satellite_survival")
    inputs = surv["inputs"]
    assert inputs["method"] == "classification_area"
    assert inputs["baseline_season_start_year"] == base_year
    assert inputs["latest_season_start_year"] == latest_year
    change = 100 * (inputs["latest_mangrove_ha"] - inputs["baseline_mangrove_ha"])
    change /= inputs["baseline_mangrove_ha"]
    assert inputs["mangrove_area_change_pct"] == pytest.approx(change)  # 11 of 12 columns: −8.3 %
    expected = 100 * (change + 20) / 15
    assert surv["score"] == pytest.approx(round(expected, 2))
    assert surv["sub_scores"]["alert_penalty"] == 0.0
    # the NDVI rule would have had no data (no scenes): area rule fills the gap
    assert not surv["insufficient_data"]
    assert not any(n.startswith("no two comparable") for n in surv["notes"])
