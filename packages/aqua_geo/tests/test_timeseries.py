import numpy as np
import pytest
import rasterio
import xarray as xr
from shapely.geometry import box

from aqua_geo import sentinel2 as s2
from aqua_geo.cog import write_cog
from aqua_geo.timeseries import TIMESERIES_INDICES, scene_statistics

EPSG = 32645
X = 500_005.0 + 10.0 * np.arange(10)
Y = 2_290_005.0 - 10.0 * np.arange(10)


def _scene(nir: float = 0.35, red: float = 0.05, cloud_rows: int = 0) -> xr.Dataset:
    shape = (1, 10, 10)
    clear = np.ones(shape, dtype=bool)
    clear[:, :cloud_rows, :] = False

    def band(value: float) -> np.ndarray:
        return np.where(clear, value, np.nan).astype("float32")

    dims = ("time", "y", "x")
    return xr.Dataset(
        {
            "blue": (dims, band(0.04)),
            "green": (dims, band(0.08)),
            "red": (dims, band(red)),
            "rededge1": (dims, band(0.1)),
            "nir": (dims, band(nir)),
            "nir08": (dims, band(nir)),
            "swir16": (dims, band(0.15)),
            "clear": (dims, clear),
        },
        coords={"time": [np.datetime64("2025-01-10")], "y": Y, "x": X},
    )


def test_scene_statistics_clear_fraction_and_indices() -> None:
    inside = np.ones((10, 10), dtype=bool)
    inside[:, 8:] = False  # 80 polygon pixels
    stats = scene_statistics(_scene(cloud_rows=5), inside)
    assert stats.polygon_pixels == 80
    assert stats.clear_pixels == 40
    assert stats.clear_fraction == 0.5
    assert set(stats.index_stats) == set(TIMESERIES_INDICES)
    ndvi = stats.index_stats["ndvi"]
    assert ndvi["valid_pixels"] == 40
    assert ndvi["p50"] == pytest.approx((0.35 - 0.05) / (0.35 + 0.05), abs=1e-4)


def test_fully_cloudy_scene_has_zero_clear_fraction_and_no_stats() -> None:
    stats = scene_statistics(_scene(cloud_rows=10), np.ones((10, 10), dtype=bool))
    assert stats.clear_fraction == 0.0
    assert stats.index_stats["ndvi"] == {"valid_pixels": 0}


def test_scene_statistics_rejects_multiple_times_and_empty_polygons() -> None:
    two = xr.concat([_scene(), _scene()], dim="time")
    with pytest.raises(ValueError, match="single time step"):
        scene_statistics(two, np.ones((10, 10), dtype=bool))
    with pytest.raises(ValueError, match="no pixels"):
        scene_statistics(_scene(), np.zeros((10, 10), dtype=bool))


def test_mvi_is_clipped() -> None:
    scene = _scene()
    scene["swir16"] = scene["green"] + 1e-5  # MVI denominator ≈ 0 → huge ratios
    stats = scene_statistics(scene, np.ones((10, 10), dtype=bool))
    assert stats.index_stats["mvi"]["p90"] <= 10.0


def test_polygon_mask_marks_pixel_centres_inside() -> None:
    from pyproj import Transformer

    to_wgs = Transformer.from_crs(f"EPSG:{EPSG}", "EPSG:4326", always_xy=True)
    # left half of the 100 m x 100 m grid (box extends beyond the grid vertically)
    lon0, lat0 = to_wgs.transform(499_990.0, 2_289_850.0)
    lon1, lat1 = to_wgs.transform(500_050.0, 2_290_050.0)
    template = xr.DataArray(np.zeros((10, 10)), dims=("y", "x"), coords={"y": Y, "x": X})
    mask = s2.polygon_mask(template, box(lon0, lat0, lon1, lat1), EPSG)
    assert mask.dtype == bool
    assert 40 <= int(mask.sum()) <= 60
    assert bool(mask.isel(x=0).all())
    assert not bool(mask.isel(x=9).any())


def test_write_cog_names_bands(tmp_path: object) -> None:
    from pathlib import Path

    ds = xr.Dataset(
        {
            "ndvi": (("y", "x"), np.random.default_rng(0).random((10, 10)).astype("float32")),
            "mvi": (("y", "x"), np.full((10, 10), np.nan, dtype="float32")),
        },
        coords={"y": Y, "x": X},
    )
    path = Path(str(tmp_path)) / "c.tif"
    assert write_cog(ds, path, epsg=EPSG) == ["ndvi", "mvi"]
    with rasterio.open(path) as src:
        assert src.count == 2
        assert src.descriptions == ("ndvi", "mvi")
        assert src.crs.to_epsg() == EPSG
        assert src.tags(ns="IMAGE_STRUCTURE")["LAYOUT"] == "COG"
    with pytest.raises(ValueError, match="nothing"):
        write_cog(ds, path, epsg=EPSG, variables=[])
