import time
from collections.abc import Sequence
from datetime import UTC, datetime

import numpy as np
import pystac
import pytest
from features_extract import (
    S2_ASSETS,
    Scene,
    Throttle,
    assemble_raw,
    config_hash,
    dem_elevations,
    point_features,
    select_scenes,
    tiles_for_points,
)
from shapely.geometry import box, mapping

from aqua_geo.features import FEATURE_VARS

INSIDE = box(86.0, 20.0, 87.0, 21.0)
ELSEWHERE = box(80.0, 15.0, 81.0, 16.0)


def _scene(sid: str, day: str, cloud: float, fp: object = INSIDE, offset: float = 0.0) -> Scene:
    return Scene(id=sid, day=day, cloud=cloud, footprint=fp, offset=offset)


def test_select_scenes_covers_point_one_per_day_least_cloudy() -> None:
    scenes = [
        _scene("a", "2020-12-01", 30.0),
        _scene("a2", "2020-12-01", 5.0),  # same day, other tile, clearer → wins
        _scene("b", "2020-12-06", 1.0),
        _scene("c", "2020-11-20", 50.0),
        _scene("far", "2020-12-11", 0.0, fp=ELSEWHERE),
    ]
    chosen = select_scenes(scenes, 86.5, 20.5, max_scenes=2)
    assert [s.id for s in chosen] == ["a2", "b"]  # date order
    assert [s.id for s in select_scenes(scenes, 86.5, 20.5, max_scenes=10)] == ["c", "a2", "b"]
    assert select_scenes(scenes, 70.0, 10.0, max_scenes=10) == []


def _values(nir_dn: float, red_dn: float, scl: float = 4.0) -> list[float]:
    base = dict.fromkeys(S2_ASSETS, 1000.0)
    base["B08"] = nir_dn
    base["B04"] = red_dn
    base["SCL"] = scl
    return [base[a] for a in S2_ASSETS]


def test_assemble_raw_fills_missing_with_nodata_and_keeps_offsets() -> None:
    sc = {
        "s1": _scene("s1", "2020-12-01", 1.0),
        "s2": _scene("s2", "2021-01-05", 1.0, offset=1000.0),
    }
    obs = {(0, "s1"): _values(3000, 1000), (1, "s2"): _values(4000, 2000)}
    raw, offsets = assemble_raw(2, obs, sc)
    assert raw.sizes == {"time": 2, "point": 2}
    assert offsets == [0.0, 1000.0]
    assert int(raw.nir.isel(time=0, point=0)) == 3000
    assert int(raw.nir.isel(time=0, point=1)) == 0  # never observed
    assert int(raw.scl.isel(time=1, point=0)) == 0
    with pytest.raises(ValueError, match="no observations"):
        assemble_raw(1, {}, sc)
    with pytest.raises(ValueError, match="expected"):
        assemble_raw(1, {(0, "s1"): [1.0, 2.0]}, sc)


def test_point_features_masks_clouds_and_counts_clear() -> None:
    days = [f"2020-12-{d:02d}" for d in (1, 6, 11, 16)]
    sc = {f"s{i}": _scene(f"s{i}", d, 1.0) for i, d in enumerate(days)}
    obs = {
        (0, "s0"): _values(3000, 1000),
        (0, "s1"): _values(3000, 1000),
        (0, "s2"): _values(9000, 9000, scl=9),  # cloud: ignored
        (0, "s3"): _values(3000, 1000),
        (1, "s0"): _values(3000, 1000),  # only one look → too few for a median
    }
    raw, offsets = assemble_raw(2, obs, sc)
    frame = point_features(raw, offsets)
    assert list(frame.columns) == [*FEATURE_VARS, "n_scenes"]
    assert frame.clear_count.tolist() == [3, 1]
    assert frame.n_scenes.tolist() == [4, 1]
    assert frame.nir[0] == pytest.approx(0.3)
    assert frame.ndvi[0] == pytest.approx(0.5)
    assert np.isnan(frame.nir[1])


def test_config_hash_is_stable_and_sensitive() -> None:
    assert config_hash({"a": 1, "b": 2}) == config_hash({"b": 2, "a": 1})
    assert config_hash({"a": 1}) != config_hash({"a": 2})


def test_throttle_spaces_requests_and_backs_off() -> None:
    th = Throttle(rate_per_s=50.0)
    t0 = time.monotonic()
    for _ in range(5):
        th.wait()
    assert time.monotonic() - t0 >= 4 * 0.02 * 0.9
    th.back_off(0.05)
    th.back_off(0.01)  # a shorter pause never shortens the current one
    assert th.pauses == 1
    t1 = time.monotonic()
    th.wait()
    assert time.monotonic() - t1 >= 0.04


def test_tiles_for_points_groups_by_first_covering_footprint() -> None:
    lons = np.array([86.5, 80.5, 86.2, 70.0])
    lats = np.array([20.5, 15.5, 20.9, 10.0])
    assert tiles_for_points([INSIDE, ELSEWHERE, INSIDE], lons, lats) == {0: [0, 2], 1: [1]}


def _dem_item(item_id: str, footprint: object) -> pystac.Item:
    item = pystac.Item(
        item_id, mapping(footprint), list(footprint.bounds), datetime(2021, 1, 1, tzinfo=UTC), {}
    )  # type: ignore[attr-defined]
    item.add_asset("data", pystac.Asset(f"https://example.invalid/{item_id}.tif"))
    return item


def test_dem_elevations_samples_each_tile_once_and_reports_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import planetary_computer

    monkeypatch.setattr(planetary_computer, "sign", lambda item: item)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    calls: list[str] = []

    def sampler(href: str, lons: Sequence[float], lats: Sequence[float]) -> list[float]:
        calls.append(href)
        if "bad" in href:
            raise OSError("HTTP 403")
        return [lon + lat for lon, lat in zip(lons, lats, strict=True)]

    items = [_dem_item("good", INSIDE), _dem_item("bad", ELSEWHERE)]
    lons = np.array([86.5, 80.5, 86.25, 0.0])
    lats = np.array([20.5, 15.5, 20.25, 0.0])
    out = dem_elevations(items, lons, lats, Throttle(1000.0), sampler=sampler)
    assert out[0] == (107.0, None)
    assert out[2] == (106.5, None)
    assert np.isnan(out[1][0])
    assert "bad" in str(out[1][1])
    assert "HTTP 403" in str(out[1][1])
    assert np.isnan(out[3][0])
    assert out[3][1] is None
    assert calls.count("https://example.invalid/good.tif") == 1
    assert calls.count("https://example.invalid/bad.tif") == 5  # retried, then recorded
