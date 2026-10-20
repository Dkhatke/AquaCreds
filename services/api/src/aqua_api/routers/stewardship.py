"""Stewardship score (Phase 7 loyalty): an off-chain, non-transferable score, not a token.

Computed on request from MRV decisions, satellite scenes, alerts and field evidence (see
`aqua_core.stewardship.scoring` for the formula). Readable by the organisation's members and by
verifiers / registry admins; the response carries every input and sub-score.
"""

import uuid
from dataclasses import asdict

from fastapi import APIRouter, HTTPException, status

from aqua_api.deps import CurrentUser, SessionDep, SettingsDep, can_read_all_projects, org_role
from aqua_api.schemas.stewardship import (
    ForwardFundingOut,
    OrgStewardshipOut,
    ProjectStewardshipOut,
)
from aqua_core.models import Organization, Project
from aqua_core.stewardship import FORWARD_FUNDING_V1, eligibility_for_forward_funding
from aqua_core.stewardship.loader import org_stewardship, project_stewardship

router = APIRouter(tags=["stewardship"])


@router.get("/orgs/{org_id}/stewardship", response_model=OrgStewardshipOut)
async def get_org_stewardship(
    org_id: uuid.UUID, user: CurrentUser, session: SessionDep, settings: SettingsDep
) -> OrgStewardshipOut:
    org = await session.get(Organization, org_id)
    if org is None or (
        org_role(user, org_id) is None and not can_read_all_projects(user, settings)
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organisation not found")
    result = await org_stewardship(session, org_id)
    eligible, reasons = eligibility_for_forward_funding(result)
    return OrgStewardshipOut.model_validate(
        {
            **asdict(result),
            "forward_funding": ForwardFundingOut(
                eligible=eligible, reasons=reasons, criteria_version=FORWARD_FUNDING_V1.version
            ),
        }
    )


@router.get("/projects/{project_id}/stewardship", response_model=ProjectStewardshipOut)
async def get_project_stewardship(
    project_id: uuid.UUID, user: CurrentUser, session: SessionDep, settings: SettingsDep
) -> ProjectStewardshipOut:
    project = await session.get(Project, project_id)
    if project is None or (
        org_role(user, project.org_id) is None and not can_read_all_projects(user, settings)
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return ProjectStewardshipOut.model_validate(await project_stewardship(session, project))
