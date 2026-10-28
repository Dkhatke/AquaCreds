"""Map-tile API on real PostGIS with synthetic local COGs: TileJSON + signed tile URLs, project
visibility, palette at a known pixel, transparency outside the polygon, zoom/bounds behaviour,
ETag/304, the in-process cache and failure handling."""

import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from api_testlib import project_payload, square
from tiles_testlib import CLASSES, MANGROVE_P, hex_rgba, pixel_in_tile, rgba, tile_for, write_cogs

from aqua_api import tiles
from aqua_api.tiles import TileLayer

Z = 14
INSIDE = (86.855, 20.705)  # mangrove, inside the project square
OUTSIDE = (86.845, 20.705)  # mangrove in the COG, west of the project square (same z14 tile)


def _app(client: httpx.AsyncClient) -> Any:
    return client._transport.app  # type: ignore[attr-defined]


@pytest.fixture
def cog_dir(tmp_path: Path) -> Path:
    return tmp_path / "bucket"


@pytest.fixture(autouse=True)
def local_cogs(client: httpx.AsyncClient, cog_dir: Path) -> None:
    """Read COG keys from a local directory instead of /vsis3/."""
    app = _app(client)
    app.state.cog_locator = lambda key: str(cog_dir / key)
    app.state.cog_access = tiles.CogAccess()


async def _project(make_actor: Any, sql: Any, *, lon: float = 86.85) -> tuple[Any, uuid.UUID]:
    admin = await make_actor("NGO Admin")
    org = (
        await admin.post("/orgs", json={"name": f"Org {uuid.uuid4().hex[:4]}", "org_type": "ngo"})
    ).json()
    r = await admin.post("/projects", json=project_payload(org["id"], boundary=square(lon, 20.70)))
    assert r.status_code == 201, r.text
    pid = uuid.UUID(r.json()["id"])
    await sql("UPDATE projects SET status='submitted' WHERE id=:id", id=pid)
    return admin, pid


async def _classification(
    sql: Any, cog_dir: Path, pid: uuid.UUID, *, year: int = 2020, write: bool = True
) -> uuid.UUID:
    cid = uuid.uuid4()
    prefix = f"projects/{pid}/classifications/{year}-{(year + 1) % 100:02d}/ecosystem_classifier_v1/{cid}"
    if write:
        write_cogs(cog_dir / prefix)
    scenes = [
        {"item_id": "S2A_1", "acquired_on": f"{year}-12-01", "cloud_cover": 1.0},
        {"item_id": "S2B_2", "acquired_on": f"{year + 1}-02-10", "cloud_cover": 2.0},
    ]
    areas = dict.fromkeys(CLASSES, 0.0) | {"mangrove": 210.5, "water": 230.1}
    await sql(
        "INSERT INTO classifications (id, project_id, season_start_year, model_version, "
        "model_sha256, features_sha256, provider, scenes, dem_items, epsg, pixel_area_m2, "
        "areal_scale, polygon_pixels, classified_pixels, unclassified_pixels, classes, "
        "class_areas_ha, mangrove_ha, mangrove_mean_confidence, low_confidence_threshold, "
        "low_confidence_share, classes_cog_key, classes_cog_sha256, probability_cog_key, "
        "probability_cog_sha256) VALUES (:id, :pid, :year, 'ecosystem_classifier_v1', :m, :f, "
        "'planetary_computer', CAST(:scenes AS jsonb), '[]'::jsonb, 32645, 100.0, 0.9996, 47000, "
        "46000, 1000, CAST(:classes AS jsonb), CAST(:areas AS jsonb), 210.5, 0.88, 0.6, 0.05, "
        ":ck, :cs, :pk, :ps)",
        id=cid,
        pid=pid,
        year=year,
        m="1" * 64,
        f="2" * 64,
        scenes=json.dumps(scenes),
        classes=json.dumps(list(CLASSES)),
        areas=json.dumps(areas),
        ck=f"{prefix}/classes.tif",
        cs=uuid.uuid4().hex * 2,
        pk=f"{prefix}/max_probability.tif",
        ps=uuid.uuid4().hex * 2,
    )
    return cid


def _path(url: str) -> str:
    """TileJSON URLs are absolute (http://test/api/v1/...); the client's base is /api/v1."""
    return url.removeprefix("http://test/api/v1")


def _tile_url(tilejson: dict[str, Any], lon: float, lat: float, z: int = Z) -> str:
    x, y = tile_for(lon, lat, z)
    return _path(tilejson["tiles"][0].format(z=z, x=x, y=y))


async def test_tilejson_and_signed_class_tiles(
    client: httpx.AsyncClient, make_actor: Any, sql: Any, cog_dir: Path
) -> None:
    admin, pid = await _project(make_actor, sql)
    cid = await _classification(sql, cog_dir, pid)

    r = await admin.get(
        f"/projects/{pid}/tilejson", params={"layer": "classification", "cid": str(cid)}
    )
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "private, no-store"
    tj = r.json()
    assert tj["tilejson"] == "3.0.0"
    assert tj["scheme"] == "xyz"
    assert (tj["minzoom"], tj["maxzoom"]) == (14 - tiles.ZOOM_SPAN, 14)
    assert tj["bounds"] == pytest.approx([86.85, 20.70, 86.87, 20.72])
    assert tj["attribution"] == (
        "Contains modified Copernicus Sentinel data 2020-2021; AquaCreds ecosystem classifier v1"
    )
    assert tj["classification_id"] == str(cid)
    assert tj["layer"] == "classification"
    assert tj["legend_url"].endswith(f"/projects/{pid}/classifications/{cid}/legend")
    template = tj["tiles"][0]
    assert template.startswith(
        f"http://test/api/v1/projects/{pid}/classifications/{cid}/tiles/{{z}}/{{x}}/{{y}}.png?token="
    )
    expires = datetime.fromisoformat(tj["expires_at"]).timestamp()
    assert expires > time.time() + 3600 - 1

    # No Authorization header: the signed URL alone loads the tile.
    tile = await client.get(_tile_url(tj, *INSIDE))
    assert tile.status_code == 200, tile.text
    assert tile.headers["content-type"] == "image/png"
    assert tile.headers["cache-control"] == "private, max-age=86400, immutable"
    image = rgba(tile.content)
    x, y = tile_for(*INSIDE, Z)
    row, col = pixel_in_tile(*INSIDE, Z, x, y)
    assert tuple(int(v) for v in image[row, col]) == hex_rgba("#43C9A4")
    row, col = pixel_in_tile(*OUTSIDE, Z, x, y)
    assert image[row, col][3] == 0  # data exists there, but outside the polygon

    # Bearer auth works too (e.g. server-side rendering).
    assert (await admin.get(_tile_url(tj, *INSIDE))).status_code == 200
    # Without either: 401.
    plain = _tile_url(tj, *INSIDE).split("?")[0]
    assert (await client.get(plain)).status_code == 401


async def test_confidence_layer_and_default_classification(
    client: httpx.AsyncClient, make_actor: Any, sql: Any, cog_dir: Path
) -> None:
    admin, pid = await _project(make_actor, sql)
    assert (await admin.get(f"/projects/{pid}/tilejson")).status_code == 404  # nothing yet
    await _classification(sql, cog_dir, pid, year=2019)
    newest = await _classification(sql, cog_dir, pid, year=2020)

    r = await admin.get(f"/projects/{pid}/tilejson", params={"layer": "confidence"})
    assert r.status_code == 200, r.text
    tj = r.json()
    assert tj["classification_id"] == str(newest)
    assert f"/classifications/{newest}/confidence/{{z}}" in tj["tiles"][0]
    tile = await client.get(_tile_url(tj, *INSIDE))
    assert tile.status_code == 200
    image = rgba(tile.content)
    x, y = tile_for(*INSIDE, Z)
    row, col = pixel_in_tile(*INSIDE, Z, x, y)
    assert tuple(image[row, col]) == tuple(tiles.confidence_lut()[round(MANGROVE_P * 255)])

    # A confidence token does not open the class layer.
    swapped = _tile_url(tj, *INSIDE).replace("/confidence/", "/tiles/")
    assert (await client.get(swapped)).status_code == 403
    assert (
        await admin.get(f"/projects/{pid}/tilejson", params={"layer": "ndvi"})
    ).status_code == 422


async def test_legend(client: httpx.AsyncClient, make_actor: Any, sql: Any, cog_dir: Path) -> None:
    admin, pid = await _project(make_actor, sql)
    cid = await _classification(sql, cog_dir, pid)
    r = await admin.get(f"/projects/{pid}/classifications/{cid}/legend")
    assert r.status_code == 200, r.text
    legend = r.json()
    by_class = {c["class_name"]: c for c in legend["classes"]}
    assert [c["value"] for c in legend["classes"]] == list(range(len(CLASSES)))
    assert by_class["mangrove"] == {
        "value": 2,
        "class_name": "mangrove",
        "label": "Mangrove",
        "color": "#43C9A4",
        "area_ha": 210.5,
    }
    assert by_class["water"]["color"] == tiles.class_style("water").color
    assert legend["nodata"] == {"value": 255, "label": tiles.NODATA_LABEL, "color": None}
    assert legend["confidence"]["stops"][0] == {"value": 0.0, "color": "#0D3B32"}
    assert legend["confidence"]["low_confidence_threshold"] == 0.6
    assert legend["attribution"].startswith("Contains modified Copernicus Sentinel data 2020-2021")
    outsider = await make_actor("Outsider")
    assert (await outsider.get(f"/projects/{pid}/classifications/{cid}/legend")).status_code == 404
    other_admin, other = await _project(make_actor, sql, lon=86.90)
    r = await other_admin.get(f"/projects/{other}/classifications/{cid}/legend")
    assert r.status_code == 404  # classification of another project


async def test_tokens_are_bound_to_project_and_expire(
    client: httpx.AsyncClient, make_actor: Any, sql: Any, cog_dir: Path
) -> None:
    admin, pid = await _project(make_actor, sql)
    cid = await _classification(sql, cog_dir, pid)
    tj = (await admin.get(f"/projects/{pid}/tilejson", params={"cid": str(cid)})).json()
    url = _tile_url(tj, *INSIDE)
    token = url.split("token=")[1]

    # Project B, visible to the same user, with its own classification: A's token fails there.
    _, other = await _project(make_actor, sql, lon=86.90)
    await sql(
        "INSERT INTO memberships (user_id, org_id, role) SELECT :u, org_id, 'admin' "
        "FROM projects WHERE id=:p",
        u=admin.id,
        p=other,
    )
    other_cid = await _classification(sql, cog_dir, other)
    for bad in (
        url.replace(str(pid), str(other)),  # A's classification path under project B
        url.replace(str(pid), str(other)).replace(str(cid), str(other_cid)),
        url.replace(str(cid), str(other_cid)),
    ):
        r = await client.get(bad)
        assert r.status_code == 403, bad
        assert "invalid" in r.json()["detail"].lower()

    settings = _app(client).state.settings
    expired = tiles.sign_tile_token(
        settings,
        project_id=pid,
        classification_id=cid,
        layer=TileLayer.CLASSIFICATION,
        user_id=admin.id,
        expires=int(time.time()) - 1,
    )
    r = await client.get(url.replace(token, expired))
    assert r.status_code == 403
    assert "expired" in r.json()["detail"].lower()
    assert (await client.get(url.replace(token, "garbage"))).status_code == 403

    # A token for a user who cannot see the project (e.g. after losing membership) is refused.
    await sql("DELETE FROM memberships WHERE user_id=:u", u=admin.id)
    assert (await client.get(url)).status_code == 404
    await sql("UPDATE users SET is_active=false WHERE id=:u", u=admin.id)
    assert (await client.get(url)).status_code == 403


async def test_visibility(
    client: httpx.AsyncClient, make_actor: Any, sql: Any, cog_dir: Path
) -> None:
    admin, pid = await _project(make_actor, sql)
    cid = await _classification(sql, cog_dir, pid)
    outsider = await make_actor("Outsider")
    assert (await outsider.get(f"/projects/{pid}/tilejson")).status_code == 404
    tj = (await admin.get(f"/projects/{pid}/tilejson", params={"cid": str(cid)})).json()
    plain = _tile_url(tj, *INSIDE).split("?")[0]
    assert (await outsider.get(plain)).status_code == 404
    # A self-signed token for the outsider (as if leaked key material were absent) still needs
    # project visibility.
    settings = _app(client).state.settings
    forged = tiles.sign_tile_token(
        settings,
        project_id=pid,
        classification_id=cid,
        layer=TileLayer.CLASSIFICATION,
        user_id=outsider.id,
        expires=int(time.time()) + 600,
    )
    assert (await client.get(f"{plain}?token={forged}")).status_code == 404
    # Staff read every project (MFA not required in tests).
    verifier = await make_actor("Verifier")
    await sql("UPDATE users SET platform_role='verifier' WHERE id=:u", u=verifier.id)
    assert (await verifier.get(plain)).status_code == 200
    unknown = plain.replace(str(cid), str(uuid.uuid4()))
    assert (await admin.get(unknown)).status_code == 404
    assert (
        await admin.get(f"/projects/{pid}/tilejson", params={"cid": str(uuid.uuid4())})
    ).status_code == 404


async def test_zoom_bounds_etag_and_cache(
    client: httpx.AsyncClient, make_actor: Any, sql: Any, cog_dir: Path
) -> None:
    admin, pid = await _project(make_actor, sql)
    cid = await _classification(sql, cog_dir, pid)
    tj = (await admin.get(f"/projects/{pid}/tilejson", params={"cid": str(cid)})).json()

    assert (await client.get(_tile_url(tj, *INSIDE, z=15))).status_code == 404
    assert (await client.get(_tile_url(tj, *INSIDE, z=tj["minzoom"] - 1))).status_code == 404
    bad_xy = _path(tj["tiles"][0].format(z=14, x=2**14, y=0))
    assert (await client.get(bad_xy)).status_code == 404
    far = await client.get(_tile_url(tj, 80.0, 15.0))  # valid tile, far from the project
    assert far.status_code == 204
    assert far.content == b""
    # Low zoom: rendered from overviews.
    low = await client.get(_tile_url(tj, 86.86, 20.71, z=tj["minzoom"]))
    assert low.status_code == 200

    url = _tile_url(tj, *INSIDE)
    first = await client.get(url)
    etag = first.headers["etag"]
    again = await client.get(url, headers={"If-None-Match": etag})
    assert again.status_code == 304
    assert again.headers["etag"] == etag
    assert again.content == b""
    assert (await client.get(url, headers={"If-None-Match": '"other"'})).status_code == 200

    # Served from the in-process cache: storage is not touched again.
    app = _app(client)

    def unreachable(key: str) -> str:
        raise AssertionError("cache miss")

    app.state.cog_locator = unreachable
    cached = await client.get(url)
    assert cached.status_code == 200
    assert cached.content == first.content
    # An uncached tile now fails honestly instead of returning a blank tile.
    neighbour = _tile_url(tj, 86.868, 20.705)
    failed = await client.get(neighbour)
    assert failed.status_code == 502


async def test_missing_raster_and_unexpected_key(
    client: httpx.AsyncClient, make_actor: Any, sql: Any, cog_dir: Path
) -> None:
    admin, pid = await _project(make_actor, sql)
    cid = await _classification(sql, cog_dir, pid, write=False)
    tj = (await admin.get(f"/projects/{pid}/tilejson", params={"cid": str(cid)})).json()
    assert (await client.get(_tile_url(tj, *INSIDE))).status_code == 502

    await sql(
        "UPDATE classifications SET classes_cog_key='https://evil.example/x.tif' WHERE id=:c", c=cid
    )
    assert (await client.get(_tile_url(tj, *INSIDE))).status_code == 404
    r = await admin.get(f"/projects/{pid}/tilejson", params={"cid": str(cid)})
    assert r.status_code == 404


async def test_busy_renderer_answers_503(
    client: httpx.AsyncClient, make_actor: Any, sql: Any, cog_dir: Path, monkeypatch: Any
) -> None:
    import asyncio

    from aqua_api.routers import tiles as tile_routes

    admin, pid = await _project(make_actor, sql)
    cid = await _classification(sql, cog_dir, pid)
    tj = (await admin.get(f"/projects/{pid}/tilejson", params={"cid": str(cid)})).json()
    monkeypatch.setattr(tile_routes, "SLOT_WAIT_S", 0.05)
    _app(client).state.tile_semaphore = asyncio.Semaphore(0)  # every slot taken
    r = await client.get(_tile_url(tj, *INSIDE))
    assert r.status_code == 503
    assert r.headers["retry-after"] == "2"
