"""Map-tile engine without a database: palette, ramp, zoom/bounds, signed tokens, rendering of
synthetic COGs, the tile LRU and the GDAL settings."""

import uuid
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import box
from tiles_testlib import (
    CLASSES,
    MANGROVE_P,
    hex_rgba,
    pixel_in_tile,
    rgba,
    tile_for,
    write_cogs,
)

from aqua_api import tiles
from aqua_api.tiles import TileLayer
from aqua_core.settings import Settings

POLYGON = box(86.85, 20.70, 86.87, 20.72)
Z = 14


@pytest.fixture(scope="module")
def cogs(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    return write_cogs(tmp_path_factory.mktemp("cogs"))


def _settings(**kw: object) -> Settings:
    options: dict[str, object] = {"app_env": "test", "jwt_secret": "s" * 48, **kw}
    return Settings(_env_file=None, **options)  # type: ignore[arg-type]


def _spec(layer: TileLayer, z: int, x: int, y: int) -> tiles.TileSpec:
    return tiles.TileSpec(layer, "k.tif", "a" * 64, CLASSES, "b" * 64, z, x, y)


def _render(path: Path, layer: TileLayer, lon: float = 86.855, lat: float = 20.705) -> bytes | None:
    x, y = tile_for(lon, lat, Z)
    return tiles.render_tile(
        _spec(layer, Z, x, y), str(path), tiles.footprint_mercator(POLYGON), {}
    )


# ---------------------------------------------------------------- palette / ramp


def test_palette_mangrove_is_lagoon_mint_and_colours_are_distinct() -> None:
    assert tiles.class_style("mangrove").color == "#43C9A4"
    colours = [tiles.class_style(c).color for c in CLASSES]
    assert len(set(colours)) == len(CLASSES)
    assert tiles.class_style("salt_marsh") == tiles.ClassStyle(
        "Salt marsh", tiles.UNKNOWN_CLASS_COLOR
    )
    lut = tiles.class_lut(list(CLASSES))
    assert tuple(lut[2]) == hex_rgba("#43C9A4")
    assert lut[len(CLASSES)][3] == 0  # no class → transparent
    assert lut[tiles.CLASS_NODATA][3] == 0


def test_confidence_ramp_is_single_hue_and_monotonic() -> None:
    lut = tiles.confidence_lut()
    assert tuple(lut[0]) == hex_rgba(tiles.CONFIDENCE_STOPS[0][1])
    assert tuple(lut[255]) == hex_rgba(tiles.CONFIDENCE_STOPS[-1][1])
    lightness = lut[:, :3].astype(int).sum(axis=1)
    assert np.all(np.diff(lightness) >= 0)
    assert np.all(lut[:, 3] == 255)


# ---------------------------------------------------------------- zoom / bounds


def test_zoom_range_follows_10_m_pixels() -> None:
    assert tiles.native_maxzoom(10.0, 20.71) == 14
    assert tiles.native_maxzoom(10.0, 0.0) == 14
    assert tiles.native_maxzoom(30.0, 20.71) == 13
    grid = tiles.TileGrid.for_footprint(POLYGON, 100.0)
    assert (grid.minzoom, grid.maxzoom) == (14 - tiles.ZOOM_SPAN, 14)
    assert grid.bounds == pytest.approx((86.85, 20.70, 86.87, 20.72))


def test_tile_grid_check() -> None:
    grid = tiles.TileGrid.for_footprint(POLYGON, 100.0)
    x, y = tile_for(86.86, 20.71, 14)
    assert grid.check(14, x, y) is True
    assert grid.check(15, x * 2, y * 2) is None  # deeper than the data
    assert grid.check(6, x >> 8, y >> 8) is None  # shallower than the zoom span
    assert grid.check(14, 2**14, y) is None  # not a tile
    assert grid.check(14, x, -1) is None
    assert grid.check(14, x + 5, y) is False  # valid tile, nothing there


def test_tile_bounds_agree() -> None:
    x, y = tile_for(86.86, 20.71, 14)
    west, south, east, north = tiles.tile_bounds_wgs84(14, x, y)
    assert west <= 86.86 < east
    assert south <= 20.71 < north
    left, bottom, right, top = tiles.tile_bounds_mercator(0, 0, 0)
    assert (left, top) == pytest.approx((-20037508.342789244, 20037508.342789244))
    assert (right, bottom) == pytest.approx((20037508.342789244, -20037508.342789244))


# ---------------------------------------------------------------- tokens


def test_tile_token_binds_project_classification_layer_and_expiry() -> None:
    settings = _settings()
    pid, cid, uid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    expires = tiles.token_expiry(settings, now=1_000_000)
    token = tiles.sign_tile_token(
        settings,
        project_id=pid,
        classification_id=cid,
        layer=TileLayer.CLASSIFICATION,
        user_id=uid,
        expires=expires,
    )
    ok = {"project_id": pid, "classification_id": cid, "layer": TileLayer.CLASSIFICATION}
    assert tiles.verify_tile_token(settings, token, **ok, now=1_000_000) == (uid, expires)

    bad = [
        {**ok, "project_id": uuid.uuid4()},
        {**ok, "classification_id": uuid.uuid4()},
        {**ok, "layer": TileLayer.CONFIDENCE},
    ]
    for other in bad:
        with pytest.raises(tiles.TileTokenError, match="invalid"):
            tiles.verify_tile_token(settings, token, **other, now=1_000_000)  # type: ignore[arg-type]
    with pytest.raises(tiles.TileTokenError, match="expired"):
        tiles.verify_tile_token(settings, token, **ok, now=expires)
    # A different secret, a forged user or expiry, or garbage never verify.
    with pytest.raises(tiles.TileTokenError, match="invalid"):
        tiles.verify_tile_token(_settings(jwt_secret="x" * 48), token, **ok, now=1_000_000)
    exp, _, mac = token.split(".")
    with pytest.raises(tiles.TileTokenError, match="invalid"):
        tiles.verify_tile_token(
            settings, f"{exp}.{uuid.uuid4().hex}.{mac}", **ok, now=1_000_000
        )
    with pytest.raises(tiles.TileTokenError, match="invalid"):
        tiles.verify_tile_token(
            settings, f"{int(exp) + 900}.{uid.hex}.{mac}", **ok, now=1_000_000
        )
    for garbage in ("", "a.b", "1.2.3", f"x.{uid.hex}.{mac}"):
        with pytest.raises(tiles.TileTokenError):
            tiles.verify_tile_token(settings, garbage, **ok, now=1_000_000)


def test_token_expiry_is_bucketed() -> None:
    settings = _settings(tile_token_ttl_s=3600)
    a = tiles.token_expiry(settings, now=1_000_000)
    b = tiles.token_expiry(settings, now=1_000_100)
    assert a == b
    assert a % tiles.TOKEN_BUCKET_S == 0
    assert 1_000_000 + 3600 <= a < 1_000_000 + 3600 + tiles.TOKEN_BUCKET_S


# ---------------------------------------------------------------- rendering


def test_class_tile_palette_polygon_mask_and_nodata(cogs: tuple[Path, Path]) -> None:
    png = _render(cogs[0], TileLayer.CLASSIFICATION)
    assert png is not None
    image = rgba(png)
    x, y = tile_for(86.855, 20.705, Z)

    def at(lon: float, lat: float) -> tuple[int, ...]:
        row, col = pixel_in_tile(lon, lat, Z, x, y)
        return tuple(int(v) for v in image[row, col])

    assert at(86.855, 20.705) == hex_rgba("#43C9A4")  # mangrove inside the polygon
    assert at(86.845, 20.705)[3] == 0  # mangrove data, but outside the polygon
    assert at(86.855, 20.695)[3] == 0  # south of the polygon
    # The water side is in the neighbouring tile; check it there.
    wx, wy = tile_for(86.868, 20.705, Z)
    water = tiles.render_tile(
        _spec(TileLayer.CLASSIFICATION, Z, wx, wy),
        str(cogs[0]),
        tiles.footprint_mercator(POLYGON),
        {},
    )
    assert water is not None
    wimg = rgba(water)
    row, col = pixel_in_tile(86.868, 20.705, Z, wx, wy)
    assert tuple(int(v) for v in wimg[row, col]) == hex_rgba(tiles.class_style("water").color)
    row, col = pixel_in_tile(86.864, 20.714, Z, wx, wy)
    assert wimg[row, col][3] == 0  # no prediction → transparent


def test_confidence_tile_uses_the_ramp(cogs: tuple[Path, Path]) -> None:
    png = _render(cogs[1], TileLayer.CONFIDENCE)
    assert png is not None
    image = rgba(png)
    x, y = tile_for(86.855, 20.705, Z)
    row, col = pixel_in_tile(86.855, 20.705, Z, x, y)
    expected = tiles.confidence_lut()[round(MANGROVE_P * 255)]
    assert tuple(image[row, col]) == tuple(expected)
    row, col = pixel_in_tile(86.845, 20.705, Z, x, y)
    assert image[row, col][3] == 0


def test_empty_tiles_render_as_none(cogs: tuple[Path, Path]) -> None:
    far = box(80.0, 15.0, 80.01, 15.01)  # polygon nowhere near this tile
    x, y = tile_for(86.855, 20.705, Z)
    spec = _spec(TileLayer.CLASSIFICATION, Z, x, y)
    assert tiles.render_tile(spec, str(cogs[0]), tiles.footprint_mercator(far), {}) is None
    # Polygon covers the tile but the COG does not.
    fx, fy = tile_for(80.005, 15.005, Z)
    spec = _spec(TileLayer.CLASSIFICATION, Z, fx, fy)
    assert tiles.render_tile(spec, str(cogs[0]), tiles.footprint_mercator(far), {}) is None
    # Inside the polygon and the COG, but only no-data there.
    hole = box(86.8625, 20.7125, 86.8635, 20.7135)
    hx, hy = tile_for(86.863, 20.713, Z)
    spec = _spec(TileLayer.CONFIDENCE, Z, hx, hy)
    assert tiles.render_tile(spec, str(cogs[1]), tiles.footprint_mercator(hole), {}) is None


def test_low_zoom_reads_overviews(cogs: tuple[Path, Path]) -> None:
    z = 14 - tiles.ZOOM_SPAN
    x, y = tile_for(86.86, 20.71, z)
    png = tiles.render_tile(
        _spec(TileLayer.CLASSIFICATION, z, x, y),
        str(cogs[0]),
        tiles.footprint_mercator(POLYGON),
        {},
    )
    assert png is not None
    assert rgba(png)[..., 3].any()


def test_etag_changes_with_inputs() -> None:
    a = _spec(TileLayer.CLASSIFICATION, 14, 1, 2)
    assert a.etag == _spec(TileLayer.CLASSIFICATION, 14, 1, 2).etag
    assert a.etag != _spec(TileLayer.CONFIDENCE, 14, 1, 2).etag
    assert a.etag != _spec(TileLayer.CLASSIFICATION, 14, 1, 3).etag
    assert a.etag.startswith('"') and a.etag.endswith('"')


# ---------------------------------------------------------------- cache / config


def test_tile_cache_is_an_lru_bounded_by_bytes() -> None:
    cache = tiles.TileCache(max_bytes=3 * (100 + 64))
    assert cache.get("a") == (False, None)
    for key in "abc":
        cache.put(key, b"x" * 100)
    cache.get("a")  # a is now the most recent
    cache.put("d", b"x" * 100)
    assert cache.get("b") == (False, None)
    assert cache.get("a") == (True, b"x" * 100)
    cache.put("e", None)  # empty tiles are cached too
    assert cache.get("e") == (True, None)
    cache.put("a", b"y" * 100)  # replacing keeps the byte count honest
    assert cache.get("a") == (True, b"y" * 100)
    assert len(cache) <= 3
    cache.put("huge", b"x" * 10_000)  # larger than the whole cache: not stored
    assert cache.get("huge") == (False, None)
    disabled = tiles.TileCache(0)
    disabled.put("a", b"x")
    assert disabled.get("a") == (False, None)


def test_gdal_env_keeps_the_aqua_geo_network_hardening() -> None:
    from aqua_geo.network import GDAL_NETWORK_ENV

    assert set(GDAL_NETWORK_ENV) <= set(tiles.GDAL_TILE_ENV)
    for key in ("GDAL_HTTP_CONNECTTIMEOUT", "GDAL_HTTP_TIMEOUT", "GDAL_HTTP_MAX_RETRY"):
        assert int(tiles.GDAL_TILE_ENV[key]) <= int(GDAL_NETWORK_ENV[key])


def test_s3_locator_and_env() -> None:
    settings = _settings(s3_endpoint_url="http://s3:8333", s3_bucket="b")
    assert tiles.s3_cog_locator(settings)("projects/x/a.tif") == "/vsis3/b/projects/x/a.tif"
    env = tiles.gdal_s3_env(settings)
    assert env["AWS_S3_ENDPOINT"] == "s3:8333"
    assert env["AWS_HTTPS"] == "NO"
    assert env["AWS_VIRTUAL_HOSTING"] == "FALSE"
    r2 = tiles.gdal_s3_env(_settings(s3_endpoint_url="https://acct.r2.cloudflarestorage.com"))
    assert (r2["AWS_S3_ENDPOINT"], r2["AWS_HTTPS"]) == ("acct.r2.cloudflarestorage.com", "YES")
    assert "AWS_S3_ENDPOINT" not in tiles.gdal_s3_env(_settings(s3_endpoint_url=None))


def test_only_project_classification_keys_are_read() -> None:
    pid = uuid.uuid4()
    assert tiles.valid_cog_key(f"projects/{pid}/classifications/2020-21/v1/r/classes.tif", pid)
    assert not tiles.valid_cog_key(f"projects/{uuid.uuid4()}/classifications/c.tif", pid)
    assert not tiles.valid_cog_key(f"projects/{pid}/classifications/../../x.tif", pid)
    assert not tiles.valid_cog_key("https://evil.example/x.tif", pid)
    assert not tiles.valid_cog_key(f"projects/{pid}/classifications/c.vrt", pid)
