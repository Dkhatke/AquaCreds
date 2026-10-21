"""Notifications: who is told what, and the optional email hand-off.

`notify(...)` inserts in-app notifications in the caller's transaction (the caller commits, like
`audit.record`). Hooks in the API routers and worker tasks call the event helpers below, one line
each. When email is enabled, the ids of notifications that should also be emailed are kept on the
session and handed to `aqua.default.send_notification_email` only **after the commit succeeds**
(SQLAlchemy `after_commit`), so a rolled-back transaction never sends mail.

Fan-out rules (who gets what):

| Event (kind)                    | Recipients                                              | Email default |
|---------------------------------|---------------------------------------------------------|---------------|
| report_submitted_for_review     | active verifiers NOT in the project's org and not the   | on            |
|                                 | report's generator (conflict of interest, D24)          |               |
| report_decided (approve/reject) | admins of the project's org                             | on            |
| changes_requested               | admins of the project's org                             | on            |
| project_registered              | admins of the project's org (baseline approved)         | on            |
| project_submitted               | active registry admins NOT in the project's org         | off           |
| alert_raised                    | admins of the project's org; email only for HIGH        | on (high)     |
| submission_rejected             | the field agent who submitted the photo                 | off           |
| credits_issued                  | admins of the org whose custodial wallet received them  | on            |
| forward_fund_offer_status       | admins of the project's org (offer/withdrawal settled)  | off           |
| mfa_recovery_codes_low          | the user (<= 2 recovery codes left after using one)     | on            |

A user can override the email default per kind (`notification_preferences`); in-app
notifications are always created. Inactive users are never notified.
"""

import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import event, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session

from aqua_core.logging import get_logger
from aqua_core.models import (
    AlertSeverity,
    ChainTransaction,
    ChainTxPurpose,
    ChainTxStatus,
    FieldSubmission,
    Membership,
    MRVReport,
    Notification,
    NotificationKind,
    NotificationPreference,
    OrgRole,
    OrgWallet,
    PlatformRole,
    Project,
    ReportStatus,
    User,
)

log = get_logger("aqua_core.notifications")

EMAIL_TASK = "aqua.default.send_notification_email"
EMAIL_QUEUE = "default"
LOW_RECOVERY_CODES = 2

EMAIL_DEFAULTS: dict[NotificationKind, bool] = {
    NotificationKind.REPORT_SUBMITTED_FOR_REVIEW: True,
    NotificationKind.REPORT_DECIDED: True,
    NotificationKind.CHANGES_REQUESTED: True,
    NotificationKind.ALERT_RAISED: True,  # high severity only, see `email_wanted`
    NotificationKind.SUBMISSION_REJECTED: False,
    NotificationKind.PROJECT_SUBMITTED: False,
    NotificationKind.PROJECT_REGISTERED: True,
    NotificationKind.CREDITS_ISSUED: True,
    NotificationKind.FORWARD_FUND_OFFER_STATUS: False,
    NotificationKind.MFA_RECOVERY_CODES_LOW: True,
}

_TITLE_MAX, _BODY_MAX = 200, 1000


def email_wanted(
    kind: NotificationKind, severity: AlertSeverity | None, preference: bool | None
) -> bool:
    """Whether one notification should also be emailed (before the global switch / rate limit).
    Alerts are emailed only when high severity, whatever the preference."""
    if kind is NotificationKind.ALERT_RAISED and severity is not AlertSeverity.HIGH:
        return False
    return EMAIL_DEFAULTS[kind] if preference is None else preference


# ---------------------------------------------------------------- email dispatch (after commit)

Enqueue = Callable[..., Any]  # enqueue(task_name, *args, queue=...)


@dataclass(frozen=True)
class EmailDispatch:
    enqueue: Enqueue
    enabled: bool


_DISPATCH_KEY = "aqua_notification_dispatch"
_PENDING_KEY = "aqua_notification_emails"
_default_dispatch: EmailDispatch | None = None


def set_default_email_dispatch(enqueue: Enqueue | None, *, enabled: bool) -> None:
    """Process-wide dispatcher (the worker sets one at start-up). None disables email."""
    global _default_dispatch
    _default_dispatch = None if enqueue is None else EmailDispatch(enqueue, enabled)


def bind_email_dispatch(session: AsyncSession, enqueue: Enqueue, *, enabled: bool) -> None:
    """Per-session dispatcher (the API binds its own `enqueue` to each request session)."""
    session.info[_DISPATCH_KEY] = EmailDispatch(enqueue, enabled)


def _dispatch(info: dict[Any, Any]) -> EmailDispatch | None:
    found: EmailDispatch | None = info.get(_DISPATCH_KEY, _default_dispatch)
    return found


@event.listens_for(Session, "after_commit")
def _send_pending_emails(session: Session) -> None:
    pending: list[uuid.UUID] = session.info.pop(_PENDING_KEY, [])
    dispatch = _dispatch(session.info)
    if not pending or dispatch is None:
        return
    for notification_id in pending:
        try:
            dispatch.enqueue(EMAIL_TASK, str(notification_id), queue=EMAIL_QUEUE)
        except Exception as exc:  # the in-app notification stands; the email is lost
            log.error(
                "notification.email_enqueue_failed",
                notification_id=str(notification_id),
                error=f"{type(exc).__name__}: {exc}"[:300],
            )


@event.listens_for(Session, "after_rollback")
def _drop_pending_emails(session: Session) -> None:
    session.info.pop(_PENDING_KEY, None)


# ---------------------------------------------------------------- core


async def notify(
    session: AsyncSession,
    user_ids: Iterable[uuid.UUID],
    *,
    kind: NotificationKind,
    title: str,
    body: str,
    org_id: uuid.UUID | None = None,
    severity: AlertSeverity | None = None,
    entity_type: str | None = None,
    entity_id: str | uuid.UUID | None = None,
    link: str | None = None,
    dedupe_key: str | None = None,
) -> list[uuid.UUID]:
    """Create one notification per (distinct) user in the caller's transaction. With a
    `dedupe_key`, a user already notified under that key is skipped. Returns the new ids."""
    recipients = list(dict.fromkeys(user_ids))
    if not recipients:
        return []
    created: list[uuid.UUID] = []
    for user_id in recipients:
        new_id = await session.scalar(
            insert(Notification)
            .values(
                id=uuid.uuid4(),
                user_id=user_id,
                org_id=org_id,
                kind=kind,
                severity=severity,
                title=title[:_TITLE_MAX],
                body=body[:_BODY_MAX],
                entity_type=entity_type,
                entity_id=None if entity_id is None else str(entity_id),
                link=link,
                dedupe_key=dedupe_key,
            )
            .on_conflict_do_nothing(constraint="uq_notification_dedupe")
            .returning(Notification.id)
        )
        if new_id is not None:
            created.append(new_id)
    dispatch = _dispatch(session.info)
    if created and dispatch is not None and dispatch.enabled:
        prefs = dict(
            (
                await session.execute(
                    select(NotificationPreference.user_id, NotificationPreference.email).where(
                        NotificationPreference.user_id.in_(recipients),
                        NotificationPreference.kind == kind,
                    )
                )
            ).all()
        )
        owners = dict(
            (
                await session.execute(
                    select(Notification.id, Notification.user_id).where(
                        Notification.id.in_(created)
                    )
                )
            ).all()
        )
        pending: list[uuid.UUID] = session.info.setdefault(_PENDING_KEY, [])
        pending += [n for n in created if email_wanted(kind, severity, prefs.get(owners[n]))]
    return created


# ---------------------------------------------------------------- recipients


def _active_users() -> Any:
    return select(User.id).where(User.is_active.is_(True))


def _org_members(org_id: uuid.UUID) -> Any:
    return select(Membership.user_id).where(Membership.org_id == org_id)


async def org_admins(session: AsyncSession, org_id: uuid.UUID) -> list[uuid.UUID]:
    rows = await session.scalars(
        _active_users()
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.org_id == org_id, Membership.role == OrgRole.ADMIN)
        .order_by(User.id)
    )
    return list(rows)


async def independent_staff(
    session: AsyncSession,
    role: PlatformRole,
    org_id: uuid.UUID,
    *,
    exclude: Iterable[uuid.UUID] = (),
) -> list[uuid.UUID]:
    """Active users with a platform role who are not members of `org_id` (conflict of interest)."""
    excluded = list(exclude)
    query = _active_users().where(User.platform_role == role, User.id.not_in(_org_members(org_id)))
    if excluded:
        query = query.where(User.id.not_in(excluded))
    return list(await session.scalars(query.order_by(User.id)))


# ---------------------------------------------------------------- event helpers (hooks)


async def report_submitted_for_review(
    session: AsyncSession, report: MRVReport, project: Project
) -> list[uuid.UUID]:
    verifiers = await independent_staff(
        session, PlatformRole.VERIFIER, project.org_id, exclude=[report.generated_by]
    )
    return await notify(
        session,
        verifiers,
        kind=NotificationKind.REPORT_SUBMITTED_FOR_REVIEW,
        title=f"MRV report ready for review: {project.name}",
        body=(
            f"Version {report.version} of the MRV report for {project.name} "
            f"({report.period_start_year}-{report.period_end_year}) awaits an independent review."
        ),
        entity_type="mrv_report",
        entity_id=report.id,
        link=f"/reports/{report.id}",
        dedupe_key=f"report_review:{report.id}",
    )


_DECISION_WORDS = {
    ReportStatus.APPROVED: "approved",
    ReportStatus.REJECTED: "rejected",
    ReportStatus.CHANGES_REQUESTED: "returned with requested changes",
}


async def report_reviewed(
    session: AsyncSession, report: MRVReport, project: Project, *, registered: bool
) -> list[uuid.UUID]:
    """Decision on a report (+ registration when a baseline approval registered the project)."""
    admins = await org_admins(session, project.org_id)
    kind = (
        NotificationKind.CHANGES_REQUESTED
        if report.status is ReportStatus.CHANGES_REQUESTED
        else NotificationKind.REPORT_DECIDED
    )
    word = _DECISION_WORDS.get(report.status, report.status.value)
    comment = f" Verifier comment: {report.decision_comment}" if report.decision_comment else ""
    created = await notify(
        session,
        admins,
        kind=kind,
        title=f"MRV report {word}: {project.name}",
        body=f"Version {report.version} of the MRV report for {project.name} was {word}.{comment}",
        org_id=project.org_id,
        entity_type="mrv_report",
        entity_id=report.id,
        link=f"/reports/{report.id}",
        dedupe_key=f"report_decision:{report.id}",
    )
    if registered:
        created += await notify(
            session,
            admins,
            kind=NotificationKind.PROJECT_REGISTERED,
            title=f"Project registered: {project.name}",
            body=f"{project.name} is registered: its baseline report was approved.",
            org_id=project.org_id,
            entity_type="project",
            entity_id=project.id,
            link=f"/projects/{project.id}",
            dedupe_key=f"project_registered:{project.id}",
        )
    return created


async def alert_raised(
    session: AsyncSession,
    project_id: uuid.UUID,
    alert_id: uuid.UUID,
    severity: AlertSeverity,
    message: str,
) -> list[uuid.UUID]:
    project = await session.get(Project, project_id)
    if project is None:  # pragma: no cover - FK guarantees it
        return []
    return await notify(
        session,
        await org_admins(session, project.org_id),
        kind=NotificationKind.ALERT_RAISED,
        severity=severity,
        title=f"{severity.value.capitalize()}-severity alert: {project.name}",
        body=message,
        org_id=project.org_id,
        entity_type="alert",
        entity_id=alert_id,
        link=f"/projects/{project.id}/alerts",
        dedupe_key=f"alert:{alert_id}",
    )


async def submission_rejected(session: AsyncSession, sub: FieldSubmission) -> list[uuid.UUID]:
    failed = [
        c.get("name", "?") for c in sub.checks or [] if c.get("outcome") == "fail" and c.get("hard")
    ]
    reasons = ", ".join(str(f) for f in failed) or "an automatic check failed"
    project = await session.get(Project, sub.project_id)
    return await notify(
        session,
        [sub.submitted_by],
        kind=NotificationKind.SUBMISSION_REJECTED,
        title="Field photo rejected",
        body=f"A photo you submitted was rejected ({reasons}). Capture it again on site.",
        org_id=project.org_id if project else None,
        entity_type="field_submission",
        entity_id=sub.id,
        link=f"/field/submissions/{sub.id}",
        dedupe_key=f"submission_rejected:{sub.id}",
    )


async def project_submitted(session: AsyncSession, project: Project) -> list[uuid.UUID]:
    staff = await independent_staff(session, PlatformRole.REGISTRY_ADMIN, project.org_id)
    return await notify(
        session,
        staff,
        kind=NotificationKind.PROJECT_SUBMITTED,
        title=f"Project submitted: {project.name}",
        body=f"{project.name} ({project.state}, {project.area_ha:.1f} ha) was submitted.",
        entity_type="project",
        entity_id=project.id,
        link=f"/projects/{project.id}",
        dedupe_key=f"project_submitted:{project.id}:{project.submitted_at}",
    )


async def mfa_recovery_codes_low(
    session: AsyncSession, user_id: uuid.UUID, remaining: int
) -> list[uuid.UUID]:
    if remaining > LOW_RECOVERY_CODES:
        return []
    return await notify(
        session,
        [user_id],
        kind=NotificationKind.MFA_RECOVERY_CODES_LOW,
        title="Few two-factor recovery codes left",
        body=(
            f"You have {remaining} unused recovery code(s). Generate a new set in your security "
            "settings before you run out."
        ),
        entity_type="user",
        entity_id=user_id,
        link="/settings/security",
    )


_SETTLED = {ChainTxStatus.CONFIRMED, ChainTxStatus.FAILED, ChainTxStatus.REVERTED}
_FORWARD_FUND = {ChainTxPurpose.FORWARD_FUND_OFFER, ChainTxPurpose.FORWARD_FUND_WITHDRAW}


async def _chain_tx_notifications(session: AsyncSession, tx: ChainTransaction) -> list[uuid.UUID]:
    dedupe = f"chain_tx:{tx.id}:{tx.status.value}"
    if tx.purpose is ChainTxPurpose.ISSUE_CREDITS and tx.status is ChainTxStatus.CONFIRMED:
        # issue(reportId, creditType, to, amount, expiresAt): `to` is the org's custodial wallet.
        org_id = await session.scalar(
            select(OrgWallet.org_id).where(func.lower(OrgWallet.address) == str(tx.args[2]).lower())
        )
        if org_id is None:
            return []
        return await notify(
            session,
            await org_admins(session, org_id),
            kind=NotificationKind.CREDITS_ISSUED,
            title="Carbon credits issued",
            body=f"{tx.args[3]} credit units were issued to your organisation's wallet.",
            org_id=org_id,
            entity_type="chain_transaction",
            entity_id=tx.id,
            link="/credits",
            dedupe_key=dedupe,
        )
    if tx.purpose in _FORWARD_FUND and tx.entity_type == "project":
        project = await session.get(Project, uuid.UUID(tx.entity_id))
        if project is None:
            return []
        what = "offer" if tx.purpose is ChainTxPurpose.FORWARD_FUND_OFFER else "offer withdrawal"
        detail = f" Error: {tx.error_name or tx.error}" if tx.error or tx.error_name else ""
        return await notify(
            session,
            await org_admins(session, project.org_id),
            kind=NotificationKind.FORWARD_FUND_OFFER_STATUS,
            title=f"Forward-fund {what} {tx.status.value}: {project.name}",
            body=f"Your forward-fund {what} for {project.name} is {tx.status.value}.{detail}",
            org_id=project.org_id,
            entity_type="chain_transaction",
            entity_id=tx.id,
            link=f"/projects/{project.id}/forward-fund",
            dedupe_key=dedupe,
        )
    return []


async def chain_tx_settled(
    sessionmaker: async_sessionmaker[AsyncSession], tx_id: uuid.UUID
) -> list[uuid.UUID]:
    """After the relayer processed a transaction: notify once it is confirmed/failed/reverted
    (credits issued, forward-fund offers). Safe to call repeatedly (deduplicated)."""
    async with sessionmaker() as session:
        tx = await session.get(ChainTransaction, tx_id)
        if tx is None or tx.status not in _SETTLED:
            return []
        created = await _chain_tx_notifications(session, tx)
        await session.commit()
        return created
