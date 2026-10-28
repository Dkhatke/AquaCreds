"""Password hashing (Argon2id) and token handling (JWT access + opaque rotating refresh)."""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from aqua_core.settings import Settings

_hasher = PasswordHasher()  # Argon2id with library defaults (RFC 9106 recommended params)
ACCESS_AUDIENCE = "aquacreds-api"
# Explore Demo tokens use their own audience, so `decode_access_token` (and the MFA challenge
# decoder) can never accept one as an ordinary session, whatever its other claims say.
DEMO_AUDIENCE = "aquacreds-demo"
DEMO_SCOPE = "read"
ALGORITHM = "HS256"

# Hash compared against when the user does not exist, so login timing doesn't reveal accounts.
_DUMMY_HASH = _hasher.hash("timing-equaliser")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def create_access_token(user_id: uuid.UUID, settings: Settings) -> tuple[str, int]:
    ttl = settings.jwt_access_ttl_min * 60
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "aud": ACCESS_AUDIENCE,
        "iat": now,
        "exp": now + timedelta(seconds=ttl),
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(payload, settings.jwt_secret.get_secret_value(), algorithm=ALGORITHM)
    return token, ttl


def decode_access_token(token: str, settings: Settings) -> dict[str, Any]:
    """Raises jwt.PyJWTError on any problem (expired, bad signature, wrong audience...)."""
    payload: dict[str, Any] = jwt.decode(
        token,
        settings.jwt_secret.get_secret_value(),
        algorithms=[ALGORITHM],
        audience=ACCESS_AUDIENCE,
        options={"require": ["exp", "sub", "aud"]},
    )
    return payload


def new_refresh_token() -> tuple[str, str]:
    """Return (token, sha256_hex). Only the hash is stored."""
    token = secrets.token_urlsafe(48)
    return token, hash_refresh_token(token)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_demo_token(
    user_id: uuid.UUID, org_id: uuid.UUID, settings: Settings
) -> tuple[str, int, str]:
    """A short-lived, read-only Explore Demo token for the demo org's visitor account.
    Returns (token, ttl seconds, jti). There is no refresh token."""
    ttl = settings.demo_session_ttl_min * 60
    now = datetime.now(UTC)
    jti = uuid.uuid4().hex
    payload = {
        "sub": str(user_id),
        "aud": DEMO_AUDIENCE,
        "iat": now,
        "exp": now + timedelta(seconds=ttl),
        "jti": jti,
        "demo": True,
        "scope": DEMO_SCOPE,
        "org": str(org_id),
    }
    token = jwt.encode(payload, settings.jwt_secret.get_secret_value(), algorithm=ALGORITHM)
    return token, ttl, jti


def decode_demo_token(token: str, settings: Settings) -> dict[str, Any]:
    """Raises jwt.PyJWTError unless this is a valid, unexpired demo token (demo=true, scope=read)."""
    payload: dict[str, Any] = jwt.decode(
        token,
        settings.jwt_secret.get_secret_value(),
        algorithms=[ALGORITHM],
        audience=DEMO_AUDIENCE,
        options={"require": ["exp", "sub", "aud", "org"]},
    )
    if payload.get("demo") is not True or payload.get("scope") != DEMO_SCOPE:
        raise jwt.InvalidTokenError("not a read-only demo token")
    return payload


def looks_like_demo_token(token: str) -> bool:
    """True when the (unverified) token claims to be a demo token: demo audience or `demo` claim.
    Used to refuse writes before any route runs; a forged claim only ever makes things stricter."""
    try:
        claims = jwt.decode(token, options={"verify_signature": False})
    except jwt.PyJWTError:
        return False
    if not isinstance(claims, dict):  # pragma: no cover - PyJWT always returns a dict
        return False
    aud = claims.get("aud")
    audiences = aud if isinstance(aud, list) else [aud]
    return DEMO_AUDIENCE in audiences or claims.get("demo") is not None
