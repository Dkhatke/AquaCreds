"""Notification email: after-commit dispatch, and the `aqua.default.send_notification_email`
task with a fake backend (idempotency, preferences, hourly rate limit, retry with backoff)."""

import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import pyotp
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from aqua_core import notifications
from aqua_core.db import session_factory
from aqua_core.models import AlertSeverity, Notification, NotificationKind
from aqua_core.models._types import utcnow
from aqua_core.settings import Settings
from aqua_worker import email as mail
from aqua_worker.tasks import notifications as task

Enqueued = list[tuple[str, tuple[str, ...], str]]


@pytest.fixture
def settings_overrides() -> dict[str, Any]:
    return {"notification_email_enabled": True}


@pytest.fixture
async def sessionmaker(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(database_url)
    yield session_factory(engine)
    await engine.dispose()


class FakeBackend:
    def __init__(self, failures: list[Exception] | None = None) -> None:
        self.sent: list[mail.OutgoingEmail] = []
        self.failures = failures or []

    def send(self, message: mail.OutgoingEmail) -> None:
        if self.failures:
            raise self.failures.pop(0)
        self.sent.append(message)


def _settings(database_url: str, **kw: Any) -> Settings:
    options: dict[str, Any] = {
        "database_url": database_url,
        "notification_email_enabled": True,
        "public_base_url": "https://app.example",
        **kw,
    }
    return Settings(_env_file=None, **options)


async def _notify(
    sessionmaker: Any,
    user_id: uuid.UUID,
    kind: NotificationKind = NotificationKind.CREDITS_ISSUED,
    severity: AlertSeverity | None = None,
) -> uuid.UUID:
    async with sessionmaker() as session:
        [nid] = await notifications.notify(
            session,
            [user_id],
            kind=kind,
            severity=severity,
            title="Carbon credits issued",
            body="120 credit units were issued.",
            link="/credits",
        )
        await session.commit()
        return nid


async def _row(sessionmaker: Any, nid: uuid.UUID) -> Notification:
    async with sessionmaker() as session:
        row = await session.get(Notification, nid)
        assert row is not None
        return row


# ---------------------------------------------------------------- dispatch


async def test_api_queues_email_after_commit(
    client: Any, make_actor: Any, enqueued: Enqueued, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aqua_api import mfa

    monkeypatch.setattr(mfa, "RECOVERY_CODE_COUNT", 3)
    user = await make_actor("Staff")
    secret = (await user.post("/auth/mfa/setup")).json()["secret"]
    codes = (await user.post("/auth/mfa/enable", json={"code": pyotp.TOTP(secret).now()})).json()[
        "recovery_codes"
    ]
    login = await client.post(
        "/auth/login", json={"email": user.email, "password": "correct-horse-battery"}
    )
    r = await client.post(
        "/auth/mfa/verify", json={"mfa_token": login.json()["mfa_token"], "code": codes[0]}
    )
    assert r.status_code == 200, r.text
    [n] = (await user.get("/notifications")).json()["items"]
    assert n["kind"] == "mfa_recovery_codes_low"
    assert enqueued[-1] == (notifications.EMAIL_TASK, (n["id"],), "default")


async def test_dispatch_respects_preferences_rollback_and_enqueue_failures(
    make_actor: Any, sessionmaker: Any
) -> None:
    user = await make_actor("Admin")
    other = await make_actor("Other")
    r = await user.client.put(
        "/notifications/preferences",
        headers=user.headers,
        json={"email": {"credits_issued": False}},
    )
    assert r.status_code == 200, r.text
    sent: Enqueued = []

    def enqueue(name: str, *args: str, queue: str = "default") -> None:
        sent.append((name, args, queue))

    async with sessionmaker() as session:
        notifications.bind_email_dispatch(session, enqueue, enabled=True)
        ids = await notifications.notify(
            session,
            [user.id, other.id, other.id],
            kind=NotificationKind.CREDITS_ISSUED,
            title="t",
            body="b",
            dedupe_key="k1",
        )
        assert len(ids) == 2  # duplicate recipients collapse
        assert sent == []  # nothing before the commit
        await session.commit()
        rows = [await session.get(Notification, i) for i in ids]
        other_id = next(r.id for r in rows if r is not None and r.user_id == other.id)
        assert sent == [(notifications.EMAIL_TASK, (str(other_id),), "default")]  # user opted out

        # Same dedupe key again: no new rows, nothing queued.
        assert (
            await notifications.notify(
                session,
                [other.id],
                kind=NotificationKind.CREDITS_ISSUED,
                title="t",
                body="b",
                dedupe_key="k1",
            )
            == []
        )
        await session.commit()
        assert len(sent) == 1

        await notifications.notify(
            session, [other.id], kind=NotificationKind.CREDITS_ISSUED, title="t", body="b"
        )
        await session.rollback()
        assert len(sent) == 1  # rolled back: never mailed

    def broken(*_: Any, **__: Any) -> None:
        raise ConnectionError("broker down")

    async with sessionmaker() as session:
        notifications.bind_email_dispatch(session, broken, enabled=True)
        [kept] = await notifications.notify(
            session, [other.id], kind=NotificationKind.CREDITS_ISSUED, title="t", body="b"
        )
        await session.commit()  # the enqueue failure is logged, not raised
    assert (await _row(sessionmaker, kept)).emailed_at is None

    async with sessionmaker() as session:  # email disabled: in-app only
        notifications.bind_email_dispatch(session, enqueue, enabled=False)
        await notifications.notify(
            session, [other.id], kind=NotificationKind.CREDITS_ISSUED, title="t", body="b"
        )
        await session.commit()
    assert len(sent) == 1


# ---------------------------------------------------------------- the email task


async def test_email_is_sent_once(make_actor: Any, sessionmaker: Any, database_url: str) -> None:
    user = await make_actor("Priya Admin")
    nid = await _notify(sessionmaker, user.id)
    backend = FakeBackend()
    settings = _settings(database_url)

    result = await task.send_notification_email_core(sessionmaker, backend, settings, nid)
    assert result == {"status": "sent"}
    [message] = backend.sent
    assert message.to == user.email
    assert message.subject == "Carbon credits issued"
    assert "https://app.example/credits" in message.text
    assert message.unsubscribe_url == "https://app.example/settings/notifications"
    row = await _row(sessionmaker, nid)
    assert row.emailed_at is not None
    assert row.email_status == "sent"
    # A duplicated or retried task never sends twice.
    again = await task.send_notification_email_core(sessionmaker, backend, settings, nid)
    assert again == {"status": "already_sent"}
    assert len(backend.sent) == 1
    missing = await task.send_notification_email_core(sessionmaker, backend, settings, uuid.uuid4())
    assert missing == {"status": "missing"}
    # The inbox shows when it was emailed.
    item = (await user.get("/notifications")).json()["items"][0]
    assert item["emailed_at"] is not None


async def test_email_task_skips(
    make_actor: Any, sessionmaker: Any, database_url: str, sql: Any
) -> None:
    user = await make_actor("Skipper")
    backend = FakeBackend()
    settings = _settings(database_url)
    nid = await _notify(sessionmaker, user.id)

    off = _settings(database_url, notification_email_enabled=False)
    assert await task.send_notification_email_core(sessionmaker, backend, off, nid) == {
        "status": "disabled"
    }
    await user.client.put(
        "/notifications/preferences",
        headers=user.headers,
        json={"email": {"credits_issued": False}},
    )
    assert (await task.send_notification_email_core(sessionmaker, backend, settings, nid))[
        "status"
    ] == "opted_out"
    medium = await _notify(
        sessionmaker, user.id, NotificationKind.ALERT_RAISED, AlertSeverity.MEDIUM
    )
    assert (await task.send_notification_email_core(sessionmaker, backend, settings, medium))[
        "status"
    ] == "opted_out"
    high = await _notify(sessionmaker, user.id, NotificationKind.ALERT_RAISED, AlertSeverity.HIGH)
    await sql("UPDATE users SET is_active=false WHERE id=:id", id=user.id)
    assert (await task.send_notification_email_core(sessionmaker, backend, settings, high))[
        "status"
    ] == "skipped_inactive"
    assert backend.sent == []
    assert (await _row(sessionmaker, nid)).email_status == "opted_out"


async def test_email_rate_limit_per_user_per_hour(
    make_actor: Any, sessionmaker: Any, database_url: str
) -> None:
    user = await make_actor("Busy")
    other = await make_actor("Calm")
    backend = FakeBackend()
    settings = _settings(database_url, notification_email_rate_per_hour=2)
    now = utcnow()
    ids = [await _notify(sessionmaker, user.id) for _ in range(4)]
    results = [
        (await task.send_notification_email_core(sessionmaker, backend, settings, i, now=now))[
            "status"
        ]
        for i in ids[:3]
    ]
    assert results == ["sent", "sent", "rate_limited"]
    # Another user is not affected.
    theirs = await _notify(sessionmaker, other.id)
    assert (await task.send_notification_email_core(sessionmaker, backend, settings, theirs))[
        "status"
    ] == "sent"
    # An hour later the window has room again.
    later = now + timedelta(hours=1, seconds=1)
    assert (
        await task.send_notification_email_core(sessionmaker, backend, settings, ids[3], now=later)
    )["status"] == "sent"
    assert len(backend.sent) == 4
    assert (await _row(sessionmaker, ids[2])).emailed_at is None


async def test_transient_errors_retry_and_permanent_errors_stop(
    make_actor: Any, sessionmaker: Any, database_url: str
) -> None:
    user = await make_actor("Flaky")
    settings = _settings(database_url)
    nid = await _notify(sessionmaker, user.id)
    backend = FakeBackend([mail.TransientEmailError("SMTPServerDisconnected")])
    with pytest.raises(mail.TransientEmailError):
        await task.send_notification_email_core(sessionmaker, backend, settings, nid)
    row = await _row(sessionmaker, nid)
    assert (row.emailed_at, row.email_status) == (None, "retrying: SMTPServerDisconnected")
    assert (await task.send_notification_email_core(sessionmaker, backend, settings, nid))[
        "status"
    ] == "sent"

    bad = await _notify(sessionmaker, user.id)
    backend = FakeBackend([mail.PermanentEmailError("SMTP 550")])
    assert (await task.send_notification_email_core(sessionmaker, backend, settings, bad)) == {
        "status": "failed: SMTP 550"
    }
    assert (await _row(sessionmaker, bad)).emailed_at is None


async def test_task_wrapper_uses_settings_and_backend(
    make_actor: Any, sessionmaker: Any, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await make_actor("Wrapped")
    nid = await _notify(sessionmaker, user.id)
    backend = FakeBackend()
    monkeypatch.setattr(task, "get_settings", lambda: _settings(database_url))
    monkeypatch.setattr(mail, "email_backend", lambda _s: backend)
    assert await task._send(str(nid)) == {"status": "sent"}
    assert len(backend.sent) == 1


def test_celery_task_retries_transient_failures_with_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def flaky(notification_id: str) -> dict[str, Any]:
        calls.append(notification_id)
        if len(calls) < 3:
            raise mail.TransientEmailError("SMTP 451")
        return {"status": "sent"}

    monkeypatch.setattr(task, "_send", flaky)
    t = task.send_notification_email
    assert t.name == "aqua.default.send_notification_email"
    assert (t.retry_backoff, t.retry_backoff_max, t.max_retries) == (30, 1800, 6)
    result = t.apply(args=["n1"])  # eager: retries run inline
    assert result.get() == {"status": "sent"}
    assert calls == ["n1", "n1", "n1"]
