"""The signed-in user's notification inbox and email preferences.

Notifications are created by hooks in the workflow code (`aqua_core.notifications`, which
documents who gets what). Every endpoint here only ever touches the caller's own rows."""

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert

from aqua_api.deps import CurrentUser, SessionDep, SettingsDep
from aqua_api.schemas.notification import (
    EmailPreferenceOut,
    MarkedRead,
    NotificationOut,
    NotificationPage,
    NotificationPreferencesIn,
    NotificationPreferencesOut,
    UnreadCount,
)
from aqua_core.models import Notification, NotificationKind, NotificationPreference
from aqua_core.models._types import utcnow
from aqua_core.notifications import EMAIL_DEFAULTS
from aqua_core.settings import Settings

router = APIRouter(prefix="/notifications", tags=["notifications"])

_NOTES = {NotificationKind.ALERT_RAISED: "Only high-severity alerts are emailed"}


async def _unread(session: SessionDep, user_id: uuid.UUID) -> int:
    count = await session.scalar(
        select(func.count(Notification.id)).where(
            Notification.user_id == user_id, Notification.read_at.is_(None)
        )
    )
    return int(count or 0)


@router.get("", response_model=NotificationPage)
async def list_notifications(
    user: CurrentUser,
    session: SessionDep,
    unread: Annotated[bool, Query(description="Only unread notifications")] = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> NotificationPage:
    """Newest first."""
    where = [Notification.user_id == user.id]
    if unread:
        where.append(Notification.read_at.is_(None))
    total = await session.scalar(select(func.count(Notification.id)).where(*where))
    rows = await session.scalars(
        select(Notification)
        .where(*where)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return NotificationPage(
        items=[NotificationOut.model_validate(r) for r in rows],
        total=int(total or 0),
        unread=await _unread(session, user.id),
        limit=limit,
        offset=offset,
    )


@router.get("/unread-count", response_model=UnreadCount)
async def unread_count(user: CurrentUser, session: SessionDep) -> UnreadCount:
    return UnreadCount(unread=await _unread(session, user.id))


@router.post("/read-all", response_model=MarkedRead)
async def mark_all_read(user: CurrentUser, session: SessionDep) -> MarkedRead:
    result = await session.execute(
        update(Notification)
        .where(Notification.user_id == user.id, Notification.read_at.is_(None))
        .values(read_at=utcnow())
    )
    await session.commit()
    return MarkedRead(updated=int(getattr(result, "rowcount", 0) or 0))


async def _preferences(
    session: SessionDep, user_id: uuid.UUID, settings: Settings
) -> NotificationPreferencesOut:
    chosen = dict(
        (
            await session.execute(
                select(NotificationPreference.kind, NotificationPreference.email).where(
                    NotificationPreference.user_id == user_id
                )
            )
        ).all()
    )
    return NotificationPreferencesOut(
        email_enabled=settings.notification_email_enabled,
        preferences=[
            EmailPreferenceOut(
                kind=kind,
                email=chosen.get(kind, default),
                default=default,
                customised=kind in chosen,
                note=_NOTES.get(kind),
            )
            for kind, default in EMAIL_DEFAULTS.items()
        ],
    )


@router.get("/preferences", response_model=NotificationPreferencesOut)
async def get_preferences(
    user: CurrentUser, session: SessionDep, settings: SettingsDep
) -> NotificationPreferencesOut:
    """Email preference per kind. In-app notifications are always on."""
    return await _preferences(session, user.id, settings)


@router.put("/preferences", response_model=NotificationPreferencesOut)
async def set_preferences(
    body: NotificationPreferencesIn, user: CurrentUser, session: SessionDep, settings: SettingsDep
) -> NotificationPreferencesOut:
    """Set (true/false) or reset (null) the email preference of the listed kinds; others keep
    their current setting. This is also where the email unsubscribe link leads."""
    for kind, email in body.email.items():
        if email is None:
            await session.execute(
                delete(NotificationPreference).where(
                    NotificationPreference.user_id == user.id, NotificationPreference.kind == kind
                )
            )
            continue
        await session.execute(
            insert(NotificationPreference)
            .values(user_id=user.id, kind=kind, email=email)
            .on_conflict_do_update(
                index_elements=["user_id", "kind"], set_={"email": email, "updated_at": utcnow()}
            )
        )
    await session.commit()
    return await _preferences(session, user.id, settings)


@router.post("/{notification_id}/read", response_model=NotificationOut)
async def mark_read(
    notification_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> Notification:
    n = await session.get(Notification, notification_id)
    if n is None or n.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notification not found")
    if n.read_at is None:
        n.read_at = utcnow()
        await session.commit()
        await session.refresh(n)
    return n
