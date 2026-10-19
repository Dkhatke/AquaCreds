"""Hash-chained audit log writer and verifier."""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import rfc8785
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from aqua_core.models import AuditLog
from aqua_core.models._types import utcnow
from aqua_core.models.audit import GENESIS_HASH

# Arbitrary constant key; all audit writers serialise on this transaction-scoped advisory lock so
# the chain never forks under concurrency.
AUDIT_LOCK_KEY = 0x41_51_41_55  # "AQAU"


def compute_entry_hash(
    prev_hash: str,
    *,
    at: datetime,
    actor_id: uuid.UUID | None,
    action: str,
    entity_type: str,
    entity_id: str,
    data: dict[str, Any],
) -> str:
    canonical = rfc8785.dumps(
        {
            "at": at.isoformat(),
            "actor_id": str(actor_id) if actor_id else None,
            "action": action,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "data": data,
        }
    )
    return hashlib.sha256(prev_hash.encode() + canonical).hexdigest()


async def record(
    session: AsyncSession,
    *,
    actor_id: uuid.UUID | None,
    action: str,
    entity_type: str,
    entity_id: str | uuid.UUID,
    data: dict[str, Any] | None = None,
) -> AuditLog:
    """Append an entry in the caller's transaction (commit is the caller's responsibility)."""
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": AUDIT_LOCK_KEY})
    prev_hash = (
        await session.scalar(select(AuditLog.hash).order_by(AuditLog.id.desc()).limit(1))
    ) or GENESIS_HASH
    at = utcnow()
    payload = data or {}
    entry = AuditLog(
        at=at,
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        data=payload,
        prev_hash=prev_hash,
        hash=compute_entry_hash(
            prev_hash,
            at=at,
            actor_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id),
            data=payload,
        ),
    )
    session.add(entry)
    return entry


@dataclass(frozen=True)
class ChainReport:
    ok: bool
    entries_checked: int
    first_broken_id: int | None = None


async def verify_chain(session: AsyncSession, batch: int = 5000) -> ChainReport:
    prev_hash = GENESIS_HASH
    checked = 0
    last_id = 0
    while True:
        rows = (
            await session.scalars(
                select(AuditLog).where(AuditLog.id > last_id).order_by(AuditLog.id).limit(batch)
            )
        ).all()
        if not rows:
            return ChainReport(ok=True, entries_checked=checked)
        for row in rows:
            expected = compute_entry_hash(
                prev_hash,
                at=row.at,
                actor_id=row.actor_id,
                action=row.action,
                entity_type=row.entity_type,
                entity_id=row.entity_id,
                data=row.data,
            )
            if row.prev_hash != prev_hash or row.hash != expected:
                return ChainReport(ok=False, entries_checked=checked, first_broken_id=row.id)
            prev_hash = row.hash
            checked += 1
            last_id = row.id
