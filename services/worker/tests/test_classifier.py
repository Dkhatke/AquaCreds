"""Classifier serving without network or database: feature parity with the training code path,
ONNX inference, model distribution (SHA-256 pinning), scene selection, area-loss alerts, wiring."""

import json
import os
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from classify_fakes import (
    CLASSES,
    POLYGON_PIXELS,
    SIZE,
    FakeDemSource,
    FakeStackSource,
    MemoryStorage,
    features_doc,
    linear_onnx,
    linear_weights,
    season,
    synthetic_model_files,
)
from features_extract import S2_ASSETS, Scene, assemble_raw, point_features
from shapely.geometry import box

from aqua_core.models import AlertKind, AlertSeverity
from aqua_core.settings import Settings
from aqua_geo import sentinel2 as s2
from aqua_geo.classify import PRODUCTION_FEATURES, ModelSpec, tile_features
from aqua_worker import model_store
from aqua_worker.alerts import (
    AreaLossRule,
    SeasonArea,
    detect_mangrove_area_loss,
    season_end,
)
from aqua_worker.celery_app import _verify_classifier, app, beat_schedule, consumes_ml_queue
from aqua_worker.model_store import (
    CURRENT_ECOSYSTEM_CLASSIFIER,
    ECOSYSTEM_CLASSIFIER_V1,
    ECOSYSTEM_CLASSIFIER_V1_1,
    MANIFESTS,
    ModelManifest,
    ModelUnavailableError,
    fetch,
    get_model,
    load_model,
    publish,
)
from aqua_worker.scenes import SceneRef
from aqua_worker.tasks import ml

ROOT = Path(__file__).resolve().parents[3]
ARTIFACTS = ROOT / "ml/artifacts/ecosystem_classifier_v1_1"
FEATURES_PARQUET = ROOT / "data/processed/features.parquet"
CARDS = ROOT / "ml/model_cards"
GEOMETRY = box(86.85, 20.70, 86.87, 20.72)


# ---------------------------------------------------------------- feature parity


def test_production_features_equal_the_training_code_path() -> None:
    """The same DNs through (a) training: features_extract.assemble_raw + point_features on a
    (time, point) cube, and (b) production: sentinel2.scale_and_mask on a (time, y, x) stack +
    aqua_geo.classify.tile_features, give identical float32 feature vectors (elevation and its
    0 m fill included, like train_classifier.load)."""
    rng = np.random.default_rng(7)
    t, ny, nx = 6, 3, 4
    n = ny * nx
    days = [f"2020-12-{d:02d}" for d in (1, 6, 11, 16, 21, 26)]
    offsets = [0.0, 0.0, 0.0, 1000.0, 1000.0, 0.0]  # mixed processing baselines
    scenes = {
        f"s{i}": Scene(id=f"s{i}", day=d, cloud=1.0, footprint=box(0, 0, 1, 1), offset=o)
        for i, (d, o) in enumerate(zip(days, offsets, strict=True))
    }
    dn = rng.integers(100, 6000, size=(len(S2_ASSETS), t, n)).astype("float64")
    scl_idx = S2_ASSETS.index("SCL")
    dn[scl_idx] = rng.choice([4, 5, 6, 8, 9, 3], size=(t, n), p=[0.4, 0.2, 0.2, 0.1, 0.05, 0.05])
    for i, o in enumerate(offsets):  # offset scenes carry +1000
        if o:
            dn[:scl_idx, i] += 1000
    dn[:scl_idx, 2, 5] = 0.0  # a no-data pixel
    dn[scl_idx, :4, 7] = 9  # pixel 7: only 2 clear looks → no features
    obs = {(p, f"s{i}"): list(dn[:, i, p]) for i in range(t) for p in range(n)}
    raw, offs = assemble_raw(n, obs, scenes)
    training = point_features(raw, offs)
    elevation = rng.uniform(-2, 30, n).astype("float32")
    elevation[[1, 4]] = np.nan  # no DEM tile
    training["elevation"] = elevation
    training = training[training.clear_count >= 3].fillna({"elevation": 0.0})
    expected = training[list(PRODUCTION_FEATURES)].to_numpy(dtype=np.float32)

    # production: the same DNs as a (time, y, x) raster
    names = [*s2.BANDS, "scl"]
    grid = {name: (("time", "y", "x"), raw[name].values.reshape(t, ny, nx)) for name in names}
    import xarray as xr

    raster = xr.Dataset(
        grid, coords={"time": raw.time.values, "y": np.arange(ny), "x": np.arange(nx)}
    )
    refl = s2.scale_and_mask(raster, offs, list(s2.BANDS))
    spec = ModelSpec.from_features_json(json.dumps(features_doc()))
    prod = tile_features(refl, elevation.reshape(ny, nx), np.ones((ny, nx), bool), spec)

    assert prod.index.tolist() == training.index.tolist()  # same pixels usable (7 dropped)
    assert prod.too_few_clear == 1
    np.testing.assert_array_equal(prod.matrix, expected)


# ---------------------------------------------------------------- ONNX + model store


def _session_predict(model: model_store.LoadedModel, x: np.ndarray) -> Any:
    return model.predict(x.astype("float32"))


def test_onnx_inference_on_a_synthetic_stack_is_deterministic(tmp_path: Path) -> None:
    manifest = synthetic_model_files(tmp_path / "m")
    model = load_model(tmp_path / "m", manifest)
    rng = np.random.default_rng(3)
    x = rng.normal(0, 0.3, (500, len(PRODUCTION_FEATURES))).astype("float32")
    labels, probs = _session_predict(model, x)
    labels2, probs2 = _session_predict(model, x)
    np.testing.assert_array_equal(labels, labels2)
    np.testing.assert_array_equal(probs, probs2)
    w, b = linear_weights()
    logits = x @ w + b
    ref = np.exp(logits - logits.max(1, keepdims=True))
    ref /= ref.sum(1, keepdims=True)
    np.testing.assert_allclose(probs, ref, rtol=1e-5, atol=1e-6)
    np.testing.assert_array_equal(labels, ref.argmax(1))
    empty_labels, empty_probs = model.predict(np.zeros((0, 29), "float32"))
    assert empty_labels.shape == (0,)
    assert empty_probs.shape == (0, 6)
    with pytest.raises(ValueError, match="expected"):
        model.predict(np.zeros((2, 5), "float32"))


def test_compute_classification_on_the_fake_stack(tmp_path: Path) -> None:
    manifest = synthetic_model_files(tmp_path / "m")
    model = load_model(tmp_path / "m", manifest)
    source = FakeStackSource(season(2020, veg_cols=6, n=4))
    refs = source.search(GEOMETRY, date(2020, 11, 1), date(2021, 3, 31), max_cloud=80)
    product = ml.compute_classification(
        source, FakeDemSource(), refs, GEOMETRY, model, tile=5, low_confidence_threshold=0.6
    )
    s = product.summary
    rows = SIZE
    assert s.polygon_pixels == POLYGON_PIXELS == s.classified_pixels
    assert s.class_pixels["mangrove"] == rows * (6 + 2)  # vegetated + NDVI-0 tie columns
    assert s.class_pixels["water"] == POLYGON_PIXELS - rows * 8
    assert s.low_confidence_pixels == rows * 2  # the tie columns (p ≈ 0.497)
    assert s.mangrove_mean_confidence is not None
    assert 0.85 < s.mangrove_mean_confidence < 0.9
    assert source.loads[0]["bands"] == tuple(s2.BANDS)  # all 10 bands, not the index subset
    assert product.classes_cog.startswith(b"II*\x00")
    assert product.probability_cog
    assert product.dem_items == ["Copernicus_DSM_COG_10_N20_00_E086_00"]
    assert product.tiles == 12  # 4 x 4 tiles of 5 px; the last column (x 15) is outside


def _put_files(storage: MemoryStorage, directory: Path, manifest: Any) -> None:
    for name in manifest.files:
        storage.put_bytes(manifest.prefix + name, (directory / name).read_bytes(), "x")


def test_fetch_verifies_hashes_and_repairs_the_cache(tmp_path: Path) -> None:
    manifest = synthetic_model_files(tmp_path / "src")
    storage = MemoryStorage()
    with pytest.raises(ModelUnavailableError, match="publish it"):
        fetch(storage, tmp_path / "cache", manifest)
    _put_files(storage, tmp_path / "src", manifest)
    target = fetch(storage, tmp_path / "cache", manifest)
    assert sorted(p.name for p in target.iterdir()) == ["features.json", "model.onnx"]
    storage.gets.clear()
    fetch(storage, tmp_path / "cache", manifest)
    assert storage.gets == []  # verified cache: nothing downloaded
    (target / "model.onnx").write_bytes(b"corrupted")
    fetch(storage, tmp_path / "cache", manifest)
    assert storage.gets == [manifest.prefix + "model.onnx"]  # re-downloaded
    storage.objects[manifest.prefix + "model.onnx"] = (b"tampered", "x")
    (target / "model.onnx").unlink()
    with pytest.raises(ModelUnavailableError, match="refusing"):
        fetch(storage, tmp_path / "cache", manifest)
    assert not (target / "model.onnx").exists()  # nothing unverified was cached


def test_load_model_rejects_mismatches(tmp_path: Path) -> None:
    manifest = synthetic_model_files(tmp_path / "ok")
    (tmp_path / "ok" / "model.onnx").write_bytes(b"other")
    with pytest.raises(ModelUnavailableError, match="pinned"):
        load_model(tmp_path / "ok", manifest)
    with pytest.raises(ModelUnavailableError, match="missing"):
        load_model(tmp_path / "nothing", manifest)
    other = synthetic_model_files(tmp_path / "v", doc=features_doc("some_other_model"))
    with pytest.raises(ModelUnavailableError, match=r"features\.json is for"):
        load_model(tmp_path / "v", other)
    w, b = linear_weights()
    three = synthetic_model_files(tmp_path / "k", onnx_bytes=linear_onnx(w[:, :3], b[:3]))
    with pytest.raises(ModelUnavailableError, match="class probabilities"):
        load_model(tmp_path / "k", three)
    bad_order = features_doc(feature_columns=list(reversed(PRODUCTION_FEATURES)))
    wrong = synthetic_model_files(tmp_path / "o", doc=bad_order)
    with pytest.raises(ValueError, match="feature columns differ"):
        load_model(tmp_path / "o", wrong)


def test_publish_refuses_unpinned_files_and_uploads_pinned_ones(tmp_path: Path) -> None:
    manifest = synthetic_model_files(tmp_path / "src")
    storage = MemoryStorage()
    uploaded = publish(storage, tmp_path / "src", manifest)
    assert set(uploaded) == {manifest.prefix + "model.onnx", manifest.prefix + "features.json"}
    assert storage.objects[manifest.prefix + "features.json"][1] == "application/json"
    assert uploaded[manifest.prefix + "model.onnx"] == manifest.sha256["model.onnx"]
    with pytest.raises(ModelUnavailableError, match="not publishing"):
        publish(storage, tmp_path / "src", CURRENT_ECOSYSTEM_CLASSIFIER)
    with pytest.raises(ModelUnavailableError, match="missing"):
        publish(storage, tmp_path / "none", manifest)


def test_get_model_loads_once_per_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = synthetic_model_files(tmp_path / "src", doc=features_doc("ecosystem_classifier_x"))
    manifest = replace(manifest, version="ecosystem_classifier_x")
    storage = MemoryStorage()
    _put_files(storage, tmp_path / "src", manifest)
    monkeypatch.setattr(model_store, "_loaded", {})
    first = get_model(storage, tmp_path / "cache", manifest)
    storage.objects.clear()
    assert get_model(storage, tmp_path / "cache", manifest) is first


def test_cli_publish_and_verify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import aqua_core.storage

    manifest = synthetic_model_files(tmp_path / "src")
    storage = MemoryStorage()
    monkeypatch.setattr(model_store, "CURRENT_ECOSYSTEM_CLASSIFIER", manifest)
    monkeypatch.setattr(aqua_core.storage, "S3ObjectStorage", lambda settings: storage)
    monkeypatch.setenv("ML_MODEL_CACHE_DIR", str(tmp_path / "cache"))
    from aqua_core.settings import get_settings

    get_settings.cache_clear()
    try:
        assert model_store.main(["publish", str(tmp_path / "src")]) == 0
        assert model_store.main(["verify"]) == 0
    finally:
        get_settings.cache_clear()
    out = capsys.readouterr().out
    assert "uploaded models/test-classifier/v1/model.onnx" in out
    assert f"sha256={manifest.sha256['model.onnx']}" in out


@pytest.mark.parametrize("manifest", [ECOSYSTEM_CLASSIFIER_V1, ECOSYSTEM_CLASSIFIER_V1_1])
def test_manifest_metrics_match_the_model_card(manifest: ModelManifest) -> None:
    stem = manifest.version.replace(".", "_")
    assert manifest.model_card == f"ml/model_cards/{stem}.md"
    assert (ROOT / manifest.model_card).is_file()
    doc = json.loads((CARDS / f"{stem}_metrics.json").read_text())
    assert doc["model_version"] == manifest.version
    held = doc["models"]["lightgbm"]["heldout"]
    m = manifest.heldout_metrics
    assert doc["winner"] == "lightgbm"
    assert m["points"] == held["n"]
    assert m["macro_f1"] == round(held["macro_f1"], 4)
    assert m["accuracy"] == round(held["accuracy"], 4)
    assert m["mangrove_precision"] == held["per_class"]["mangrove"]["precision"]
    assert m["mangrove_recall"] == held["per_class"]["mangrove"]["recall"]
    assert m["mangrove_f1"] == held["per_class"]["mangrove"]["f1"]
    assert (m["brier"], m["ece"]) == (held["calibration"]["brier"], held["calibration"]["ece"])
    assert doc["onnx_parity_heldout"]["onnx_bytes"] == 1_412_038


def test_production_model_is_v1_1_and_versions_are_distinct() -> None:
    assert CURRENT_ECOSYSTEM_CLASSIFIER is ECOSYSTEM_CLASSIFIER_V1_1
    assert CURRENT_ECOSYSTEM_CLASSIFIER.prefix == "models/ecosystem_classifier/v1.1/"
    assert set(MANIFESTS) == {"ecosystem_classifier_v1", "ecosystem_classifier_v1.1"}
    assert model_store.manifest_for("ecosystem_classifier_v1") is ECOSYSTEM_CLASSIFIER_V1
    assert model_store.manifest_for("ecosystem_classifier_v2") is None
    v1, v11 = ECOSYSTEM_CLASSIFIER_V1, ECOSYSTEM_CLASSIFIER_V1_1
    assert v1.prefix != v11.prefix
    assert not set(v1.sha256.values()) & set(v11.sha256.values())
    # Same training configuration: same scene stack.
    assert (v1.season_months, v1.max_scenes, v1.max_tile_cloud_pct) == (
        v11.season_months,
        v11.max_scenes,
        v11.max_tile_cloud_pct,
    )


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("ecosystem_classifier_v1", ECOSYSTEM_CLASSIFIER_V1.heldout_metrics),
        ("ecosystem_classifier_v1.1", ECOSYSTEM_CLASSIFIER_V1_1.heldout_metrics),
        ("ecosystem_classifier_x", None),
    ],
)
def test_report_quotes_the_metrics_of_the_model_that_classified(
    version: str, expected: Any
) -> None:
    from types import SimpleNamespace

    from aqua_worker.tasks.reports import classification_context

    row: Any = SimpleNamespace(
        model_version=version,
        season_start_year=2024,
        mangrove_ha=1.0,
        class_areas_ha={"mangrove": 1.0},
        mangrove_mean_confidence=0.9,
        low_confidence_share=0.0,
        low_confidence_threshold=0.6,
        unclassified_pixels=0,
        polygon_pixels=100,
        scenes=[],
        model_sha256="x",
        pixel_area_m2=100.0,
        epsg=32645,
        areal_scale=1.0,
    )
    got = classification_context(row)["model_heldout_metrics"]
    assert got == (dict(expected) if expected is not None else None)


@pytest.mark.skipif(not (ARTIFACTS / "model.onnx").exists(), reason="DVC output not present")
def test_real_v1_1_artifacts_match_the_pinned_hashes_and_predict() -> None:
    model = load_model(ARTIFACTS)
    assert model.version == "ecosystem_classifier_v1.1"
    assert model.spec.classes == CLASSES
    x = np.random.default_rng(1).normal(0.2, 0.1, (64, 29)).astype("float32")
    labels, probs = model.predict(x)
    labels2, probs2 = model.predict(x)
    np.testing.assert_array_equal(labels, labels2)
    np.testing.assert_array_equal(probs, probs2)
    np.testing.assert_allclose(probs.sum(1), 1.0, atol=1e-5)
    np.testing.assert_array_equal(labels, probs.argmax(1))


@pytest.mark.skipif(
    not (ARTIFACTS / "model.lgb.txt").exists() or not FEATURES_PARQUET.exists(),
    reason="DVC outputs (artifacts + data/processed/features.parquet) not present",
)
def test_real_v1_1_onnx_matches_native_lightgbm_on_the_heldout_region() -> None:
    """The pinned ONNX model against the native LightGBM model on all 1,133 Gulf of Kutch
    points, prepared like train_classifier.load: same classes, probabilities within float32
    tree-threshold error (0.014 at export)."""
    lgb: Any = pytest.importorskip("lightgbm")
    import pandas as pd

    model = load_model(ARTIFACTS)
    df = pd.read_parquet(FEATURES_PARQUET)
    df = df[(df.clear_count >= model.spec.min_clear) & (df.split == "heldout_region")]
    x = df[list(model.spec.feature_columns)].fillna(dict(model.spec.fill_values))
    assert len(x) == 1133
    assert not x.isna().any().any()
    labels, probs = model.predict(x.to_numpy(dtype="float32"))
    native = np.asarray(lgb.Booster(model_file=str(ARTIFACTS / "model.lgb.txt")).predict(x))
    np.testing.assert_array_equal(labels, native.argmax(1))
    assert float(np.abs(probs - native).max()) < 0.02


# ---------------------------------------------------------------- scene selection


def _ref(item: str, day: str, cloud: float | None) -> SceneRef:
    d = date.fromisoformat(day)
    return SceneRef(item, datetime(d.year, d.month, d.day, 5, tzinfo=UTC), cloud, "05.10", "pc")


def test_season_scene_selection_matches_the_training_rule() -> None:
    refs = [
        _ref("a1", "2020-12-01", 30.0),
        _ref("a2", "2020-12-01", 5.0),  # same day, other tile: the day ranks by 5 %
        _ref("b", "2020-12-06", 1.0),
        _ref("c", "2020-11-20", 50.0),
        _ref("d", "2021-01-10", None),  # unknown cloud ranks last
    ]
    two = ml.select_season_scenes(refs, 2)
    assert [r.item_id for r in two] == ["a1", "a2", "b"]  # both tiles of the kept day
    assert [r.item_id for r in ml.select_season_scenes(refs, 10)] == ["c", "a1", "a2", "b", "d"]
    assert ml.select_season_scenes([], 12) == []


def test_last_completed_season_and_prefix() -> None:
    assert ml.last_completed_season(date(2026, 4, 15)) == 2025
    assert ml.last_completed_season(date(2026, 3, 31)) == 2024
    assert ml.last_completed_season(date(2026, 12, 1)) == 2025
    pid = run = "00000000-0000-0000-0000-000000000001"
    import uuid

    key = ml.classification_prefix(uuid.UUID(pid), 2020, "v1", uuid.UUID(run))
    assert key == f"projects/{pid}/classifications/2020-21/v1/{run}"


# ---------------------------------------------------------------- area-loss alerts


def test_mangrove_area_loss_rule() -> None:
    rule = AreaLossRule(threshold_pct=10.0, min_ha=1.0)
    prev = SeasonArea("p", 2020, "v1", 100.0)
    assert detect_mangrove_area_loss(None, SeasonArea("c", 2021, "v1", 10.0), rule) is None
    assert detect_mangrove_area_loss(prev, SeasonArea("c", 2021, "v1", 91.0), rule) is None
    assert detect_mangrove_area_loss(prev, SeasonArea("c", 2021, "v2", 10.0), rule) is None
    assert detect_mangrove_area_loss(prev, SeasonArea("c", 2022, "v1", 10.0), rule) is None
    small = SeasonArea("p", 2020, "v1", 5.0)
    assert (
        detect_mangrove_area_loss(small, SeasonArea("c", 2021, "v1", 4.2), rule) is None
    )  # < 1 ha
    medium = detect_mangrove_area_loss(prev, SeasonArea("c", 2021, "v1", 85.0), rule)
    assert medium is not None
    assert medium.kind is AlertKind.MANGROVE_AREA_LOSS
    assert medium.severity is AlertSeverity.MEDIUM
    assert medium.observed_on == season_end(2021) == date(2022, 3, 31)
    assert medium.expected["drop_pct"] == 15.0
    assert medium.expected["drop_ha"] == 15.0
    assert "2021-22" in medium.message
    assert "2020-21" in medium.message
    high = detect_mangrove_area_loss(prev, SeasonArea("c", 2021, "v1", 79.0), rule)
    assert high is not None
    assert high.severity is AlertSeverity.HIGH
    settings = Settings(_env_file=None, ml_mangrove_loss_alert_pct=15, ml_mangrove_loss_min_ha=2)
    assert AreaLossRule.from_settings(settings) == AreaLossRule(15.0, 2.0)


# ---------------------------------------------------------------- Celery wiring


def test_ml_tasks_registered_on_ml_queue_and_scheduled_yearly() -> None:
    assert "aqua.ml.classify_project" in app.tasks
    assert "aqua.ml.classify_dry_season" in app.tasks
    assert app.conf.task_routes["aqua.ml.*"] == {"queue": "ml"}
    entry = beat_schedule(Settings(_env_file=None, ml_annual_classification_day=20))[
        "ml-classify-dry-season"
    ]
    assert entry["task"] == "aqua.ml.classify_dry_season"
    assert entry["options"]["queue"] == "ml"
    sched = entry["schedule"]
    assert sched.month_of_year == {4}
    assert sched.day_of_month == {20}
    assert sched.hour == {2}
    limit = app.tasks["aqua.ml.classify_project"].time_limit
    assert Settings(_env_file=None).geo_stale_run_timeout_min * 60 > limit


def test_classify_wrapper_passes_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    async def fake_with_db(job: Any) -> Any:
        return await job("SM")

    async def fake_core(*args: Any, **kwargs: Any) -> dict[str, Any]:
        seen["args"], seen["kwargs"] = args, kwargs
        return {"ok": True}

    settings = Settings(_env_file=None, ml_classify_tile_px=128, ml_low_confidence_threshold=0.7)
    monkeypatch.setattr(ml, "_with_db", fake_with_db)
    monkeypatch.setattr(ml, "classify_project", fake_core)
    monkeypatch.setattr(ml, "get_settings", lambda: settings)
    monkeypatch.setattr(ml, "S3ObjectStorage", lambda s: "STORAGE")
    run_id = "00000000-0000-0000-0000-0000000000aa"
    pid = "00000000-0000-0000-0000-0000000000bb"
    assert ml.classify_project_task.run(pid, "2020", run_id) == {"ok": True}
    args, kwargs = seen["args"], seen["kwargs"]
    assert args[0] == "SM"
    assert args[3] == "STORAGE"
    assert str(args[5]) == pid
    assert args[6] == 2020
    assert str(kwargs["run_id"]) == run_id
    assert kwargs["tile"] == 128
    assert kwargs["low_confidence_threshold"] == 0.7
    assert kwargs["loss_rule"] == AreaLossRule(10.0, 1.0)


def test_dry_season_wrapper_queues_one_task_per_project(monkeypatch: pytest.MonkeyPatch) -> None:
    import uuid

    queued: list[tuple[Any, Any]] = []

    async def fake_with_db(job: Any) -> Any:
        return [uuid.UUID(int=1), uuid.UUID(int=2)]

    monkeypatch.setattr(ml, "_with_db", fake_with_db)
    monkeypatch.setattr(ml, "_today", lambda: date(2026, 4, 15))
    monkeypatch.setattr(
        ml.classify_project_task, "apply_async", lambda args, queue: queued.append((args, queue))
    )
    out = ml.classify_dry_season_task.run()
    assert out["season_start_year"] == 2025
    assert len(out["queued"]) == 2
    assert queued == [([str(uuid.UUID(int=1)), 2025], "ml"), ([str(uuid.UUID(int=2)), 2025], "ml")]
    assert ml.classify_dry_season_task.run(2023)["season_start_year"] == 2023


def _Sender(names: list[str] | None) -> Any:
    from types import SimpleNamespace

    if names is None:
        return SimpleNamespace(app=None)
    queues = {n: SimpleNamespace(name=n) for n in names}
    return SimpleNamespace(
        app=SimpleNamespace(amqp=SimpleNamespace(queues=SimpleNamespace(consume_from=queues)))
    )


def test_worker_start_verifies_the_model_only_on_ml_workers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert consumes_ml_queue(_Sender(["default", "ml"]))
    assert not consumes_ml_queue(_Sender(["geo"]))
    assert consumes_ml_queue(_Sender(None))  # unknown → verify
    import aqua_core.storage
    from aqua_worker import celery_app

    calls: list[str] = []
    monkeypatch.setattr(aqua_core.storage, "S3ObjectStorage", lambda s: MemoryStorage())
    monkeypatch.setattr(model_store, "fetch", lambda *a, **k: calls.append("fetch") or tmp_path)
    _verify_classifier(sender=_Sender(["geo"]))
    assert calls == []
    _verify_classifier(sender=_Sender(["ml"]))  # load fails (no files): logged, not raised
    assert calls == ["fetch"]
    manifest = synthetic_model_files(tmp_path / "m")
    monkeypatch.setattr(model_store, "fetch", lambda *a, **k: tmp_path / "m")
    monkeypatch.setattr(model_store, "load_model", lambda d, m=manifest: load_model(d, manifest))
    _verify_classifier(sender=_Sender(["ml"]))
    _verify_classifier(sender=None)
    assert celery_app.app is app


@pytest.mark.skipif(
    os.environ.get("AQUA_NETWORK_TESTS") != "1",
    reason="network test: set AQUA_NETWORK_TESTS=1 (queries Planetary Computer)",
)
def test_live_dem_search_bhitarkanika() -> None:  # pragma: no cover - opt-in
    import xarray as xr
    from pyproj import Transformer

    from aqua_worker.scenes import StacDemSource

    source = StacDemSource()
    tiles = source.search(box(86.86, 20.70, 86.861, 20.701))
    assert tiles
    assert all(t.item_id.startswith("Copernicus_DSM") for t in tiles)
    # one small read: a 5 x 5 grid of 10 m pixels on the 10 m UTM 45N grid
    x0, y0 = Transformer.from_crs(4326, 32645, always_xy=True).transform(86.86, 20.70)
    x = round(x0, -1) + 5 + 10 * np.arange(5)
    y = round(y0, -1) - 5 - 10 * np.arange(5)
    like = xr.DataArray(np.ones((5, 5), bool), dims=("y", "x"), coords={"y": y, "x": x})
    elevation = source.load(tiles, like, 32645).compute()
    assert elevation.shape == (5, 5)
    assert np.isfinite(elevation.values).all()
    assert -20 < float(elevation.mean()) < 60  # tidal forest a few metres above sea level
