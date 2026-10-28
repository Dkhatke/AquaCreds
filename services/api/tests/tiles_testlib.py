"""Synthetic classification COGs for the map-tile tests (no network).

Both COGs are on a 10 m UTM 45N grid covering lon 86.84-86.88, lat 20.69-20.73: a margin of
0.01 deg around the default test project square (86.85-86.87, 20.70-20.72), so the tests can
check that data outside the polygon is transparent. Pixel values by pixel-centre location:

- lon < 86.86: mangrove (class 2), probability 0.9
- lon >= 86.86: water (class 5), probability 0.4
- 86.862 <= lon < 86.866 and 20.712 <= lat < 20.716: no prediction (255 / NaN)
"""

import io
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from pyproj import Transformer
from rasterio.transform import from_origin

CLASSES = (
    "built_bare",
    "cropland_aquaculture",
    "mangrove",
    "terrestrial_other",
    "tidal_flat",
    "water",
)
EPSG = 32645
WEST, SOUTH, EAST, NORTH = 86.84, 20.69, 86.88, 20.73
NODATA_BOX = (86.862, 20.712, 86.866, 20.716)
MANGROVE_P, WATER_P = 0.9, 0.4
TMS_EXTENT = 20037508.342789244


def _grid() -> tuple[rasterio.Affine, np.ndarray, np.ndarray]:
    to_utm = Transformer.from_crs(4326, EPSG, always_xy=True)
    xs, ys = to_utm.transform([WEST, EAST, WEST, EAST], [SOUTH, SOUTH, NORTH, NORTH])
    left, right = np.floor(min(xs) / 10) * 10, np.ceil(max(xs) / 10) * 10
    bottom, top = np.floor(min(ys) / 10) * 10, np.ceil(max(ys) / 10) * 10
    width, height = int((right - left) / 10), int((top - bottom) / 10)
    transform = from_origin(left, top, 10, 10)
    cols, rows = np.meshgrid(np.arange(width) + 0.5, np.arange(height) + 0.5)
    to_wgs = Transformer.from_crs(EPSG, 4326, always_xy=True)
    lon, lat = to_wgs.transform(left + cols * 10, top - rows * 10)
    return transform, np.asarray(lon), np.asarray(lat)


def write_cogs(directory: Path) -> tuple[Path, Path]:
    """Write `classes.tif` (uint8, nodata 255) and `max_probability.tif` (float32, NaN)."""
    directory.mkdir(parents=True, exist_ok=True)
    transform, lon, lat = _grid()
    mangrove = lon < 86.86
    w, s, e, n = NODATA_BOX
    hole = (lon >= w) & (lon < e) & (lat >= s) & (lat < n)
    classes = np.where(mangrove, 2, 5).astype("uint8")
    classes[hole] = 255
    prob = np.where(mangrove, MANGROVE_P, WATER_P).astype("float32")
    prob[hole] = np.nan
    profile = {
        "driver": "COG",
        "width": lon.shape[1],
        "height": lon.shape[0],
        "count": 1,
        "crs": f"EPSG:{EPSG}",
        "transform": transform,
        "compress": "DEFLATE",
        "blocksize": 256,
    }
    classes_path, prob_path = directory / "classes.tif", directory / "max_probability.tif"
    with rasterio.open(
        classes_path, "w", dtype="uint8", nodata=255, overview_resampling="nearest", **profile
    ) as dst:
        dst.write(classes, 1)
    with rasterio.open(prob_path, "w", dtype="float32", nodata=np.nan, **profile) as dst:
        dst.write(prob, 1)
    return classes_path, prob_path


def tile_for(lon: float, lat: float, z: int) -> tuple[int, int]:
    import math

    n = 2**z
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return x, y


def pixel_in_tile(lon: float, lat: float, z: int, x: int, y: int) -> tuple[int, int]:
    """(row, col) of a lon/lat inside tile z/x/y (256 px)."""
    import math

    n = 2**z
    fx = (lon + 180) / 360 * n - x
    fy = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n - y
    return int(fy * 256), int(fx * 256)


def rgba(png: bytes) -> np.ndarray:
    image = Image.open(io.BytesIO(png))
    assert image.mode == "RGBA"
    assert image.size == (256, 256)
    return np.asarray(image)


def hex_rgba(color: str) -> tuple[int, int, int, int]:
    c = color.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), 255
