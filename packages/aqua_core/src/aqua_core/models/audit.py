"""Append-only, hash-chained audit log.

Each entry's `hash` = SHA-256(prev_hash || JCS(canonical entry)), so editing or deleting any past
row breaks every later hash. Writers serialise on a Postgres advisory lock (see
aqua_api.services.audit)."""

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from aqua_core.db import Base

GENESIS_HASH = "0" * 64


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(), primary_key=True)
    at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    action: Mapped[str] = mapped_column(sa.String(80))
    entity_type: Mapped[str] = mapped_column(sa.String(40))
    entity_id: Mapped[str] = mapped_column(sa.String(64))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    prev_hash: Mapped[str] = mapped_column(sa.String(64))
    hash: Mapped[str] = mapped_column(sa.String(64), unique=True)

    __table_args__ = (sa.Index("ix_audit_log_entity", "entity_type", "entity_id"),)
