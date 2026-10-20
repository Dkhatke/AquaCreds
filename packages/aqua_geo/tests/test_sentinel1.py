"""Sentinel-1 RTC maths on synthetic γ⁰, grouping on recorded items, and an opt-in live read."""

import os
from datetime import UTC, date, datetime

import numpy as np
import pystac
import pytest
import xarray as xr
from shapely.geometry import box
from stac_server import StacServer, fixture_items

from aqua_geo import sentinel1 as s1

SIZE = 30


def _scene(vv: np.ndarray, vh: np.ndarray, *, time: bool = True) -> xr.Dataset:
    dims = ("time", "y", "x") if time else ("y", "x")
    coords: dict[str, object] = {"y": np.arange(vv.shape[-2]), "x": np.arange(vv.shape[-1])}
    if time:
        coords["time"] = [np.datetime64("2024-07-26")]
        vv, vh = vv[None], vh[None]
    return xr.Dataset({"vv": (dims, vv), "vh": (dims, vh)}, coords=coords)


def _speckled(mean: float, rng: np.random.Generator, looks: float = 4.4) -> np.ndarray:
    """Multiplicative gamma speckle with `looks` equivalent looks around `mean` (linear)."""
    return mean * rng.gamma(looks, 1.0 / looks, size=(SIZE, SIZE))


def test_db_ratio_and_rvi_formulas() -> None:
    assert s1.to_db(np.array([1.0, 0.1, 0.01])).tolist() == pytest.approx([0.0, -10.0, -20.0])
    assert np.isnan(s1.to_db(np.array([0.0, -1.0]))).all()
    assert s1.rvi(0.1, 0.02).item() == pytest.approx(4 * 0.02 / 0.12)
    assert np.isnan(s1.rvi(0.0, 0.0))
    m = s1.sar_metrics(np.array([0.1]), np.array([0.01]))
    assert m["vh_vv_ratio"][0] == pytest.approx(0.1)
    assert m["vh_vv_db"][0] == pytest.approx(-10.0)
    assert set(m) == set(s1.SAR_METRICS)


def test_mask_invalid_drops_nodata_and_non_positive() -> None:
    da = xr.DataArray(np.array([0.1, s1.NODATA, 0.0, -0.5, np.nan, np.inf], dtype="float32"))
    out = s1.mask_invalid(da).values
    assert out[0] == pytest.approx(0.1)
    assert np.isnan(out[1:]).all()


def test_speckle_median_removes_impulses_and_keeps_holes() -> None:
    arr = np.full((5, 5), 0.1)
    arr[2, 2] = 5.0  # a bright speckle impulse
    arr[0, 0] = np.nan  # a hole / outside pixel
    out = s1.speckle_median(arr)
    assert out[2, 2] == pytest.approx(0.1)  # impulse suppressed
    assert np.isnan(out[0, 0])  # holes are not filled
    assert out[0, 1] == pytest.approx(0.1)  # NaN neighbours are ignored
    assert np.array_equal(s1.speckle_median(arr, 1), arr, equal_nan=True)
    with pytest.raises(ValueError, match="odd"):
        s1.speckle_median(arr, 2)


def test_acquisition_statistics_on_synthetic_speckled_backscatter() -> None:
    rng = np.random.default_rng(7)
    vv, vh = _speckled(0.1, rng), _speckled(0.02, rng)  # -10 dB / -17 dB mangrove-like canopy
    inside = np.ones((SIZE, SIZE), dtype=bool)
    inside[:, -5:] = False  # outside the polygon...
    vv[:, -5:] = 50.0  # ...and very bright: must not leak in through the 3x3 filter
    vh[3, 3] = s1.NODATA  # a nodata pixel inside
    stats = s1.acquisition_statistics(_scene(vv, vh), inside)

    assert stats.polygon_pixels == SIZE * (SIZE - 5)
    assert stats.valid_pixels == stats.polygon_pixels - 1
    assert stats.valid_fraction == pytest.approx(round((stats.polygon_pixels - 1) / 750, 4))
    vv_db = stats.stats["vv_db"]
    assert vv_db["p50"] == pytest.approx(-10.0, abs=0.6)
    assert vv_db["p10"] < vv_db["p50"] < vv_db["p90"]
    assert vv_db["p90"] < 0  # the 50.0 outside pixels never reached the polygon
    assert stats.stats["vh_db"]["p50"] == pytest.approx(-17.0, abs=0.6)
    assert stats.stats["vh_vv_db"]["p50"] == pytest.approx(-7.0, abs=0.8)
    assert stats.stats["rvi"]["p50"] == pytest.approx(4 * 0.02 / 0.12, abs=0.08)

    # the 3x3 median narrows the speckle spread compared with no filtering
    raw = s1.acquisition_statistics(_scene(vv, vh), inside, speckle_window=1)
    spread = vv_db["p90"] - vv_db["p10"]
    assert spread < raw.stats["vv_db"]["p90"] - raw.stats["vv_db"]["p10"]


def test_acquisition_statistics_edge_cases() -> None:
    vv = np.full((4, 4), np.nan)
    stats = s1.acquisition_statistics(_scene(vv, vv, time=False), np.ones((4, 4), dtype=bool))
    assert (stats.valid_pixels, stats.valid_fraction) == (0, 0.0)
    assert stats.stats["vv_db"] == {"valid_pixels": 0}
    with pytest.raises(ValueError, match="no pixels"):
        s1.acquisition_statistics(_scene(vv, vv), np.zeros((4, 4), dtype=bool))
    two = xr.concat([_scene(vv, vv), _scene(vv, vv)], dim="time")
    with pytest.raises(ValueError, match="single time step"):
        s1.acquisition_statistics(two, np.ones((4, 4), dtype=bool))


def test_group_acquisitions_on_recorded_rtc_items() -> None:
    items = [pystac.Item.from_dict(f) for f in fixture_items("pc_sentinel-1-rtc.json")]
    groups = s1.group_acquisitions(items)
    assert [g.key for g in groups] == [
        "S1A_20240714_054757",
        "S1A_20240724_054895",
        "S1A_20240726_054932",
    ]
    assert [g.orbit_state for g in groups] == ["ascending", "descending", "ascending"]
    assert groups[0].relative_orbit == groups[2].relative_orbit  # 12-day repeat of one track
    assert groups[0].platform == "S1A"
    assert groups[0].item_ids == [items[2].id]


def test_slices_of_one_pass_are_grouped() -> None:
    def item(i: int, second: int, orbit: int) -> pystac.Item:
        return pystac.Item(
            id=f"S1A_slice_{i}",
            geometry=None,
            bbox=None,
            datetime=datetime(2024, 7, 26, 12, 20, second, tzinfo=UTC),
            properties={"platform": "SENTINEL-1A", "sat:absolute_orbit": orbit},
        )

    groups = s1.group_acquisitions([item(2, 40, 54932), item(1, 15, 54932), item(3, 59, 54940)])
    assert [g.item_ids for g in groups] == [["S1A_slice_1", "S1A_slice_2"], ["S1A_slice_3"]]


def test_search_keeps_dual_pol_items_in_time_order() -> None:
    with StacServer().running() as stac:
        items = s1.search(
            box(86.86, 20.70, 86.862, 20.702),
            date(2024, 7, 1),
            date(2024, 7, 31),
            stac_url=stac.url("pc"),
        )
    assert [i.datetime for i in items] == sorted(i.datetime for i in items)
    assert len(items) == 3


@pytest.mark.skipif(
    os.environ.get("AQUA_NETWORK_TESTS") != "1",
    reason="network test: set AQUA_NETWORK_TESTS=1 (reads ~1 ha of one Sentinel-1 RTC pass)",
)
def test_live_rtc_tiny_window() -> None:
    area = box(86.860, 20.700, 86.861, 20.701)  # ~1 ha of Bhitarkanika mangroves
    items = s1.search(area, date(2024, 7, 20), date(2024, 7, 31))
    assert items, "expected Sentinel-1 RTC passes over Bhitarkanika in July 2024"
    acquisition = s1.group_acquisitions(items)[0]
    ds = s1.load([s1.sign(i) for i in acquisition.items], area).compute()
    from aqua_geo.sentinel2 import polygon_mask

    stats = s1.acquisition_statistics(ds, polygon_mask(ds["vv"], area, ds.attrs["epsg"]))
    assert stats.valid_fraction > 0.5
    assert -30.0 < stats.stats["vv_db"]["p50"] < 5.0  # dB, not linear power
    assert stats.stats["vh_db"]["p50"] < stats.stats["vv_db"]["p50"]
