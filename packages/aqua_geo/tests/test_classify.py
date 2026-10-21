"""Classifier inference maths: feature matrix, tile-wise prediction, areas, COGs, DEM on a grid."""

import json
from pathlib import Path
from typing import Any

import numpy as np
import pystac
import pytest
import rasterio
import xarray as xr
from rasterio.transform import from_origin

from aqua_geo import dem
from aqua_geo import sentinel2 as s2
from aqua_geo.classify import (
    NODATA_CLASS,
    PRODUCTION_FEATURES,
    ClassifiedGrid,
    ModelSpec,
    area_summary,
    classify_stack,
    grid_pixel_area_m2,
    tile_features,
    utm_areal_scale,
)
from aqua_geo.cog import write_class_cog

ROOT = Path(__file__).resolve().parents[3]
FEATURES_JSON = ROOT / "ml/artifacts/ecosystem_classifier_v1/features.json"
CLASSES = (
    "built_bare",
    "cropland_aquaculture",
    "mangrove",
    "terrestrial_other",
    "tidal_flat",
    "water",
)
EPSG = 32645


def spec(**kw: Any) -> ModelSpec:
    doc = {
        "model_version": "test_v1",
        "feature_columns": list(PRODUCTION_FEATURES),
        "classes": list(CLASSES),
        "fill_values": {"elevation": 0.0},
        "min_clear": 3,
        "positive_class": "mangrove",
    } | kw
    return ModelSpec.from_features_json(json.dumps(doc))


def stack(ny: int, nx: int, t: int = 4, *, veg_cols: int = 0, clear_t: int | None = None) -> Any:
    """Reflectance stack: the first `veg_cols` columns are vegetation, the rest water."""
    x = 500_005.0 + 10.0 * np.arange(nx)
    y = 2_290_005.0 - 10.0 * np.arange(ny)
    veg = np.zeros((ny, nx), dtype=bool)
    veg[:, :veg_cols] = True
    values = {
        # band: (vegetation, water)
        "blue": (0.03, 0.08),
        "green": (0.06, 0.07),
        "red": (0.04, 0.05),
        "rededge1": (0.10, 0.04),
        "rededge2": (0.25, 0.03),
        "rededge3": (0.30, 0.03),
        "nir": (0.35, 0.02),
        "nir08": (0.34, 0.02),
        "swir16": (0.15, 0.01),
        "swir22": (0.08, 0.005),
    }
    clear = np.ones((t, ny, nx), dtype=bool)
    if clear_t is not None:
        clear[clear_t:] = False
    data: dict[str, Any] = {}
    rng = np.random.default_rng(0)
    for band, (v, w) in values.items():
        arr = np.where(veg, v, w)[None].repeat(t, axis=0).astype("float32")
        arr = arr * (1 + rng.normal(0, 0.01, arr.shape)).astype("float32")
        data[band] = (("time", "y", "x"), np.where(clear, arr, np.nan).astype("float32"))
    data["clear"] = (("time", "y", "x"), clear)
    times = np.array([f"2020-12-{d:02d}" for d in range(1, t + 1)], dtype="datetime64[ns]")
    return xr.Dataset(data, coords={"time": times, "y": y, "x": x})


def ndvi_predictor(threshold: float = 0.0) -> Any:
    """Mangrove where NDVI > threshold, water otherwise; probability 0.9 / 0.8."""
    col = PRODUCTION_FEATURES.index("ndvi")

    def predict(m: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        mangrove = m[:, col] > threshold
        labels = np.where(mangrove, 2, 5)
        probs = np.full((len(m), 6), 0.02, dtype="float32")
        probs[np.arange(len(m)), labels] = np.where(mangrove, 0.9, 0.8)
        return labels, probs

    return predict


# ---------------------------------------------------------------- spec


def test_spec_requires_the_production_feature_order() -> None:
    assert PRODUCTION_FEATURES[-1] == "elevation"
    assert "clear_count" not in PRODUCTION_FEATURES
    assert len(PRODUCTION_FEATURES) == 29
    with pytest.raises(ValueError, match="feature columns differ"):
        spec(feature_columns=list(reversed(PRODUCTION_FEATURES)))
    with pytest.raises(ValueError, match="positive class"):
        spec(positive_class="seagrass")
    with pytest.raises(ValueError, match="unknown features"):
        spec(fill_values={"slope": 0.0})
    with pytest.raises(ValueError, match="number of classes"):
        spec(classes=["only"])
    with pytest.raises(ValueError, match="min_clear"):
        spec(min_clear=0)
    assert spec().positive_index == 2


@pytest.mark.skipif(not FEATURES_JSON.exists(), reason="DVC output ml/artifacts not present")
def test_real_features_json_matches_production_vector() -> None:
    real = ModelSpec.from_features_json(FEATURES_JSON.read_bytes())
    assert real.feature_columns == PRODUCTION_FEATURES
    assert real.classes == CLASSES
    assert real.fill_values == {"elevation": 0.0}
    assert real.min_clear == 3


# ---------------------------------------------------------------- features + prediction


def test_tile_features_fill_rule_and_validity() -> None:
    refl = stack(2, 3, veg_cols=1)
    elevation = np.array([[np.nan, 1.5, 2.0], [3.0, np.nan, 4.0]], dtype="float32")
    inside = np.array([[True, True, True], [True, True, False]])
    out = tile_features(refl, elevation, inside, spec())
    assert out.matrix.shape == (5, 29)
    assert out.matrix.dtype == np.float32
    assert out.index.tolist() == [0, 1, 2, 3, 4]
    elev_col = out.matrix[:, -1]
    assert elev_col.tolist() == [0.0, 1.5, 2.0, 3.0, 0.0]  # missing DEM → 0 m (training fill)
    assert (out.too_few_clear, out.nonfinite) == (0, 0)

    cloudy = stack(2, 3, clear_t=2)  # only 2 clear looks < min_clear 3
    none = tile_features(cloudy, elevation, inside, spec())
    assert none.matrix.shape == (0, 29)
    assert none.too_few_clear == 5
    with pytest.raises(ValueError, match="do not match"):
        tile_features(refl, elevation[:, :2], inside, spec())
    with pytest.raises(ValueError, match="expected"):
        tile_features(refl.transpose("y", "x", "time"), elevation, inside, spec())


def test_nonfinite_features_are_unclassified_not_guessed() -> None:
    refl = stack(1, 2)
    no_fill = spec(fill_values={})
    out = tile_features(refl, np.array([[np.nan, 2.0]]), np.ones((1, 2), bool), no_fill)
    assert out.index.tolist() == [1]
    assert out.nonfinite == 1


@pytest.mark.parametrize("tile", [1, 3, 7, 64])
def test_classify_stack_is_tile_invariant(tile: int) -> None:
    refl = stack(9, 11, veg_cols=4).chunk({"y": 4, "x": 4})  # lazy, like odc-stac
    inside = np.ones((9, 11), dtype=bool)
    inside[:, -2:] = False
    inside[0, 0] = False
    elevation = xr.DataArray(np.full((9, 11), 1.0, "float32"), dims=("y", "x"))
    grid = classify_stack(refl, elevation, inside, ndvi_predictor(), spec(), tile=tile)
    expected = np.where(np.arange(11) < 4, 2, 5)[None].repeat(9, 0).astype("uint8")
    expected[~inside] = NODATA_CLASS
    np.testing.assert_array_equal(grid.labels, expected)
    assert np.isnan(grid.max_probability[~inside]).all()
    assert grid.polygon_pixels == grid.classified_pixels == int(inside.sum())
    assert np.allclose(grid.max_probability[expected == 2], 0.9)


def test_classify_stack_skips_tiles_outside_the_polygon_and_checks_the_model() -> None:
    refl = stack(4, 4, veg_cols=2)
    inside = np.zeros((4, 4), dtype=bool)
    inside[0, 0] = True
    grid = classify_stack(refl, np.zeros((4, 4)), inside, ndvi_predictor(), spec(), tile=2)
    assert grid.tiles == 1  # three tiles hold no polygon pixel and are never read

    def bad_shape(m: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return np.zeros(len(m), int), np.zeros((len(m), 3), "float32")

    def bad_label(m: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return np.full(len(m), 9), np.zeros((len(m), 6), "float32")

    with pytest.raises(ValueError, match="probabilities"):
        classify_stack(refl, np.zeros((4, 4)), inside, bad_shape, spec())
    with pytest.raises(ValueError, match="outside the class list"):
        classify_stack(refl, np.zeros((4, 4)), inside, bad_label, spec())
    with pytest.raises(ValueError, match="tile"):
        classify_stack(refl, np.zeros((4, 4)), inside, ndvi_predictor(), spec(), tile=0)
    with pytest.raises(ValueError, match="inside"):
        classify_stack(refl, np.zeros((4, 4)), inside[:2], ndvi_predictor(), spec())


# ---------------------------------------------------------------- areas


def test_area_summary_known_answer() -> None:
    """10 x 10 grid of 10 m pixels; 60 pixel centres inside the polygon: 25 mangrove (5 of them
    low confidence), 30 water, 5 without a prediction."""
    inside = np.zeros((10, 10), dtype=bool)
    inside[:6, :] = True  # 60 pixels
    labels = np.full((10, 10), NODATA_CLASS, dtype="uint8")
    probs = np.full((10, 10), np.nan, dtype="float32")
    labels[:6, :] = 5
    probs[:6, :] = 0.9
    labels[:2, :] = 2  # 20 mangrove at 0.8
    probs[:2, :] = 0.8
    labels[2, :5] = 2  # 5 mangrove at 0.5 (low confidence)
    probs[2, :5] = 0.5
    labels[5, 5:] = NODATA_CLASS  # 5 unclassified
    probs[5, 5:] = np.nan
    labels[9, 9] = 2  # outside the polygon: ignored
    grid = ClassifiedGrid(labels=labels, max_probability=probs)
    scale = 0.9992
    out = area_summary(
        grid,
        inside,
        spec(),
        pixel_area_m2=100.0,
        areal_scale=scale,
        low_confidence_threshold=0.6,
    )
    assert out.polygon_pixels == 60
    assert out.class_pixels == {
        "built_bare": 0,
        "cropland_aquaculture": 0,
        "mangrove": 25,
        "terrestrial_other": 0,
        "tidal_flat": 0,
        "water": 30,
    }
    assert out.mangrove_ha == round(25 * 100 / scale / 10_000, 4) == 0.2502
    assert out.class_areas_ha["water"] == round(30 * 100 / scale / 10_000, 4)
    assert out.unclassified_pixels == 5
    assert out.unclassified_ha == round(5 * 100 / scale / 1e4, 4)
    assert out.classified_pixels == 55
    assert out.mangrove_mean_confidence == pytest.approx((20 * 0.8 + 5 * 0.5) / 25)
    assert out.low_confidence_pixels == 5
    assert out.low_confidence_share == round(5 / 55, 6)
    assert out.as_dict()["mangrove_pixels"] == 25
    with pytest.raises(ValueError, match="positive"):
        area_summary(
            grid, inside, spec(), pixel_area_m2=0, areal_scale=1, low_confidence_threshold=0.6
        )


def test_area_summary_without_mangrove_or_predictions() -> None:
    grid = ClassifiedGrid(
        labels=np.full((2, 2), NODATA_CLASS, "uint8"),
        max_probability=np.full((2, 2), np.nan, "float32"),
    )
    out = area_summary(
        grid,
        np.ones((2, 2), bool),
        spec(),
        pixel_area_m2=100,
        areal_scale=1,
        low_confidence_threshold=0.6,
    )
    assert out.mangrove_ha == 0.0
    assert out.mangrove_mean_confidence is None
    assert out.low_confidence_share == 0.0
    assert out.unclassified_pixels == 4


def test_pixel_area_and_utm_areal_scale() -> None:
    assert grid_pixel_area_m2([5.0, 15.0, 25.0], [95.0, 85.0]) == 100.0
    with pytest.raises(ValueError, match="at least 2"):
        grid_pixel_area_m2([5.0], [95.0, 85.0])
    with pytest.raises(ValueError, match="regular"):
        grid_pixel_area_m2([5.0, 15.0, 35.0], [95.0, 85.0])
    # central meridian of UTM 45N (87 E): k0² = 0.9996²
    assert utm_areal_scale(EPSG, 87.0, 20.7) == pytest.approx(0.9996**2, abs=1e-9)
    assert utm_areal_scale(EPSG, 84.0, 20.0) > 1.0  # 3° off the meridian
    with pytest.raises(ValueError, match="not a projected"):
        utm_areal_scale(4326, 87.0, 20.7)


# ---------------------------------------------------------------- COG + DEM


def test_class_cog_round_trip(tmp_path: Path) -> None:
    labels = xr.DataArray(
        np.array([[0, 2], [5, NODATA_CLASS]], dtype="uint8"),
        dims=("y", "x"),
        coords={"y": [2_290_005.0, 2_289_995.0], "x": [500_005.0, 500_015.0]},
    )
    path = tmp_path / "classes.tif"
    write_class_cog(labels, path, epsg=EPSG, classes=CLASSES)
    with rasterio.open(path) as src:
        assert src.dtypes == ("uint8",)
        assert src.nodata == 255
        assert src.crs.to_epsg() == EPSG
        assert src.read(1).tolist() == [[0, 2], [5, 255]]
        assert src.tags()["class_2"] == "mangrove"
        assert src.res == (10.0, 10.0)
    with pytest.raises(ValueError, match="dims"):
        write_class_cog(labels.T.rename({"x": "a", "y": "b"}), path, epsg=EPSG, classes=CLASSES)
    with pytest.raises(ValueError, match="too many"):
        write_class_cog(labels, path, epsg=EPSG, classes=[str(i) for i in range(300)])


def _grid_like(ny: int = 4, nx: int = 5) -> xr.DataArray:
    x = 500_005.0 + 10.0 * np.arange(nx)
    y = 2_290_005.0 - 10.0 * np.arange(ny)
    return xr.DataArray(np.ones((ny, nx), bool), dims=("y", "x"), coords={"y": y, "x": x})


def test_dem_without_tiles_is_all_missing() -> None:
    out = dem.load_on_grid([], _grid_like(), EPSG)
    assert out.shape == (4, 5)
    assert np.isnan(out.values).all()
    assert out.name == "elevation"


def _dem_item(tmp_path: Path) -> pystac.Item:
    """A local 30 m DEM COG covering the grid, values = 100 * row + col, nodata -32767 in one
    pixel, wrapped in a STAC item with the projection extension (as on Planetary Computer)."""
    data = (100 * np.arange(3)[:, None] + np.arange(3)[None, :]).astype("float32")
    data[2, 2] = -32767.0
    transform = from_origin(499_990.0, 2_290_020.0, 30.0, 30.0)
    path = tmp_path / "dem.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=3,
        width=3,
        count=1,
        dtype="float32",
        crs=f"EPSG:{EPSG}",
        transform=transform,
        nodata=-32767.0,
    ) as dst:
        dst.write(data, 1)
    item = pystac.Item(
        id="dem-test",
        geometry={
            "type": "Polygon",
            "coordinates": [[[86.9, 20.6], [87.1, 20.6], [87.1, 20.8], [86.9, 20.8], [86.9, 20.6]]],
        },
        bbox=[86.9, 20.6, 87.1, 20.8],
        datetime=None,
        properties={
            "start_datetime": "2021-04-22T00:00:00Z",
            "end_datetime": "2021-04-22T00:00:00Z",
        },
    )
    item.properties["proj:epsg"] = EPSG
    item.properties["proj:shape"] = [3, 3]
    item.properties["proj:transform"] = list(transform)[:6]
    item.add_asset(
        "data",
        pystac.Asset(
            href=str(path),
            media_type=pystac.MediaType.COG,
            roles=["data"],
            extra_fields={"raster:bands": [{"data_type": "float32", "nodata": -32767.0}]},
        ),
    )
    return item


def test_dem_nearest_on_the_10m_grid_and_nodata_is_missing(tmp_path: Path) -> None:
    like = _grid_like(6, 6)  # 60 m x 60 m: DEM pixels (30 m) cover 3 x 3 grid pixels each
    out = dem.load_on_grid([_dem_item(tmp_path)], like, EPSG).compute()
    assert out.shape == (6, 6)
    np.testing.assert_array_equal(out.y.values, like.y.values)
    # grid row 0 (y = 2 290 005) is in DEM row 0 (2 290 020 → 2 289 990); col 0 x = 500 005 is
    # in DEM col 0 (499 990 → 500 020)
    assert float(out[0, 0]) == 0.0
    assert float(out[0, 3]) == 1.0  # x = 500 035 → DEM col 1
    assert float(out[3, 0]) == 100.0  # y = 2 289 975 → DEM row 1
    assert out.dtype == np.float32
    full = dem.load_on_grid([_dem_item(tmp_path)], _grid_like(9, 9), EPSG).compute()
    assert np.isnan(float(full[8, 8]))  # the nodata pixel → NaN (classifier fills 0 m)


def test_sentinel2_bands_cover_the_model_bands() -> None:
    assert set(s2.BANDS) <= set(PRODUCTION_FEATURES)
