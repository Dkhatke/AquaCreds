"""Photo verifier serving + evidence check, with a tiny synthetic ONNX model (no real weights).

The fake model averages each RGB channel and applies a linear softmax over three classes:
green → `avicennia` (mangrove), red → `rhizophora` (mangrove), blue → `urban_proxy` (not).
"""

import hashlib
import io
import json
from pathlib import Path
from typing import Any

import numpy as np
import onnx
import pytest
from classify_fakes import MemoryStorage
from onnx import TensorProto, helper
from PIL import Image

from aqua_worker import evidence as ev
from aqua_worker import photo_verifier as pv
from aqua_worker.model_store import ModelManifest, ModelUnavailableError
from aqua_worker.tasks import evidence as task

CLASSES = [
    {"key": "avicennia", "label": "mangrove", "genus": "Avicennia"},
    {"key": "rhizophora", "label": "mangrove", "genus": "Rhizophora"},
    {"key": "urban_proxy", "label": "non_mangrove", "genus": ""},
]


def tiny_onnx(n_classes: int = 3) -> bytes:
    # rows = R, G, B channel means; columns = classes
    weights = np.zeros((3, n_classes), dtype=np.float32)
    weights[1, 0] = weights[0, 1] = weights[2, 2 % n_classes] = 12.0
    graph = helper.make_graph(
        [
            helper.make_node("ReduceMean", ["image"], ["mean"], axes=[2, 3], keepdims=0),
            helper.make_node("MatMul", ["mean", "W"], ["logits"]),
            helper.make_node("Softmax", ["logits"], ["probabilities"], axis=1),
        ],
        "tiny_photo_verifier",
        [helper.make_tensor_value_info("image", TensorProto.FLOAT, ["N", 3, 224, 224])],
        [helper.make_tensor_value_info("probabilities", TensorProto.FLOAT, ["N", n_classes])],
        [helper.make_tensor("W", TensorProto.FLOAT, weights.shape, weights.flatten())],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    onnx.checker.check_model(model)
    return model.SerializeToString()


def labels_json(version: str = "test-photo", classes: list[dict[str, str]] | None = None) -> bytes:
    body = {
        "version": version,
        "classes": classes if classes is not None else CLASSES,
        "thresholds": {"pass_at": 0.8, "low": 0.3},
    }
    return json.dumps(body).encode()


def write_model(
    directory: Path, *, model: bytes | None = None, labels: bytes | None = None
) -> ModelManifest:
    directory.mkdir(parents=True, exist_ok=True)
    files = {"model.onnx": model or tiny_onnx(), "labels.json": labels or labels_json()}
    for name, data in files.items():
        (directory / name).write_bytes(data)
    return ModelManifest(
        version="test-photo",
        prefix="models/test-photo/v1/",
        model_file="model.onnx",
        features_file="labels.json",
        sha256={k: hashlib.sha256(v).hexdigest() for k, v in files.items()},
    )


def jpeg(rgb: tuple[int, int, int], size: tuple[int, int] = (64, 48)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, rgb).save(out, "JPEG", quality=95)
    return out.getvalue()


@pytest.fixture
def model(tmp_path: Path) -> pv.PhotoModel:
    return pv.load_photo_model(tmp_path, write_model(tmp_path))


def test_green_photo_is_plausible_mangrove_with_genus(model: pv.PhotoModel) -> None:
    photo = jpeg((0, 200, 0))
    result = pv.photo_plausibility(photo, model)
    assert result["mangrove_prob"] > 0.9
    assert result["top_genera"][0]["genus"] == "Avicennia"
    assert len(result["top_genera"]) == 2  # only the mangrove classes are genera
    assert sum(g["prob"] for g in result["top_genera"]) == pytest.approx(1.0, abs=1e-3)
    assert result["model_version"] == "test-photo"
    assert result["sha256"] == model.model_sha256
    assert result["image_sha256"] == hashlib.sha256(photo).hexdigest()


def test_blue_photo_is_unlikely_mangrove(model: pv.PhotoModel) -> None:
    result = pv.photo_plausibility(jpeg((0, 0, 220)), model)
    assert result["mangrove_prob"] < 0.1


def test_preprocess_shape_and_range() -> None:
    x = pv.preprocess(jpeg((255, 0, 0), size=(300, 100)))
    assert x.shape == (1, 3, 224, 224)
    assert x.dtype == np.float32
    assert 0.0 <= x.min() <= x.max() <= 1.0


def test_hash_mismatch_is_refused(tmp_path: Path) -> None:
    manifest = write_model(tmp_path)
    (tmp_path / "model.onnx").write_bytes(tiny_onnx() + b"tampered")
    with pytest.raises(ModelUnavailableError, match="pinned SHA-256"):
        pv.load_photo_model(tmp_path, manifest)


def test_missing_file_is_refused(tmp_path: Path) -> None:
    manifest = write_model(tmp_path)
    (tmp_path / "labels.json").unlink()
    with pytest.raises(ModelUnavailableError, match="missing"):
        pv.load_photo_model(tmp_path, manifest)


def test_labels_for_another_version_are_refused(tmp_path: Path) -> None:
    manifest = write_model(tmp_path, labels=labels_json(version="other"))
    with pytest.raises(ModelUnavailableError, match=r"labels\.json is for other"):
        pv.load_photo_model(tmp_path, manifest)


def test_class_count_mismatch_is_refused(tmp_path: Path) -> None:
    manifest = write_model(tmp_path, model=tiny_onnx(n_classes=4))
    with pytest.raises(ModelUnavailableError, match="returns 4 probabilities"):
        pv.load_photo_model(tmp_path, manifest)


@pytest.mark.parametrize(
    "classes",
    [[], [c for c in CLASSES if c["label"] == "mangrove"]],
)
def test_label_spec_needs_both_labels(classes: list[dict[str, str]]) -> None:
    with pytest.raises(ModelUnavailableError):
        pv.LabelSpec.from_json(labels_json(classes=classes))


def test_thresholds_must_be_ordered() -> None:
    with pytest.raises(ValueError, match="low ≤ pass_at"):
        pv.Thresholds(pass_at=0.2, low=0.5)


def test_fetch_publish_and_process_cache(tmp_path: Path) -> None:
    manifest = write_model(tmp_path / "src")
    storage = MemoryStorage()
    uploaded = pv.publish(storage, tmp_path / "src", manifest)
    assert set(uploaded) == {"models/test-photo/v1/model.onnx", "models/test-photo/v1/labels.json"}
    pv._loaded.pop(manifest.version, None)
    first = pv.get_photo_model(storage, tmp_path / "cache", manifest)
    second = pv.get_photo_model(storage, tmp_path / "cache", manifest)
    assert first is second
    pv._loaded.pop(manifest.version, None)


def test_publish_refuses_tampered_files(tmp_path: Path) -> None:
    manifest = write_model(tmp_path)
    (tmp_path / "labels.json").write_bytes(labels_json() + b" ")
    with pytest.raises(ModelUnavailableError):
        pv.publish(MemoryStorage(), tmp_path, manifest)
    assert pv.PHOTO_VERIFIER_V1.files == ("model.onnx", "labels.json")


# ---------------------------------------------------------------- evidence check + trust score


def _result(p: float) -> dict[str, Any]:
    return {
        "mangrove_prob": p,
        "top_genera": [{"genus": "Rhizophora", "prob": 0.7}],
        "model_version": "photo_verifier_v1",
        "sha256": "ab" * 32,
        "image_sha256": "cd" * 32,
    }


def test_check_bands_never_hard() -> None:
    passed = ev.check_photo_model(_result(0.95), pass_at=0.8, low=0.3)
    unsure = ev.check_photo_model(_result(0.5), pass_at=0.8, low=0.3)
    unlikely = ev.check_photo_model(_result(0.05), pass_at=0.8, low=0.3)
    assert (passed.outcome, unsure.outcome, unlikely.outcome) == ("pass", "warn", "warn")
    assert "Rhizophora" in passed.reason
    assert "Unsure" in unsure.reason
    assert "does not look like mangrove" in unlikely.reason
    assert not any(c.hard for c in (passed, unsure, unlikely))
    assert unlikely.to_dict()["details"]["mangrove_prob"] == 0.05


def test_photo_model_alone_never_rejects() -> None:
    others = [ev.Check("geofence", "pass", ""), ev.Check("duplicate_exact", "pass", "")]
    unlikely = ev.check_photo_model(_result(0.0), pass_at=0.8, low=0.3)
    assert ev.score([*others, unlikely]) == (100 - ev.WARN_PENALTY, False)


def test_checks_without_details_keep_their_shape() -> None:
    assert "details" not in ev.Check("a", "pass", "").to_dict()


# ---------------------------------------------------------------- task wiring


@pytest.fixture
def settings_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> pytest.MonkeyPatch:
    from aqua_core.settings import get_settings

    monkeypatch.setenv("ML_MODEL_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


def test_disabled_by_default(settings_env: pytest.MonkeyPatch) -> None:
    from aqua_core.settings import get_settings

    settings_env.delenv("PHOTO_VERIFIER_ENABLED", raising=False)
    get_settings.cache_clear()
    assert get_settings().photo_verifier_enabled is False
    assert task.photo_check_from_settings(MemoryStorage()) is None


def test_enabled_but_unpublished_model_is_reported_not_faked(
    settings_env: pytest.MonkeyPatch,
) -> None:
    settings_env.setenv("PHOTO_VERIFIER_ENABLED", "true")
    pv._loaded.pop(pv.PHOTO_VERIFIER_V1.version, None)
    check = task.photo_check_from_settings(MemoryStorage())  # empty object store
    assert check is not None
    result = check(jpeg((0, 200, 0)))
    assert result.outcome == "skipped"
    assert "enabled but unavailable" in result.reason


def test_enabled_runs_the_model(
    settings_env: pytest.MonkeyPatch, model: pv.PhotoModel, tmp_path: Path
) -> None:
    settings_env.setenv("PHOTO_VERIFIER_ENABLED", "true")
    settings_env.setattr(pv, "get_photo_model", lambda storage, cache_dir: model)
    check = task.photo_check_from_settings(MemoryStorage())
    assert check is not None
    green = check(jpeg((0, 200, 0)))
    blue = check(jpeg((0, 0, 220)))
    assert (green.outcome, blue.outcome) == ("pass", "warn")
    assert green.details is not None
    assert green.details["model_version"] == "test-photo"


def test_worker_start_verifies_photo_model_only_when_enabled(
    settings_env: pytest.MonkeyPatch, model: pv.PhotoModel
) -> None:
    from types import SimpleNamespace

    import aqua_core.storage
    from aqua_worker.celery_app import _verify_photo_verifier

    queue = SimpleNamespace(name="ml")
    sender = SimpleNamespace(
        app=SimpleNamespace(amqp=SimpleNamespace(queues=SimpleNamespace(consume_from={"q": queue})))
    )

    calls: list[str] = []

    def fake_get(storage: object, cache_dir: object) -> pv.PhotoModel:
        calls.append("get")
        return model

    settings_env.setattr(aqua_core.storage, "S3ObjectStorage", lambda s: MemoryStorage())
    settings_env.setattr(pv, "get_photo_model", fake_get)
    _verify_photo_verifier(sender=sender)  # disabled by default
    assert calls == []
    settings_env.setenv("PHOTO_VERIFIER_ENABLED", "true")
    from aqua_core.settings import get_settings

    get_settings.cache_clear()
    _verify_photo_verifier(sender=sender)
    assert calls == ["get"]

    def broken(storage: object, cache_dir: object) -> pv.PhotoModel:
        raise ModelUnavailableError("missing")

    settings_env.setattr(pv, "get_photo_model", broken)
    _verify_photo_verifier(sender=sender)  # logged, not raised
