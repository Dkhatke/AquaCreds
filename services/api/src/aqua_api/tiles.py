"""XYZ map tiles rendered from the per-project classification COGs (PR #31).

Two layers per classification, both on the Web Mercator (`WebMercatorQuad`) 256 px grid:

- `classification`: the class-index COG (uint8, nodata 255) through a categorical palette;
- `confidence`: the highest-class-probability COG (float32, NaN nodata) through a single-hue ramp.

Pixels without a prediction and pixels whose centres fall outside the project polygon are fully
transparent. Rendering uses rio-tiler (nearest-neighbour: a categorical map is never averaged).

Cheap enough for the free ARM VM because:
- zoom range and bounds come from the database (10 m pixels, project polygon), so out-of-zoom and
  out-of-bounds requests are answered without touching object storage;
- the ETag is derived from the COG's SHA-256, the polygon and the tile address, so revalidation
  (304) never renders;
- rendered tiles sit in a small in-process LRU, and a per-process semaphore bounds GDAL reads.

COGs are read only from object-storage keys recorded on the `Classification` row (never from a
client-supplied URL), through GDAL `/vsis3/` with bounded network timeouts.

Access: the TileJSON endpoint (bearer auth + project visibility) hands out tile URLs carrying an
HMAC token bound to project, classification, layer, user and expiry, so MapLibre can load tiles
without an Authorization header. Each tile request re-checks that the user is still active and can
still see the project.
"""

import base64
import hashlib
import hmac
import math
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

import numpy as np
from shapely.geometry.base import BaseGeometry

from aqua_core.settings import Settings

TILE_SIZE = 256
# Bump when the palette, ramp or masking changes: it is part of every ETag and cache key.
RENDER_VERSION = "tiles-v1"
# Overviews make low zooms cheap; below this many levels under the native zoom a 2 km project is
# a few pixels wide, so there is nothing useful to draw.
ZOOM_SPAN = 7
# Web Mercator ground resolution at the equator for zoom 0, metres per pixel (256 px tiles).
_Z0_RESOLUTION_M = 2 * math.pi * 6_378_137 / TILE_SIZE
# Token expiries are rounded up to this many seconds so repeated TileJSON fetches within the
# window return identical URLs (browser cache hits).
TOKEN_BUCKET_S = 900

# GDAL settings for tile reads. Same hardening keys as `aqua_geo.network.GDAL_NETWORK_ENV`
# (aqua-geo is a worker-only dependency, see decision D21), with tighter timeouts because a map
# tile is interactive: fail in seconds rather than retry for minutes. Pinned by a test.
GDAL_TILE_ENV: dict[str, str] = {
    "GDAL_HTTP_CONNECTTIMEOUT": "5",
    "GDAL_HTTP_TIMEOUT": "15",
    "GDAL_HTTP_MAX_RETRY": "2",
    "GDAL_HTTP_RETRY_DELAY": "1",
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.tiff,.jp2,.TIF",
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    "VSI_CACHE": "TRUE",
    "VSI_CACHE_SIZE": str(8 * 1024 * 1024),  # per open file
}
GDAL_CACHEMAX_MB = 64  # raster block cache per process (rasterio wants an int)


class TileLayer(StrEnum):
    CLASSIFICATION = "classification"
    CONFIDENCE = "confidence"


# ---------------------------------------------------------------- palette and ramp


@dataclass(frozen=True)
class ClassStyle:
    label: str
    color: str  # #RRGGBB


# Mangrove is the brand "lagoon mint"; the others are Okabe-Ito colours (distinguishable under the
# common colour-vision deficiencies), chosen to stay apart from mint and from each other.
CLASS_STYLES: dict[str, ClassStyle] = {
    "mangrove": ClassStyle("Mangrove", "#43C9A4"),
    "water": ClassStyle("Water", "#0072B2"),
    "tidal_flat": ClassStyle("Tidal flat", "#F0E442"),
    "cropland_aquaculture": ClassStyle("Cropland / aquaculture", "#CC79A7"),
    "terrestrial_other": ClassStyle("Terrestrial (other)", "#E69F00"),
    "built_bare": ClassStyle("Built-up / bare", "#D55E00"),
}
UNKNOWN_CLASS_COLOR = "#999999"
CLASS_NODATA = 255
NODATA_LABEL = "No prediction (too few clear looks); transparent"

# Single-hue (lagoon-mint hue) ramp for the highest class probability, 0 → 1.
CONFIDENCE_STOPS: tuple[tuple[float, str], ...] = (
    (0.0, "#0D3B32"),
    (0.5, "#43C9A4"),
    (1.0, "#E3F8F1"),
)


def class_style(name: str) -> ClassStyle:
    return CLASS_STYLES.get(name) or ClassStyle(
        name.replace("_", " ").capitalize(), UNKNOWN_CLASS_COLOR
    )


def _rgb(hex_color: str) -> tuple[int, int, int]:
    value = hex_color.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def class_lut(classes: list[str]) -> np.ndarray:
    """(256, 4) uint8 RGBA lookup indexed by class value; values without a class are transparent."""
    lut = np.zeros((256, 4), dtype=np.uint8)
    for index, name in enumerate(classes[:CLASS_NODATA]):
        lut[index] = (*_rgb(class_style(name).color), 255)
    return lut


def confidence_lut() -> np.ndarray:
    """(256, 4) uint8 RGBA ramp: entry i is probability i / 255, linear in sRGB between stops."""
    positions = np.linspace(0.0, 1.0, 256)
    stops = np.array([p for p, _ in CONFIDENCE_STOPS])
    colors = np.array([_rgb(c) for _, c in CONFIDENCE_STOPS], dtype=float)
    lut = np.empty((256, 4), dtype=np.uint8)
    for channel in range(3):
        lut[:, channel] = np.round(np.interp(positions, stops, colors[:, channel]))
    lut[:, 3] = 255
    return lut


# ---------------------------------------------------------------- zoom and bounds


def native_maxzoom(pixel_size_m: float, latitude: float) -> int:
    """Smallest zoom whose tile pixels are at least as fine as the COG pixels at this latitude
    (10 m pixels near 20°N → 14). MapLibre over-zooms beyond it on the client."""
    ground = _Z0_RESOLUTION_M * math.cos(math.radians(latitude))
    return max(0, min(22, math.ceil(math.log2(ground / pixel_size_m))))


@dataclass(frozen=True)
class TileGrid:
    minzoom: int
    maxzoom: int
    bounds: tuple[float, float, float, float]  # WGS84 west, south, east, north

    @classmethod
    def for_footprint(cls, footprint: BaseGeometry, pixel_area_m2: float) -> "TileGrid":
        west, south, east, north = footprint.bounds
        maxzoom = native_maxzoom(math.sqrt(pixel_area_m2), (south + north) / 2)
        return cls(max(0, maxzoom - ZOOM_SPAN), maxzoom, (west, south, east, north))

    def check(self, z: int, x: int, y: int) -> bool | None:
        """None: no such tile here (out of zoom range / invalid address → 404).
        False: a valid tile that cannot contain data (→ 204). True: render it."""
        if not (self.minzoom <= z <= self.maxzoom) or not (0 <= x < 2**z and 0 <= y < 2**z):
            return None
        west, south, east, north = tile_bounds_wgs84(z, x, y)
        bw, bs, be, bn = self.bounds
        return west < be and east > bw and south < bn and north > bs


def tile_bounds_wgs84(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    n = 2.0**z

    def lat(row: float) -> float:
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * row / n))))

    return x / n * 360.0 - 180.0, lat(y + 1), (x + 1) / n * 360.0 - 180.0, lat(y)


def tile_bounds_mercator(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    extent = math.pi * 6_378_137
    size = 2 * extent / 2**z
    return (
        -extent + x * size,
        extent - (y + 1) * size,
        -extent + (x + 1) * size,
        extent - y * size,
    )


# ---------------------------------------------------------------- signed tile tokens


class TileTokenError(ValueError):
    pass


def _token_key(settings: Settings) -> bytes:
    # Domain-separated from the JWT signing key, so a tile token can never verify as a JWT.
    secret = settings.jwt_secret.get_secret_value().encode()
    return hmac.new(secret, b"aquacreds/tile-token/v1", hashlib.sha256).digest()


def _token_mac(
    settings: Settings,
    project_id: uuid.UUID,
    classification_id: uuid.UUID,
    layer: TileLayer,
    user_id: uuid.UUID,
    expires: int,
) -> str:
    message = f"v1|{project_id}|{classification_id}|{layer.value}|{user_id}|{expires}".encode()
    digest = hmac.new(_token_key(settings), message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def token_expiry(settings: Settings, now: float | None = None) -> int:
    now = time.time() if now is None else now
    return math.ceil((now + settings.tile_token_ttl_s) / TOKEN_BUCKET_S) * TOKEN_BUCKET_S


def sign_tile_token(
    settings: Settings,
    *,
    project_id: uuid.UUID,
    classification_id: uuid.UUID,
    layer: TileLayer,
    user_id: uuid.UUID,
    expires: int,
) -> str:
    """`<expiry unix s>.<user id hex>.<base64url HMAC-SHA256>`; project, classification and layer
    come from the tile path, so the token is useless on any other project or layer."""
    mac = _token_mac(settings, project_id, classification_id, layer, user_id, expires)
    return f"{expires}.{user_id.hex}.{mac}"


def verify_tile_token(
    settings: Settings,
    token: str,
    *,
    project_id: uuid.UUID,
    classification_id: uuid.UUID,
    layer: TileLayer,
    now: float | None = None,
) -> tuple[uuid.UUID, int]:
    """The user id and expiry of a valid token; raises TileTokenError otherwise."""
    try:
        expires_s, user_hex, mac = token.split(".")
        expires = int(expires_s)
        user_id = uuid.UUID(hex=user_hex)
    except ValueError as exc:
        raise TileTokenError("malformed tile token") from exc
    expected = _token_mac(settings, project_id, classification_id, layer, user_id, expires)
    if not hmac.compare_digest(mac.encode(), expected.encode()):
        raise TileTokenError("invalid tile token")
    if expires <= (time.time() if now is None else now):
        raise TileTokenError("expired tile token")
    return user_id, expires


# ---------------------------------------------------------------- COG access


CogLocator = Callable[[str], str]


def s3_cog_locator(settings: Settings) -> CogLocator:
    """Map an object-storage key to a GDAL `/vsis3/` path in the configured bucket."""

    def locate(key: str) -> str:
        return f"/vsis3/{settings.s3_bucket}/{key}"

    return locate


def gdal_s3_env(settings: Settings) -> dict[str, str]:
    """GDAL options for `/vsis3/` against SeaweedFS (dev) or Cloudflare R2 (prod)."""
    env = {
        "AWS_ACCESS_KEY_ID": settings.s3_access_key_id,
        "AWS_SECRET_ACCESS_KEY": settings.s3_secret_access_key.get_secret_value(),
        "AWS_REGION": settings.s3_region,
        "AWS_VIRTUAL_HOSTING": "FALSE",  # path-style, like aqua_core.storage
    }
    if settings.s3_endpoint_url:
        parts = urlsplit(settings.s3_endpoint_url)
        env["AWS_S3_ENDPOINT"] = parts.netloc + parts.path.rstrip("/")
        env["AWS_HTTPS"] = "YES" if parts.scheme == "https" else "NO"
    return env


def valid_cog_key(key: str, project_id: uuid.UUID) -> bool:
    """Classification COG keys are written by the worker under the project's prefix; refuse
    anything else (defence in depth against a tampered row ever turning into an open proxy)."""
    return (
        key.startswith(f"projects/{project_id}/classifications/")
        and key.endswith(".tif")
        and ".." not in key
        and "://" not in key
    )


# ---------------------------------------------------------------- rendering


@dataclass(frozen=True)
class TileSpec:
    """Everything needed to render one tile; also its cache identity."""

    layer: TileLayer
    cog_key: str
    cog_sha256: str
    classes: tuple[str, ...]
    footprint_wkb_sha256: str
    z: int
    x: int
    y: int

    @property
    def etag(self) -> str:
        identity = "|".join(
            (
                RENDER_VERSION,
                self.layer.value,
                self.cog_sha256,
                ",".join(self.classes),
                self.footprint_wkb_sha256,
                f"{self.z}/{self.x}/{self.y}",
            )
        )
        return '"' + hashlib.sha256(identity.encode()).hexdigest()[:32] + '"'


def _polygon_mask(footprint_mercator: BaseGeometry, z: int, x: int, y: int) -> np.ndarray:
    """True where the pixel centre lies inside the project polygon."""
    from rasterio.features import geometry_mask
    from rasterio.transform import from_bounds

    transform = from_bounds(*tile_bounds_mercator(z, x, y), TILE_SIZE, TILE_SIZE)
    mask: np.ndarray = geometry_mask(
        [footprint_mercator], out_shape=(TILE_SIZE, TILE_SIZE), transform=transform, invert=True
    )
    return mask


def render_tile(
    spec: TileSpec,
    path: str,
    footprint_mercator: BaseGeometry,
    gdal_env: Mapping[str, str],
) -> bytes | None:
    """PNG bytes of one tile, or None when nothing in it is visible (outside the COG, nodata or
    outside the polygon). Blocking: call it from a worker thread."""
    import rasterio
    from rio_tiler.errors import TileOutsideBounds
    from rio_tiler.io import Reader
    from rio_tiler.utils import render

    inside = _polygon_mask(footprint_mercator, spec.z, spec.x, spec.y)
    if not inside.any():
        return None
    with (
        rasterio.Env(GDAL_CACHEMAX=GDAL_CACHEMAX_MB, **{**GDAL_TILE_ENV, **gdal_env}),
        Reader(path) as src,
    ):
        try:
            image = src.tile(
                spec.x,
                spec.y,
                spec.z,
                tilesize=TILE_SIZE,
                indexes=1,
                resampling_method="nearest",
                reproject_method="nearest",
            )
        except TileOutsideBounds:
            return None
    band = np.ma.getdata(image.array)[0]
    valid = ~np.ma.getmaskarray(image.array)[0] & inside
    if spec.layer is TileLayer.CLASSIFICATION:
        values = band.astype(np.uint8)
        valid &= values < len(spec.classes)
        lut = class_lut(list(spec.classes))
    else:
        finite = np.isfinite(band)
        valid &= finite
        values = np.round(np.clip(np.where(finite, band, 0.0), 0.0, 1.0) * 255).astype(np.uint8)
        lut = confidence_lut()
    if not valid.any():
        return None
    rgba = np.where(valid[..., None], lut[values], 0).astype(np.uint8)  # zeros compress well
    rgb = np.moveaxis(rgba[..., :3], -1, 0)
    alpha = np.where(valid, 255, 0).astype(np.uint8)
    png: bytes = render(rgb, mask=alpha, img_format="PNG")
    return png


def footprint_mercator(footprint: BaseGeometry) -> BaseGeometry:
    """The WGS84 project polygon in Web Mercator metres (for per-tile rasterisation)."""
    import shapely
    from pyproj import Transformer

    transformer = Transformer.from_crs(4326, 3857, always_xy=True)

    def to_mercator(coords: np.ndarray) -> np.ndarray:
        x, y = transformer.transform(coords[:, 0], coords[:, 1])
        return np.column_stack([x, y])

    projected: BaseGeometry = shapely.transform(footprint, to_mercator)
    return projected


# ---------------------------------------------------------------- in-process cache


class TileCache:
    """Thread-safe LRU of rendered tiles bounded by total bytes (None = an empty tile)."""

    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max_bytes
        self._items: OrderedDict[Any, bytes | None] = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()

    def get(self, key: Any) -> tuple[bool, bytes | None]:
        with self._lock:
            if key not in self._items:
                return False, None
            self._items.move_to_end(key)
            return True, self._items[key]

    @staticmethod
    def _size(value: bytes | None) -> int:
        return len(value or b"") + 64  # empty tiles count too, so they cannot grow unbounded

    def put(self, key: Any, value: bytes | None) -> None:
        size = self._size(value)
        if size > self.max_bytes:
            return
        with self._lock:
            if key in self._items:
                self._bytes -= self._size(self._items.pop(key))
            self._items[key] = value
            self._bytes += size
            while self._bytes > self.max_bytes:
                _, evicted = self._items.popitem(last=False)
                self._bytes -= self._size(evicted)

    def __len__(self) -> int:
        return len(self._items)
