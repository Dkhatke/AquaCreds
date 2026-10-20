"""FastAPI dependencies: DB session, settings, storage, authentication and authorisation."""

import uuid
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Annotated, Any

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from aqua_api.security import decode_access_token
from aqua_core.models import OrgRole, PlatformRole, User
from aqua_core.settings import Settings
from aqua_core.storage import ObjectStorage

_bearer = HTTPBearer(auto_error=False)


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessionmaker() as session:
        yield session


def get_storage(request: Request) -> ObjectStorage:
    storage: ObjectStorage = request.app.state.storage
    return storage


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
StorageDep = Annotated[ObjectStorage, Depends(get_storage)]

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_user(
    session: SessionDep,
    settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if credentials is None:
        raise _UNAUTHORIZED
    try:
        payload = decode_access_token(credentials.credentials, settings)
        user_id = uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise _UNAUTHORIZED from exc
    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise _UNAUTHORIZED
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


STAFF_ROLES = frozenset({PlatformRole.VERIFIER, PlatformRole.REGISTRY_ADMIN})
MFA_REQUIRED_DETAIL = (
    "Two-factor authentication is required for staff accounts: enrol with "
    "POST /api/v1/auth/mfa/setup and /auth/mfa/enable, then sign in again"
)


def mfa_required_for(user: User, settings: Settings) -> bool:
    """True when this account must have TOTP enabled to use its platform role."""
    return settings.staff_mfa_required and user.platform_role in STAFF_ROLES


def staff_mfa_satisfied(user: User, settings: Settings) -> bool:
    """False only for a staff account without TOTP while MFA_REQUIRED_FOR_STAFF is in effect."""
    return not mfa_required_for(user, settings) or user.mfa_enabled_at is not None


def effective_platform_role(user: User, settings: Settings) -> PlatformRole:
    """The platform role this request may exercise. A staff account that has not met the MFA
    requirement is treated as an ordinary user (it still sees its own organisations), so staff
    privileges never apply without the second factor. Staff-only endpoints use
    `require_platform_role`, which answers 403 with `MFA_REQUIRED_DETAIL` instead."""
    if not staff_mfa_satisfied(user, settings):
        return PlatformRole.USER
    return user.platform_role


def require_platform_role(
    *roles: PlatformRole,
) -> Callable[..., Coroutine[Any, Any, User]]:
    """Allow only the given platform roles. Staff roles (verifier, registry_admin) must also have
    TOTP enabled when MFA_REQUIRED_FOR_STAFF is in effect (default: production)."""

    async def _check(user: CurrentUser, settings: SettingsDep) -> User:
        if user.platform_role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient platform role")
        if not staff_mfa_satisfied(user, settings):
            raise HTTPException(status.HTTP_403_FORBIDDEN, MFA_REQUIRED_DETAIL)
        return user

    return _check


def org_role(user: User, org_id: uuid.UUID) -> OrgRole | None:
    for membership in user.memberships:
        if membership.org_id == org_id:
            return membership.role
    return None


def can_read_all_projects(user: User, settings: Settings) -> bool:
    """Staff (verifier, registry_admin) read every organisation's projects, but only once the
    MFA requirement for staff is met (see `effective_platform_role`)."""
    return effective_platform_role(user, settings) in STAFF_ROLES
