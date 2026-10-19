"""Registration, login, token refresh (rotation with reuse detection), logout, current user."""

import uuid
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from aqua_api.deps import CurrentUser, SessionDep, SettingsDep
from aqua_api.schemas.identity import LoginIn, RefreshIn, RegisterIn, TokenPair, UserOut
from aqua_api.security import (
    create_access_token,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    verify_password,
)
from aqua_api.services import audit
from aqua_core.models import RefreshToken, User
from aqua_core.models._types import utcnow
from aqua_core.settings import Settings

router = APIRouter(prefix="/auth", tags=["auth"])

_BAD_CREDENTIALS = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
_BAD_REFRESH = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired refresh token")


async def _issue_tokens(
    session: SessionDep, user: User, settings: Settings, family_id: uuid.UUID | None = None
) -> TokenPair:
    access, ttl = create_access_token(user.id, settings)
    refresh, refresh_hash = new_refresh_token()
    session.add(
        RefreshToken(
            user_id=user.id,
            family_id=family_id or uuid.uuid4(),
            token_hash=refresh_hash,
            expires_at=utcnow() + timedelta(days=settings.jwt_refresh_ttl_days),
        )
    )
    return TokenPair(access_token=access, refresh_token=refresh, expires_in=ttl)


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterIn, session: SessionDep) -> User:
    user = User(
        email=body.email, full_name=body.full_name, password_hash=hash_password(body.password)
    )
    session.add(user)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered") from exc
    await audit.record(
        session, actor_id=user.id, action="user.registered", entity_type="user", entity_id=user.id
    )
    await session.commit()
    await session.refresh(user, ["memberships"])
    return user


@router.post("/login", response_model=TokenPair)
async def login(body: LoginIn, session: SessionDep, settings: SettingsDep) -> TokenPair:
    user = await session.scalar(select(User).where(User.email == body.email))
    if not verify_password(body.password, user.password_hash if user else None):
        raise _BAD_CREDENTIALS
    if user is None or not user.is_active:
        raise _BAD_CREDENTIALS
    tokens = await _issue_tokens(session, user, settings)
    await session.commit()
    return tokens


@router.post("/refresh", response_model=TokenPair)
async def refresh(body: RefreshIn, session: SessionDep, settings: SettingsDep) -> TokenPair:
    token = await session.scalar(
        select(RefreshToken)
        .where(RefreshToken.token_hash == hash_refresh_token(body.refresh_token))
        .with_for_update()
    )
    now = utcnow()
    if token is None or token.revoked_at is not None or token.expires_at <= now:
        raise _BAD_REFRESH
    if token.used_at is not None:
        # A rotated token was replayed: assume theft, revoke the entire family.
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == token.family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        await audit.record(
            session,
            actor_id=token.user_id,
            action="auth.refresh_reuse_detected",
            entity_type="user",
            entity_id=token.user_id,
            data={"family_id": str(token.family_id)},
        )
        await session.commit()
        raise _BAD_REFRESH
    user = await session.get(User, token.user_id)
    if user is None or not user.is_active:
        raise _BAD_REFRESH
    token.used_at = now
    tokens = await _issue_tokens(session, user, settings, family_id=token.family_id)
    await session.commit()
    return tokens


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: RefreshIn, session: SessionDep) -> Response:
    token = await session.scalar(
        select(RefreshToken).where(
            RefreshToken.token_hash == hash_refresh_token(body.refresh_token)
        )
    )
    if token is not None:
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == token.family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=utcnow())
        )
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> User:
    return user
