"""Organisations (NGOs, panchayats, companies, government bodies) and their members."""

import re
import secrets
import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from aqua_api.deps import CurrentUser, SessionDep, SettingsDep, can_read_all_projects, org_role
from aqua_api.schemas.identity import MemberAdd, MemberOut, OrgCreate, OrgOut
from aqua_api.services import audit
from aqua_core.models import Membership, Organization, OrgRole, User

router = APIRouter(prefix="/orgs", tags=["organisations"])


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:100] or "org"
    return f"{slug}-{secrets.token_hex(3)}"


async def _get_org(session: SessionDep, org_id: uuid.UUID) -> Organization:
    org = await session.get(Organization, org_id)
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organisation not found")
    return org


@router.post("", response_model=OrgOut, status_code=status.HTTP_201_CREATED)
async def create_org(body: OrgCreate, user: CurrentUser, session: SessionDep) -> Organization:
    org = Organization(name=body.name, slug=_slugify(body.name), org_type=body.org_type)
    session.add(org)
    await session.flush()
    session.add(Membership(user_id=user.id, org_id=org.id, role=OrgRole.ADMIN))
    await audit.record(
        session,
        actor_id=user.id,
        action="org.created",
        entity_type="organization",
        entity_id=org.id,
        data={"name": org.name, "org_type": org.org_type.value},
    )
    await session.commit()
    return org


@router.get("", response_model=list[OrgOut])
async def list_my_orgs(user: CurrentUser, session: SessionDep) -> list[Organization]:
    rows = await session.scalars(
        select(Organization)
        .join(Membership, Membership.org_id == Organization.id)
        .where(Membership.user_id == user.id)
        .order_by(Organization.name)
    )
    return list(rows)


@router.get("/{org_id}", response_model=OrgOut)
async def get_org(
    org_id: uuid.UUID, user: CurrentUser, session: SessionDep, settings: SettingsDep
) -> Organization:
    org = await _get_org(session, org_id)
    if org_role(user, org_id) is None and not can_read_all_projects(user, settings):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organisation not found")
    return org


@router.get("/{org_id}/members", response_model=list[MemberOut])
async def list_members(
    org_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> list[MemberOut]:
    if org_role(user, org_id) != OrgRole.ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Organisation admin required")
    rows = await session.execute(
        select(User, Membership.role)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.org_id == org_id)
        .order_by(User.full_name)
    )
    return [
        MemberOut(user_id=u.id, email=u.email, full_name=u.full_name, role=role) for u, role in rows
    ]


@router.post("/{org_id}/members", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
async def add_member(
    org_id: uuid.UUID, body: MemberAdd, user: CurrentUser, session: SessionDep
) -> MemberOut:
    await _get_org(session, org_id)
    if org_role(user, org_id) != OrgRole.ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Organisation admin required")
    member = await session.scalar(select(User).where(User.email == body.email))
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No registered user with that email")
    if await session.get(Membership, (member.id, org_id)) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "User is already a member")
    session.add(Membership(user_id=member.id, org_id=org_id, role=body.role))
    await audit.record(
        session,
        actor_id=user.id,
        action="org.member_added",
        entity_type="organization",
        entity_id=org_id,
        data={"user_id": str(member.id), "role": body.role.value},
    )
    await session.commit()
    return MemberOut(
        user_id=member.id, email=member.email, full_name=member.full_name, role=body.role
    )
