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


def require_platform_role(
    *roles: PlatformRole,
) -> Callable[[User], Coroutine[Any, Any, User]]:
    async def _check(user: CurrentUser) -> User:
        if user.platform_role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient platform role")
        return user

    return _check


def org_role(user: User, org_id: uuid.UUID) -> OrgRole | None:
    for membership in user.memberships:
        if membership.org_id == org_id:
            return membership.role
    return None


def can_read_all_projects(user: User) -> bool:
    return user.platform_role in {PlatformRole.VERIFIER, PlatformRole.REGISTRY_ADMIN}
