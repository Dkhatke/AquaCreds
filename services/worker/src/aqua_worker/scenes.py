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

    def search(
        self, geometry: BaseGeometry, start: date, end: date, *, max_cloud: float
    ) -> list[SceneRef]: ...

    def load(
        self, scenes: Sequence[SceneRef], geometry: BaseGeometry, *, chunk: int = 1024
    ) -> LoadedScenes:
        """Lazy (dask) or eager load clipped to `geometry`; callers call `.load()`/compute."""
        ...


class StacSceneSource:
    """Sentinel-2 L2A via STAC (Planetary Computer by default) using `aqua_geo.sentinel2`.

    Items are searched unsigned and signed immediately before each read, because Planetary
    Computer SAS tokens expire after ~45 minutes and a backfill runs longer than that.
    """

    def __init__(self, provider: "Provider | None" = None) -> None:
        from aqua_geo import sentinel2 as s2

        self._provider = provider or s2.PLANETARY_COMPUTER
        self.name = self._provider.name

    def search(
        self, geometry: BaseGeometry, start: date, end: date, *, max_cloud: float
    ) -> list[SceneRef]:
        from aqua_geo import sentinel2 as s2

        items = s2.search(
            geometry,
            start,
            end,
            provider=self._provider,
            max_cloud=max_cloud,
            limit=5000,
            sign=False,
        )
        refs = []
        for item in items:
            props = item.properties
            cloud = props.get("eo:cloud_cover")
            baseline = props.get("s2:processing_baseline")
            acquired = item.datetime or datetime.fromisoformat(props["datetime"])
            refs.append(
                SceneRef(
                    item_id=item.id,
                    acquired_at=acquired,
                    cloud_cover=float(cloud) if cloud is not None else None,
                    processing_baseline=str(baseline) if baseline is not None else None,
                    provider=self.name,
                    item=item,
                )
            )
        return refs

    def load(
        self, scenes: Sequence[SceneRef], geometry: BaseGeometry, *, chunk: int = 1024
    ) -> LoadedScenes:
        from aqua_geo import sentinel2 as s2
        from aqua_geo.indices import REQUIRED_FOR_ALL

        signed = [self._provider.sign(s.item) for s in scenes]  # sign right before reading
        ds = s2.load(
            signed,
            geometry,
            provider=self._provider,
            bands=REQUIRED_FOR_ALL,
            chunks={"x": chunk, "y": chunk},
        )
        epsg = int(ds.attrs["epsg"])
        return LoadedScenes(ds=ds, inside=s2.polygon_mask(ds["clear"], geometry, epsg), epsg=epsg)
