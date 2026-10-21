"""Network-free fakes for the classifier: a 10-band Sentinel-2 stack source, a DEM source, an
in-memory object store and a tiny linear-softmax ONNX model with the production I/O contract.

Grid: 16 x 16 pixels at 10 m in UTM 45N; the last 2 columns are outside the polygon (224 pixels
inside). Each scene has `veg_cols` vegetated columns on the left, then `mixed_cols` columns with
NIR == red (NDVI exactly 0), then water.
"""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import xarray as xr
from shapely.geometry.base import BaseGeometry

from aqua_geo.classify import PRODUCTION_FEATURES
from aqua_worker.model_store import ECOSYSTEM_CLASSIFIER_V1, ModelManifest
from aqua_worker.scenes import DemRef, LoadedScenes, SceneRef

SIZE = 16
OUTSIDE_COLS = 2
POLYGON_PIXELS = SIZE * (SIZE - OUTSIDE_COLS)
EPSG = 32645
CLASSES = (
    "built_bare",
    "cropland_aquaculture",
    "mangrove",
    "terrestrial_other",
    "tidal_flat",
    "water",
)
VEG = {
    "blue": 0.03,
    "green": 0.06,
    "red": 0.04,
    "rededge1": 0.10,
    "rededge2": 0.25,
    "rededge3": 0.30,
    "nir": 0.35,
    "nir08": 0.34,
    "swir16": 0.15,
    "swir22": 0.08,
}
WATER = {
    "blue": 0.08,
    "green": 0.07,
    "red": 0.05,
    "rededge1": 0.04,
    "rededge2": 0.03,
    "rededge3": 0.03,
    "nir": 0.02,
    "nir08": 0.02,
    "swir16": 0.01,
    "swir22": 0.005,
}
MIXED = {**VEG, "nir": 0.04}  # NIR == red → NDVI exactly 0


@dataclass(frozen=True)
class StackScene:
    item_id: str
    when: date
    veg_cols: int = 6
    mixed_cols: int = 2
    cloud_cover: float = 10.0
    cloudy_rows: int = 0


def stack_scene(day: str, **kw: Any) -> StackScene:
    when = date.fromisoformat(day)
    return StackScene(f"S2_STACK_{when:%Y%m%d}", when, **kw)


def season(year: int, *, veg_cols: int = 6, n: int = 5, **kw: Any) -> list[StackScene]:
    days = [f"{year}-11-20", f"{year}-12-10", f"{year + 1}-01-05", f"{year + 1}-02-01"]
    days += [f"{year + 1}-03-0{d}" for d in range(1, 10)]
    return [stack_scene(d, veg_cols=veg_cols, **kw) for d in days[:n]]


@dataclass
class FakeStackSource:
    scenes: list[StackScene] = field(default_factory=list)
    name: str = "fake"
    search_errors: list[dict[str, str]] = field(default_factory=list)
    loads: list[dict[str, Any]] = field(default_factory=list)
    searches: list[tuple[date, date, float]] = field(default_factory=list)

    def search(
        self, geometry: BaseGeometry, start: date, end: date, *, max_cloud: float
    ) -> list[SceneRef]:
        self.searches.append((start, end, max_cloud))
        return [
            SceneRef(
                item_id=s.item_id,
                acquired_at=datetime(s.when.year, s.when.month, s.when.day, 5, tzinfo=UTC),
                cloud_cover=s.cloud_cover,
                processing_baseline="02.09",
                provider=self.name,
                item=s,
            )
            for s in sorted(self.scenes, key=lambda s: s.when)
            if start <= s.when <= end and s.cloud_cover < max_cloud
        ]

    def fallback_for(self, scene: SceneRef, geometry: BaseGeometry, error: BaseException) -> None:
        return None

    def load(
        self,
        scenes: Sequence[SceneRef],
        geometry: BaseGeometry,
        *,
        chunk: int = 1024,
        bands: Sequence[str] | None = None,
    ) -> LoadedScenes:
        self.loads.append({"items": [s.item_id for s in scenes], "chunk": chunk, "bands": bands})
        loaded = synthetic_stack([s.item for s in scenes])
        ds = loaded.ds.chunk({"time": 1, "y": min(chunk, 8), "x": min(chunk, 8)})
        return LoadedScenes(ds=ds, inside=loaded.inside, epsg=EPSG)


def synthetic_stack(fakes: Sequence[StackScene]) -> LoadedScenes:
    x = 500_005.0 + 10.0 * np.arange(SIZE)
    y = 2_290_005.0 - 10.0 * np.arange(SIZE)
    inside = np.ones((SIZE, SIZE), dtype=bool)
    inside[:, SIZE - OUTSIDE_COLS :] = False
    shape = (len(fakes), SIZE, SIZE)
    clear = np.zeros(shape, dtype=bool)
    bands = {b: np.empty(shape, dtype="float32") for b in VEG}
    for t, f in enumerate(fakes):
        col = np.arange(SIZE)
        kind = np.where(col < f.veg_cols, 0, np.where(col < f.veg_cols + f.mixed_cols, 1, 2))
        for b in VEG:
            row = np.choose(kind, [VEG[b], MIXED[b], WATER[b]]).astype("float32")
            bands[b][t] = row[None, :]
        clear[t] = inside
        clear[t, : f.cloudy_rows, :] = False
    dims = ("time", "y", "x")
    data: dict[str, Any] = {
        b: (dims, np.where(clear, v, np.nan).astype("float32")) for b, v in bands.items()
    }
    data["clear"] = (dims, clear)
    ds = xr.Dataset(
        data,
        coords={"time": [np.datetime64(f.when.isoformat()) for f in fakes], "y": y, "x": x},
        attrs={"epsg": EPSG},
    )
    mask = xr.DataArray(inside, dims=("y", "x"), coords={"y": y, "x": x})
    return LoadedScenes(ds=ds, inside=mask, epsg=EPSG)


@dataclass
class FakeDemSource:
    elevation: float | None = 2.0  # None → no tile (open sea)
    name: str = "fake-dem"
    collection: str = "cop-dem-glo-30"
    loads: int = 0

    def search(self, geometry: BaseGeometry) -> list[DemRef]:
        return [] if self.elevation is None else [DemRef("Copernicus_DSM_COG_10_N20_00_E086_00")]

    def load(
        self, tiles: Sequence[DemRef], like: xr.DataArray, epsg: int, *, chunk: int = 1024
    ) -> xr.DataArray:
        self.loads += 1
        grid = like.isel(time=0, drop=True) if "time" in like.dims else like
        value = np.nan if not tiles or self.elevation is None else self.elevation
        return xr.full_like(grid, value, dtype="float32")


@dataclass
class MemoryStorage:
    objects: dict[str, tuple[bytes, str]] = field(default_factory=dict)
    gets: list[str] = field(default_factory=list)

    def presign_put(self, key: str, content_type: str) -> str:
        return f"http://storage.test/{key}"

    def presign_get(self, key: str, filename: str | None = None) -> str:
        return f"http://storage.test/{key}?download={filename or ''}"

    def describe(self, key: str) -> Any:
        return None

    def get_bytes(self, key: str, max_bytes: int) -> bytes:
        self.gets.append(key)
        if key not in self.objects:
            raise KeyError(f"NoSuchKey: {key}")
        return self.objects[key][0]

    def put_bytes(self, key: str, data: bytes, content_type: str) -> str:
        self.objects[key] = (data, content_type)
        return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- synthetic ONNX model


def linear_weights() -> tuple[np.ndarray, np.ndarray]:
    """logit(mangrove) = 20·NDVI, logit(water) = −20·NDVI, the other classes −5: vegetation →
    mangrove (p ≈ 1), water → water (p ≈ 1), NDVI exactly 0 → a mangrove/water tie (p ≈ 0.4966;
    ArgMax keeps the first index, mangrove)."""
    n_features, n_classes = len(PRODUCTION_FEATURES), len(CLASSES)
    w = np.zeros((n_features, n_classes), dtype="float32")
    b = np.full(n_classes, -5.0, dtype="float32")
    ndvi = PRODUCTION_FEATURES.index("ndvi")
    w[ndvi, CLASSES.index("mangrove")] = 20.0
    w[ndvi, CLASSES.index("water")] = -20.0
    b[CLASSES.index("mangrove")] = 0.0
    b[CLASSES.index("water")] = 0.0
    return w, b


def linear_onnx(weights: np.ndarray, bias: np.ndarray) -> bytes:
    """input float32 [n, f] → probabilities = softmax(xW + b) [n, k], label = argmax int64 [n]."""
    from onnx import TensorProto, checker, helper, numpy_helper

    n_features, n_classes = weights.shape
    graph = helper.make_graph(
        [
            helper.make_node("MatMul", ["input", "W"], ["z"]),
            helper.make_node("Add", ["z", "B"], ["logits"]),
            helper.make_node("Softmax", ["logits"], ["probabilities"], axis=1),
            helper.make_node("ArgMax", ["probabilities"], ["label"], axis=1, keepdims=0),
        ],
        "linear_softmax",
        [helper.make_tensor_value_info("input", TensorProto.FLOAT, [None, n_features])],
        [
            helper.make_tensor_value_info("label", TensorProto.INT64, [None]),
            helper.make_tensor_value_info("probabilities", TensorProto.FLOAT, [None, n_classes]),
        ],
        initializer=[
            numpy_helper.from_array(weights.astype("float32"), "W"),
            numpy_helper.from_array(bias.astype("float32"), "B"),
        ],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    checker.check_model(model)
    data: bytes = model.SerializeToString()
    return data


def features_doc(version: str = ECOSYSTEM_CLASSIFIER_V1.version, **kw: Any) -> dict[str, Any]:
    return {
        "model_version": version,
        "feature_columns": list(PRODUCTION_FEATURES),
        "classes": list(CLASSES),
        "fill_values": {"elevation": 0.0},
        "min_clear": 3,
        "positive_class": "mangrove",
    } | kw


def synthetic_model_files(
    directory: Path, *, doc: dict[str, Any] | None = None, onnx_bytes: bytes | None = None
) -> ModelManifest:
    """Write model.onnx + features.json and return a manifest (v1 settings) pinning them."""
    directory.mkdir(parents=True, exist_ok=True)
    model = onnx_bytes if onnx_bytes is not None else linear_onnx(*linear_weights())
    features = json.dumps(doc or features_doc()).encode()
    (directory / "model.onnx").write_bytes(model)
    (directory / "features.json").write_bytes(features)
    return replace(
        ECOSYSTEM_CLASSIFIER_V1,
        prefix="models/test-classifier/v1/",
        sha256={
            "model.onnx": hashlib.sha256(model).hexdigest(),
            "features.json": hashlib.sha256(features).hexdigest(),
        },
    )
