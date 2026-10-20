"""Ecosystem classifications: trigger a dry-season classification, list results, download COGs.

The work runs in the worker `ml` queue (`aqua.ml.classify_project`); the API records an
`AnalysisRun` (kind `classification`, queued) and enqueues the task by name with the run id. The
beat job `aqua.ml.classify_dry_season` also classifies every active project each April.
Classified areas are context for MRV reports, loss alerts and the stewardship score; they never
create credits (decision D13).
"""

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import select

from aqua_api.deps import CurrentUser, SessionDep, SettingsDep, StorageDep
from aqua_api.routers.analysis import (
    ACTIVE_RUN,
    _project,
    _queue_run,
    _require_admin,
    _require_analysable,
)
from aqua_api.schemas.analysis import (
    AnalysisRunOut,
    ClassificationDetail,
    ClassificationDownloads,
    ClassificationOut,
    ClassificationRequest,
)
from aqua_core.models import AnalysisKind, AnalysisRun, Classification

router = APIRouter(tags=["ecosystem classification"])

ML_QUEUE = "ml"
CLASSIFY_TASK = "aqua.ml.classify_project"


@router.post(
    "/projects/{project_id}/classifications", response_model=AnalysisRunOut, status_code=202
)
async def request_classification(
    project_id: uuid.UUID,
    body: ClassificationRequest,
    request: Request,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
) -> AnalysisRun:
    """Classify one completed dry season (Nov `season_start_year` – Mar of the next year) with
    the ecosystem classifier. Organisation admins of the project and registry admins only."""
    project = await _project(session, project_id, user, settings)
    _require_admin(user, project, settings)
    _require_analysable(project)
    year = body.season_start_year
    if date(year + 1, 3, 31) >= date.today():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "That dry season has not ended yet (it runs until 31 March)",
        )
    active = await session.scalar(
        select(AnalysisRun.id).where(
            AnalysisRun.project_id == project_id,
            AnalysisRun.kind == AnalysisKind.CLASSIFICATION,
            AnalysisRun.status.in_(ACTIVE_RUN),
            AnalysisRun.params["season_start_year"].as_integer() == year,
        )
    )
    if active:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"A classification of this season is already queued or running ({active})",
        )
    return await _queue_run(
        request,
        session,
        project=project,
        user=user,
        kind=AnalysisKind.CLASSIFICATION,
        params={"season_start_year": year},
        task=CLASSIFY_TASK,
        args=(str(project.id), str(year)),
        queue=ML_QUEUE,
    )


@router.get("/projects/{project_id}/classifications", response_model=list[ClassificationOut])
async def list_classifications(
    project_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    season_start_year: Annotated[int | None, Query(ge=2016, le=2100)] = None,
) -> list[Classification]:
    """Classification results, newest season first (re-runs of a season: newest first)."""
    await _project(session, project_id, user, settings)
    query = select(Classification).where(Classification.project_id == project_id)
    if season_start_year is not None:
        query = query.where(Classification.season_start_year == season_start_year)
    rows = await session.scalars(
        query.order_by(Classification.season_start_year.desc(), Classification.created_at.desc())
    )
    return list(rows)


@router.get("/classifications/{classification_id}", response_model=ClassificationDetail)
async def get_classification(
    classification_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    storage: StorageDep,
) -> ClassificationDetail:
    """One result with time-limited download URLs for the class map and probability COGs."""
    row = await session.get(Classification, classification_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Classification not found")
    await _project(session, row.project_id, user, settings)
    stem = f"classification-{row.season_start_year}-{row.model_version}"
    base = ClassificationOut.model_validate(row)
    return ClassificationDetail(
        **base.model_dump(),
        downloads=ClassificationDownloads(
            classes_url=storage.presign_get(row.classes_cog_key, f"{stem}-classes.tif"),
            probability_url=storage.presign_get(
                row.probability_cog_key, f"{stem}-max-probability.tif"
            ),
        ),
    )
