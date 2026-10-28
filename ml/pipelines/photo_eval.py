"""Evaluate field-photo verifier candidates (Model C) on observer- and state-held-out tests.

Usage::

    uv run python -u ml/pipelines/photo_eval.py embed      # cache image embeddings (both encoders)
    uv run python -u ml/pipelines/photo_eval.py evaluate   # all candidates → metrics JSON
    uv run python -u ml/pipelines/photo_eval.py export     # winner → ONNX + parity + latency

Candidates (weights within the user's 500 MB/file limit; see the model card for why the official
BioCLIP 2 (1.71 GB) and BioCLIP ViT-B/16 (599 MB) checkpoints were not downloaded):

* ``zeroshot_bioclip25_student``: the MIT-licensed FastViT student distilled from BioCLIP 2.5
  (``crazedcodernate/bioclip-2.5-mobile-fastvit``, 47 MB) against its 4,271-species iNat21 plant
  text-embedding table. P(mangrove) = softmax mass on the mangrove taxa in that table.
* ``probe_bioclip25_student`` and ``probe_bioclip_v1``: multinomial logistic regression on the
  student's 1024-d embeddings and on the original BioCLIP ViT-B/16 image encoder's 512-d embeddings
  (``birder-project/vit_b16_pn_bioclip-v1``, 345 MB, the BioCLIP weights in another format).
* ``mobilenetv2_finetune``: torchvision MobileNetV2 (ImageNet-1k V2 weights) fine-tuned.

All supervised candidates predict the 20 dataset classes (11 genera + 9 negative classes);
P(mangrove) is the summed probability of the genus classes, and genus ranking uses the genus-class
probabilities. Metric functions are numpy-only so CI can test them without the ML group.
"""

import argparse
import json
import os
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from photos_download import CLASSES, POSITIVE_GENERA, RAW  # noqa: E402

ART = ROOT / "ml" / "artifacts" / "photo_verifier"
HF = ROOT / "ml" / "artifacts" / "hf_downloads"
STUDENT_ONNX = HF / "fastvit" / "flora_student_fp32.onnx"
STUDENT_TABLE = HF / "fastvit" / "taxa_table.npy"
STUDENT_LABELS = HF / "fastvit" / "taxa_labels.json"
BIOCLIP_V1 = HF / "birder" / "vit_b16_pn_bioclip-v1.pt"
METRICS_JSON = ROOT / "ml" / "model_cards" / "photo_verifier_v1_metrics.json"
SPLITS = ROOT / "data" / "photos_splits.csv"
MANIFEST = ROOT / "data" / "photos_manifest.csv"

CLASS_KEYS = tuple(c.key for c in CLASSES)
GENUS_INDEX = {g: i for i, g in enumerate(POSITIVE_GENERA)}
N_GENERA = len(POSITIVE_GENERA)
MANGROVE_TAXA_PREFIXES = ("Avicennia", "Rhizophora", "Laguncularia", "Conocarpus")
ZERO_SHOT_LOGIT_SCALE = 100.0  # CLIP-family convention; not tuned
N_BOOT = 1000
SEED = 0
IMG = 224
CLIP_MEAN = (0.48145466, 0.4578275, 0.40821073)
CLIP_STD = (0.26862954, 0.26130258, 0.27577711)
IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)
TEST_SPLITS = ("test_observer", "test_state")
# iNat21 (the student's distillation data) and TreeOfLife-10M (BioCLIP) contain iNaturalist photos
# up to 2020, so pre-2021 test images may have been seen in pretraining. Report this subset too.
POST_PRETRAINING = "2021-01-01"


# ---------------------------------------------------------------- metrics (numpy only)


def binary_at(y: np.ndarray, p: np.ndarray, thr: float) -> dict[str, float]:
    pred = p >= thr
    tp = float(np.sum(pred & (y == 1)))
    fp = float(np.sum(pred & (y == 0)))
    fn = float(np.sum(~pred & (y == 1)))
    tn = float(np.sum(~pred & (y == 0)))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    far = fp / (fp + tn) if fp + tn else 0.0
    return {"precision": precision, "recall": recall, "f1": f1, "false_accept_rate": far}


def auroc(y: np.ndarray, p: np.ndarray) -> float:
    """Mann-Whitney AUROC with average ranks for ties."""
    pos, neg = int(np.sum(y == 1)), int(np.sum(y == 0))
    if pos == 0 or neg == 0:
        return float("nan")
    order = np.argsort(p, kind="mergesort")
    ranks = np.empty(len(p), dtype=float)
    sorted_p = p[order]
    i = 0
    while i < len(p):
        j = i
        while j + 1 < len(p) and sorted_p[j + 1] == sorted_p[i]:
            j += 1
        ranks[order[i : j + 1]] = (i + j) / 2 + 1
        i = j + 1
    return float((ranks[y == 1].sum() - pos * (pos + 1) / 2) / (pos * neg))


def ece(y: np.ndarray, p: np.ndarray, bins: int = 10) -> float:
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, bins - 1)
    total = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            total += m.mean() * abs(float(p[m].mean()) - float(y[m].mean()))
    return total


def best_f1_threshold(y: np.ndarray, p: np.ndarray) -> float:
    candidates = np.unique(p)
    scores = [binary_at(y, p, float(t))["f1"] for t in candidates]
    return float(candidates[int(np.argmax(scores))])


def threshold_for_recall(y: np.ndarray, p: np.ndarray, target: float) -> float:
    """Largest threshold whose recall on (y, p) is ≥ target."""
    pos = np.sort(p[y == 1])[::-1]
    if len(pos) == 0:
        return 0.0
    k = int(np.ceil(target * len(pos))) - 1
    return float(pos[min(max(k, 0), len(pos) - 1)])


def threshold_for_precision(y: np.ndarray, p: np.ndarray, target: float) -> float:
    """Smallest threshold whose precision on (y, p) is ≥ target (1.0 if none)."""
    for t in np.unique(p):
        if binary_at(y, p, float(t))["precision"] >= target:
            return float(t)
    return 1.0


def genus_topk(true_idx: np.ndarray, scores: np.ndarray, k: int) -> float:
    if len(true_idx) == 0:
        return float("nan")
    top = np.argsort(-scores, axis=1)[:, :k]
    return float(np.mean([t in row for t, row in zip(true_idx, top, strict=True)]))


def cluster_bootstrap(
    groups: np.ndarray,
    stat: Callable[[np.ndarray], float],
    n_boot: int = N_BOOT,
    seed: int = SEED,
) -> tuple[float, float]:
    """95% percentile CI, resampling whole groups (observers) with replacement."""
    rng = np.random.default_rng(seed)
    uniq, inverse = np.unique(groups, return_inverse=True)
    members = [np.flatnonzero(inverse == g) for g in range(len(uniq))]
    values = []
    for _ in range(n_boot):
        pick = rng.integers(0, len(uniq), len(uniq))
        idx = np.concatenate([members[g] for g in pick])
        v = stat(idx)
        if not np.isnan(v):
            values.append(v)
    if not values:
        return float("nan"), float("nan")
    lo, hi = np.percentile(values, [2.5, 97.5])
    return float(lo), float(hi)


def evaluate_split(
    y: np.ndarray,
    p: np.ndarray,
    genus_true: np.ndarray,
    genus_scores: np.ndarray,
    groups: np.ndarray,
    thr: float,
    thr_recall95: float,
    n_boot: int = N_BOOT,
) -> dict[str, Any]:
    """Point estimates + observer-cluster bootstrap CIs. `genus_true` = -1 for negatives."""
    is_pos = genus_true >= 0

    def stats(idx: np.ndarray) -> dict[str, float]:
        yi, pi = y[idx], p[idx]
        b = binary_at(yi, pi, thr)
        g = idx[is_pos[idx]]
        return {
            **b,
            "auroc": auroc(yi, pi),
            "far_at_val_recall95": binary_at(yi, pi, thr_recall95)["false_accept_rate"],
            "ece": ece(yi, pi),
            "genus_top1": genus_topk(genus_true[g], genus_scores[g], 1),
            "genus_top3": genus_topk(genus_true[g], genus_scores[g], 3),
        }

    all_idx = np.arange(len(y))
    point = stats(all_idx)
    out: dict[str, Any] = {
        "n": len(y),
        "n_pos": int(y.sum()),
        "n_observers": len(np.unique(groups)),
    }
    for name, value in point.items():
        lo, hi = cluster_bootstrap(groups, lambda i, n=name: stats(i)[n], n_boot)
        out[name] = {"value": round(value, 4), "ci95": [round(lo, 4), round(hi, 4)]}
    return out


def paired_f1_diff_ci(
    y: np.ndarray,
    pa: np.ndarray,
    ta: float,
    pb: np.ndarray,
    tb: float,
    groups: np.ndarray,
    n_boot: int = N_BOOT,
) -> dict[str, Any]:
    def diff(idx: np.ndarray) -> float:
        return binary_at(y[idx], pa[idx], ta)["f1"] - binary_at(y[idx], pb[idx], tb)["f1"]

    lo, hi = cluster_bootstrap(groups, diff, n_boot)
    return {"value": round(diff(np.arange(len(y))), 4), "ci95": [round(lo, 4), round(hi, 4)]}


# ---------------------------------------------------------------- data


def load_dataset() -> pd.DataFrame:
    manifest = pd.read_csv(MANIFEST, dtype={"state": str})
    splits = pd.read_csv(SPLITS)[["photo_id", "split"]]
    df = manifest.merge(splits, on="photo_id", how="inner")
    df = df[df["split"] != "excluded_overlap"].reset_index(drop=True)
    df["path"] = [str(RAW / f) for f in df["file"]]
    df["y"] = (df["label"] == "mangrove").astype(int)
    df["class_idx"] = df["class_key"].map({k: i for i, k in enumerate(CLASS_KEYS)})
    df["genus_idx"] = df["genus"].map(GENUS_INDEX).fillna(-1).astype(int)
    return df


def _setup_caches() -> None:
    cache = ROOT / "ml" / "artifacts" / "cache"
    os.environ.setdefault("HF_HOME", str(cache / "hf"))
    os.environ.setdefault("TORCH_HOME", str(cache / "torch"))


def load_rgb_224(path: str) -> np.ndarray:
    """Production preprocessing: RGB, plain resize to 224x224 (bilinear), float32 0..1, CHW."""
    from PIL import Image

    with Image.open(path) as img:
        rgb = img.convert("RGB").resize((IMG, IMG), Image.Resampling.BILINEAR)
    return (np.asarray(rgb, dtype=np.float32) / 255.0).transpose(2, 0, 1)


def ort_session(model: Path | bytes, threads: int = 4) -> Any:
    import onnxruntime as ort

    opts = ort.SessionOptions()
    opts.intra_op_num_threads = threads
    opts.inter_op_num_threads = 1
    src = model if isinstance(model, bytes) else str(model)
    return ort.InferenceSession(src, sess_options=opts, providers=["CPUExecutionProvider"])


def embed_student(paths: Sequence[str]) -> np.ndarray:
    """The published student graph has a fixed batch size of 1."""
    sess = ort_session(STUDENT_ONNX)
    name = sess.get_inputs()[0].name
    out = []
    for i, path in enumerate(paths):
        out.append(sess.run(None, {name: load_rgb_224(path)[None]})[0][0])
        if i % 500 == 0:
            print(f"  student {i}/{len(paths)}", flush=True)
    return np.stack(out).astype(np.float32)


def bioclip_v1_visual() -> Any:
    """open_clip ViT-B-16 visual tower loaded with the BioCLIP weights from the birder checkpoint."""
    import open_clip
    import torch

    model = open_clip.create_model("ViT-B-16", pretrained=None)
    visual = model.visual
    del model
    st = torch.load(BIOCLIP_V1, map_location="cpu", weights_only=True)["state"]
    mapped: dict[str, Any] = {
        "class_embedding": st["class_token"].reshape(-1),
        "positional_embedding": st["pos_embedding"][0],
        "conv1.weight": st["conv_proj.weight"],
        "ln_pre.weight": st["encoder.pre_block.0.weight"],
        "ln_pre.bias": st["encoder.pre_block.0.bias"],
        "ln_post.weight": st["norm.weight"],
        "ln_post.bias": st["norm.bias"],
        "proj": st["classifier.weight"].T.contiguous(),
    }
    if float(st["classifier.bias"].abs().max()) != 0.0:
        raise ValueError("BioCLIP projection has a bias; open_clip's proj does not")
    for i in range(12):
        s, d = f"encoder.block.{i}.", f"transformer.resblocks.{i}."
        pairs = {
            "norm1": "ln_1",
            "norm2": "ln_2",
            "attn.proj": "attn.out_proj",
            "mlp.0": "mlp.c_fc",
            "mlp.3": "mlp.c_proj",
        }
        for a, b in pairs.items():
            mapped[f"{d}{b}.weight"] = st[f"{s}{a}.weight"]
            mapped[f"{d}{b}.bias"] = st[f"{s}{a}.bias"]
        mapped[f"{d}attn.in_proj_weight"] = st[f"{s}attn.qkv.weight"]
        mapped[f"{d}attn.in_proj_bias"] = st[f"{s}attn.qkv.bias"]
    visual.load_state_dict(mapped, strict=True)
    return visual.eval()


class NormalizedVisual:
    """Wraps an image tower so it takes the production input (RGB 0..1, NCHW) directly."""

    @staticmethod
    def build(visual: Any, mean: Sequence[float], std: Sequence[float]) -> Any:
        import torch

        class _Wrapped(torch.nn.Module):
            def __init__(self) -> None:
                super().__init__()
                self.visual = visual
                self.register_buffer("mean", torch.tensor(mean).view(1, 3, 1, 1))
                self.register_buffer("std", torch.tensor(std).view(1, 3, 1, 1))

            def forward(self, x: Any) -> Any:
                e = self.visual((x - self.mean) / self.std)
                return torch.nn.functional.normalize(e, dim=-1)

        return _Wrapped().eval()


def embed_bioclip_v1(paths: Sequence[str], batch: int = 32) -> np.ndarray:
    """Same plain 224 resize as production (no centre crop), CLIP normalisation folded in."""
    import torch

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = NormalizedVisual.build(bioclip_v1_visual(), CLIP_MEAN, CLIP_STD).to(device)
    out = []
    with torch.no_grad():
        for i in range(0, len(paths), batch):
            x = torch.from_numpy(np.stack([load_rgb_224(p) for p in paths[i : i + batch]]))
            out.append(model(x.to(device)).cpu().numpy())
            if i % 640 == 0:
                print(f"  bioclip_v1 {i}/{len(paths)}", flush=True)
    del model
    return np.concatenate(out).astype(np.float32)


def cmd_embed(df: pd.DataFrame) -> None:
    ART.mkdir(parents=True, exist_ok=True)
    for name, fn in (("student", embed_student), ("bioclip_v1", embed_bioclip_v1)):
        path = ART / f"emb_{name}.npz"
        if path.exists() and set(np.load(path)["photo_id"]) == set(df["photo_id"]):
            continue
        t0 = time.time()
        emb = fn(list(df["path"]))
        np.savez(path, photo_id=df["photo_id"].to_numpy(), emb=emb)
        print(f"{name}: {emb.shape} in {time.time() - t0:.0f} s", flush=True)


def load_emb(name: str, df: pd.DataFrame) -> np.ndarray:
    data = np.load(ART / f"emb_{name}.npz")
    index = {int(p): i for i, p in enumerate(data["photo_id"])}
    return data["emb"][[index[int(p)] for p in df["photo_id"]]]


# ---------------------------------------------------------------- candidates
# Each returns (p_mangrove (N,), genus_scores (N, 11)) for every row of df.


def zero_shot_student(emb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    table = np.load(STUDENT_TABLE, allow_pickle=False)
    labels = [t["scientific"] for t in json.loads(STUDENT_LABELS.read_text())]
    logits = ZERO_SHOT_LOGIT_SCALE * emb @ table.T
    logits -= logits.max(axis=1, keepdims=True)
    probs = np.exp(logits)
    probs /= probs.sum(axis=1, keepdims=True)
    genus_of = np.array([s.split(" ")[0] for s in labels])
    mangrove = np.isin(genus_of, MANGROVE_TAXA_PREFIXES)
    p_mangrove = probs[:, mangrove].sum(axis=1)
    genus_scores = np.full((len(emb), N_GENERA), -np.inf)
    for g, gi in GENUS_INDEX.items():
        cols = genus_of == g
        if cols.any():
            genus_scores[:, gi] = probs[:, cols].sum(axis=1)
    return p_mangrove, genus_scores


def split_class_probs(probs: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    genus_cols = [CLASS_KEYS.index(g.lower()) for g in POSITIVE_GENERA]
    return probs[:, genus_cols].sum(axis=1), probs[:, genus_cols]


def linear_probe(emb: np.ndarray, df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, Any]:
    from sklearn.linear_model import LogisticRegression

    tr, va = (df["split"] == "train").to_numpy(), (df["split"] == "val").to_numpy()
    y = df["class_idx"].to_numpy()
    best: tuple[float, Any] | None = None
    for c in (0.1, 0.3, 1.0, 3.0, 10.0, 30.0):
        clf = LogisticRegression(C=c, max_iter=3000)
        clf.fit(emb[tr], y[tr])
        p = clf.predict_proba(emb[va])
        loss = -np.mean(np.log(np.clip(p[np.arange(va.sum()), y[va]], 1e-12, 1)))
        print(f"    C={c:<5} val log-loss {loss:.4f}", flush=True)
        if best is None or loss < best[0]:
            best = (loss, clf)
    if best is None:
        raise RuntimeError("no probe was fitted")
    clf = best[1]
    if list(clf.classes_) != list(range(len(CLASS_KEYS))):
        raise ValueError("every class must appear in the training split")
    pm, gs = split_class_probs(clf.predict_proba(emb))
    return pm, gs, clf


class _Images:
    def __init__(self, paths: Sequence[str], labels: Sequence[int], tf: Any) -> None:
        self.paths, self.labels, self.tf = list(paths), list(labels), tf

    def __len__(self) -> int:
        return len(self.paths)

    def __getitem__(self, i: int) -> tuple[Any, int]:
        from PIL import Image

        with Image.open(self.paths[i]) as im:
            return self.tf(im.convert("RGB")), self.labels[i]


def mobilenet_model(n_classes: int, pretrained: bool = True) -> Any:
    import torch
    from torchvision.models import MobileNet_V2_Weights, mobilenet_v2

    model = mobilenet_v2(weights=MobileNet_V2_Weights.IMAGENET1K_V2 if pretrained else None)
    model.classifier[1] = torch.nn.Linear(model.last_channel, n_classes)
    return model


def mobilenet_finetune(
    df: pd.DataFrame, epochs: int = 12, batch: int = 32
) -> tuple[np.ndarray, np.ndarray, Any]:
    import torch
    from torch.utils.data import DataLoader
    from torchvision import transforms as T

    torch.manual_seed(SEED)
    # CPU, not MPS: on MPS the unified-memory allocator pushed the process footprint to 5.4 GB
    # (run 1), over the 3 GB limit. CPU training keeps it bounded and is deterministic.
    device = "cpu"
    torch.set_num_threads(4)
    norm = T.Normalize(IMAGENET_MEAN, IMAGENET_STD)
    train_tf = T.Compose(
        [
            T.RandomResizedCrop(IMG, scale=(0.4, 1.0)),
            T.RandomHorizontalFlip(),
            T.ColorJitter(0.2, 0.2, 0.2),
            T.ToTensor(),
            norm,
        ]
    )
    eval_tf = T.Compose([T.Resize((IMG, IMG)), T.ToTensor(), norm])  # = production resize
    tr = df[df["split"] == "train"]
    va = df[df["split"] == "val"]
    train_dl = DataLoader(
        _Images(tr["path"], tr["class_idx"], train_tf), batch_size=batch, shuffle=True
    )
    model = mobilenet_model(len(CLASS_KEYS)).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=5e-4, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=epochs)
    loss_fn = torch.nn.CrossEntropyLoss(label_smoothing=0.1)

    def predict(rows: pd.DataFrame) -> np.ndarray:
        dl = DataLoader(_Images(rows["path"], rows["class_idx"], eval_tf), batch_size=64)
        model.eval()
        outs = []
        with torch.no_grad():
            for x, _ in dl:
                outs.append(torch.softmax(model(x.to(device)), -1).cpu().numpy())
        return np.concatenate(outs)

    best_loss, best_state = float("inf"), None
    yv = va["class_idx"].to_numpy()
    for epoch in range(epochs):
        model.train()
        t0 = time.time()
        for x, yb in train_dl:
            opt.zero_grad()
            loss = loss_fn(model(x.to(device)), yb.to(device))
            loss.backward()
            opt.step()
        sched.step()
        pv = predict(va)
        vloss = float(-np.mean(np.log(np.clip(pv[np.arange(len(yv)), yv], 1e-12, 1))))
        print(f"    epoch {epoch + 1}: val log-loss {vloss:.4f} ({time.time() - t0:.0f} s)")
        if vloss < best_loss:
            best_loss = vloss
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
    if best_state is None:
        raise RuntimeError("no epoch finished")
    model.load_state_dict(best_state)
    pm, gs = split_class_probs(predict(df))
    return pm, gs, model.cpu().eval()


# ---------------------------------------------------------------- evaluation


def score_candidate(df: pd.DataFrame, pm: np.ndarray, gs: np.ndarray) -> dict[str, Any]:
    y = df["y"].to_numpy()
    va = (df["split"] == "val").to_numpy()
    thr = best_f1_threshold(y[va], pm[va])
    thr95 = threshold_for_recall(y[va], pm[va], 0.95)
    out: dict[str, Any] = {
        "threshold_val_best_f1": round(thr, 6),
        "threshold_val_recall95": round(thr95, 6),
        "threshold_val_precision95": round(threshold_for_precision(y[va], pm[va], 0.95), 6),
    }
    recent = (df["observed_on"].fillna("").astype(str) >= POST_PRETRAINING).to_numpy()
    subsets = {s: (df["split"] == s).to_numpy() for s in ("val", *TEST_SPLITS)}
    subsets |= {f"{s}_observed_2021plus": subsets[s] & recent for s in TEST_SPLITS}
    for split, m in subsets.items():
        out[split] = evaluate_split(
            y[m],
            pm[m],
            df["genus_idx"].to_numpy()[m],
            gs[m],
            df["observer_id"].to_numpy()[m],
            thr,
            thr95,
        )
    return out


def cmd_evaluate(df: pd.DataFrame) -> dict[str, Any]:
    ART.mkdir(parents=True, exist_ok=True)
    preds: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    emb_s = load_emb("student", df)
    emb_b = load_emb("bioclip_v1", df)
    print("zero-shot (student)", flush=True)
    preds["zeroshot_bioclip25_student"] = zero_shot_student(emb_s)
    import joblib

    for name, emb in (("probe_bioclip25_student", emb_s), ("probe_bioclip_v1", emb_b)):
        print(name, flush=True)
        pm, gs, clf = linear_probe(emb, df)
        preds[name] = (pm, gs)
        joblib.dump(clf, ART / f"{name}.joblib")
    print("mobilenetv2_finetune", flush=True)
    import torch

    pm, gs, model = mobilenet_finetune(df)
    preds["mobilenetv2_finetune"] = (pm, gs)
    torch.save(model.state_dict(), ART / "mobilenetv2_finetune.pt")
    np.savez(
        ART / "predictions.npz",
        photo_id=df["photo_id"].to_numpy(),
        **{f"{k}__pm": v[0] for k, v in preds.items()},
        **{f"{k}__gs": v[1] for k, v in preds.items()},
    )
    return summarise(df, preds)


def summarise(df: pd.DataFrame, preds: dict[str, tuple[np.ndarray, np.ndarray]]) -> dict[str, Any]:
    results = {name: score_candidate(df, pm, gs) for name, (pm, gs) in preds.items()}
    ranked = sorted(results, key=lambda n: -results[n]["test_observer"]["f1"]["value"])
    m = (df["split"] == "test_observer").to_numpy()
    a, b = ranked[0], ranked[1]
    diff = paired_f1_diff_ci(
        df["y"].to_numpy()[m],
        preds[a][0][m],
        results[a]["threshold_val_best_f1"],
        preds[b][0][m],
        results[b]["threshold_val_best_f1"],
        df["observer_id"].to_numpy()[m],
    )
    counts = {
        s: {
            "images": int((df["split"] == s).sum()),
            "mangrove": int(((df["split"] == s) & (df["y"] == 1)).sum()),
            "observers": int(df.loc[df["split"] == s, "observer_id"].nunique()),
        }
        for s in ("train", "val", *TEST_SPLITS)
    }
    recent = df["observed_on"].fillna("").astype(str) >= POST_PRETRAINING
    for s in TEST_SPLITS:
        m = (df["split"] == s) & recent
        counts[f"{s}_observed_2021plus"] = {
            "images": int(m.sum()),
            "mangrove": int((m & (df["y"] == 1)).sum()),
            "observers": int(df.loc[m, "observer_id"].nunique()),
        }
    out = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "bootstrap": f"{N_BOOT} observer-cluster resamples, 95% percentile CI",
        "splits": counts,
        "ranking_by_test_observer_f1": ranked,
        "top2_paired_f1_difference": {"a": a, "b": b, **diff},
        "candidates": results,
    }
    METRICS_JSON.write_text(json.dumps(out, indent=1) + "\n")
    print_table(out)
    return out


def print_table(out: dict[str, Any]) -> None:
    keys = ("precision", "recall", "f1", "auroc", "genus_top1", "genus_top3")
    for split in (*TEST_SPLITS, *(f"{s}_observed_2021plus" for s in TEST_SPLITS)):
        print(f"\n{split}")
        for name, r in out["candidates"].items():
            cells = []
            for k in keys:
                v = r[split][k]
                cells.append(f"{k}={v['value']:.3f}[{v['ci95'][0]:.3f},{v['ci95'][1]:.3f}]")
            print(f"  {name:28s} " + " ".join(cells))
    print("\npaired F1 diff:", out["top2_paired_f1_difference"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=["embed", "evaluate"])
    args = parser.parse_args(argv)
    _setup_caches()
    df = load_dataset()
    print(df.groupby("split").size().to_dict(), flush=True)
    if args.command == "embed":
        cmd_embed(df)
    else:
        cmd_evaluate(df)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
