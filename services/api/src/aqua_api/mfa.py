"""TOTP two-factor authentication helpers (RFC 6238 via pyotp).

- Secrets: 160-bit base32, Fernet-encrypted at rest (`MFA_ENCRYPTION_KEY`, MultiFernet so keys can
  be rotated: the first key encrypts, every key decrypts).
- Verification accepts the current 30 s step ±1 (clock drift) and returns the matched step; callers
  store it and reject any step <= the last accepted one, so a code can never be replayed.
- Recovery codes: 10 × 80-bit random codes shown once; only SHA-256 hashes are stored.
- Login challenge: a short-lived signed JWT (`mfa_token`, own audience) proves the password step.
"""

import hashlib
import hmac
import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pyotp
import segno
from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from aqua_api.security import ALGORITHM
from aqua_core.settings import Settings

MFA_AUDIENCE = "aquacreds-mfa"
TOTP_PERIOD_S = 30
TOTP_DIGITS = 6
DRIFT_STEPS = 1
RECOVERY_CODE_COUNT = 10
_RECOVERY_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"  # no 0/O, 1/I/L, U


_now = time.time  # replaced in tests to pin the TOTP clock


class MfaKeyError(RuntimeError):
    """The stored secret cannot be decrypted with the configured MFA_ENCRYPTION_KEY."""


def _fernet(settings: Settings) -> MultiFernet:
    return MultiFernet([Fernet(key.encode()) for key in settings.mfa_encryption_keys])


def new_secret() -> str:
    return pyotp.random_base32(length=32)  # 32 base32 chars = 160 bits (RFC 4226 recommendation)


def encrypt_secret(secret: str, settings: Settings) -> str:
    return _fernet(settings).encrypt(secret.encode()).decode()


def decrypt_secret(token: str, settings: Settings) -> str:
    try:
        return _fernet(settings).decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise MfaKeyError("MFA secret cannot be decrypted with MFA_ENCRYPTION_KEY") from exc


def provisioning_uri(secret: str, email: str, settings: Settings) -> str:
    return pyotp.TOTP(secret, digits=TOTP_DIGITS, interval=TOTP_PERIOD_S).provisioning_uri(
        name=email, issuer_name=settings.mfa_issuer
    )


def qr_svg(uri: str) -> str:
    svg: str = segno.make(uri, error="m").svg_inline(scale=4)
    return svg


def current_step(now: float | None = None) -> int:
    return int((_now() if now is None else now) // TOTP_PERIOD_S)


def match_totp(secret: str, code: str, now: float | None = None) -> int | None:
    """Return the time step `code` is valid for (current ±DRIFT_STEPS), or None."""
    code = code.strip().replace(" ", "")
    if len(code) != TOTP_DIGITS or not code.isdigit():
        return None
    totp = pyotp.TOTP(secret, digits=TOTP_DIGITS, interval=TOTP_PERIOD_S)
    step = current_step(now)
    matched = None
    for candidate in range(step - DRIFT_STEPS, step + DRIFT_STEPS + 1):
        # Compare every candidate (no early exit) so timing does not reveal which step matched.
        if hmac.compare_digest(totp.generate_otp(candidate), code):
            matched = candidate
    return matched


def new_recovery_codes() -> list[str]:
    codes = []
    for _ in range(RECOVERY_CODE_COUNT):
        raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(16))  # ~78.5 bits
        codes.append("-".join(raw[i : i + 4] for i in range(0, 16, 4)))
    return codes


def normalise_recovery_code(code: str) -> str:
    return "".join(ch for ch in code.upper() if ch.isalnum())


def hash_recovery_code(code: str) -> str:
    return hashlib.sha256(normalise_recovery_code(code).encode()).hexdigest()


def looks_like_recovery_code(code: str) -> bool:
    return len(normalise_recovery_code(code)) == 16


def create_mfa_token(user_id: uuid.UUID, settings: Settings) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "aud": MFA_AUDIENCE,
        "iat": now,
        "exp": now + timedelta(seconds=settings.mfa_token_ttl_s),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.jwt_secret.get_secret_value(), algorithm=ALGORITHM)


def decode_mfa_token(token: str, settings: Settings) -> dict[str, Any]:
    """Raises jwt.PyJWTError if expired/forged/not an MFA challenge token."""
    payload: dict[str, Any] = jwt.decode(
        token,
        settings.jwt_secret.get_secret_value(),
        algorithms=[ALGORITHM],
        audience=MFA_AUDIENCE,
        options={"require": ["exp", "sub", "aud"]},
    )
    return payload
