"""Field-photo verifier (Model C) serving: "does this photo plausibly show mangroves, which genus?"

One advisory signal in the evidence trust score (`aqua_worker.evidence.check_photo_model`). It is
behind `PHOTO_VERIFIER_ENABLED` (default false) and it never rejects a submission on its own.

Distribution is the same as the ecosystem classifier (`aqua_worker.model_store`): the files live
in object storage under `models/photo_verifier/v1/`, their SHA-256 values are pinned below (in
git), and the worker verifies them on every load. Publish them once with::

    uv run python -m aqua_worker.photo_verifier publish ml/artifacts/photo_verifier/export

I/O contract of `model.onnx` (checked at load time):

* input  ``image``: float32 ``[N, 3, 224, 224]``, RGB in 0..1, plain (non-cropping) bilinear
  resize; any normalisation is folded into the graph;
* output ``probabilities``: float32 ``[N, C]`` over the classes listed in `labels.json`.

`labels.json` lists each class with its label (``mangrove`` / ``non_mangrove``) and genus, plus
the decision thresholds chosen on the validation split (see `ml/model_cards/photo_verifier_v1.md`).
"""

import argparse
import hashlib
import io
import json
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageOps

from aqua_core.logging import get_logger
from aqua_core.storage import ObjectStorage
from aqua_worker.model_store import ModelManifest, ModelUnavailableError, fetch, sha256_hex

log = get_logger("aqua_worker.photo_verifier")

INPUT_SIZE = 224
TOP_GENERA = 3

PHOTO_VERIFIER_V1 = ModelManifest(
    version="photo_verifier_v1",
    prefix="models/photo_verifier/v1/",
    model_file="model.onnx",
    features_file="labels.json",
    sha256={
        "model.onnx": "3609c78f1ff91dbfd83a84dc2c58d6006c13497f99ea303683e4269b17971e69",
        "labels.json": "1f54be790ddfe83a5d9469ebb15734194385507e9eaf874909fcc944edc19ae1",
    },
    # Copied from ml/model_cards/photo_verifier_v1_metrics.json (candidates.probe_bioclip_v1);
    # test_photo_verifier checks they still match that file.
    heldout_metrics={
        "candidate": "probe_bioclip_v1",
        "test_observer_f1": 0.9875,
        "test_observer_auroc": 0.9971,
        "test_state_f1": 0.962,
        "test_state_auroc": 0.996,
        "test_state": "Maharashtra (never used for training, tuning or thresholds)",
        "label_note": "iNaturalist research-grade photos, not field captures from the app",
    },
    model_card="ml/model_cards/photo_verifier_v1.md",
    max_file_bytes=400 * 1024 * 1024,  # fp32 ViT-B/16 + head: 345207351 bytes
)


@dataclass(frozen=True)
class Thresholds:
    """P(mangrove) bands: ≥ `pass_at` passes; [`low`, `pass_at`) is uncertain; < `low` is unlikely."""

    pass_at: float
    low: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.low <= self.pass_at <= 1.0:
            raise ValueError(f"need 0 ≤ low ≤ pass_at ≤ 1, got {self.low}, {self.pass_at}")


@dataclass(frozen=True)
class LabelSpec:
    version: str
    classes: tuple[str, ...]
    mangrove: tuple[bool, ...]
    genus: tuple[str, ...]  # "" for non-mangrove classes
    thresholds: Thresholds

    @classmethod
    def from_json(cls, data: bytes) -> "LabelSpec":
        raw = json.loads(data)
        classes = raw["classes"]
        if not classes:
            raise ModelUnavailableError("labels.json lists no classes")
        spec = cls(
            version=str(raw["version"]),
            classes=tuple(str(c["key"]) for c in classes),
            mangrove=tuple(c["label"] == "mangrove" for c in classes),
            genus=tuple(str(c.get("genus") or "") for c in classes),
            thresholds=Thresholds(
                pass_at=float(raw["thresholds"]["pass_at"]), low=float(raw["thresholds"]["low"])
            ),
        )
        if not any(spec.mangrove) or all(spec.mangrove):
            raise ModelUnavailableError("labels.json needs both mangrove and non-mangrove classes")
        return spec


@dataclass
class PhotoModel:
    manifest: ModelManifest
    spec: LabelSpec
    model_sha256: str
    session: Any
    input_name: str

    @property
    def version(self) -> str:
        return self.manifest.version

    def probabilities(self, x: np.ndarray) -> np.ndarray:
        out = self.session.run(["probabilities"], {self.input_name: x.astype("float32")})
        return np.asarray(out[0], dtype="float64")


def preprocess(image_bytes: bytes) -> np.ndarray:
    """Production preprocessing: EXIF orientation applied, RGB, 224x224 bilinear, 0..1, NCHW."""
    with Image.open(io.BytesIO(image_bytes)) as img:
        upright = ImageOps.exif_transpose(img).convert("RGB")
    resized = upright.resize((INPUT_SIZE, INPUT_SIZE), Image.Resampling.BILINEAR)
    arr = np.asarray(resized, dtype=np.float32) / 255.0
    return arr.transpose(2, 0, 1)[None]


def photo_plausibility(image_bytes: bytes, model: PhotoModel) -> dict[str, Any]:
    """{mangrove_prob, top_genera, model_version, sha256, image_sha256}.

    * `mangrove_prob`: summed probability of the mangrove-genus classes.
    * `top_genera`: up to 3 genera with P(genus | mangrove), i.e. the genus guess *if* it is a
      mangrove; meaningful only when `mangrove_prob` is high.
    * `sha256`: SHA-256 of the `model.onnx` that produced the result (traceability);
      `image_sha256` is the analysed file's hash.
    """
    probs = model.probabilities(preprocess(image_bytes))[0]
    if probs.shape != (len(model.spec.classes),):
        raise ModelUnavailableError(f"model returned {probs.shape}, expected one row of classes")
    mask = np.array(model.spec.mangrove)
    mangrove_prob = float(probs[mask].sum())
    genus_probs: dict[str, float] = {}
    for p, is_mangrove, genus in zip(probs, model.spec.mangrove, model.spec.genus, strict=True):
        if is_mangrove:
            genus_probs[genus] = genus_probs.get(genus, 0.0) + float(p)
    ranked = sorted(genus_probs.items(), key=lambda kv: -kv[1])[:TOP_GENERA]
    top = [
        {"genus": g, "prob": round(p / mangrove_prob, 4) if mangrove_prob > 0 else 0.0}
        for g, p in ranked
    ]
    return {
        "mangrove_prob": round(mangrove_prob, 4),
        "top_genera": top,
        "model_version": model.version,
        "sha256": model.model_sha256,
        "image_sha256": hashlib.sha256(image_bytes).hexdigest(),
    }


def load_photo_model(
    directory: str | Path, manifest: ModelManifest = PHOTO_VERIFIER_V1, *, threads: int = 2
) -> PhotoModel:
    """Verify the files against the manifest, open an ONNX Runtime session, smoke-test it."""
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
    spec = LabelSpec.from_json(blobs[manifest.features_file])
    if spec.version != manifest.version:
        raise ModelUnavailableError(
            f"labels.json is for {spec.version}, the manifest pins {manifest.version}"
        )
    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(
        blobs[manifest.model_file], sess_options=options, providers=["CPUExecutionProvider"]
    )
    inputs = session.get_inputs()
    if len(inputs) != 1 or "probabilities" not in {o.name for o in session.get_outputs()}:
        raise ModelUnavailableError("model must have one input and a `probabilities` output")
    model = PhotoModel(
        manifest=manifest,
        spec=spec,
        model_sha256=manifest.sha256[manifest.model_file],
        session=session,
        input_name=inputs[0].name,
    )
    probs = model.probabilities(np.zeros((1, 3, INPUT_SIZE, INPUT_SIZE), dtype="float32"))
    if probs.shape != (1, len(spec.classes)):
        raise ModelUnavailableError(
            f"model returns {probs.shape[1]} probabilities, labels.json lists {len(spec.classes)}"
        )
    return model


_loaded: dict[str, PhotoModel] = {}
_lock = threading.Lock()


def get_photo_model(
    storage: ObjectStorage, cache_dir: str | Path, manifest: ModelManifest = PHOTO_VERIFIER_V1
) -> PhotoModel:
    """The process-wide model: fetched (verified) into the disk cache, loaded once."""
    with _lock:
        cached = _loaded.get(manifest.version)
        if cached is None:
            cached = load_photo_model(fetch(storage, cache_dir, manifest), manifest)
            _loaded[manifest.version] = cached
        return cached


def publish(
    storage: ObjectStorage, source_dir: str | Path, manifest: ModelManifest = PHOTO_VERIFIER_V1
) -> dict[str, str]:
    """Upload verified model files (refusing any hash mismatch). Returns {key: sha256}."""
    base = Path(source_dir)
    load_photo_model(base, manifest)  # verifies every pinned hash and that the model runs
    uploaded: dict[str, str] = {}
    for name in manifest.files:
        data = (base / name).read_bytes()
        content_type = "application/json" if name.endswith(".json") else "application/octet-stream"
        uploaded[manifest.prefix + name] = storage.put_bytes(
            manifest.prefix + name, data, content_type
        )
    return uploaded


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin CLI over tested code
    from aqua_core.settings import get_settings
    from aqua_core.storage import S3ObjectStorage

    parser = argparse.ArgumentParser(prog="python -m aqua_worker.photo_verifier")
    sub = parser.add_subparsers(dest="command", required=True)
    pub = sub.add_parser("publish", help="verify local model files and upload them")
    pub.add_argument("source_dir")
    sub.add_parser("verify", help="download into the cache, verify and load the model")
    args = parser.parse_args(argv)
    settings = get_settings()
    storage = S3ObjectStorage(settings)
    if args.command == "publish":
        for key, digest in publish(storage, args.source_dir).items():
            print(f"uploaded {key} sha256={digest}")
        return 0
    model = load_photo_model(fetch(storage, settings.ml_model_cache_dir, PHOTO_VERIFIER_V1))
    print(f"{model.version}: model.onnx sha256={model.model_sha256} classes={model.spec.classes}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
