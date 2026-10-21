"""Notification inbox and email-preference schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from aqua_core.models import AlertSeverity, NotificationKind


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    kind: NotificationKind
    severity: AlertSeverity | None
    org_id: uuid.UUID | None
    title: str
    body: str
    entity_type: str | None
    entity_id: str | None
    link: str | None = Field(description="Web path to open, e.g. /reports/<id>")
    created_at: datetime
    read_at: datetime | None
    emailed_at: datetime | None


class NotificationPage(BaseModel):
    items: list[NotificationOut]
    total: int = Field(description="Matching notifications (all pages)")
    unread: int = Field(description="Unread notifications of the user (any page)")
    limit: int
    offset: int


class UnreadCount(BaseModel):
    unread: int


class MarkedRead(BaseModel):
    updated: int


class EmailPreferenceOut(BaseModel):
    kind: NotificationKind
    email: bool = Field(description="Effective setting (the user's choice, else the default)")
    default: bool
    customised: bool
    note: str | None = None


class NotificationPreferencesOut(BaseModel):
    email_enabled: bool = Field(description="Whether this deployment sends email at all")
    preferences: list[EmailPreferenceOut]


class NotificationPreferencesIn(BaseModel):
    email: dict[NotificationKind, bool | None] = Field(
        description="Per kind: true/false to choose, null to go back to the default"
    )
