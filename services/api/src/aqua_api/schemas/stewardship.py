"""Stewardship score responses (mirror `aqua_core.stewardship.scoring` dataclasses)."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class ComponentScoreOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    name: str
    weight: float
    score: float | None
    insufficient_data: bool
    sub_scores: dict[str, float | None]
    inputs: dict[str, Any]
    notes: list[str]


class ProjectStewardshipOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    project_id: uuid.UUID
    area_ha: float
    score: float | None
    insufficient_data: bool
    renormalised: bool
    components: list[ComponentScoreOut]
    notes: list[str]
    score_version: str
    computed_at: datetime


class ForwardFundingOut(BaseModel):
    eligible: bool
    reasons: list[str]
    criteria_version: str


class OrgStewardshipOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    org_id: uuid.UUID
    score: float | None
    insufficient_data: bool
    components: dict[str, float | None]
    projects: list[ProjectStewardshipOut]
    notes: list[str]
    score_version: str
    computed_at: datetime
    params: dict[str, Any]
    forward_funding: ForwardFundingOut
