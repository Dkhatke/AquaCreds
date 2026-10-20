import time

import numpy as np
import pytest
from features_extract import (
    S2_ASSETS,
    Scene,
    Throttle,
    assemble_raw,
    config_hash,
    point_features,
    select_scenes,
)
from shapely.geometry import box

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
