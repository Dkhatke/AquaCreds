import numpy as np
import pytest
import xarray as xr

from aqua_geo import sentinel2 as s2
from aqua_geo.features import FEATURE_VARS, pixel_features, sample_points
from aqua_geo.indices import ndvi


def _refl(
    values: dict[str, list[float]], clear: list[bool], other_dim: str = "point"
) -> xr.Dataset:
    n = len(clear)
    dims = ("time", other_dim)
    data = {}
    for band in s2.BANDS:
        arr = np.asarray(values.get(band, [0.1] * n), dtype="float32")
        arr = np.where(clear, arr, np.nan)
        data[band] = (dims, arr[:, None])
    data["clear"] = (dims, np.asarray(clear)[:, None])
    times = np.arange(n).astype("datetime64[D]")
    return xr.Dataset(data, coords={"time": times, other_dim: [0]})


def test_feature_vars_order_and_names() -> None:
    assert FEATURE_VARS[:10] == tuple(s2.BANDS)
    assert "ndvi_p10" in FEATURE_VARS
    assert "mvi_p90" in FEATURE_VARS
    assert FEATURE_VARS[-1] == "clear_count"
    assert len(FEATURE_VARS) == len(set(FEATURE_VARS)) == 10 + 9 + 9 + 1


def test_indices_are_computed_per_scene_then_aggregated() -> None:
    nir = [0.4, 0.3, 0.5, 0.2, 0.6]
    red = [0.3, 0.05, 0.2, 0.15, 0.02]
    out = pixel_features(_refl({"nir": nir, "red": red}, [True] * 5))
    per_scene = ndvi(np.asarray(nir), np.asarray(red))
    assert float(out.ndvi[0]) == pytest.approx(np.median(per_scene), rel=1e-5)
    # median of the ratio differs from the ratio of the medians here
    assert float(out.ndvi[0]) != pytest.approx(float(ndvi(np.median(nir), np.median(red))))
    assert float(out.ndvi_p10[0]) == pytest.approx(np.quantile(per_scene, 0.1), rel=1e-5)
    assert float(out.ndvi_p90[0]) == pytest.approx(np.quantile(per_scene, 0.9), rel=1e-5)
    assert int(out.clear_count[0]) == 5
    assert list(out.data_vars) == list(FEATURE_VARS)


def test_cloudy_scenes_are_ignored_and_too_few_clear_gives_nan() -> None:
    nir = [0.4, 0.9, 0.4, 0.4]
    out = pixel_features(_refl({"nir": nir}, [True, False, True, True]))
    assert int(out.clear_count[0]) == 3
    assert float(out.nir[0]) == pytest.approx(0.4)
    sparse = pixel_features(_refl({"nir": nir}, [True, False, False, True]))
    assert int(sparse.clear_count[0]) == 2
    assert np.isnan(float(sparse.nir[0]))
    assert np.isnan(float(sparse.ndvi_p50[0]))


def test_pixel_features_validates_input() -> None:
    ds = _refl({}, [True, True, True])
    with pytest.raises(ValueError, match="time"):
        pixel_features(ds.isel(time=0))
    with pytest.raises(KeyError, match="swir16"):
        pixel_features(ds.drop_vars("swir16"))


def _grid() -> xr.Dataset:
    x = np.array([100.0, 110.0, 120.0])
    y = np.array([520.0, 510.0, 500.0])  # north-up: y decreasing
    vals = np.arange(2 * 3 * 3, dtype="float32").reshape(2, 3, 3)
    return xr.Dataset(
        {"nir": (("time", "y", "x"), vals)},
        coords={"time": np.arange(2).astype("datetime64[D]"), "y": y, "x": x},
    )


def test_sample_points_nearest_pixel() -> None:
    out = sample_points(_grid(), x=[101.0, 119.0], y=[519.0, 501.0])
    assert out.nir.dims == ("time", "point")
    assert out.nir.isel(time=0).values.tolist() == [0.0, 8.0]
    assert out.nir.isel(time=1).values.tolist() == [9.0, 17.0]
    assert "x" not in out.coords
    assert "y" not in out.coords


def test_sample_points_rejects_points_outside_and_bad_input() -> None:
    with pytest.raises(ValueError, match="outside"):
        sample_points(_grid(), x=[500.0], y=[510.0])
    with pytest.raises(ValueError, match="equal length"):
        sample_points(_grid(), x=[100.0, 110.0], y=[510.0])
    with pytest.raises(ValueError, match="no points"):
        sample_points(_grid(), x=[], y=[])
