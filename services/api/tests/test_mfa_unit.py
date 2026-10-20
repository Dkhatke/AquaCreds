"""TOTP/recovery-code/encryption units, including the RFC 6238 Appendix B test vectors."""

import base64

import pyotp
import pytest
from cryptography.fernet import Fernet

from aqua_api import mfa
from aqua_core.settings import Settings

# RFC 6238 Appendix B: SHA-1, 8 digits, 30 s steps, ASCII secret "12345678901234567890".
RFC_SECRET = base64.b32encode(b"12345678901234567890").decode()
RFC_VECTORS = [
    (59, "94287082"),
    (1111111109, "07081804"),
    (1111111111, "14050471"),
    (1234567890, "89005924"),
    (2000000000, "69279037"),
    (20000000000, "65353130"),
]


@pytest.mark.parametrize(("t", "expected"), RFC_VECTORS)
def test_rfc6238_vectors(t: int, expected: str) -> None:
    assert pyotp.TOTP(RFC_SECRET, digits=8, interval=30).at(t) == expected
    # Our step arithmetic agrees with the RFC's T = floor(t / 30).
    assert mfa.current_step(t) == t // 30


def test_match_totp_returns_the_matched_step_within_one_step_of_drift() -> None:
    secret = mfa.new_secret()
    now = 1_900_000_015.0
    step = mfa.current_step(now)
    totp = pyotp.TOTP(secret)
    assert mfa.match_totp(secret, totp.at(int(now)), now) == step
    assert mfa.match_totp(secret, totp.at(int(now) - 30), now) == step - 1
    assert mfa.match_totp(secret, totp.at(int(now) + 30), now) == step + 1
    assert mfa.match_totp(secret, totp.at(int(now) - 60), now) is None
    assert mfa.match_totp(secret, totp.at(int(now) + 60), now) is None
    spaced = totp.at(int(now))
    assert mfa.match_totp(secret, f"{spaced[:3]} {spaced[3:]}", now) == step
    for junk in ("", "12345", "1234567", "abcdef"):
        assert mfa.match_totp(secret, junk, now) is None


def test_secret_is_160_bits_base32() -> None:
    secret = mfa.new_secret()
    assert len(base64.b32decode(secret)) == 20


def test_encryption_round_trip_and_key_rotation() -> None:
    old_key, new_key = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    old = Settings(_env_file=None, mfa_encryption_key=old_key)
    rotated = Settings(_env_file=None, mfa_encryption_key=f"{new_key}, {old_key}")
    token = mfa.encrypt_secret("JBSWY3DPEHPK3PXP", old)
    assert "JBSWY3DPEHPK3PXP" not in token
    assert mfa.decrypt_secret(token, old) == "JBSWY3DPEHPK3PXP"
    assert mfa.decrypt_secret(token, rotated) == "JBSWY3DPEHPK3PXP"  # old key still decrypts
    fresh = mfa.encrypt_secret("JBSWY3DPEHPK3PXP", rotated)
    with pytest.raises(mfa.MfaKeyError):
        mfa.decrypt_secret(fresh, old)  # new secrets use the first (new) key
    with pytest.raises(mfa.MfaKeyError):
        mfa.decrypt_secret(token, Settings(_env_file=None))  # dev key


def test_recovery_codes() -> None:
    codes = mfa.new_recovery_codes()
    assert len(codes) == mfa.RECOVERY_CODE_COUNT == 10
    assert len(set(codes)) == 10
    for code in codes:
        groups = code.split("-")
        assert [len(g) for g in groups] == [4, 4, 4, 4]
        assert mfa.looks_like_recovery_code(code)
        assert mfa.hash_recovery_code(code) == mfa.hash_recovery_code(
            code.lower().replace("-", " ")
        )
    assert not mfa.looks_like_recovery_code("123456")


def test_mfa_token_is_bound_to_its_audience() -> None:
    import uuid

    import jwt

    from aqua_api.security import create_access_token, decode_access_token

    settings = Settings(_env_file=None, jwt_secret="k" * 40)
    user_id = uuid.uuid4()
    token = mfa.create_mfa_token(user_id, settings)
    assert mfa.decode_mfa_token(token, settings)["sub"] == str(user_id)
    with pytest.raises(jwt.InvalidAudienceError):
        decode_access_token(token, settings)
    access, _ = create_access_token(user_id, settings)
    with pytest.raises(jwt.InvalidAudienceError):
        mfa.decode_mfa_token(access, settings)


def test_qr_svg_and_uri() -> None:
    settings = Settings(_env_file=None)
    uri = mfa.provisioning_uri("JBSWY3DPEHPK3PXP", "asha@example.org", settings)
    assert uri.startswith("otpauth://totp/AquaCreds:asha%40example.org?secret=JBSWY3DPEHPK3PXP")
    assert "issuer=AquaCreds" in uri
    assert mfa.qr_svg(uri).startswith("<svg")
