"""Where scenes come from: a small interface over STAC search + polygon-clipped loading.

The geo tasks depend on `SceneSource`, not on STAC directly, so tests inject a fake source that
returns synthetic xarray datasets (no network). `StacSceneSource` is the production source.

Heavy imports (xarray, odc-stac, aqua_geo) happen inside methods: the Celery worker imports all
task modules at start-up, and workers that never serve the `geo` queue need not install them.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Protocol

from shapely.geometry.base import BaseGeometry

if TYPE_CHECKING:
    import xarray as xr

    from aqua_geo.sentinel2 import Provider


@dataclass(frozen=True)
class SceneRef:
    """Metadata of one STAC item (enough to decide whether to ingest it)."""

    item_id: str
    acquired_at: datetime
    cloud_cover: float | None
    processing_baseline: str | None
    provider: str
    item: Any = field(default=None, compare=False, repr=False)  # the pystac.Item (unsigned)


@dataclass(frozen=True)
class LoadedScenes:
    """Masked reflectance (time, y, x) with a boolean `clear` variable, plus the polygon mask
    (y, x) on the same grid and the grid's EPSG code."""

    ds: "xr.Dataset"
    inside: "xr.DataArray"
    epsg: int


class SceneSource(Protocol):
    name: str
    # Retryable search failures of providers that were skipped in the last `search` call.
    search_errors: list[dict[str, str]]

    def search(
        self, geometry: BaseGeometry, start: date, end: date, *, max_cloud: float
    ) -> list[SceneRef]: ...

    def load(
        self,
        scenes: Sequence[SceneRef],
        geometry: BaseGeometry,
        *,
        chunk: int = 1024,
        bands: Sequence[str] | None = None,
    ) -> LoadedScenes:
        """Lazy (dask) or eager load clipped to `geometry`; callers call `.load()`/compute.
        `bands` defaults to the bands every index needs (`aqua_geo.indices.REQUIRED_FOR_ALL`);
        the classifier asks for all of `aqua_geo.sentinel2.BANDS`."""
        ...

    def fallback_for(
        self, scene: SceneRef, geometry: BaseGeometry, error: BaseException
    ) -> SceneRef | None:
        """The same acquisition from the next provider after `error` while reading `scene`, or
        None if the error is not retryable or no later provider has the scene."""
        ...


def _ref(item: Any, provider: str) -> SceneRef:
    props = item.properties
    cloud = props.get("eo:cloud_cover")
    baseline = props.get("s2:processing_baseline")
    acquired = item.datetime or datetime.fromisoformat(props["datetime"])
    return SceneRef(
        item_id=item.id,
        acquired_at=acquired,
        cloud_cover=float(cloud) if cloud is not None else None,
        processing_baseline=str(baseline) if baseline is not None else None,
        provider=provider,
        item=item,
    )


def drop_reprocessed_duplicates(refs: Sequence[SceneRef]) -> list[SceneRef]:
    """Keep one item per acquisition when a catalogue lists the same scene under two processing
    baselines (Earth Search `…_0_L2A` 03.01 next to its `…_1_L2A` 05.00 reprocessing): the highest
    baseline wins. Items with the same baseline are kept (split products of one datatake)."""
    from aqua_geo.sentinel2 import scene_key

    best: dict[str, str] = {}
    for ref in refs:
        key = scene_key(ref.item_id, ref.acquired_at.date())
        best[key] = max(best.get(key, ""), ref.processing_baseline or "")
    return [
        r
        for r in refs
        if (r.processing_baseline or "") == best[scene_key(r.item_id, r.acquired_at.date())]
    ]


class StacSceneSource:
    """Sentinel-2 L2A via STAC using `aqua_geo.sentinel2`, with provider fallback.

    Providers are tried in order (default: settings `GEO_STAC_PROVIDERS`): a search that fails
    with a timeout, HTTP 5xx/429 or 401/403 moves to the next one, and a scene whose read fails
    that way is looked up again on the next provider (`fallback_for`). Every SceneRef names the
    provider that served it.

    Items are searched unsigned and signed immediately before each read, because Planetary
    Computer SAS tokens expire after ~45 minutes and a backfill runs longer than that.
    """

    def __init__(
        self,
        provider: "Provider | None" = None,
        *,
        provider_names: Sequence[str] | None = None,
    ) -> None:
        from aqua_geo import sentinel2 as s2

        if provider is not None:
            self._providers = [provider]
        elif provider_names:
            self._providers = s2.providers_from_names(provider_names)
        else:
            self._providers = [s2.PLANETARY_COMPUTER]
        self.name = ",".join(p.name for p in self._providers)
        self.search_errors: list[dict[str, str]] = []

    @property
    def providers(self) -> list["Provider"]:
        return list(self._providers)

    def _provider(self, name: str) -> "Provider":
        for p in self._providers:
            if p.name == name:
                return p
        raise LookupError(f"scene served by unconfigured provider {name!r}")

    def _search_one(
        self, provider: "Provider", geometry: BaseGeometry, start: date, end: date, max_cloud: float
    ) -> list[SceneRef]:
        from aqua_geo import sentinel2 as s2

        items = s2.search(
            geometry, start, end, provider=provider, max_cloud=max_cloud, limit=5000, sign=False
        )
        return drop_reprocessed_duplicates([_ref(item, provider.name) for item in items])

    def search(
        self, geometry: BaseGeometry, start: date, end: date, *, max_cloud: float
    ) -> list[SceneRef]:
        from aqua_geo.fallback import describe, first_successful

        errors: list[dict[str, str]] = []

        def skipped(provider: "Provider", exc: BaseException) -> None:
            errors.append({"provider": provider.name, "error": describe(exc)})

        try:
            _provider, refs = first_successful(
                self._providers,
                lambda p: self._search_one(p, geometry, start, end, max_cloud),
                on_fallback=skipped,
            )
        finally:
            self.search_errors = errors
        return refs

    def load(
        self,
        scenes: Sequence[SceneRef],
        geometry: BaseGeometry,
        *,
        chunk: int = 1024,
        bands: Sequence[str] | None = None,
    ) -> LoadedScenes:
        from aqua_geo import sentinel2 as s2
        from aqua_geo.indices import REQUIRED_FOR_ALL

        names = {s.provider for s in scenes}
        if len(names) > 1:
            raise ValueError(f"scenes from several providers cannot be loaded together: {names}")
        provider = self._provider(names.pop()) if names else self._providers[0]
        signed = [provider.sign(s.item) for s in scenes]  # sign right before reading
        ds = s2.load(
            signed,
            geometry,
            provider=provider,
            bands=tuple(bands) if bands else REQUIRED_FOR_ALL,
            chunks={"x": chunk, "y": chunk},
        )
        epsg = int(ds.attrs["epsg"])
        return LoadedScenes(ds=ds, inside=s2.polygon_mask(ds["clear"], geometry, epsg), epsg=epsg)

    def fallback_for(
        self, scene: SceneRef, geometry: BaseGeometry, error: BaseException
    ) -> SceneRef | None:
        from aqua_geo.fallback import is_retryable
        from aqua_geo.sentinel2 import scene_key

        if not is_retryable(error):
            return None
        names = [p.name for p in self._providers]
        if scene.provider not in names:
            return None
        day = scene.acquired_at.date()
        key = scene_key(scene.item_id, day)
        for provider in self._providers[names.index(scene.provider) + 1 :]:
            try:
                refs = self._search_one(provider, geometry, day, day, 100.0)
            except Exception as exc:
                if is_retryable(exc):
                    continue
                raise
            for ref in refs:
                if scene_key(ref.item_id, ref.acquired_at.date()) == key:
                    return ref
        return None


# ---------------------------------------------------------------- Sentinel-1


@dataclass(frozen=True)
class SarRef:
    """One Sentinel-1 pass (all slices of one absolute orbit) over the project."""

    key: str
    acquired_at: datetime
    platform: str
    orbit_state: str | None
    relative_orbit: int | None
    item_ids: tuple[str, ...]
    provider: str
    item: Any = field(default=None, compare=False, repr=False)  # aqua_geo Acquisition (unsigned)


@dataclass(frozen=True)
class LoadedSar:
    """Linear γ⁰ `vv` / `vh` (time=1, y, x), the polygon mask and the grid's EPSG code."""

    ds: "xr.Dataset"
    inside: "xr.DataArray"
    epsg: int


class SarSource(Protocol):
    name: str
    collection: str

    def search(self, geometry: BaseGeometry, start: date, end: date) -> list[SarRef]: ...

    def load(self, acquisition: SarRef, geometry: BaseGeometry) -> LoadedSar: ...


class StacSarSource:
    """Sentinel-1 RTC from Planetary Computer `sentinel-1-rtc` (no fallback catalogue offers RTC;
    Earth Search only has GRD, which would need our own terrain correction)."""

    name = "planetary-computer"

    def __init__(self) -> None:
        from aqua_geo import sentinel1 as s1

        self.collection = s1.COLLECTION

    def search(self, geometry: BaseGeometry, start: date, end: date) -> list[SarRef]:
        from aqua_geo import sentinel1 as s1

        return [
            SarRef(
                key=a.key,
                acquired_at=a.acquired_at,
                platform=a.platform,
                orbit_state=a.orbit_state,
                relative_orbit=a.relative_orbit,
                item_ids=tuple(a.item_ids),
                provider=self.name,
                item=a,
            )
            for a in s1.group_acquisitions(s1.search(geometry, start, end))
        ]

    def load(self, acquisition: SarRef, geometry: BaseGeometry) -> LoadedSar:
        from aqua_geo import sentinel1 as s1
        from aqua_geo import sentinel2 as s2

        signed = [s1.sign(item) for item in acquisition.item.items]  # sign right before reading
        ds = s1.load(signed, geometry)
        epsg = int(ds.attrs["epsg"])
        return LoadedSar(ds=ds, inside=s2.polygon_mask(ds["vv"], geometry, epsg), epsg=epsg)


# ---------------------------------------------------------------- Copernicus DEM


@dataclass(frozen=True)
class DemRef:
    item_id: str
    item: Any = field(default=None, compare=False, repr=False)  # the pystac.Item (unsigned)


class DemSource(Protocol):
    name: str
    collection: str

    def search(self, geometry: BaseGeometry) -> list[DemRef]:
        """DEM tiles over `geometry`; empty over open sea (elevation then falls back to 0 m)."""
        ...

    def load(
        self, tiles: Sequence[DemRef], like: "xr.DataArray", epsg: int, *, chunk: int = 1024
    ) -> "xr.DataArray":
        """Elevation (m, NaN where missing) on the (y, x) grid of `like`."""
        ...


class StacDemSource:
    """Copernicus DEM GLO-30 (`cop-dem-glo-30`) from Planetary Computer, the training source."""

    name = "planetary-computer"

    def __init__(self) -> None:
        from aqua_geo import dem

        self.collection = dem.COLLECTION

    def search(self, geometry: BaseGeometry) -> list[DemRef]:
        from aqua_geo import dem

        return [DemRef(item.id, item) for item in dem.search(geometry)]

    def load(
        self, tiles: Sequence[DemRef], like: "xr.DataArray", epsg: int, *, chunk: int = 1024
    ) -> "xr.DataArray":
        from aqua_geo import dem

        signed = [dem.sign(t.item) for t in tiles]  # sign right before reading
        return dem.load_on_grid(signed, like, epsg, chunk=chunk)
