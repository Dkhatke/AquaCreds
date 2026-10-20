"""STAC provider conventions (asset keys, BOA offsets, scene identity) on recorded items, and the
fallback rules, against a local STAC API (no network)."""

import os
from collections.abc import Iterator
from dataclasses import replace
from datetime import date

import numpy as np
import pystac
import pytest
import requests
from pystac_client.exceptions import APIError
from shapely.geometry import box
from stac_server import StacServer, fixture_items

from aqua_core.settings import GEO_STAC_PROVIDER_NAMES
from aqua_geo import fallback
from aqua_geo import sentinel2 as s2

AREA = box(86.86, 20.70, 86.862, 20.702)  # ~4 ha of Bhitarkanika


def _items(name: str) -> dict[str, pystac.Item]:
    return {f["id"]: pystac.Item.from_dict(f) for f in fixture_items(name)}


PC = _items("pc_sentinel-2-l2a.json")
ES = _items("es_sentinel-2-l2a.json")
C1 = _items("es_sentinel-2-c1-l2a.json")


@pytest.fixture
def stac() -> Iterator[StacServer]:
    with StacServer().running() as server:
        yield server


def _at(provider: s2.Provider, url: str) -> s2.Provider:
    return replace(provider, stac_url=url)


# ---------------------------------------------------------------- conventions


@pytest.mark.parametrize(
    ("provider", "items"),
    [(s2.PLANETARY_COMPUTER, PC), (s2.EARTH_SEARCH, ES), (s2.EARTH_SEARCH_C1, C1)],
)
def test_every_canonical_band_maps_to_a_recorded_asset(
    provider: s2.Provider, items: dict[str, pystac.Item]
) -> None:
    assert set(provider.asset_names) == {*s2.BANDS, "scl"}  # one canonical naming
    for item in items.values():
        missing = [k for k in provider.asset_names.values() if k not in item.assets]
        assert missing == [], f"{provider.name} {item.id} lacks {missing}"


def test_boa_offset_per_provider_matches_recorded_metadata() -> None:
    pc = PC["S2B_MSIL2A_20240114T044139_R033_T45QVC_20240114T081709"]
    assert s2.boa_offset(pc, s2.PLANETARY_COMPUTER) == 1000.0  # PC never harmonises
    # Earth Search sentinel-2-l2a: harmonised when flagged, else the baseline rule
    assert s2.boa_offset(ES["S2B_45QVC_20240114_0_L2A"], s2.EARTH_SEARCH) == 0.0
    assert s2.boa_offset(ES["S2B_45QVC_20220213_0_L2A"], s2.EARTH_SEARCH) == 1000.0
    assert s2.boa_offset(ES["S2B_45QVC_20211205_0_L2A"], s2.EARTH_SEARCH) == 0.0  # 03.01
    assert s2.boa_offset(ES["S2B_45QVC_20211205_1_L2A"], s2.EARTH_SEARCH) == 0.0  # 05.00 flagged
    # Collection 1 keeps the offset (its raster:bands offset is -0.1 = -1000 DN)
    c1 = C1["S2B_T45QVC_20240114T044648_L2A"]
    assert s2.boa_offset(c1, s2.EARTH_SEARCH_C1) == 1000.0
    assert c1.assets["red"].extra_fields["raster:bands"][0]["offset"] == -0.1


def test_scene_key_identifies_one_acquisition_across_catalogues() -> None:
    day = date(2024, 1, 14)
    keys = {s2.scene_key(i, day) for i in (*PC, "S2B_45QVC_20240114_0_L2A", *C1)}
    assert keys == {"S2B_45QVC_20240114"}
    assert s2.scene_key("S2A_45QVC_20240114_0_L2A", day) != "S2B_45QVC_20240114"
    assert s2.scene_key("S2_FAKE_20240114", day) == "S2_FAKE_20240114"  # no tile: id is the key


def test_provider_registry_matches_settings_and_resolves_in_order() -> None:
    assert tuple(s2.PROVIDERS) == GEO_STAC_PROVIDER_NAMES
    resolved = s2.providers_from_names(["earth-search", "planetary-computer"])
    assert [p.name for p in resolved] == ["earth-search", "planetary-computer"]
    with pytest.raises(ValueError, match="unknown STAC provider"):
        s2.providers_from_names(["planetary-computer", "gee"])
    with pytest.raises(ValueError, match="at least one"):
        s2.providers_from_names([])


# ---------------------------------------------------------------- error classification


def _api_error(status: int) -> APIError:
    err = APIError("simulated")
    err.status_code = status
    return err


def _wrapped_timeout() -> APIError:
    """What pystac-client raises on a read timeout: APIError(str(err)) without a status."""
    try:
        try:
            raise requests.ReadTimeout("HTTPSConnectionPool(host='x'): Read timed out.")
        except requests.ReadTimeout as err:
            raise APIError(str(err)) from None
    except APIError as wrapped:
        wrapped.__context__ = requests.ReadTimeout("Read timed out.")
        return wrapped


@pytest.mark.parametrize(
    ("exc", "retryable"),
    [
        (TimeoutError("read"), True),
        (requests.ConnectTimeout("connect"), True),
        (requests.ConnectionError("Max retries exceeded"), True),
        (_wrapped_timeout(), True),
        (_api_error(503), True),
        (_api_error(500), True),
        (_api_error(429), True),
        (_api_error(403), True),
        (_api_error(401), True),
        (_api_error(400), False),
        (_api_error(404), False),
        (RuntimeError("HTTP response code: 403"), True),  # GDAL / rasterio
        (RuntimeError("HTTP response code: 404"), False),
        (requests.HTTPError("403 Client Error: Forbidden for url: https://x"), True),
        (APIError("HTTPSConnectionPool(host='x', port=443): Read timed out."), True),
        (ValueError("no items to load"), False),
        (KeyError("B04"), False),
    ],
)
def test_retryable_errors(exc: BaseException, retryable: bool) -> None:
    assert fallback.is_retryable(exc) is retryable


def _read_band() -> None:
    try:
        raise TimeoutError("socket read")
    except TimeoutError as inner:
        raise RuntimeError("odc-stac: failed to read band") from inner


def test_retryable_error_found_through_the_exception_chain() -> None:
    with pytest.raises(RuntimeError) as info:
        _read_band()
    assert fallback.is_retryable(info.value)


def test_first_successful_falls_back_only_on_retryable_errors() -> None:
    providers = [s2.PLANETARY_COMPUTER, s2.EARTH_SEARCH]
    skipped: list[str] = []

    def attempt(p: s2.Provider) -> str:
        if p is s2.PLANETARY_COMPUTER:
            raise _api_error(503)
        return f"served by {p.name}"

    provider, result = fallback.first_successful(
        providers, attempt, on_fallback=lambda p, e: skipped.append(p.name)
    )
    assert (provider.name, result, skipped) == (
        "earth-search",
        "served by earth-search",
        ["planetary-computer"],
    )

    def bad_query(p: s2.Provider) -> str:
        raise _api_error(400)

    with pytest.raises(APIError):
        fallback.first_successful(providers, bad_query)

    def down(p: s2.Provider) -> str:
        raise TimeoutError(p.name)

    with pytest.raises(fallback.AllProvidersFailedError) as info:
        fallback.first_successful(providers, down)
    assert [name for name, _ in info.value.errors] == ["planetary-computer", "earth-search"]
    with pytest.raises(ValueError, match="no STAC providers"):
        fallback.first_successful([], down)


# ---------------------------------------------------------------- search over HTTP


def test_search_reads_recorded_items_and_applies_the_cloud_filter(stac: StacServer) -> None:
    es = _at(s2.EARTH_SEARCH, stac.url("es"))
    items = s2.search(
        AREA, date(2021, 12, 1), date(2024, 1, 31), provider=es, max_cloud=101, sign=False
    )
    assert [i.datetime for i in items] == sorted(i.datetime for i in items)
    assert sorted(i.id for i in items) == [
        "S2B_45QVC_20211205_0_L2A",
        "S2B_45QVC_20211205_1_L2A",
        "S2B_45QVC_20220213_0_L2A",
        "S2B_45QVC_20240114_0_L2A",
    ]
    assert all(i.collection_id == "sentinel-2-l2a" for i in items)
    few = s2.search(AREA, date(2024, 1, 14), date(2024, 1, 14), provider=es, max_cloud=1.0)
    assert few == []  # the 2024-01-14 scene has 4.1 % cloud


@pytest.mark.parametrize(("mode", "retryable"), [(503, True), (403, True), (400, False)])
def test_search_http_failures_are_classified(stac: StacServer, mode: int, retryable: bool) -> None:
    stac.behaviour["pc"] = mode
    pc = _at(s2.PLANETARY_COMPUTER, stac.url("pc"))
    with pytest.raises(APIError) as info:
        s2.search(AREA, date(2024, 1, 1), date(2024, 1, 31), provider=pc, sign=False)
    assert fallback.is_retryable(info.value) is retryable


def test_search_timeout_is_retryable(stac: StacServer, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(s2, "STAC_TIMEOUT_S", 0.3)
    stac.behaviour["pc"] = "slow"
    pc = _at(s2.PLANETARY_COMPUTER, stac.url("pc"))
    with pytest.raises(APIError) as info:
        s2.search(AREA, date(2024, 1, 1), date(2024, 1, 31), provider=pc, sign=False)
    assert fallback.is_retryable(info.value)


@pytest.mark.skipif(
    os.environ.get("AQUA_NETWORK_TESTS") != "1",
    reason="network test: set AQUA_NETWORK_TESTS=1 (reads one Earth Search scene from AWS)",
)
def test_live_earth_search_matches_planetary_computer_reflectance_scale() -> None:
    """One harmonised Earth Search scene read through the shared loader gives reflectance."""
    items = s2.search(AREA, date(2024, 1, 14), date(2024, 1, 14), provider=s2.EARTH_SEARCH)
    assert items
    assert items[0].properties.get("earthsearch:boa_offset_applied") is True
    ds = s2.load(items[:1], AREA, provider=s2.EARTH_SEARCH, bands=("red", "nir")).compute()
    values = ds.nir.values[~np.isnan(ds.nir.values)]
    assert values.size > 0
    assert 0.0 <= float(np.median(values)) <= 1.0
