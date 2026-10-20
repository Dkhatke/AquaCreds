import os
from datetime import UTC, date, datetime

import numpy as np
import pystac
import pytest
import xarray as xr
from shapely.geometry import box

from aqua_geo import sentinel2 as s2
from aqua_geo.composite import median_composite, percentile_composite, zonal_summary


def _item(baseline: str, **extra: object) -> pystac.Item:
    return pystac.Item(
        id=f"S2_{baseline}",
        geometry=None,
        bbox=None,
        datetime=datetime(2024, 12, 1, tzinfo=UTC),
        properties={"s2:processing_baseline": baseline, **extra},
    )


def test_utm_zone_for_indian_coast() -> None:
    assert s2.utm_epsg(86.85, 20.7) == 32645  # Bhitarkanika
    assert s2.utm_epsg(69.0, 22.8) == 32642  # Gulf of Kutch
    assert s2.utm_epsg(-70.0, -10.0) == 32719  # southern hemisphere


def test_boa_offset_depends_on_baseline_and_provider() -> None:
    assert s2.boa_offset(_item("05.11"), s2.PLANETARY_COMPUTER) == 1000.0
    assert s2.boa_offset(_item("04.00"), s2.PLANETARY_COMPUTER) == 1000.0
    assert s2.boa_offset(_item("03.01"), s2.PLANETARY_COMPUTER) == 0.0
    # Earth Search: harmonised only where the item says so (see test_providers for recorded items)
    assert s2.boa_offset(_item("05.11"), s2.EARTH_SEARCH) == 1000.0
    assert s2.boa_offset(_item("05.11"), s2.EARTH_SEARCH_C1) == 1000.0  # C1 keeps the offset
    flagged = _item("05.11", **{"earthsearch:boa_offset_applied": True})
    assert s2.boa_offset(flagged, s2.EARTH_SEARCH) == 0.0
    assert s2.boa_offset(flagged, s2.PLANETARY_COMPUTER) == 0.0


def _raw(scl: list[list[int]], dn: int = 3000) -> xr.Dataset:
    arr = np.array(scl, dtype="uint8")[None, ...]  # (time=1, y, x)
    dims = ("time", "y", "x")
    coords = {"time": [np.datetime64("2024-12-01")], "y": [0, 1], "x": [0, 1]}
    return xr.Dataset(
        {
            "nir": (dims, np.full(arr.shape, dn, dtype="uint16")),
            "scl": (dims, arr),
        },
        coords=coords,
    )


def test_scale_and_mask_applies_offset_and_scl() -> None:
    raw = _raw([[4, 8], [3, 6]])  # vegetation, cloud, shadow, water
    out = s2.scale_and_mask(raw, offsets=[1000.0], band_names=["nir"])
    nir = out.nir.isel(time=0).values
    assert nir[0, 0] == pytest.approx(0.2)  # (3000 − 1000) / 10000
    assert np.isnan(nir[0, 1])  # cloud
    assert np.isnan(nir[1, 0])  # shadow
    assert nir[1, 1] == pytest.approx(0.2)  # water is a valid surface
    assert out.clear.isel(time=0).values.tolist() == [[True, False], [False, True]]


def test_scale_and_mask_treats_zero_dn_as_nodata() -> None:
    out = s2.scale_and_mask(_raw([[4, 4], [4, 4]], dn=0), offsets=[0.0], band_names=["nir"])
    assert np.isnan(out.nir.values).all()
    assert not out.clear.values.any()


def test_median_composite_requires_minimum_clear_observations() -> None:
    times = [np.datetime64(f"2024-12-{d:02d}") for d in (1, 6, 11, 16)]
    nir = np.array([[[0.2, 0.3]], [[0.4, np.nan]], [[0.3, np.nan]], [[np.nan, np.nan]]], "float32")
    clear = ~np.isnan(nir)
    ds = xr.Dataset(
        {"nir": (("time", "y", "x"), nir), "clear": (("time", "y", "x"), clear)},
        coords={"time": times, "y": [0], "x": [0, 1]},
    )
    comp = median_composite(ds, min_clear=3)
    assert comp.nir.values[0, 0] == pytest.approx(0.3)  # median of 0.2, 0.4, 0.3
    assert np.isnan(comp.nir.values[0, 1])  # only 1 clear observation
    assert comp.clear_count.values.tolist() == [[3, 1]]

    pct = percentile_composite(ds, ["nir"])
    assert set(pct.data_vars) == {"nir_p10", "nir_p50", "nir_p90"}


def test_zonal_summary_ignores_nan() -> None:
    da = xr.DataArray(np.array([[0.1, 0.2], [np.nan, 0.3]]), dims=("y", "x"))
    stats = zonal_summary(da)
    assert stats["valid_pixels"] == 3
    assert stats["valid_fraction"] == 0.75
    assert stats["mean"] == pytest.approx(0.2)
    empty = zonal_summary(xr.DataArray(np.full((2, 2), np.nan), dims=("y", "x")))
    assert empty["valid_pixels"] == 0


@pytest.mark.skipif(
    os.environ.get("AQUA_NETWORK_TESTS") != "1",
    reason="network test: set AQUA_NETWORK_TESTS=1 (queries Planetary Computer)",
)
def test_live_search_and_load_small_area() -> None:
    area = box(86.86, 20.70, 86.865, 20.705)  # ~0.3 km² of Bhitarkanika
    items = s2.search(area, date(2024, 12, 1), date(2025, 1, 31), max_cloud=40)
    assert items, "expected Sentinel-2 scenes over Bhitarkanika"
    ds = s2.load(items[:3], area).compute()
    assert ds.attrs["epsg"] == 32645
    values = ds.nir.values[~np.isnan(ds.nir.values)]
    assert values.size > 0
    assert 0.0 <= float(np.median(values)) <= 1.0  # surface reflectance, not DNs
