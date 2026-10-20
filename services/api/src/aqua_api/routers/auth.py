"""Registration, login (+ TOTP second factor), token refresh (rotation with reuse detection),
logout, current user, and MFA enrolment.

Rate limits (Valkey, see `aqua_api.ratelimit`): per IP on register/login/refresh/MFA, plus per
account on login (email) and on every TOTP/recovery-code check (user id, shared bucket).
"""

import uuid
from datetime import timedelta
from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import delete, select, update
from sqlalchemy import func as sa_func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from aqua_api import mfa
from aqua_api.deps import CurrentUser, SessionDep, SettingsDep
from aqua_api.ratelimit import RateLimiter, get_rate_limiter, ip_limit
from aqua_api.schemas.identity import (
    LoginIn,
    MfaChallenge,
    MfaCodeIn,
    MfaEnabledOut,
    MfaSetupOut,
    MfaVerifyIn,
    RefreshIn,
    RegisterIn,
    TokenPair,
    UserOut,
)
from aqua_api.security import (
    create_access_token,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    verify_password,
)
from aqua_api.services import audit
from aqua_core.models import MfaRecoveryCode, RefreshToken, User
from aqua_core.models._types import utcnow
from aqua_core.settings import Settings

router = APIRouter(prefix="/auth", tags=["auth"])

_BAD_CREDENTIALS = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
_BAD_REFRESH = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired refresh token")
_BAD_MFA_TOKEN = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired MFA challenge")
_BAD_CODE = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid authentication code")

LimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]


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


@router.post(
    "/register",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(ip_limit("register", "rate_limit_register_ip"))],
)
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


@router.post(
    "/login",
    response_model=TokenPair | MfaChallenge,
    dependencies=[Depends(ip_limit("login", "rate_limit_login_ip"))],
)
async def login(
    body: LoginIn, session: SessionDep, settings: SettingsDep, limiter: LimiterDep
) -> TokenPair | MfaChallenge:
    await limiter.enforce_account("login", settings.rate_limit_login_account, body.email)
    user = await session.scalar(select(User).where(User.email == body.email))
    if not verify_password(body.password, user.password_hash if user else None):
        raise _BAD_CREDENTIALS
    if user is None or not user.is_active:
        raise _BAD_CREDENTIALS
    if user.mfa_enabled_at is not None:
        return MfaChallenge(
            mfa_token=mfa.create_mfa_token(user.id, settings), expires_in=settings.mfa_token_ttl_s
        )
    tokens = await _issue_tokens(session, user, settings)
    await session.commit()
    return tokens


async def _consume_code(
    session: AsyncSession, user: User, code: str, settings: Settings, *, allow_recovery: bool
) -> str:
    """Check a TOTP code (current step ±1, never a step already used) or, if allowed, an unused
    recovery code, and mark it used. Returns "totp" or "recovery"; raises 401 otherwise.
    The caller must hold the user row lock (SELECT ... FOR UPDATE) and commit."""
    if user.mfa_secret_encrypted is None:
        raise _BAD_CODE
    secret = mfa.decrypt_secret(user.mfa_secret_encrypted, settings)
    step = mfa.match_totp(secret, code)
    if step is not None:
        if user.mfa_last_used_step is not None and step <= user.mfa_last_used_step:
            raise _BAD_CODE  # replay of an already accepted (or older) code
        user.mfa_last_used_step = step
        return "totp"
    if allow_recovery and mfa.looks_like_recovery_code(code):
        recovery = await session.scalar(
            select(MfaRecoveryCode)
            .where(
                MfaRecoveryCode.user_id == user.id,
                MfaRecoveryCode.code_hash == mfa.hash_recovery_code(code),
                MfaRecoveryCode.used_at.is_(None),
            )
            .with_for_update()
        )
        if recovery is not None:
            recovery.used_at = utcnow()
            remaining = await session.scalar(
                select(sa_func.count(MfaRecoveryCode.id)).where(
                    MfaRecoveryCode.user_id == user.id,
                    MfaRecoveryCode.used_at.is_(None),
                    MfaRecoveryCode.id != recovery.id,
                )
            )
            await audit.record(
                session,
                actor_id=user.id,
                action="auth.mfa_recovery_code_used",
                entity_type="user",
                entity_id=user.id,
                data={"remaining": int(remaining or 0)},
            )
            return "recovery"
    raise _BAD_CODE


async def _lock_user(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    return await session.scalar(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


@router.post(
    "/mfa/verify",
    response_model=TokenPair,
    dependencies=[Depends(ip_limit("mfa", "rate_limit_mfa_ip"))],
)
async def mfa_verify(
    body: MfaVerifyIn, session: SessionDep, settings: SettingsDep, limiter: LimiterDep
) -> TokenPair:
    """Second login step: exchange the `mfa_token` from /auth/login + a TOTP or recovery code
    for the token pair."""
    try:
        user_id = uuid.UUID(mfa.decode_mfa_token(body.mfa_token, settings)["sub"])
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise _BAD_MFA_TOKEN from exc
    await limiter.enforce_account("mfa", settings.rate_limit_mfa_account, str(user_id))
    user = await _lock_user(session, user_id)
    if user is None or not user.is_active or user.mfa_enabled_at is None:
        raise _BAD_MFA_TOKEN
    try:
        await _consume_code(session, user, body.code, settings, allow_recovery=True)
    except HTTPException:
        await session.rollback()
        raise
    tokens = await _issue_tokens(session, user, settings)
    await session.commit()
    return tokens


@router.post("/mfa/setup", response_model=MfaSetupOut)
async def mfa_setup(user: CurrentUser, session: SessionDep, settings: SettingsDep) -> MfaSetupOut:
    """Start (or restart) TOTP enrolment. Not active until /auth/mfa/enable confirms a code."""
    locked = await _lock_user(session, user.id)
    if locked is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    if locked.mfa_enabled_at is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Two-factor authentication is already enabled"
        )
    secret = mfa.new_secret()
    locked.mfa_secret_encrypted = mfa.encrypt_secret(secret, settings)
    locked.mfa_last_used_step = None
    await session.commit()
    uri = mfa.provisioning_uri(secret, locked.email, settings)
    return MfaSetupOut(secret=secret, otpauth_uri=uri, qr_svg=mfa.qr_svg(uri))


@router.post("/mfa/enable", response_model=MfaEnabledOut)
async def mfa_enable(
    body: MfaCodeIn,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    limiter: LimiterDep,
) -> MfaEnabledOut:
    """Confirm enrolment with a TOTP code. Returns 10 one-time recovery codes (shown once) and
    signs out every existing session (refresh tokens issued before MFA)."""
    await limiter.enforce_account("mfa", settings.rate_limit_mfa_account, str(user.id))
    locked = await _lock_user(session, user.id)
    if locked is None or locked.mfa_secret_encrypted is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Start enrolment with /auth/mfa/setup")
    if locked.mfa_enabled_at is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Two-factor authentication is already enabled"
        )
    try:
        await _consume_code(session, locked, body.code, settings, allow_recovery=False)
    except HTTPException:
        await session.rollback()
        raise
    now = utcnow()
    locked.mfa_enabled_at = now
    codes = mfa.new_recovery_codes()
    await session.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == locked.id))
    session.add_all(
        MfaRecoveryCode(user_id=locked.id, code_hash=mfa.hash_recovery_code(c)) for c in codes
    )
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == locked.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    await audit.record(
        session,
        actor_id=locked.id,
        action="auth.mfa_enabled",
        entity_type="user",
        entity_id=locked.id,
    )
    await session.commit()
    return MfaEnabledOut(recovery_codes=codes)


@router.post("/mfa/disable", status_code=status.HTTP_204_NO_CONTENT)
async def mfa_disable(
    body: MfaCodeIn,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    limiter: LimiterDep,
) -> Response:
    """Turn TOTP off (a current TOTP or a recovery code is required). Staff accounts lose access
    to staff-only endpoints while MFA_REQUIRED_FOR_STAFF is in effect."""
    await limiter.enforce_account("mfa", settings.rate_limit_mfa_account, str(user.id))
    locked = await _lock_user(session, user.id)
    if locked is None or locked.mfa_enabled_at is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Two-factor authentication is not enabled")
    try:
        await _consume_code(session, locked, body.code, settings, allow_recovery=True)
    except HTTPException:
        await session.rollback()
        raise
    locked.mfa_secret_encrypted = None
    locked.mfa_enabled_at = None
    locked.mfa_last_used_step = None
    await session.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == locked.id))
    await audit.record(
        session,
        actor_id=locked.id,
        action="auth.mfa_disabled",
        entity_type="user",
        entity_id=locked.id,
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/refresh",
    response_model=TokenPair,
    dependencies=[Depends(ip_limit("refresh", "rate_limit_refresh_ip"))],
)
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
