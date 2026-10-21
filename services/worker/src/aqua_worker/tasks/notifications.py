"""Notification email task (queue `default`).

`aqua.default.send_notification_email(notification_id)` is queued after the commit that created
the notification (see `aqua_core.notifications`). It is idempotent: `emailed_at` is set once and
the row is locked while sending, so a retried or duplicated task never mails twice. Per user at
most NOTIFICATION_EMAIL_RATE_PER_HOUR emails per rolling hour are sent; the rest stay in-app
(`email_status = "rate_limited"`). Transient SMTP errors retry with exponential backoff."""

import asyncio
import uuid
from datetime import datetime, timedelta
from typing import Any

from celery import shared_task
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aqua_core.db import create_engine, session_factory
from aqua_core.logging import get_logger
from aqua_core.models import Notification, NotificationPreference, User
from aqua_core.models._types import utcnow
from aqua_core.notifications import email_wanted
from aqua_core.settings import Settings, get_settings
from aqua_worker import email as mail

log = get_logger("aqua_worker.notifications")

_LOCK_NAMESPACE = 0x4E_4F_54_49  # "NOTI": per-user advisory lock for the hourly rate limit


def _user_lock_key(user_id: uuid.UUID) -> int:
    return _LOCK_NAMESPACE << 32 | (user_id.int & 0xFFFF_FFFF)  # fits a signed bigint


async def send_notification_email_core(
    sessionmaker: async_sessionmaker[AsyncSession],
    backend: mail.EmailBackend,
    settings: Settings,
    notification_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    async with sessionmaker() as session:
        n = await session.get(Notification, notification_id, with_for_update=True)
        if n is None:
            return {"status": "missing"}
        if n.emailed_at is not None:
            return {"status": "already_sent"}
        if not settings.notification_email_enabled:
            return {"status": "disabled"}

        async def done(status: str) -> dict[str, Any]:
            n.email_status = status[:300]
            await session.commit()
            log.info("notification.email", notification_id=str(n.id), status=status)
            return {"status": status}

        user = await session.get(User, n.user_id)
        if user is None or not user.is_active:
            return await done("skipped_inactive")
        pref = await session.get(NotificationPreference, (n.user_id, n.kind))
        if not email_wanted(n.kind, n.severity, pref.email if pref else None):
            return await done("opted_out")

        at = now or utcnow()
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:k)"), {"k": _user_lock_key(n.user_id)}
        )
        sent = await session.scalar(
            select(func.count(Notification.id)).where(
                Notification.user_id == n.user_id,
                Notification.emailed_at > at - timedelta(hours=1),
            )
        )
        if int(sent or 0) >= settings.notification_email_rate_per_hour:
            return await done("rate_limited")

        message = mail.render(
            to=user.email,
            full_name=user.full_name,
            title=n.title,
            body=n.body,
            link=n.link,
            settings=settings,
        )
        to_hash = mail.address_hash(user.email, settings)
        try:
            await asyncio.to_thread(backend.send, message)
        except mail.PermanentEmailError as exc:
            log.error("notification.email_failed", notification_id=str(n.id), to_hash=to_hash)
            return await done(f"failed: {exc}")
        except mail.TransientEmailError as exc:
            n.email_status = f"retrying: {exc}"[:300]
            await session.commit()
            log.warning("notification.email_retry", notification_id=str(n.id), to_hash=to_hash)
            raise
        n.emailed_at = at
        log.info("notification.email_sent", notification_id=str(n.id), to_hash=to_hash)
        return await done("sent")


async def _send(notification_id: str) -> dict[str, Any]:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    try:
        return await send_notification_email_core(
            session_factory(engine),
            mail.email_backend(settings),
            settings,
            uuid.UUID(notification_id),
        )
    finally:
        await engine.dispose()


@shared_task(
    name="aqua.default.send_notification_email",
    autoretry_for=(mail.TransientEmailError, ConnectionError, TimeoutError),
    retry_backoff=30,
    retry_backoff_max=1800,
    retry_jitter=True,
    max_retries=6,
)
def send_notification_email(notification_id: str) -> dict[str, Any]:
    return asyncio.run(_send(notification_id))
