"""Model cards over the API: held-out metrics and pins of every published model version.

Public and cacheable (model metrics are published in the repository anyway). Clients show a
classification's validation through `ClassificationOut.model_card` or these endpoints instead of
copying the model-card JSON into their builds.
"""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import RootModel

from aqua_api.routers.public import cached
from aqua_api.schemas.models import ModelCardSummary, ModelFamilyOut, card_summary
from aqua_core.ml_models import FAMILIES

router = APIRouter(prefix="/models", tags=["models"])

Family = Literal["ecosystem-classifier", "photo-verifier"]
MAX_AGE_S = 3600


def _family(family: str) -> ModelFamilyOut:
    versions, current = FAMILIES[family]
    return ModelFamilyOut(
        family=family,
        current_version=current.version,
        versions=[card_summary(m) for m in versions],
    )


@router.get("", response_model=list[ModelFamilyOut])
async def list_models(request: Request) -> Response:
    """Every model family with all its published versions."""
    return cached(
        request, RootModel[list[ModelFamilyOut]]([_family(f) for f in FAMILIES]), MAX_AGE_S
    )


@router.get("/{family}", response_model=ModelFamilyOut)
async def get_model_family(family: Family, request: Request) -> Response:
    return cached(request, _family(family), MAX_AGE_S)


@router.get("/{family}/{version}", response_model=ModelCardSummary)
async def get_model_version(family: Family, version: str, request: Request) -> Response:
    """One version's model card summary (`version` as stored on results, e.g.
    `ecosystem_classifier_v1.1`). 404 for a version this API does not know."""
    versions, _ = FAMILIES[family]
    for manifest in versions:
        if manifest.version == version:
            return cached(request, card_summary(manifest), MAX_AGE_S)
    raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown {family} version {version}")
