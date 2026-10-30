"""Published model versions: pinned file hashes and the evaluation metrics of their model cards.

Pure data (no numpy / ONNX Runtime), so both the worker (`aqua_worker.model_store`, which loads
and verifies the files) and the API (`GET /api/v1/models/...`, classification responses) read the
same manifests. Every metric here is copied from `ml/model_cards/<version>_metrics.json`, and
tests check the copies still match those files (services/worker/tests/test_classifier.py,
test_photo_verifier.py and services/api/tests/test_model_info.py), so the API never shows a
number the model card does not contain.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

MAX_MODEL_FILE_BYTES = 64 * 1024 * 1024

ECOSYSTEM_CLASSIFIER = "ecosystem-classifier"
PHOTO_VERIFIER = "photo-verifier"


@dataclass(frozen=True)
class ModelManifest:
    """Everything the worker needs to know about one published model version."""

    version: str
    prefix: str  # object-storage key prefix (ends with "/")
    model_file: str
    features_file: str
    sha256: Mapping[str, str]  # file name → SHA-256 (hex)
    # Scene stack the model was trained on (features_extract.py config); satellite models only.
    season_months: tuple[int, ...] = ()
    max_scenes: int = 0
    max_tile_cloud_pct: float = 0.0
    # Held-out metrics from the model card, quoted in MRV reports (do not add keys casually: the
    # MRV report record that quotes them is hashed and anchored).
    heldout_metrics: Mapping[str, Any] = field(default_factory=dict)
    model_card: str = ""
    # Largest file `fetch` will download for this model (bounded read from object storage).
    max_file_bytes: int = MAX_MODEL_FILE_BYTES
    # Shown by the API only (not quoted in MRV reports).
    family: str = ""
    algorithm: str = ""
    # Held-out per-class precision / recall / f1 / support (classifiers).
    heldout_per_class: Mapping[str, Mapping[str, float]] = field(default_factory=dict)
    # Cross-validation summary (optimistic: same folds were used for tuning).
    cv_metrics: Mapping[str, Any] = field(default_factory=dict)
    # False when the files can no longer be loaded (kept for provenance of stored results).
    files_available: bool = True

    @property
    def files(self) -> tuple[str, ...]:
        return tuple(self.sha256)

    @property
    def metrics_file(self) -> str:
        """The model card's metrics JSON (`ml/model_cards/<stem>_metrics.json`)."""
        return self.model_card.removesuffix(".md") + "_metrics.json" if self.model_card else ""


# Copied from ml/model_cards/ecosystem_classifier_v1{,_1}_metrics.json, models.lightgbm.heldout
# .per_class (identical for v1 and v1.1: the retrain reproduced every metric to 4 decimals).
_CLASSIFIER_V1_PER_CLASS: Mapping[str, Mapping[str, float]] = {
    "built_bare": {"precision": 0.4141, "recall": 0.7736, "f1": 0.5395, "support": 53},
    "cropland_aquaculture": {"precision": 0.5575, "recall": 0.5431, "f1": 0.5502, "support": 116},
    "mangrove": {"precision": 0.9825, "recall": 0.9106, "f1": 0.9451, "support": 123},
    "terrestrial_other": {"precision": 0.5825, "recall": 0.6186, "f1": 0.6, "support": 97},
    "tidal_flat": {"precision": 0.2397, "recall": 0.5088, "f1": 0.3258, "support": 57},
    "water": {"precision": 0.9434, "recall": 0.8006, "f1": 0.8661, "support": 687},
}
# models.lightgbm.cv (5 spatial folds, H3 cells).
_CLASSIFIER_V1_CV: Mapping[str, Any] = {
    "folds": 5,
    "macro_f1_mean": 0.7596,
    "macro_f1_std": 0.0254,
    "accuracy_mean": 0.9128,
    "mangrove_vs_rest_f1_mean": 0.9729,
    "note": "spatial CV is slightly optimistic (same folds for tuning); the held-out region is "
    "the clean estimate",
}

# v1 is kept for provenance only: classifications stored with model_version
# "ecosystem_classifier_v1" refer to these pins and metrics. Its files are UNAVAILABLE (the DVC
# outputs were never pushed to a remote and are lost), so no worker can load it; v1.1 replaces it.
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
    family=ECOSYSTEM_CLASSIFIER,
    algorithm="lightgbm",
    heldout_per_class=_CLASSIFIER_V1_PER_CLASS,
    cv_metrics=_CLASSIFIER_V1_CV,
    files_available=False,
)

# v1.1: the same training configuration retrained on re-extracted (byte-identical) features after
# the v1 files were lost; every metric equals v1's to 4 decimals, but the bytes are new, hence a
# new version. Classifications by v1 and v1.1 are never compared with each other (area-loss
# alerts and stewardship comparisons match on model_version).
ECOSYSTEM_CLASSIFIER_V1_1 = ModelManifest(
    version="ecosystem_classifier_v1.1",
    prefix="models/ecosystem_classifier/v1.1/",
    model_file="model.onnx",
    features_file="features.json",
    sha256={
        "model.onnx": "bcdd2297fe0caec6d6c66f775a8d01d9387dc7d9e12c81c03ebef80cbb4f7542",
        "features.json": "9c16c74d383c4a0bd5275ba8bb68259df91925588828ae478ba82e5a42c42311",
    },
    season_months=(11, 12, 1, 2, 3),
    max_scenes=12,
    max_tile_cloud_pct=80.0,
    # Copied from ml/model_cards/ecosystem_classifier_v1_1_metrics.json (models.lightgbm.heldout,
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
    model_card="ml/model_cards/ecosystem_classifier_v1_1.md",
    family=ECOSYSTEM_CLASSIFIER,
    algorithm="lightgbm",
    heldout_per_class=_CLASSIFIER_V1_PER_CLASS,
    cv_metrics=_CLASSIFIER_V1_CV,
)

# The model new classifications use. Change it only together with a new manifest above.
CURRENT_ECOSYSTEM_CLASSIFIER = ECOSYSTEM_CLASSIFIER_V1_1

MANIFESTS: Mapping[str, ModelManifest] = {
    m.version: m for m in (ECOSYSTEM_CLASSIFIER_V1, ECOSYSTEM_CLASSIFIER_V1_1)
}


def manifest_for(version: str) -> ModelManifest | None:
    """The ecosystem-classifier manifest a stored `model_version` refers to, or None."""
    return MANIFESTS.get(version)


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
    family=PHOTO_VERIFIER,
    algorithm="BioCLIP ViT-B/16 embeddings + linear probe",
)

CURRENT_PHOTO_VERIFIER = PHOTO_VERIFIER_V1

# API families (URL segment) → (all published versions, the current one).
FAMILIES: Mapping[str, tuple[tuple[ModelManifest, ...], ModelManifest]] = {
    ECOSYSTEM_CLASSIFIER: (
        (ECOSYSTEM_CLASSIFIER_V1, ECOSYSTEM_CLASSIFIER_V1_1),
        CURRENT_ECOSYSTEM_CLASSIFIER,
    ),
    PHOTO_VERIFIER: ((PHOTO_VERIFIER_V1,), CURRENT_PHOTO_VERIFIER),
}
