"""Photo-verifier data pipeline + metrics (pure functions; no network, no torch)."""

import io
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import photo_eval as pe
import photos_download as pd_dl
import photos_splits as ps
import pytest
from PIL import Image

# ---------------------------------------------------------------- download helpers


def _obs(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": 11,
        "quality_grade": "research",
        "taxon": {"id": 5, "name": "Avicennia marina"},
        "user": {"id": 7, "login": "obs7"},
        "location": "19.1,72.9",
        "place_ids": [6681, 6683],
        "observed_on": "2023-01-02",
        "photos": [
            {
                "id": 99,
                "license_code": "cc-by-nc",
                "attribution": "(c) obs7, some rights reserved (CC BY-NC)",
                "url": "https://inaturalist-open-data.s3.amazonaws.com/photos/99/square.jpg",
            }
        ],
    }
    base.update(kw)
    return base


AVICENNIA = next(c for c in pd_dl.CLASSES if c.key == "avicennia")
PROSOPIS = next(c for c in pd_dl.CLASSES if c.key == "prosopis")


@pytest.mark.parametrize(
    ("code", "ok"),
    [("cc0", True), ("CC-BY", True), ("cc-by-nc", True), ("cc-by-sa", False), (None, False)],
)
def test_licence_filter(code: str | None, ok: bool) -> None:
    assert pd_dl.licence_ok(code) is ok


def test_medium_url() -> None:
    assert pd_dl.medium_url("https://x/photos/1/square.jpeg") == "https://x/photos/1/medium.jpeg"


def test_observation_row_records_licence_and_attribution() -> None:
    row = pd_dl.observation_row(_obs(), AVICENNIA, "india", {6683: "Maharashtra"})
    assert row is not None
    assert row["licence"] == "cc-by-nc"
    assert row["attribution"].startswith("(c) obs7")
    assert row["state"] == "Maharashtra"
    assert row["country_india"] is True
    assert row["photo_url"].endswith("/99/medium.jpg")
    assert row["file"] == "images/avicennia/99.jpg"
    assert (row["lat"], row["lon"]) == ("19.1", "72.9")


def test_observation_row_skips_closed_licences_and_wrong_genus() -> None:
    closed = _obs(photos=[{**_obs()["photos"][0], "license_code": "cc-by-nd"}])
    assert pd_dl.observation_row(closed, AVICENNIA, "india", {}) is None
    assert pd_dl.observation_row(_obs(quality_grade="needs_id"), AVICENNIA, "asia", {}) is None
    other = _obs(taxon={"id": 1, "name": "Rhizophora mucronata"})
    assert pd_dl.observation_row(other, AVICENNIA, "asia", {}) is None
    negative = pd_dl.observation_row(other, PROSOPIS, "asia", {})
    assert negative is not None
    assert negative["genus"] == ""


def test_select_rows_caps_observers_and_dedupes() -> None:
    rows = [
        {"observation_id": i, "photo_id": i, "observer_id": i % 2 if i < 6 else 9}
        for i in range(10)
    ]
    chosen = pd_dl.select_rows([*rows, rows[0]], quota=5, max_per_observer=2)
    assert len(chosen) == 5
    assert max(pd.Series([r["observer_id"] for r in chosen]).value_counts()) <= 2
    more = pd_dl.select_rows(rows, quota=6, max_per_observer=2, taken=chosen)
    assert len(more) == 1  # quota counts the rows already taken
    assert not {r["photo_id"] for r in more} & {r["photo_id"] for r in chosen}


def test_resize_jpeg_caps_long_side() -> None:
    buf = io.BytesIO()
    Image.new("RGBA", (1200, 600), (0, 128, 0, 255)).save(buf, "PNG")
    data, w, h = pd_dl.resize_jpeg(buf.getvalue())
    assert (w, h) == (512, 256)
    with Image.open(io.BytesIO(data)) as img:
        assert img.format == "JPEG"
        assert img.mode == "RGB"


def test_manifest_round_trip_and_summary(tmp_path: Path) -> None:
    row = pd_dl.observation_row(_obs(), AVICENNIA, "india", {6683: "Maharashtra"})
    assert row is not None
    row.update(sha256="ab", bytes=10)
    path = tmp_path / "m.csv"
    pd_dl.write_manifest([row], path)
    back = pd_dl.read_manifest(path)
    assert back[0]["photo_id"] == "99"
    summary = pd_dl.summary(back)
    assert summary["per_licence"] == {"cc-by-nc": 1}
    assert summary["india_by_state"] == {"Maharashtra": 1}
    assert pd_dl.read_manifest(tmp_path / "missing.csv") == []


def test_fetch_is_resumable_and_size_guarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pd_dl, "RAW", tmp_path)
    monkeypatch.setattr(pd_dl, "MANIFEST", tmp_path / "m.csv")
    monkeypatch.setattr(pd_dl, "DOWNLOAD_LOG", tmp_path / "log.json")
    buf = io.BytesIO()
    Image.new("RGB", (800, 400), (10, 120, 30)).save(buf, "JPEG")
    rows = []
    for i in range(3):
        photo = {**_obs()["photos"][0], "id": i, "url": f"https://x/photos/{i}/square.jpg"}
        row = pd_dl.observation_row(_obs(id=i, photos=[photo]), AVICENNIA, "india", {})
        assert row is not None
        rows.append(row)
    calls: list[str] = []

    def fake_get(url: str) -> bytes:
        calls.append(url)
        if "/1/" in url:
            raise ValueError("too big; refused")
        return buf.getvalue()

    log = pd_dl.cmd_fetch(pd_dl.Client(tmp_path / "cache"), rows, fake_get, workers=1)
    assert log["files"] == 2
    assert log["failed"] == 1
    assert rows[0]["width"] == 512
    assert rows[0]["sha256"]
    assert not rows[1]["sha256"]
    calls.clear()
    pd_dl.cmd_fetch(pd_dl.Client(tmp_path / "cache"), rows, fake_get, workers=1)
    assert calls == [rows[1]["photo_url"]]  # only the failed one is retried
    saved = json.loads((tmp_path / "log.json").read_text())
    assert saved["files"] == 2


# ---------------------------------------------------------------- splits


def _split_frame() -> pd.DataFrame:
    rng = np.random.default_rng(3)
    n = 600
    observers = rng.integers(0, 80, n)
    states = np.where(observers < 10, "Maharashtra", "Kerala")
    # observer 0 also photographs outside the held-out state
    states[(observers == 0) & (np.arange(n) % 2 == 0)] = "Tamil Nadu"
    return pd.DataFrame(
        {
            "photo_id": np.arange(n),
            "observer_id": observers,
            "state": states,
            "country_india": True,
            "class_key": rng.choice(["avicennia", "prosopis"], n),
            "label": "mangrove",
        }
    )


def test_state_holdout_and_observer_grouping() -> None:
    out = ps.assign_photo_splits(_split_frame())
    state = out[out["state"] == "Maharashtra"]
    assert (state["split"] == "test_state").all()
    overlap = out[(out["observer_id"] == 0) & (out["state"] != "Maharashtra")]
    assert len(overlap) > 0
    assert (overlap["split"] == "excluded_overlap").all()
    ps.check_no_leakage(out)
    assert {"train", "val", "test_observer"} <= set(out["split"])
    summary = ps.split_summary(out)
    assert summary["per_split"]["test_state"] == len(state)


def test_leakage_check_raises() -> None:
    df = pd.DataFrame({"observer_id": [1, 1], "split": ["train", "val"]})
    with pytest.raises(AssertionError):
        ps.check_no_leakage(df)


def test_observer_bucket_is_stable() -> None:
    assert ps.observer_bucket(123) == ps.observer_bucket("123")
    assert 0 <= ps.observer_bucket(5) < 100


# ---------------------------------------------------------------- metrics


def test_binary_metrics_and_auroc() -> None:
    y = np.array([1, 1, 0, 0])
    p = np.array([0.9, 0.4, 0.6, 0.1])
    m = pe.binary_at(y, p, 0.5)
    assert m == {"precision": 0.5, "recall": 0.5, "f1": 0.5, "false_accept_rate": 0.5}
    assert pe.auroc(y, p) == pytest.approx(0.75)
    assert pe.auroc(np.array([1, 0]), np.array([0.5, 0.5])) == pytest.approx(0.5)
    assert np.isnan(pe.auroc(np.array([1, 1]), np.array([0.1, 0.2])))


def test_thresholds() -> None:
    y = np.array([1, 1, 1, 1, 0, 0])
    p = np.array([0.9, 0.8, 0.7, 0.2, 0.3, 0.1])
    assert pe.best_f1_threshold(y, p) == pytest.approx(0.2)  # P 0.8, R 1.0 beats P 1, R 0.75
    assert pe.threshold_for_recall(y, p, 0.75) == pytest.approx(0.7)
    assert pe.threshold_for_recall(y, p, 1.0) == pytest.approx(0.2)
    assert pe.threshold_for_precision(y, p, 1.0) == pytest.approx(0.7)


def test_ece_and_genus_topk() -> None:
    assert pe.ece(np.array([1, 0]), np.array([1.0, 0.0])) == pytest.approx(0.0)
    assert pe.ece(np.array([0, 0]), np.array([0.95, 0.95])) == pytest.approx(0.95)
    scores = np.array([[0.1, 0.7, 0.2], [0.5, 0.3, 0.2]])
    assert pe.genus_topk(np.array([1, 2]), scores, 1) == 0.5
    assert pe.genus_topk(np.array([1, 2]), scores, 3) == 1.0
    assert np.isnan(pe.genus_topk(np.array([], dtype=int), scores[:0], 1))


def test_cluster_bootstrap_and_evaluate_split() -> None:
    rng = np.random.default_rng(0)
    n = 200
    y = rng.integers(0, 2, n)
    p = np.clip(y * 0.6 + rng.random(n) * 0.5, 0, 1)
    genus_true = np.where(y == 1, rng.integers(0, 3, n), -1)
    genus_scores = rng.random((n, 3))
    groups = rng.integers(0, 30, n)
    lo, hi = pe.cluster_bootstrap(groups, lambda i: float(y[i].mean()), n_boot=200)
    assert lo <= y.mean() <= hi
    out = pe.evaluate_split(y, p, genus_true, genus_scores, groups, 0.5, 0.4, n_boot=50)
    assert out["n"] == n
    assert out["f1"]["ci95"][0] <= out["f1"]["value"] <= out["f1"]["ci95"][1]
    diff = pe.paired_f1_diff_ci(y, p, 0.5, p, 0.5, groups, n_boot=50)
    assert diff["value"] == 0.0
    assert diff["ci95"] == [0.0, 0.0]
