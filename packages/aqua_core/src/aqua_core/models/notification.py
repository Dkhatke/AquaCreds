"""In-app notifications (+ optional email) and per-user email preferences.

Preferences live in their own table rather than on `users`: the users table has no JSON column,
and one row per (user, kind) keeps the kind constrained by the same CHECK as notifications, so a
renamed or removed kind cannot leave stale keys behind. A missing row means the kind's default
(`aqua_core.notifications.EMAIL_DEFAULTS`)."""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from aqua_core.db import Base
from aqua_core.models._types import UUIDPk, str_enum, utcnow
from aqua_core.models.enums import AlertSeverity, NotificationKind


class Notification(UUIDPk, Base):
    __tablename__ = "notifications"
    __table_args__ = (
        # Re-running a hook (task retry, repeated poll) never notifies twice.
        sa.UniqueConstraint("user_id", "dedupe_key", name="uq_notification_dedupe"),
        sa.Index("ix_notifications_inbox", "user_id", "read_at", "created_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"))
    org_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("organizations.id", ondelete="SET NULL")
    )
    kind: Mapped[NotificationKind] = mapped_column(str_enum(NotificationKind, "notification_kind"))
    severity: Mapped[AlertSeverity | None] = mapped_column(
        str_enum(AlertSeverity, "notification_severity")
    )
    title: Mapped[str] = mapped_column(sa.String(200))
    body: Mapped[str] = mapped_column(sa.String(1000))
    entity_type: Mapped[str | None] = mapped_column(sa.String(40))
    entity_id: Mapped[str | None] = mapped_column(sa.String(64))
    link: Mapped[str | None] = mapped_column(sa.String(300))  # web path, e.g. /reports/<id>
    dedupe_key: Mapped[str | None] = mapped_column(sa.String(200))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=utcnow, server_default=sa.func.now()
    )
    read_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    emailed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    # Why no email went out (opted_out, rate_limited, …) or the last delivery error.
    email_status: Mapped[str | None] = mapped_column(sa.String(300))


class NotificationPreference(Base):
    __tablename__ = "notification_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[NotificationKind] = mapped_column(
        str_enum(NotificationKind, "notification_kind"), primary_key=True
    )
    email: Mapped[bool]
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=sa.func.now()
    )
