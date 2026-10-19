"""Audit trail access and integrity verification (registry admins)."""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from aqua_api.deps import SessionDep, require_platform_role
from aqua_api.services.audit import ChainReport, verify_chain
from aqua_core.models import AuditLog, PlatformRole, User

router = APIRouter(prefix="/audit", tags=["audit"])

AdminDep = Annotated[User, Depends(require_platform_role(PlatformRole.REGISTRY_ADMIN))]


class AuditEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    at: datetime
    actor_id: Any
    action: str
    entity_type: str
    entity_id: str
    data: dict[str, Any]
    prev_hash: str
    hash: str


@router.get("", response_model=list[AuditEntryOut])
async def list_entries(
    _: AdminDep,
    session: SessionDep,
    entity_type: str | None = None,
    entity_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> list[AuditLog]:
    query = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
    if entity_type:
        query = query.where(AuditLog.entity_type == entity_type)
    if entity_id:
        query = query.where(AuditLog.entity_id == entity_id)
    return list(await session.scalars(query))


@router.get("/verify")
async def verify(_: AdminDep, session: SessionDep) -> ChainReport:
    return await verify_chain(session)
