"""Stewardship score (Phase 7 loyalty): off-chain, non-transferable, recomputed on request.

`scoring` is pure (no I/O; the formula is documented there). `loader` reads the inputs from the
database with an async session, so both the API and the worker (e.g. the forward-funding relayer)
can compute the same score. No table stores the score.
"""

from aqua_core.stewardship.scoring import (
    CONTINUITY,
    EVIDENCE,
    FORWARD_FUNDING_V1,
    STEWARDSHIP_V1,
    SURVIVAL,
    ComponentScore,
    EvidenceInputs,
    ForwardFundingParams,
    OrgStewardship,
    ProjectStewardship,
    ReportDecision,
    SceneValue,
    StewardshipParams,
    SurvivalInputs,
    aggregate_org,
    eligibility_for_forward_funding,
    score_continuity,
    score_evidence,
    score_project,
    score_survival,
)

__all__ = [
    "CONTINUITY",
    "EVIDENCE",
    "FORWARD_FUNDING_V1",
    "STEWARDSHIP_V1",
    "SURVIVAL",
    "ComponentScore",
    "EvidenceInputs",
    "ForwardFundingParams",
    "OrgStewardship",
    "ProjectStewardship",
    "ReportDecision",
    "SceneValue",
    "StewardshipParams",
    "SurvivalInputs",
    "aggregate_org",
    "eligibility_for_forward_funding",
    "score_continuity",
    "score_evidence",
    "score_project",
    "score_survival",
]
