"""TOTP two-factor auth: enrolment → login challenge → verify; replay, drift, recovery codes,
encryption at rest, and the staff-must-enrol rule (MFA_REQUIRED_FOR_STAFF)."""

from typing import Any

import httpx
import pyotp
import pytest

from aqua_api import mfa

PASSWORD = "correct-horse-battery"
T0 = 1_900_000_000.0  # pinned TOTP clock (start of a 30 s step: 1_900_000_000 / 30 = 63_333_333.3)


@pytest.fixture
def settings_overrides() -> dict[str, Any]:
    return {"mfa_required_for_staff": True}


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> dict[str, float]:
    state = {"now": T0}
    monkeypatch.setattr(mfa, "_now", lambda: state["now"])
    return state


def code_at(secret: str, t: float) -> str:
    return pyotp.TOTP(secret).at(int(t))


async def enrol(actor: Any, now: float) -> tuple[str, list[str]]:
    setup = await actor.post("/auth/mfa/setup")
    assert setup.status_code == 200, setup.text
    body = setup.json()
    assert body["otpauth_uri"].startswith("otpauth://totp/AquaCreds:")
    assert body["qr_svg"].startswith("<svg")
    secret = body["secret"]
    enabled = await actor.post("/auth/mfa/enable", json={"code": code_at(secret, now)})
    assert enabled.status_code == 200, enabled.text
    codes = enabled.json()["recovery_codes"]
    assert len(codes) == 10
    assert len(set(codes)) == 10
    return secret, codes


async def login(client: httpx.AsyncClient, email: str) -> dict[str, Any]:
    r = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


async def verify(client: httpx.AsyncClient, token: str, code: str) -> httpx.Response:
    return await client.post("/auth/mfa/verify", json={"mfa_token": token, "code": code})


async def test_full_enrolment_login_and_verify(
    client: httpx.AsyncClient, make_actor: Any, clock: dict[str, float], sql: Any
) -> None:
    user = await make_actor("Asha")
    secret, _ = await enrol(user, clock["now"])

    me = await user.get("/auth/me")
    assert me.json()["mfa_enabled"] is True

    challenge = await login(client, user.email)
    assert challenge["mfa_required"] is True
    assert "access_token" not in challenge
    # The challenge token is not an access token.
    probe = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {challenge['mfa_token']}"}
    )
    assert probe.status_code == 401

    clock["now"] += 30
    r = await verify(client, challenge["mfa_token"], code_at(secret, clock["now"]))
    assert r.status_code == 200, r.text
    tokens = r.json()
    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert me.status_code == 200

    # Enabling MFA signed out sessions created before it (their refresh tokens are revoked).
    rows = await sql(
        "SELECT count(*) FROM refresh_tokens WHERE user_id=:id AND revoked_at IS NULL", id=user.id
    )
    assert rows[0][0] == 1  # only the session created by the MFA login

    actions = {r[0] for r in await sql("SELECT action FROM audit_log")}
    assert "auth.mfa_enabled" in actions


async def test_secret_is_encrypted_at_rest(
    make_actor: Any, clock: dict[str, float], sql: Any
) -> None:
    user = await make_actor("Ravi")
    secret, codes = await enrol(user, clock["now"])
    stored = (await sql("SELECT mfa_secret_encrypted FROM users WHERE id=:id", id=user.id))[0][0]
    assert secret not in stored
    assert stored.startswith("gAAAAA")  # Fernet token
    from aqua_core.settings import Settings

    assert mfa.decrypt_secret(stored, Settings(_env_file=None)) == secret
    hashes = {r[0] for r in await sql("SELECT code_hash FROM mfa_recovery_codes")}
    assert len(hashes) == 10
    assert not any(code.replace("-", "") in h for code in codes for h in hashes)
    assert mfa.hash_recovery_code(codes[0]) in hashes


async def test_code_replay_is_rejected(
    client: httpx.AsyncClient, make_actor: Any, clock: dict[str, float]
) -> None:
    user = await make_actor("Meera")
    secret, _ = await enrol(user, clock["now"])
    # The code used to enable cannot be reused to log in.
    challenge = await login(client, user.email)
    assert (
        await verify(client, challenge["mfa_token"], code_at(secret, clock["now"]))
    ).status_code == 401

    clock["now"] += 30
    code = code_at(secret, clock["now"])
    assert (await verify(client, challenge["mfa_token"], code)).status_code == 200
    second = await login(client, user.email)
    replay = await verify(client, second["mfa_token"], code)
    assert replay.status_code == 401
    assert replay.json()["detail"] == "Invalid authentication code"
    # An older (but still in-window) code is also refused once a newer step was accepted.
    assert (
        await verify(client, second["mfa_token"], code_at(secret, clock["now"] - 30))
    ).status_code == 401


async def test_drift_window_is_one_step(
    client: httpx.AsyncClient, make_actor: Any, clock: dict[str, float]
) -> None:
    user = await make_actor("Kiran")
    secret, _ = await enrol(user, clock["now"])
    clock["now"] += 300
    challenge = await login(client, user.email)
    token = challenge["mfa_token"]
    # Two steps behind / ahead: rejected.
    assert (await verify(client, token, code_at(secret, clock["now"] - 60))).status_code == 401
    assert (await verify(client, token, code_at(secret, clock["now"] + 60))).status_code == 401
    # One step behind (phone clock slow): accepted.
    assert (await verify(client, token, code_at(secret, clock["now"] - 30))).status_code == 200
    # One step ahead of the clock: accepted (newer than the last used step).
    later = await login(client, user.email)
    assert (
        await verify(client, later["mfa_token"], code_at(secret, clock["now"] + 30))
    ).status_code == 200


async def test_recovery_codes_work_once(
    client: httpx.AsyncClient, make_actor: Any, clock: dict[str, float], sql: Any
) -> None:
    user = await make_actor("Lata")
    _, codes = await enrol(user, clock["now"])
    challenge = await login(client, user.email)
    # Codes are case- and dash-insensitive.
    r = await verify(client, challenge["mfa_token"], codes[3].lower().replace("-", ""))
    assert r.status_code == 200, r.text
    again = await login(client, user.email)
    assert (await verify(client, again["mfa_token"], codes[3])).status_code == 401
    assert (await verify(client, again["mfa_token"], "AAAA-BBBB-CCCC-DDDD")).status_code == 401
    assert (await verify(client, again["mfa_token"], codes[4])).status_code == 200
    used = await sql(
        "SELECT action, data FROM audit_log WHERE action='auth.mfa_recovery_code_used'"
    )
    assert [row[1]["remaining"] for row in used] == [9, 8]


async def test_bad_or_expired_challenge_tokens(
    client: httpx.AsyncClient, make_actor: Any, clock: dict[str, float]
) -> None:
    user = await make_actor("Dev")
    secret, _ = await enrol(user, clock["now"])
    r = await verify(client, "x" * 40, code_at(secret, clock["now"] + 30))
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid or expired MFA challenge"
    # An access token is not a challenge token.
    assert (await verify(client, user.token, code_at(secret, clock["now"] + 30))).status_code == 401


async def test_setup_enable_disable_rules(
    client: httpx.AsyncClient, make_actor: Any, clock: dict[str, float], sql: Any
) -> None:
    user = await make_actor("Nila")
    assert (await user.post("/auth/mfa/enable", json={"code": "123456"})).status_code == 409
    assert (await user.post("/auth/mfa/disable", json={"code": "123456"})).status_code == 409
    setup = (await user.post("/auth/mfa/setup")).json()
    wrong = await user.post("/auth/mfa/enable", json={"code": "000000"})
    assert wrong.status_code == 401
    # Pending (unconfirmed) enrolment does not change login.
    assert "access_token" in await login(client, user.email)
    ok = await user.post("/auth/mfa/enable", json={"code": code_at(setup["secret"], clock["now"])})
    assert ok.status_code == 200
    assert (await user.post("/auth/mfa/setup")).status_code == 409

    clock["now"] += 30
    assert (await user.post("/auth/mfa/disable", json={"code": "999999"})).status_code == 401
    off = await user.post(
        "/auth/mfa/disable", json={"code": code_at(setup["secret"], clock["now"])}
    )
    assert off.status_code == 204
    assert "access_token" in await login(client, user.email)
    remaining = await sql("SELECT count(*) FROM mfa_recovery_codes WHERE user_id=:id", id=user.id)
    assert remaining[0][0] == 0
    stored = await sql("SELECT mfa_secret_encrypted FROM users WHERE id=:id", id=user.id)
    assert stored[0][0] is None


async def test_staff_without_mfa_get_403_on_staff_endpoints(
    client: httpx.AsyncClient, make_actor: Any, clock: dict[str, float], sql: Any
) -> None:
    admin = await make_actor("Registry Admin")
    await sql("UPDATE users SET platform_role='registry_admin' WHERE id=:id", id=admin.id)
    r = await admin.get("/audit/verify")
    assert r.status_code == 403
    assert "Two-factor authentication is required" in r.json()["detail"]

    secret, _ = await enrol(admin, clock["now"])
    clock["now"] += 30
    challenge = await login(client, admin.email)
    tokens = (await verify(client, challenge["mfa_token"], code_at(secret, clock["now"]))).json()
    ok = await client.get(
        "/audit/verify", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert ok.status_code == 200
    # A plain user is still refused for lack of role (not MFA).
    plain = await make_actor("Plain")
    denied = await plain.get("/audit/verify")
    assert denied.status_code == 403
    assert denied.json()["detail"] == "Insufficient platform role"
