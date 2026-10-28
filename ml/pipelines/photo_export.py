"""Export the selected photo-verifier candidate to ONNX, check parity, measure CPU latency.

Usage:  uv run python -u ml/pipelines/photo_export.py <candidate>

Writes `ml/artifacts/photo_verifier/export/{model.onnx,labels.json}` (the files the worker pins in
`aqua_worker.photo_verifier`) and adds an `export` section to the metrics JSON.

The exported graph follows the worker contract: input `image` float32 [N, 3, 224, 224], RGB 0..1
after a plain bilinear resize (normalisation folded in), output `probabilities` [N, 20].
"""

import hashlib
import json
import platform
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import photo_eval as pe
from photos_download import CLASSES

EXPORT = pe.ART / "export"
VERSION = "photo_verifier_v1"
PARITY_ATOL = 1e-4
LATENCY_RUNS = 60
# Labelled ESTIMATE, not a measurement: Oracle Always Free Ampere A1 (Neoverse-N1, ~3.0 GHz)
# single-thread throughput is roughly 2-3x slower than an Apple M-series performance core on
# ML kernels. The model card states this range; measure on the VM before relying on it.
ARM_SLOWDOWN_RANGE = (2.0, 3.0)


def labels_json(thresholds: dict[str, float], metrics_ref: str) -> bytes:
    body = {
        "version": VERSION,
        "classes": [{"key": c.key, "label": c.label, "genus": c.genus} for c in CLASSES],
        "input": {
            "name": "image",
            "shape": ["N", 3, 224, 224],
            "range": "RGB 0..1",
            "resize": "plain bilinear to 224x224 after EXIF orientation",
        },
        "output": "probabilities",
        "thresholds": thresholds,
        "metrics": metrics_ref,
    }
    return (json.dumps(body, indent=1) + "\n").encode()


def fuse_student_probe(clf: Any) -> bytes:
    """Student ONNX graph (embedding [1,1024]) + logistic-regression head + softmax."""
    import onnx
    from onnx import TensorProto, helper, numpy_helper

    model = onnx.load(str(pe.STUDENT_ONNX))
    w = clf.coef_.T.astype(np.float32)  # (1024, C)
    b = clf.intercept_.astype(np.float32)
    g = model.graph
    g.initializer.extend(
        [numpy_helper.from_array(w, "probe_W"), numpy_helper.from_array(b, "probe_b")]
    )
    emb = g.output[0].name
    g.node.extend(
        [
            helper.make_node("MatMul", [emb, "probe_W"], ["probe_mm"]),
            helper.make_node("Add", ["probe_mm", "probe_b"], ["probe_logits"]),
            helper.make_node("Softmax", ["probe_logits"], ["probabilities"], axis=1),
        ]
    )
    del g.output[:]
    g.output.append(
        helper.make_tensor_value_info("probabilities", TensorProto.FLOAT, [1, w.shape[1]])
    )
    onnx.checker.check_model(model)
    return model.SerializeToString()


def export_torch(module: Any) -> bytes:
    import io

    import torch

    buf = io.BytesIO()
    torch.onnx.export(
        module.eval(),
        (torch.zeros(1, 3, pe.IMG, pe.IMG),),
        buf,
        input_names=["image"],
        output_names=["probabilities"],
        dynamic_axes={"image": {0: "N"}, "probabilities": {0: "N"}},
        opset_version=18,
        dynamo=False,
    )
    return buf.getvalue()


def torch_probe_module(clf: Any) -> Any:
    import torch

    visual = pe.NormalizedVisual.build(pe.bioclip_v1_visual(), pe.CLIP_MEAN, pe.CLIP_STD)
    head = torch.nn.Linear(clf.coef_.shape[1], clf.coef_.shape[0])
    head.weight.data = torch.tensor(clf.coef_, dtype=torch.float32)
    head.bias.data = torch.tensor(clf.intercept_, dtype=torch.float32)
    return torch.nn.Sequential(visual, head, torch.nn.Softmax(dim=1))


def torch_mobilenet_module() -> Any:
    import torch

    model = pe.mobilenet_model(len(pe.CLASS_KEYS), pretrained=False)
    model.load_state_dict(torch.load(pe.ART / "mobilenetv2_finetune.pt", weights_only=True))

    class _M(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.m = model
            self.register_buffer("mean", torch.tensor(pe.IMAGENET_MEAN).view(1, 3, 1, 1))
            self.register_buffer("std", torch.tensor(pe.IMAGENET_STD).view(1, 3, 1, 1))

        def forward(self, x: Any) -> Any:
            return torch.softmax(self.m((x - self.mean) / self.std), dim=1)

    return _M().eval()


def build(candidate: str) -> bytes:
    import joblib

    if candidate == "probe_bioclip25_student":
        return fuse_student_probe(joblib.load(pe.ART / f"{candidate}.joblib"))
    if candidate == "probe_bioclip_v1":
        return export_torch(torch_probe_module(joblib.load(pe.ART / f"{candidate}.joblib")))
    if candidate == "mobilenetv2_finetune":
        return export_torch(torch_mobilenet_module())
    raise SystemExit(f"{candidate} has no export path (zero-shot needs no training; see card)")


def parity(model_bytes: bytes, df: Any, candidate: str, n: int = 200) -> dict[str, Any]:
    """ONNX P(mangrove) vs the evaluation pipeline's P(mangrove) on the same test images."""
    sess = pe.ort_session(model_bytes)
    preds = np.load(pe.ART / "predictions.npz")
    index = {int(p): i for i, p in enumerate(preds["photo_id"])}
    rows = df[df["split"].isin(pe.TEST_SPLITS)].head(n)
    genus_cols = [pe.CLASS_KEYS.index(g.lower()) for g in pe.POSITIVE_GENERA]
    diffs = []
    for photo_id, path in zip(rows["photo_id"], rows["path"], strict=True):
        probs = sess.run(["probabilities"], {"image": pe.load_rgb_224(path)[None]})[0][0]
        ref = preds[f"{candidate}__pm"][index[int(photo_id)]]
        diffs.append(abs(float(probs[genus_cols].sum()) - float(ref)))
    worst = max(diffs)
    if worst > PARITY_ATOL:
        raise RuntimeError(f"ONNX parity failed: max |ΔP(mangrove)| = {worst:.2e}")
    return {"images": len(diffs), "max_abs_diff_p_mangrove": worst, "atol": PARITY_ATOL}


def latency(model_bytes: bytes, sample: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "machine": f"{platform.machine()} {platform.platform()}",
        "includes": "JPEG decode + resize + ONNX Runtime CPU inference, batch 1",
    }
    for threads in (1, 2, 4):
        sess = pe.ort_session(model_bytes, threads=threads)
        for _ in range(5):
            sess.run(None, {"image": pe.load_rgb_224(sample)[None]})
        times = []
        for _ in range(LATENCY_RUNS):
            t0 = time.perf_counter()
            x = pe.load_rgb_224(sample)[None]  # JPEG decode + resize, as the worker does
            sess.run(None, {"image": x})
            times.append(time.perf_counter() - t0)
        times.sort()
        med = statistics.median(times)
        out[f"threads_{threads}"] = {
            "median_ms": round(1000 * med, 1),
            "p95_ms": round(1000 * times[int(0.95 * len(times)) - 1], 1),
        }
    med2 = out["threads_2"]["median_ms"]
    out["oracle_arm_4core_ESTIMATE_ms"] = {
        "basis": f"Mac median at 2 threads x {ARM_SLOWDOWN_RANGE} (not measured on the VM)",
        "range": [round(med2 * ARM_SLOWDOWN_RANGE[0]), round(med2 * ARM_SLOWDOWN_RANGE[1])],
    }
    return out


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        raise SystemExit(__doc__)
    candidate = args[0]
    pe._setup_caches()
    metrics = json.loads(pe.METRICS_JSON.read_text())
    result = metrics["candidates"][candidate]
    df = pe.load_dataset()
    model_bytes = build(candidate)
    thresholds = {
        # pass: precision ≥ 0.95 on val; low: 95% of val mangrove photos are above it.
        "pass_at": result["threshold_val_precision95"],
        "low": min(result["threshold_val_recall95"], result["threshold_val_precision95"]),
        "best_f1_val": result["threshold_val_best_f1"],
    }
    labels = labels_json(thresholds, "ml/model_cards/photo_verifier_v1_metrics.json")
    EXPORT.mkdir(parents=True, exist_ok=True)
    (EXPORT / "model.onnx").write_bytes(model_bytes)
    (EXPORT / "labels.json").write_bytes(labels)
    sample = str(df[df["split"] == "test_observer"]["path"].iloc[0])
    metrics["export"] = {
        "candidate": candidate,
        "files": {
            "model.onnx": {
                "sha256": hashlib.sha256(model_bytes).hexdigest(),
                "bytes": len(model_bytes),
            },
            "labels.json": {"sha256": hashlib.sha256(labels).hexdigest(), "bytes": len(labels)},
        },
        "thresholds": thresholds,
        "parity": parity(model_bytes, df, candidate),
        "latency_cpu_onnxruntime": latency(model_bytes, sample),
    }
    pe.METRICS_JSON.write_text(json.dumps(metrics, indent=1) + "\n")
    print(json.dumps(metrics["export"], indent=1))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
