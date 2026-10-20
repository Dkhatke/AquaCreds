"""StacSceneSource provider fallback against a local STAC API with recorded items, the Sentinel-1
selection policy and settings (no database, no network)."""

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from geo_fakes import FakeSceneSource, sar_pass, scene, synthetic
from pydantic import ValidationError
from pystac_client.exceptions import APIError
from shapely.geometry import box
from stac_server import StacServer

from aqua_core.settings import Settings
from aqua_geo import sentinel2 as s2
from aqua_geo.fallback import AllProvidersFailedError
from aqua_worker.scenes import SarRef, SceneRef, StacSarSource, StacSceneSource
from aqua_worker.tasks import geo

AREA = box(86.86, 20.70, 86.862, 20.702)
PC_ID = "S2B_MSIL2A_20240114T044139_R033_T45QVC_20240114T081709"
ES_ID = "S2B_45QVC_20240114_0_L2A"


@pytest.fixture
def stac() -> Iterator[StacServer]:
    with StacServer().running() as server:
        yield server


def _source(stac: StacServer, *names: str) -> StacSceneSource:
    providers = {
        "planetary-computer": replace(
            s2.PLANETARY_COMPUTER, stac_url=stac.url("pc"), sign=lambda item: item
        ),
        "earth-search": replace(s2.EARTH_SEARCH, stac_url=stac.url("es")),
    }
    source = StacSceneSource(provider_names=list(names))
    source._providers = [providers[n] for n in names]
    return source


def test_search_falls_back_on_5xx_and_records_the_provider(stac: StacServer) -> None:
    stac.behaviour["pc"] = 503
    source = _source(stac, "planetary-computer", "earth-search")
    assert source.name == "planetary-computer,earth-search"
    refs = source.search(AREA, date(2024, 1, 1), date(2024, 1, 31), max_cloud=80)
    assert [(r.item_id, r.provider) for r in refs] == [(ES_ID, "earth-search")]
    assert refs[0].processing_baseline == "05.10"
    assert source.search_errors[0]["provider"] == "planetary-computer"
    assert (
        "503" in source.search_errors[0]["error"] or "simulated" in source.search_errors[0]["error"]
    )

    stac.behaviour["pc"] = "ok"  # recovered: the primary serves again, no errors recorded
    refs = source.search(AREA, date(2024, 1, 1), date(2024, 1, 31), max_cloud=80)
    assert [(r.item_id, r.provider) for r in refs] == [(PC_ID, "planetary-computer")]
    assert source.search_errors == []


def test_search_does_not_fall_back_on_a_bad_request(stac: StacServer) -> None:
    stac.behaviour["pc"] = 400
    source = _source(stac, "planetary-computer", "earth-search")
    with pytest.raises(APIError):
        source.search(AREA, date(2024, 1, 1), date(2024, 1, 31), max_cloud=80)
    assert all(cat == "pc" for cat, _ in stac.requests)  # Earth Search never asked


def test_search_fails_when_every_provider_is_down(stac: StacServer) -> None:
    stac.behaviour.update(pc=403, es=502)
    source = _source(stac, "planetary-computer", "earth-search")
    with pytest.raises(AllProvidersFailedError, match="all STAC providers failed"):
        source.search(AREA, date(2024, 1, 1), date(2024, 1, 31), max_cloud=80)
    assert len(source.search_errors) == 2


def test_reprocessed_duplicates_keep_the_highest_baseline(stac: StacServer) -> None:
    source = _source(stac, "earth-search")
    refs = source.search(AREA, date(2021, 12, 1), date(2021, 12, 31), max_cloud=101)
    assert [(r.item_id, r.processing_baseline) for r in refs] == [
        ("S2B_45QVC_20211205_1_L2A", "05.00")
    ]


def test_same_baseline_items_are_both_kept() -> None:
    when = datetime(2024, 1, 14, 5, tzinfo=UTC)
    a = SceneRef("S2B_45QVC_20240114_0_L2A", when, 1.0, "05.10", "earth-search")
    b = replace(a, item_id="S2B_45QVC_20240114_1_L2A")
    from aqua_worker.scenes import drop_reprocessed_duplicates

    assert drop_reprocessed_duplicates([a, b]) == [a, b]


def test_fallback_for_finds_the_same_acquisition_on_the_next_provider(stac: StacServer) -> None:
    source = _source(stac, "planetary-computer", "earth-search")
    (pc_ref,) = source.search(AREA, date(2024, 1, 14), date(2024, 1, 14), max_cloud=80)
    alt = source.fallback_for(pc_ref, AREA, RuntimeError("HTTP response code: 403"))
    assert alt is not None
    assert (alt.item_id, alt.provider) == (ES_ID, "earth-search")
    assert source.fallback_for(pc_ref, AREA, ValueError("bug")) is None  # not retryable
    assert source.fallback_for(alt, AREA, TimeoutError()) is None  # no later provider
    stac.behaviour["es"] = 503
    assert source.fallback_for(pc_ref, AREA, TimeoutError()) is None  # fallback also down
    other = replace(pc_ref, provider="unconfigured")
    assert source.fallback_for(other, AREA, TimeoutError()) is None


def test_load_signs_with_the_provider_that_served_the_scene(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}
    signed: list[str] = []
    pc = replace(s2.PLANETARY_COMPUTER, sign=lambda it: signed.append(f"pc:{it}") or it)
    es = replace(s2.EARTH_SEARCH, sign=lambda it: signed.append(f"es:{it}") or it)

    def fake_load(items: Any, geometry: Any, **kwargs: Any) -> Any:
        seen["provider"] = kwargs["provider"].name
        return synthetic([]).ds.assign_attrs(epsg=32645)

    monkeypatch.setattr(s2, "load", fake_load)
    monkeypatch.setattr(s2, "polygon_mask", lambda template, geometry, epsg: "MASK")
    source = StacSceneSource()
    source._providers = [pc, es]
    when = datetime(2024, 1, 14, 5, tzinfo=UTC)
    ref = SceneRef(ES_ID, when, 4.1, "05.10", "earth-search", item="ITEM")
    assert source.load([ref], AREA).inside == "MASK"
    assert (seen["provider"], signed) == ("earth-search", ["es:ITEM"])
    with pytest.raises(ValueError, match="several providers"):
        source.load([ref, replace(ref, provider="planetary-computer")], AREA)
    with pytest.raises(LookupError, match="unconfigured"):
        source.load([replace(ref, provider="gee")], AREA)
    assert source.providers == [pc, es]


def test_serve_scene_retries_on_the_fallback_provider() -> None:
    bad = scene("2024-01-10", fail=True)
    source = FakeSceneSource([bad], fallback_provider="fake-alt")
    (ref,) = source.search(AREA, date(2024, 1, 1), date(2024, 1, 31), max_cloud=80)
    served = geo._serve_scene(source, ref, AREA)
    assert (served.ref.item_id, served.ref.provider) == (f"{bad.item_id}_ALT", "fake-alt")
    assert served.fallbacks[0]["from"] == "fake"
    assert "simulated read timeout" in served.fallbacks[0]["error"]
    no_fallback = FakeSceneSource([bad])
    (ref,) = no_fallback.search(AREA, date(2024, 1, 1), date(2024, 1, 31), max_cloud=80)
    with pytest.raises(TimeoutError):
        geo._serve_scene(no_fallback, ref, AREA)


# ---------------------------------------------------------------- Sentinel-1 policy


def _ref(day: str) -> SarRef:
    p = sar_pass(day)
    return SarRef(p.key, p.when, "S1A", "ascending", 85, (p.key,), "fake-sar", p)


def test_select_sar_uses_monsoon_months_and_low_clear_windows() -> None:
    policy = geo.Sentinel1Policy(monsoon_months=(6, 7, 8, 9), clear_floor=0.3, window_days=6)
    acquisitions = [_ref(d) for d in ("2024-01-04", "2024-01-12", "2024-01-25", "2024-07-05")]
    low_clear = [(date(2024, 1, 10), "S2_CLOUDY_0110"), (date(2024, 1, 2), "S2_CLOUDY_0102")]
    chosen = geo.select_sar(acquisitions, policy, low_clear)
    assert [(a.acquired_at.date().isoformat(), why) for a, why in chosen] == [
        ("2024-01-04", "low_clear:S2_CLOUDY_0102"),  # the nearest cloudy scene wins
        ("2024-01-12", "low_clear:S2_CLOUDY_0110"),
        ("2024-07-05", "monsoon"),
    ]
    off = geo.Sentinel1Policy(monsoon_months=(), window_days=0)
    assert geo.select_sar(acquisitions, off, []) == []


def test_policy_from_settings_and_validation() -> None:
    settings = Settings(
        _env_file=None,
        geo_s1_monsoon_months="9,6,7",
        geo_s1_clear_floor=0.5,
        geo_s1_window_days=3,
    )
    policy = geo.Sentinel1Policy.from_settings(settings)
    assert policy == geo.Sentinel1Policy((6, 7, 9), 0.5, 3)
    assert policy is not None
    assert policy.as_dict()["monsoon_months"] == [6, 7, 9]
    assert geo.Sentinel1Policy.from_settings(Settings(_env_file=None, geo_s1_enabled=False)) is None
    with pytest.raises(ValidationError, match="month"):
        Settings(_env_file=None, geo_s1_monsoon_months="6,13")


def test_stac_provider_settings() -> None:
    assert Settings(_env_file=None).geo_stac_providers == ["planetary-computer", "earth-search"]
    parsed = Settings(_env_file=None, geo_stac_providers="earth-search, planetary-computer")
    assert parsed.geo_stac_providers == ["earth-search", "planetary-computer"]
    for bad in ("gee", "", "earth-search,earth-search"):
        with pytest.raises(ValidationError, match="GEO_STAC_PROVIDERS"):
            Settings(_env_file=None, geo_stac_providers=bad)


def test_stac_sar_source_wiring(monkeypatch: pytest.MonkeyPatch) -> None:
    import numpy as np
    import pystac
    import xarray as xr

    from aqua_geo import sentinel1 as s1

    item = pystac.Item(
        id="S1A_rtc",
        geometry=None,
        bbox=None,
        datetime=datetime(2024, 7, 26, 12, 20, tzinfo=UTC),
        properties={"platform": "SENTINEL-1A", "sat:absolute_orbit": 54932},
    )
    signed: list[str] = []
    monkeypatch.setattr(s1, "search", lambda geometry, start, end: [item])
    monkeypatch.setattr(s1, "sign", lambda it: signed.append(it.id) or it)
    ds = xr.Dataset(
        {"vv": (("time", "y", "x"), np.ones((1, 2, 2), "float32"))},
        coords={"time": [np.datetime64("2024-07-26")], "y": [0, 1], "x": [0, 1]},
        attrs={"epsg": 32645},
    )
    monkeypatch.setattr(s1, "load", lambda items, geometry: ds)
    monkeypatch.setattr(s2, "polygon_mask", lambda template, geometry, epsg: "MASK")

    source = StacSarSource()
    assert source.collection == "sentinel-1-rtc"
    (ref,) = source.search(AREA, date(2024, 7, 1), date(2024, 7, 31))
    assert (ref.key, ref.item_ids, ref.provider) == (
        "S1A_20240726_054932",
        ("S1A_rtc",),
        "planetary-computer",
    )
    assert signed == []  # never signed at search time
    loaded = source.load(ref, AREA)
    assert (signed, loaded.inside, loaded.epsg) == (["S1A_rtc"], "MASK", 32645)


def test_sar_statistics_of_a_fake_pass() -> None:
    from geo_fakes import FakeSarSource

    source = FakeSarSource([sar_pass("2024-07-05", vv=0.1, vh=0.01)])
    (ref,) = source.search(AREA, date(2024, 7, 1), date(2024, 7, 31))
    stats = geo._sar_statistics(source, ref, AREA)
    assert stats.valid_fraction == 1.0
    assert stats.stats["vv_db"]["p50"] == pytest.approx(-10.0, abs=1e-4)
    assert stats.stats["vh_vv_db"]["p50"] == pytest.approx(-10.0, abs=1e-4)
    assert ref.acquired_at - ref.acquired_at.replace(hour=0) == timedelta(hours=12)
