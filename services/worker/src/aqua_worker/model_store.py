"""Ecosystem-classifier distribution: model files in object storage, pinned by SHA-256, cached on disk.

Why object storage and not baked into the image: the model files are DVC outputs without a DVC
remote, so they are not in git; the worker image is built by CI from a git checkout (and
`.dockerignore` excludes `*.onnx`), so a baked image could not be rebuilt from the repository.
Pinning every file's SHA-256 here, in git, gives the same guarantee as baking: a worker only ever
runs the exact bytes that were evaluated in `ml/model_cards/ecosystem_classifier_v1.md`, and the
image stays reproducible. R2 (production) and SeaweedFS (development) are already the platform's
object stores (decision D9), so this costs nothing extra.

Flow:
1. Publish (operator, once per model version, from a machine that has the DVC outputs)::

       uv run python -m aqua_worker.model_store publish ml/artifacts/ecosystem_classifier_v1

   verifies the local files against the pinned hashes, then uploads them to
   `models/ecosystem_classifier/v1/`. A file that does not match is refused.
2. Worker start (`worker_ready`, workers consuming the `ml` queue): `fetch` downloads missing
   files into `ML_MODEL_CACHE_DIR`, verifies them, and logs the result.
3. Every classification: `get_model` re-verifies the cached files (1.4 MB, milliseconds), loads one
   ONNX Runtime session per process, and every stored prediction records the model version and
   the SHA-256 of `model.onnx` and `features.json`.
"""

import argparse
import hashlib
import os
import sys
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from aqua_core.logging import get_logger
from aqua_core.storage import ObjectStorage
from aqua_geo.classify import ModelSpec

log = get_logger("aqua_worker.model_store")

MAX_MODEL_FILE_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class ModelManifest:
    """Everything the worker needs to know about one published model version."""

    version: str
    prefix: str  # object-storage key prefix (ends with "/")
    model_file: str
    features_file: str
    sha256: Mapping[str, str]  # file name → SHA-256 (hex)
    # Scene stack the model was trained on (features_extract.py config).
    season_months: tuple[int, ...]
    max_scenes: int
    max_tile_cloud_pct: float
    # Held-out (Gulf of Kutch) metrics from the model card, quoted in MRV reports.
    heldout_metrics: Mapping[str, Any] = field(default_factory=dict)
    model_card: str = ""

    @property
    def files(self) -> tuple[str, ...]:
        return tuple(self.sha256)


ECOSYSTEM_CLASSIFIER_V1 = ModelManifest(
    version="ecosystem_classifier_v1",
    prefix="models/ecosystem_classifier/v1/",
    model_file="model.onnx",
    features_file="features.json",
    sha256={
        "model.onnx": "0ae00736ae70770c4a2ee5f9a2e649d0ad246f909036054a93d434717c063ed7",
        "features.json": "d492251ff3197c34268802b2503ea934db0fab16644196ca01b51faa04f84895",
    },
    season_months=(11, 12, 1, 2, 3),
    max_scenes=12,
    max_tile_cloud_pct=80.0,
    # Copied from ml/model_cards/ecosystem_classifier_v1_metrics.json (models.lightgbm.heldout,
    # rounded to 4 decimals); test_model_store checks they still match that file.
    heldout_metrics={
        "region": "Gulf of Kutch (68.0-70.8 E, 22-24 N), never used for training or tuning",
        "points": 1133,
        "macro_f1": 0.6378,
        "accuracy": 0.7546,
        "mangrove_precision": 0.9825,
        "mangrove_recall": 0.9106,
        "mangrove_f1": 0.9451,
        "brier": 0.3144,
        "ece": 0.0807,
        "label_note": "agreement with map-derived labels, not a manually checked gold set",
    },
    model_card="ml/model_cards/ecosystem_classifier_v1.md",
)


class ModelUnavailableError(RuntimeError):
    """The model files are missing from object storage or do not match their pinned hashes."""


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _verified(path: Path, expected: str) -> bool:
    return path.is_file() and sha256_hex(path.read_bytes()) == expected


def model_dir(cache_dir: str | Path, manifest: ModelManifest) -> Path:
    return Path(cache_dir) / manifest.version


def fetch(
    storage: ObjectStorage, cache_dir: str | Path, manifest: ModelManifest = ECOSYSTEM_CLASSIFIER_V1
) -> Path:
    """Make sure every model file is in the disk cache with its pinned SHA-256; returns the dir.

    Cached files that do not match are re-downloaded; a download that does not match raises
    `ModelUnavailableError` (nothing unverified is ever written to the cache)."""
    target = model_dir(cache_dir, manifest)
    target.mkdir(parents=True, exist_ok=True)
    for name, expected in manifest.sha256.items():
        path = target / name
        if _verified(path, expected):
            continue
        key = manifest.prefix + name
        try:
            data = storage.get_bytes(key, MAX_MODEL_FILE_BYTES)
        except Exception as exc:
            raise ModelUnavailableError(
                f"cannot download {key} from object storage ({type(exc).__name__}: {exc}); "
                "publish it with `python -m aqua_worker.model_store publish <artifact dir>`"
            ) from exc
        actual = sha256_hex(data)
        if actual != expected:
            raise ModelUnavailableError(
                f"{key} has SHA-256 {actual}, expected {expected}: refusing to use it"
            )
        tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
        tmp.write_bytes(data)
        tmp.replace(path)  # atomic: concurrent workers never see a partial file
        log.info("model.downloaded", key=key, sha256=actual, bytes=len(data))
    return target


@dataclass
class LoadedModel:
    """An ONNX Runtime session plus the spec and the hashes recorded with every prediction."""

    manifest: ModelManifest
    spec: ModelSpec
    model_sha256: str
    features_sha256: str
    session: Any
    input_name: str

    @property
    def version(self) -> str:
        return self.manifest.version

    def predict(self, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Class index and class probabilities per row of a float32 (n, n_features) matrix."""
        if x.ndim != 2 or x.shape[1] != len(self.spec.feature_columns):
            raise ValueError(f"expected (n, {len(self.spec.feature_columns)}), got {x.shape}")
        if x.shape[0] == 0:
            return np.zeros(0, dtype="int64"), np.zeros((0, len(self.spec.classes)), "float32")
        outputs = self.session.run(
            ["label", "probabilities"], {self.input_name: x.astype("float32")}
        )
        return np.asarray(outputs[0]), np.asarray(outputs[1])


def load_model(
    directory: str | Path, manifest: ModelManifest = ECOSYSTEM_CLASSIFIER_V1, *, threads: int = 2
) -> LoadedModel:
    """Verify the files in `directory` against the manifest and open an inference session."""
    import onnxruntime as ort

    base = Path(directory)
    blobs: dict[str, bytes] = {}
    for name, expected in manifest.sha256.items():
        path = base / name
        if not path.is_file():
            raise ModelUnavailableError(f"{path} is missing")
        data = path.read_bytes()
        if sha256_hex(data) != expected:
            raise ModelUnavailableError(f"{path} does not match its pinned SHA-256 {expected}")
        blobs[name] = data
    spec = ModelSpec.from_features_json(blobs[manifest.features_file])
    if spec.version != manifest.version:
        raise ModelUnavailableError(
            f"features.json is for {spec.version}, the manifest pins {manifest.version}"
        )
    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    # The LightGBM export declares the label output as shape [1]; ORT warns on every batch.
    options.log_severity_level = 3
    session = ort.InferenceSession(
        blobs[manifest.model_file], sess_options=options, providers=["CPUExecutionProvider"]
    )
    inputs = session.get_inputs()
    outputs = {o.name for o in session.get_outputs()}
    if len(inputs) != 1 or not {"label", "probabilities"} <= outputs:
        raise ModelUnavailableError("model must have one input and label/probabilities outputs")
    model = LoadedModel(
        manifest=manifest,
        spec=spec,
        model_sha256=manifest.sha256[manifest.model_file],
        features_sha256=manifest.sha256[manifest.features_file],
        session=session,
        input_name=inputs[0].name,
    )
    # Smoke prediction: the output shapes must match the class list in features.json.
    _, probs = model.predict(np.zeros((1, len(spec.feature_columns)), dtype="float32"))
    if probs.shape != (1, len(spec.classes)):
        raise ModelUnavailableError(
            f"model returns {probs.shape[1]} class probabilities, features.json lists "
            f"{len(spec.classes)} classes"
        )
    return model


_loaded: dict[str, LoadedModel] = {}
_lock = threading.Lock()


def get_model(
    storage: ObjectStorage,
    cache_dir: str | Path,
    manifest: ModelManifest = ECOSYSTEM_CLASSIFIER_V1,
) -> LoadedModel:
    """The process-wide model: fetched (verified) into the disk cache, loaded once."""
    with _lock:
        cached = _loaded.get(manifest.version)
        if cached is None:
            cached = load_model(fetch(storage, cache_dir, manifest), manifest)
            _loaded[manifest.version] = cached
        return cached


def publish(
    storage: ObjectStorage,
    source_dir: str | Path,
    manifest: ModelManifest = ECOSYSTEM_CLASSIFIER_V1,
) -> dict[str, str]:
    """Upload the model files from a local artifact directory, refusing any file whose SHA-256
    differs from the pinned one. Returns {object key: sha256}."""
    base = Path(source_dir)
    blobs: dict[str, bytes] = {}
    for name, expected in manifest.sha256.items():
        path = base / name
        if not path.is_file():
            raise ModelUnavailableError(f"{path} is missing")
        data = path.read_bytes()
        actual = sha256_hex(data)
        if actual != expected:
            raise ModelUnavailableError(
                f"{path} has SHA-256 {actual}, the manifest pins {expected}: not publishing"
            )
        blobs[name] = data
    load_model(base, manifest)  # the files must also open and predict
    uploaded: dict[str, str] = {}
    for name, data in blobs.items():
        content_type = "application/json" if name.endswith(".json") else "application/octet-stream"
        key = manifest.prefix + name
        uploaded[key] = storage.put_bytes(key, data, content_type)
    return uploaded


def main(argv: list[str] | None = None) -> int:
    from aqua_core.settings import get_settings
    from aqua_core.storage import S3ObjectStorage

    parser = argparse.ArgumentParser(prog="python -m aqua_worker.model_store")
    sub = parser.add_subparsers(dest="command", required=True)
    pub = sub.add_parser("publish", help="verify local model files and upload them")
    pub.add_argument("source_dir", help="e.g. ml/artifacts/ecosystem_classifier_v1")
    sub.add_parser("verify", help="download into the cache, verify and load the model")
    args = parser.parse_args(argv)
    settings = get_settings()
    storage = S3ObjectStorage(settings)
    manifest = ECOSYSTEM_CLASSIFIER_V1
    if args.command == "publish":
        for key, digest in publish(storage, args.source_dir, manifest).items():
            print(f"uploaded {key} sha256={digest}")
        return 0
    model = load_model(fetch(storage, settings.ml_model_cache_dir, manifest), manifest)
    print(f"{model.version}: model.onnx sha256={model.model_sha256} classes={model.spec.classes}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
