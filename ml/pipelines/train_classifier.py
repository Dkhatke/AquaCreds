"""Train and compare ecosystem classifiers (Random Forest baseline vs LightGBM vs XGBoost).

Usage:  uv run python -u ml/pipelines/train_classifier.py [--trials 30] [--jobs 4]

Inputs : data/processed/features.parquet  (features_extract.py)
Outputs: ml/artifacts/ecosystem_classifier_v1/  winning model + features.json (DVC out)
         ml/model_cards/ecosystem_classifier_v1_metrics.json   every model's scores (DVC metrics)
         mlruns/mlflow.db                                      MLflow tracking (git-ignored)

Validation (no random splits anywhere):
- Spatial 5-fold CV: `split` = fold_0..fold_4 were assigned per H3 res-5 cell (splits.py), so a
  cell never contributes to both training and validation.
- Held-out region: Gulf of Kutch (`heldout_region`), never used for training or tuning, scored
  once per model after refitting on all five folds.
- Optuna tunes each model on the mean CV macro-F1 only. Because the same folds are used to tune
  and to report CV, CV scores are slightly optimistic; the held-out region is the clean estimate.

Selection: held-out macro-F1, then CV macro-F1 (ties = within 0.01), then simplicity
(classifier_eval.select_winner).
"""

import argparse
import copy
import hashlib
import json
import os
import resource
import subprocess
import sys
import time
import warnings
from pathlib import Path
from typing import Any

import joblib
import lightgbm as lgb
import mlflow
import numpy as np
import optuna
import pandas as pd
from classifier_eval import Candidate, calibration, mean_std, report, select_winner
from sklearn.ensemble import RandomForestClassifier
from sklearn.utils.class_weight import compute_sample_weight
from xgboost import XGBClassifier

from aqua_geo.features import FEATURE_VARS

os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")

ROOT = Path(__file__).resolve().parents[2]
FEATURES = ROOT / "data/processed/features.parquet"
ART = ROOT / "ml/artifacts/ecosystem_classifier_v1"
METRICS = ROOT / "ml/model_cards/ecosystem_classifier_v1_metrics.json"
MLRUNS = ROOT / "mlruns"

MODEL_VERSION = "ecosystem_classifier_v1"
SEED = 42
FOLDS = [f"fold_{i}" for i in range(5)]
HELDOUT = "heldout_region"
MIN_CLEAR = 3
POSITIVE = "mangrove"
# clear_count describes acquisition (cloudiness), not the surface, so it is not a model input.
FEATURE_COLS = [c for c in FEATURE_VARS if c != "clear_count"] + ["elevation"]
COMPLEXITY = {"random_forest": 1, "lightgbm": 2, "xgboost": 3}
# Copernicus DEM has no tiles over open sea, so elevation is missing for a few offshore water
# points; sea level (0 m) is the physically correct value. Production must apply the same fill.
FILL_VALUES = {"elevation": 0.0}


# --------------------------------------------------------------------------- models


def make_model(name: str, params: dict[str, Any], jobs: int) -> Any:
    if name == "random_forest":
        return RandomForestClassifier(
            class_weight="balanced", n_jobs=jobs, random_state=SEED, **params
        )
    if name == "lightgbm":
        return lgb.LGBMClassifier(
            objective="multiclass",
            class_weight="balanced",
            subsample_freq=1,
            n_jobs=jobs,
            random_state=SEED,
            verbose=-1,
            **params,
        )
    if name == "xgboost":
        return XGBClassifier(
            objective="multi:softprob",
            tree_method="hist",
            n_jobs=jobs,
            random_state=SEED,
            **params,
        )
    raise ValueError(name)


def fit(name: str, params: dict[str, Any], x: pd.DataFrame, y: np.ndarray, jobs: int) -> Any:
    model = make_model(name, params, jobs)
    if name == "xgboost":  # no class_weight option: balanced sample weights instead
        model.fit(x, y, sample_weight=compute_sample_weight("balanced", y))
    else:
        model.fit(x, y)
    return model


def suggest(name: str, trial: optuna.Trial) -> dict[str, Any]:
    if name == "random_forest":
        return {
            "n_estimators": trial.suggest_int("n_estimators", 200, 600, step=100),
            "max_depth": trial.suggest_categorical("max_depth", [None, 12, 20, 30]),
            "min_samples_leaf": trial.suggest_int("min_samples_leaf", 1, 10),
            "max_features": trial.suggest_categorical("max_features", ["sqrt", 0.3, 0.5]),
        }
    if name == "lightgbm":
        return {
            "n_estimators": trial.suggest_int("n_estimators", 100, 600, step=50),
            "learning_rate": trial.suggest_float("learning_rate", 0.02, 0.2, log=True),
            "num_leaves": trial.suggest_int("num_leaves", 8, 96, log=True),
            "min_child_samples": trial.suggest_int("min_child_samples", 10, 100, log=True),
            "subsample": trial.suggest_float("subsample", 0.6, 1.0),
            "colsample_bytree": trial.suggest_float("colsample_bytree", 0.5, 1.0),
            "reg_lambda": trial.suggest_float("reg_lambda", 1e-3, 10.0, log=True),
        }
    if name == "xgboost":
        return {
            "n_estimators": trial.suggest_int("n_estimators", 100, 600, step=50),
            "learning_rate": trial.suggest_float("learning_rate", 0.02, 0.2, log=True),
            "max_depth": trial.suggest_int("max_depth", 3, 10),
            "min_child_weight": trial.suggest_float("min_child_weight", 1.0, 20.0, log=True),
            "subsample": trial.suggest_float("subsample", 0.6, 1.0),
            "colsample_bytree": trial.suggest_float("colsample_bytree", 0.5, 1.0),
            "reg_lambda": trial.suggest_float("reg_lambda", 1e-3, 10.0, log=True),
        }
    raise ValueError(name)


# --------------------------------------------------------------------------- evaluation


def cross_validate(
    name: str,
    params: dict[str, Any],
    df: pd.DataFrame,
    y: np.ndarray,
    classes: list[str],
    jobs: int,
) -> dict[str, Any]:
    """Spatial CV over fold_0..fold_4. Returns per-fold metrics + pooled out-of-fold report."""
    folds = []
    oof = np.full(len(df), -1)
    for fold in FOLDS:
        val = (df.split == fold).to_numpy()
        model = fit(name, params, df.loc[~val, FEATURE_COLS], y[~val], jobs)
        pred = np.asarray(model.predict(df.loc[val, FEATURE_COLS])).astype(int)
        oof[val] = pred
        folds.append(report(y[val], pred, classes, POSITIVE))
    keys = ["macro_f1", "accuracy", f"{POSITIVE}_vs_rest_f1"]
    return {
        "per_fold": [{k: round(f[k], 4) for k in keys} | {"n": f["n"]} for f in folds],
        **{k: mean_std([f[k] for f in folds]) for k in keys},
        "pooled_out_of_fold": report(y, oof, classes, POSITIVE),
    }


def tune(
    name: str, df: pd.DataFrame, y: np.ndarray, classes: list[str], trials: int, jobs: int
) -> tuple[dict[str, Any], float]:
    def objective(trial: optuna.Trial) -> float:
        params = suggest(name, trial)
        scores = []
        for fold in FOLDS:
            val = (df.split == fold).to_numpy()
            model = fit(name, params, df.loc[~val, FEATURE_COLS], y[~val], jobs)
            pred = np.asarray(model.predict(df.loc[val, FEATURE_COLS])).astype(int)
            scores.append(report(y[val], pred, classes, POSITIVE)["macro_f1"])
            trial.report(float(np.mean(scores)), len(scores) - 1)
        return float(np.mean(scores))

    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=SEED),
        study_name=f"{MODEL_VERSION}-{name}",
    )
    study.optimize(objective, n_trials=trials, show_progress_bar=False)
    return dict(study.best_params), float(study.best_value)


def importances(name: str, model: Any) -> dict[str, float]:
    if name == "lightgbm":
        raw = model.booster_.feature_importance(importance_type="gain")
    else:
        raw = model.feature_importances_
    raw = np.asarray(raw, dtype=float)
    norm = raw / raw.sum() if raw.sum() > 0 else raw
    order = np.argsort(-norm)
    return {FEATURE_COLS[i]: round(float(norm[i]), 4) for i in order}


def export_onnx(name: str, model: Any, x: pd.DataFrame, path: Path) -> dict[str, Any]:
    """Convert the winner to ONNX and verify it against the native model on `x`: identical
    predicted classes and max |probability difference| (float32 vs float64 trees)."""
    import onnxruntime as ort
    from onnxmltools.convert.common.data_types import FloatTensorType

    n = x.shape[1]
    if name == "lightgbm":
        from onnxmltools import convert_lightgbm

        onx = convert_lightgbm(
            model, initial_types=[("input", FloatTensorType([None, n]))], zipmap=False
        )
    elif name == "xgboost":
        from onnxmltools import convert_xgboost

        # onnxmltools requires XGBoost's positional feature names (f0, f1, ...).
        plain = copy.deepcopy(model)
        plain.get_booster().feature_names = None
        onx = convert_xgboost(plain, initial_types=[("input", FloatTensorType([None, n]))])
    else:
        from skl2onnx import to_onnx

        onx = to_onnx(
            model,
            x.to_numpy(dtype=np.float32)[:1],
            options={id(model): {"zipmap": False}},
        )
        onx.graph.input[0].name = "input"
        for node in onx.graph.node:
            node.input[:] = ["input" if i == "X" else i for i in node.input]
    path.write_bytes(onx.SerializeToString())

    sess = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    in_name = sess.get_inputs()[0].name
    label, proba = sess.run(None, {in_name: x.to_numpy(dtype=np.float32)})[:2]
    native_label = np.asarray(model.predict(x)).astype(int)
    native_proba = np.asarray(model.predict_proba(x))
    return {
        "points": len(x),
        "label_agreement": round(float((np.asarray(label).astype(int) == native_label).mean()), 6),
        "max_abs_proba_diff": float(np.abs(np.asarray(proba) - native_proba).max()),
        "onnx_bytes": path.stat().st_size,
    }


# --------------------------------------------------------------------------- utilities


def peak_rss_mb() -> float:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss / 1e6 if sys.platform == "darwin" else rss / 1e3


def file_md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes(), usedforsecurity=False).hexdigest()


def git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S607
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def load(path: Path) -> tuple[pd.DataFrame, list[str], dict[str, Any]]:
    raw = pd.read_parquet(path)
    usable = raw[raw.clear_count >= MIN_CLEAR].reset_index(drop=True)
    filled = {c: int(usable[c].isna().sum()) for c in FILL_VALUES}
    usable = usable.fillna(FILL_VALUES)
    remaining = usable[FEATURE_COLS].isna().sum()
    if remaining.any():
        raise SystemExit(f"unexpected missing features: {remaining[remaining > 0].to_dict()}")
    classes = sorted(usable.label.unique())
    info = {
        "rows_in_features": len(raw),
        "rows_dropped_too_few_clear": int((raw.clear_count < MIN_CLEAR).sum()),
        "rows_used": len(usable),
        "filled_missing": {c: {"rows": n, "value": FILL_VALUES[c]} for c, n in filled.items()},
        "by_label_and_split": usable.groupby(["label", "split"])
        .size()
        .unstack(fill_value=0)
        .to_dict(),
    }
    return usable, classes, info


# --------------------------------------------------------------------------- main


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--trials", type=int, default=30, help="Optuna trials per model")
    parser.add_argument("--jobs", type=int, default=4, help="CPU threads per model")
    parser.add_argument("--models", nargs="+", default=["random_forest", "lightgbm", "xgboost"])
    args = parser.parse_args()
    t_start = time.time()
    warnings.filterwarnings("ignore", category=UserWarning)  # sklearn feature-name chatter
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    df, classes, data_info = load(FEATURES)
    y = df.label.map({c: i for i, c in enumerate(classes)}).to_numpy()
    train = df.split.isin(FOLDS).to_numpy()
    test = (df.split == HELDOUT).to_numpy()
    dtrain, ytrain = df[train].reset_index(drop=True), y[train]
    print(f"{len(df)} usable points; classes {classes}; train {train.sum()}, held-out {test.sum()}")

    majority = int(np.bincount(ytrain).argmax())
    reference = {
        "majority_class": classes[majority],
        "heldout": report(y[test], np.full(test.sum(), majority), classes, POSITIVE),
    }

    # Local tracking in mlruns/ (git-ignored). MLflow 3.x refuses the plain file store, so runs
    # go to a SQLite file there; browse with `uv run mlflow ui --backend-store-uri sqlite:///mlruns/mlflow.db`.
    MLRUNS.mkdir(parents=True, exist_ok=True)
    mlflow.set_tracking_uri(f"sqlite:///{MLRUNS / 'mlflow.db'}")
    if mlflow.get_experiment_by_name(MODEL_VERSION) is None:
        mlflow.create_experiment(MODEL_VERSION, artifact_location=(MLRUNS / "artifacts").as_uri())
    mlflow.set_experiment(MODEL_VERSION)
    features_md5 = file_md5(FEATURES)
    sha = git_sha()

    results: dict[str, dict[str, Any]] = {}
    fitted: dict[str, Any] = {}
    for name in args.models:
        t0 = time.time()
        print(f"[{name}] tuning ({args.trials} trials on spatial CV)…", flush=True)
        params, best_cv = tune(name, dtrain, ytrain, classes, args.trials, args.jobs)
        cv = cross_validate(name, params, dtrain, ytrain, classes, args.jobs)
        t_fit = time.time()
        model = fit(name, params, dtrain[FEATURE_COLS], ytrain, args.jobs)
        fit_s = time.time() - t_fit
        t_pred = time.time()
        pred = np.asarray(model.predict(df.loc[test, FEATURE_COLS])).astype(int)
        pred_ms = (time.time() - t_pred) / max(int(test.sum()), 1) * 1000
        heldout = report(y[test], pred, classes, POSITIVE)
        heldout["calibration"] = calibration(
            np.asarray(model.predict_proba(df.loc[test, FEATURE_COLS])), y[test]
        )
        results[name] = {
            "params": params,
            "optuna_best_cv_macro_f1": round(best_cv, 4),
            "cv": cv,
            "heldout": heldout,
            "feature_importance": importances(name, model),
            "fit_seconds_full_train": round(fit_s, 2),
            "predict_ms_per_point": round(pred_ms, 4),
            "tuning_and_eval_seconds": round(time.time() - t0, 1),
        }
        fitted[name] = model
        print(
            f"[{name}] CV macro-F1 {cv['macro_f1']['mean']:.3f}±{cv['macro_f1']['std']:.3f} | "
            f"held-out macro-F1 {heldout['macro_f1']:.3f}, acc {heldout['accuracy']:.3f}, "
            f"mangrove F1 {heldout['mangrove_vs_rest_f1']:.3f}",
            flush=True,
        )
        with mlflow.start_run(run_name=name):
            mlflow.set_tags({"git_sha": sha, "features_md5": features_md5, "model": name})
            mlflow.log_params({f"p_{k}": v for k, v in params.items()} | {"trials": args.trials})
            mlflow.log_metrics(
                {
                    "cv_macro_f1_mean": cv["macro_f1"]["mean"],
                    "cv_macro_f1_std": cv["macro_f1"]["std"],
                    "cv_accuracy_mean": cv["accuracy"]["mean"],
                    "cv_mangrove_f1_mean": cv["mangrove_vs_rest_f1"]["mean"],
                    "heldout_macro_f1": heldout["macro_f1"],
                    "heldout_accuracy": heldout["accuracy"],
                    "heldout_mangrove_f1": heldout["mangrove_vs_rest_f1"],
                }
            )

    winner = select_winner(
        [
            Candidate(
                name=n,
                heldout_macro_f1=r["heldout"]["macro_f1"],
                cv_macro_f1=r["cv"]["macro_f1"]["mean"],
                complexity=COMPLEXITY[n],
            )
            for n, r in results.items()
        ]
    )
    print(f"winner: {winner.name}")

    ART.mkdir(parents=True, exist_ok=True)
    for old in ART.iterdir():
        old.unlink()
    model = fitted[winner.name]
    if winner.name == "lightgbm":
        model_file = "model.lgb.txt"
        model.booster_.save_model(str(ART / model_file))
    else:
        model_file = "model.joblib"
        joblib.dump(model, ART / model_file, compress=3)
    onnx_check = export_onnx(winner.name, model, df.loc[test, FEATURE_COLS], ART / "model.onnx")
    print(f"ONNX parity on held-out: {onnx_check}")
    (ART / "features.json").write_text(
        json.dumps(
            {
                "model_version": MODEL_VERSION,
                "algorithm": winner.name,
                "model_file": model_file,
                "onnx_file": "model.onnx",
                "onnx_input": "input (float32, [n, len(feature_columns)])",
                "onnx_outputs": ["label (int64 class index)", "probabilities (float32)"],
                "fill_values": FILL_VALUES,
                "feature_columns": FEATURE_COLS,
                "classes": classes,  # predicted index i → classes[i]
                "positive_class": POSITIVE,
                "min_clear": MIN_CLEAR,
                "feature_code": "aqua_geo.features.pixel_features + Copernicus DEM elevation",
                "season": "dry season Nov 2020 - Mar 2021, Sentinel-2 L2A",
                "params": results[winner.name]["params"],
                "features_md5": features_md5,
                "git_sha": sha,
            },
            indent=2,
        )
        + "\n"
    )

    metrics = {
        "model_version": MODEL_VERSION,
        "winner": winner.name,
        "selection_rule": "held-out macro-F1, then CV macro-F1 (ties within 0.01), then simplicity",
        "classes": classes,
        "data": data_info,
        "reference_majority_class": reference,
        "models": results,
        "onnx_parity_heldout": onnx_check,
        "summary": {
            n: {
                "cv_macro_f1": r["cv"]["macro_f1"],
                "cv_accuracy": r["cv"]["accuracy"],
                "cv_mangrove_f1": r["cv"]["mangrove_vs_rest_f1"],
                "heldout_macro_f1": round(r["heldout"]["macro_f1"], 4),
                "heldout_accuracy": round(r["heldout"]["accuracy"], 4),
                "heldout_mangrove_f1": round(r["heldout"]["mangrove_vs_rest_f1"], 4),
                "heldout_mangrove_precision": r["heldout"]["per_class"]["mangrove"]["precision"],
                "heldout_mangrove_recall": r["heldout"]["per_class"]["mangrove"]["recall"],
                "heldout_calibration": r["heldout"]["calibration"],
            }
            for n, r in results.items()
        },
        "trials_per_model": args.trials,
        "features_md5": features_md5,
        "runtime_s": round(time.time() - t_start, 1),
        "peak_rss_mb": round(peak_rss_mb(), 1),
    }
    METRICS.parent.mkdir(parents=True, exist_ok=True)
    METRICS.write_text(json.dumps(metrics, indent=2) + "\n")
    print(json.dumps(metrics["summary"], indent=2))


if __name__ == "__main__":
    main()
