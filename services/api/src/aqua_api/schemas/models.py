"""Published model versions and their model-card metrics (from `aqua_core.ml_models`)."""

from typing import Any

from pydantic import BaseModel

from aqua_core.ml_models import FAMILIES, ModelManifest

REPO_BLOB = "https://github.com/Dkhatke/AquaCreds/blob/main/"


class ModelCardSummary(BaseModel):
    """What a client needs to show a model's validation next to its output. Every number is
    copied from the model card's metrics JSON (`metrics_file`) and checked against it in tests.
    `heldout_metrics` is the clean estimate (a region never used for training or tuning)."""

    family: str
    version: str
    current: bool  # the version new results are produced with
    files_available: bool  # false: kept for provenance only, cannot be re-run
    algorithm: str
    model_card: str  # repository path of the model card
    model_card_url: str
    metrics_file: str
    heldout_metrics: dict[str, Any]
    heldout_per_class: dict[str, dict[str, float]]
    cv_metrics: dict[str, Any]
    sha256: dict[str, str]  # pinned file hashes


class ModelFamilyOut(BaseModel):
    family: str
    current_version: str
    versions: list[ModelCardSummary]


def card_summary(manifest: ModelManifest) -> ModelCardSummary:
    _, current = FAMILIES[manifest.family]
    return ModelCardSummary(
        family=manifest.family,
        version=manifest.version,
        current=manifest.version == current.version,
        files_available=manifest.files_available,
        algorithm=manifest.algorithm,
        model_card=manifest.model_card,
        model_card_url=REPO_BLOB + manifest.model_card,
        metrics_file=manifest.metrics_file,
        heldout_metrics=dict(manifest.heldout_metrics),
        heldout_per_class={k: dict(v) for k, v in manifest.heldout_per_class.items()},
        cv_metrics=dict(manifest.cv_metrics),
        sha256=dict(manifest.sha256),
    )
