"""Load stewardship inputs from the database and score them (no writes, no new tables).

A handful of small indexed queries per project; nothing is cached, so the score always reflects the
current verifier decisions, scenes, alerts and evidence.
"""

import math
import uuid
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from aqua_core.models import (
    Alert,
    AlertSeverity,
    FieldSubmission,
    MonitoringPeriod,
    MRVReport,
    Plot,
    Project,
    ProjectStatus,
    SceneObservation,
    SubmissionStatus,
)
from aqua_core.models._types import utcnow
from aqua_core.stewardship.scoring import (
    DECIDED_STATUSES,
    FORWARD_FUNDING_V1,
    STEWARDSHIP_V1,
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
    score_project,
)


def _p50(stats: dict[str, Any], index: str) -> float | None:
    entry = stats.get(index)
    if not isinstance(entry, dict):
        return None
    value = entry.get("p50")
    if isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


async def _decisions(session: AsyncSession, project_id: uuid.UUID) -> list[ReportDecision]:
    rows = await session.execute(
        select(
            MRVReport.id,
            MRVReport.period_id,
            MRVReport.version,
            MRVReport.status,
            MRVReport.decided_at,
        )
        .where(
            MRVReport.project_id == project_id,
            MRVReport.status.in_(DECIDED_STATUSES),
            MRVReport.decided_at.is_not(None),
        )
        .order_by(MRVReport.decided_at)
    )
    return [
        ReportDecision(str(rid), str(pid), version, status, decided_at)
        for rid, pid, version, status, decided_at in rows
        if decided_at is not None
    ]


async def _survival(
    session: AsyncSession, project: Project, as_of: date, params: StewardshipParams
) -> SurvivalInputs:
    window = timedelta(days=params.window_days)
    start = project.start_date
    rows = await session.execute(
        select(
            SceneObservation.acquired_on,
            SceneObservation.clear_fraction,
            SceneObservation.index_stats,
        ).where(
            SceneObservation.project_id == project.id,
            or_(
                SceneObservation.acquired_on.between(start - window, start),
                SceneObservation.acquired_on.between(as_of - window, as_of),
            ),
        )
    )
    scenes = [
        SceneValue(day, clear, _p50(stats or {}, "ndvi"), _p50(stats or {}, "mvi"))
        for day, clear, stats in rows
    ]
    alerts = await session.scalar(
        select(func.count())
        .select_from(Alert)
        .where(
            Alert.project_id == project.id,
            Alert.severity == AlertSeverity.HIGH,
            Alert.acknowledged_at.is_(None),
        )
    )
    return SurvivalInputs(start, as_of, scenes, int(alerts or 0))


async def _evidence(session: AsyncSession, project_id: uuid.UUID) -> EvidenceInputs:
    decided = (SubmissionStatus.CHECKED, SubmissionStatus.REJECTED)
    plots_total = int(
        await session.scalar(
            select(func.count()).select_from(Plot).where(Plot.project_id == project_id)
        )
        or 0
    )
    period_id = await session.scalar(
        select(MonitoringPeriod.id)
        .join(FieldSubmission, FieldSubmission.period_id == MonitoringPeriod.id)
        .where(MonitoringPeriod.project_id == project_id, FieldSubmission.status.in_(decided))
        .order_by(MonitoringPeriod.end_date.desc(), MonitoringPeriod.start_date.desc())
        .limit(1)
    )
    if period_id is None:
        return EvidenceInputs(None, [], 0, 0, plots_total, 0)
    rows = (
        await session.execute(
            select(FieldSubmission.status, FieldSubmission.trust_score, FieldSubmission.plot_id)
            .where(FieldSubmission.period_id == period_id)
            .where(FieldSubmission.project_id == project_id)
        )
    ).all()
    checked = [r for r in rows if r.status is SubmissionStatus.CHECKED]
    return EvidenceInputs(
        period_id=str(period_id),
        checked_trust_scores=[int(r.trust_score) for r in checked if r.trust_score is not None],
        rejected=sum(r.status is SubmissionStatus.REJECTED for r in rows),
        pending=sum(r.status is SubmissionStatus.PENDING_CHECKS for r in rows),
        plots_total=plots_total,
        plots_photographed=len({r.plot_id for r in checked if r.plot_id is not None}),
    )


async def project_stewardship(
    session: AsyncSession,
    project: Project,
    *,
    computed_at: datetime | None = None,
    params: StewardshipParams = STEWARDSHIP_V1,
) -> ProjectStewardship:
    now = computed_at or utcnow()
    return score_project(
        project_id=str(project.id),
        area_ha=project.area_ha,
        decisions=await _decisions(session, project.id),
        survival=await _survival(session, project, now.date(), params),
        evidence=await _evidence(session, project.id),
        computed_at=now,
        params=params,
    )


async def org_stewardship(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    computed_at: datetime | None = None,
    params: StewardshipParams = STEWARDSHIP_V1,
) -> OrgStewardship:
    """Every non-draft project of the organisation, area-weighted."""
    now = computed_at or utcnow()
    projects = await session.scalars(
        select(Project)
        .where(Project.org_id == org_id, Project.status != ProjectStatus.DRAFT)
        .order_by(Project.created_at, Project.id)
    )
    scored = [
        await project_stewardship(session, p, computed_at=now, params=params) for p in projects
    ]
    return aggregate_org(org_id=str(org_id), projects=scored, computed_at=now, params=params)


async def forward_funding_eligibility(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    params: ForwardFundingParams = FORWARD_FUNDING_V1,
) -> tuple[bool, list[str]]:
    """Entry point for the forward-funding relayer: score the org now and apply the threshold."""
    return eligibility_for_forward_funding(await org_stewardship(session, org_id), params)
