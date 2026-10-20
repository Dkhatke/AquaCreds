"""A network-free SceneSource: synthetic Sentinel-2 scenes as xarray datasets.

Each fake scene has a target NDVI over its clear pixels and a cloudy fraction (top rows are
masked like SCL clouds). The grid is 20 x 20 pixels at 10 m in UTM 45N; the last two columns lie
outside the "polygon", so 360 pixels are inside.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime

import numpy as np
import xarray as xr
from shapely.geometry.base import BaseGeometry

from aqua_geo.fallback import is_retryable
from aqua_worker.scenes import LoadedSar, LoadedScenes, SarRef, SceneRef

SIZE = 20
OUTSIDE_COLS = 2
POLYGON_PIXELS = SIZE * (SIZE - OUTSIDE_COLS)
EPSG = 32645


@dataclass(frozen=True)
class FakeScene:
    item_id: str
    when: date
    ndvi: float = 0.75
    cloudy: float = 0.0  # fraction of grid rows covered by cloud
    fail: bool = False
    cloud_cover: float = 10.0


def scene(day: str, ndvi: float = 0.75, cloudy: float = 0.0, **kw: object) -> FakeScene:
    when = date.fromisoformat(day)
    return FakeScene(f"S2_FAKE_{when:%Y%m%d}", when, ndvi, cloudy, **kw)  # type: ignore[arg-type]


@dataclass
class FakeSceneSource:
    scenes: list[FakeScene] = field(default_factory=list)
    name: str = "fake"
    loads: list[list[str]] = field(default_factory=list)  # item ids per load() call
    searches: list[tuple[date, date]] = field(default_factory=list)
    search_error: Exception | None = None
    chunked: bool = False  # return lazy dask arrays (like the real loader)
    search_errors: list[dict[str, str]] = field(default_factory=list)
    # provider that serves a scene again after a retryable read failure (None: no fallback)
    fallback_provider: str | None = None

    def search(
        self, geometry: BaseGeometry, start: date, end: date, *, max_cloud: float
    ) -> list[SceneRef]:
        self.searches.append((start, end))
        if self.search_error:
            raise self.search_error
        return [
            SceneRef(
                item_id=s.item_id,
                acquired_at=datetime(s.when.year, s.when.month, s.when.day, 5, 0, tzinfo=UTC),
                cloud_cover=s.cloud_cover,
                processing_baseline="05.11",
                provider=self.name,
                item=s,
            )
            for s in sorted(self.scenes, key=lambda s: s.when)
            if start <= s.when <= end and s.cloud_cover < max_cloud
        ]

    def fallback_for(
        self, scene: SceneRef, geometry: BaseGeometry, error: BaseException
    ) -> SceneRef | None:
        if (
            self.fallback_provider is None
            or scene.provider == self.fallback_provider
            or not is_retryable(error)
        ):
            return None
        fake: FakeScene = scene.item
        alt = replace(fake, item_id=f"{fake.item_id}_ALT", fail=False)
        return replace(scene, item_id=alt.item_id, provider=self.fallback_provider, item=alt)

    def load(
        self, scenes: Sequence[SceneRef], geometry: BaseGeometry, *, chunk: int = 1024
    ) -> LoadedScenes:
        self.loads.append([s.item_id for s in scenes])
        fakes: list[FakeScene] = [s.item for s in scenes]
        for f in fakes:
            if f.fail:
                raise TimeoutError(f"simulated read timeout for {f.item_id}")
        loaded = synthetic(fakes)
        if not self.chunked:
            return loaded
        # dask-backed like odc-stac: one chunk per time step, small spatial chunks
        size = min(chunk, 8)
        ds = loaded.ds.chunk({"time": 1, "y": size, "x": size})
        return LoadedScenes(ds=ds, inside=loaded.inside.chunk({"y": size, "x": size}), epsg=EPSG)


def synthetic(fakes: Sequence[FakeScene]) -> LoadedScenes:
    x = 500_005.0 + 10.0 * np.arange(SIZE)
    y = 2_290_005.0 - 10.0 * np.arange(SIZE)
    inside = np.ones((SIZE, SIZE), dtype=bool)
    inside[:, SIZE - OUTSIDE_COLS :] = False
    rng = np.random.default_rng(42)

    shape = (len(fakes), SIZE, SIZE)
    red = np.full(shape, 0.05, dtype="float32")
    nir = np.empty(shape, dtype="float32")
    clear = np.zeros(shape, dtype=bool)
    for t, f in enumerate(fakes):
        jitter = rng.normal(0.0, 0.005, (SIZE, SIZE))
        target = np.clip(f.ndvi + jitter, -0.9, 0.95)
        nir[t] = red[t] * (1 + target) / (1 - target)
        cloud_rows = round(f.cloudy * SIZE)
        clear[t] = inside
        clear[t, :cloud_rows, :] = False

    def band(values: np.ndarray) -> np.ndarray:
        return np.where(clear, values, np.nan).astype("float32")

    dims = ("time", "y", "x")
    ds = xr.Dataset(
        {
            "blue": (dims, band(np.full(shape, 0.04))),
            "green": (dims, band(np.full(shape, 0.08))),
            "red": (dims, band(red)),
            "rededge1": (dims, band(np.full(shape, 0.10))),
            "nir": (dims, band(nir)),
            "nir08": (dims, band(nir * 0.98)),
            "swir16": (dims, band(np.full(shape, 0.15))),
            "clear": (dims, clear),
        },
        coords={
            "time": [np.datetime64(f.when.isoformat()) for f in fakes],
            "y": y,
            "x": x,
        },
        attrs={"epsg": EPSG},
    )
    mask = xr.DataArray(inside, dims=("y", "x"), coords={"y": y, "x": x})
    return LoadedScenes(ds=ds, inside=mask, epsg=EPSG)


# ---------------------------------------------------------------- Sentinel-1


@dataclass(frozen=True)
class FakePass:
    """A synthetic Sentinel-1 pass with constant linear γ⁰ over the grid."""

    key: str
    when: datetime
    vv: float = 0.1
    vh: float = 0.02
    fail: bool = False


def sar_pass(day: str, hour: int = 12, **kw: object) -> FakePass:
    when = datetime.fromisoformat(day).replace(hour=hour, tzinfo=UTC)
    return FakePass(f"S1A_{when:%Y%m%d}_{hour:02d}", when, **kw)  # type: ignore[arg-type]


@dataclass
class FakeSarSource:
    passes: list[FakePass] = field(default_factory=list)
    name: str = "fake-sar"
    collection: str = "sentinel-1-rtc"
    search_error: Exception | None = None
    loads: list[str] = field(default_factory=list)

    def search(self, geometry: BaseGeometry, start: date, end: date) -> list[SarRef]:
        if self.search_error:
            raise self.search_error
        return [
            SarRef(
                key=p.key,
                acquired_at=p.when,
                platform="S1A",
                orbit_state="ascending",
                relative_orbit=85,
                item_ids=(f"{p.key}_rtc",),
                provider=self.name,
                item=p,
            )
            for p in sorted(self.passes, key=lambda p: p.when)
            if start <= p.when.date() <= end
        ]

    def load(self, acquisition: SarRef, geometry: BaseGeometry) -> LoadedSar:
        fake: FakePass = acquisition.item
        self.loads.append(fake.key)
        if fake.fail:
            raise TimeoutError(f"simulated RTC read timeout for {fake.key}")
        grid = synthetic([])
        inside = grid.inside
        shape = (1, SIZE, SIZE)
        dims = ("time", "y", "x")
        ds = xr.Dataset(
            {
                "vv": (dims, np.full(shape, fake.vv, dtype="float32")),
                "vh": (dims, np.full(shape, fake.vh, dtype="float32")),
            },
            coords={
                "time": [np.datetime64(fake.when.date().isoformat())],
                **{k: grid.ds.coords[k].values for k in ("y", "x")},
            },
        )
        return LoadedSar(ds=ds, inside=inside, epsg=EPSG)
