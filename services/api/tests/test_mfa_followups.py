"""MFA follow-ups: recovery-code regeneration, the status endpoint, the low-recovery-code warning,
and staff privileges (reading every organisation's projects) gated on MFA_REQUIRED_FOR_STAFF."""

import json
import uuid
from typing import Any

import httpx
import pytest
from api_testlib import project_payload, square
from test_mfa import code_at, enrol, login, verify

from aqua_api import mfa

T0 = 1_900_000_000.0


@pytest.fixture
def settings_overrides() -> dict[str, Any]:
    return {"mfa_required_for_staff": True}


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> dict[str, float]:
    state = {"now": T0}
    monkeypatch.setattr(mfa, "_now", lambda: state["now"])
    return state


async def _promote(sql: Any, actor: Any, role: str) -> None:
    await sql("UPDATE users SET platform_role=:r WHERE id=:id", r=role, id=actor.id)


# ---------------------------------------------------------------- recovery-code regeneration


async def test_regenerate_recovery_codes(
    client: httpx.AsyncClient, make_actor: Any, clock: dict[str, float], sql: Any
) -> None:
    user = await make_actor("Regen")
    url = "/auth/mfa/recovery-codes"
    secret, old_codes = await enrol(user, clock["now"])

    # The code that enabled MFA cannot be replayed to regenerate.
    replay = await user.post(url, json={"code": code_at(secret, clock["now"])})
    assert replay.status_code == 401
    # A recovery code is not accepted: regeneration needs the authenticator.
    assert (await user.post(url, json={"code": old_codes[0]})).status_code == 401

    clock["now"] += 30
    code = code_at(secret, clock["now"])
    r = await user.post(url, json={"code": code})
    assert r.status_code == 200, r.text
    new_codes = r.json()["recovery_codes"]
    assert len(new_codes) == 10
    assert len(set(new_codes)) == 10
    assert not set(new_codes) & set(old_codes)
    # The same TOTP code cannot be used twice (replay protection holds for regeneration).
    assert (await user.post(url, json={"code": code})).status_code == 401

    # Only hashes are stored, and only of the new codes.
    hashes = {
        r[0]
        for r in await sql("SELECT code_hash FROM mfa_recovery_codes WHERE user_id=:id", id=user.id)
    }
    assert hashes == {mfa.hash_recovery_code(c) for c in new_codes}

    # Old codes are rejected at sign-in; new ones work.
    challenge = await login(client, user.email)
    assert (await verify(client, challenge["mfa_token"], old_codes[1])).status_code == 401
    assert (await verify(client, challenge["mfa_token"], new_codes[0])).status_code == 200

    # Audited without the codes.
    rows = await sql(
        "SELECT data FROM audit_log WHERE action='auth.mfa_recovery_codes_regenerated'"
    )
    assert len(rows) == 1
    assert rows[0][0] == {"issued": 10, "invalidated_unused": 10}
    dumped = json.dumps(rows[0][0])
    assert not any(c.replace("-", "") in dumped.replace("-", "") for c in new_codes + old_codes)


async def test_regenerate_requires_enabled_mfa(make_actor: Any) -> None:
    user = await make_actor("NoMfa")
    r = await user.post("/auth/mfa/recovery-codes", json={"code": "123456"})
    assert r.status_code == 409
    assert (
        await user.client.post("/auth/mfa/recovery-codes", json={"code": "123456"})
    ).status_code == 401


# ---------------------------------------------------------------- status


async def test_mfa_status(
    client: httpx.AsyncClient, make_actor: Any, clock: dict[str, float], sql: Any
) -> None:
    user = await make_actor("Status")
    off = (await user.get("/auth/mfa")).json()
    assert off == {
        "enabled": False,
        "enabled_at": None,
        "recovery_codes_remaining": 0,
        "required": False,
    }

    _, codes = await enrol(user, clock["now"])
    on = (await user.get("/auth/mfa")).json()
    assert on["enabled"] is True
    assert on["enabled_at"] is not None
    assert on["recovery_codes_remaining"] == 10
    assert on["required"] is False

    challenge = await login(client, user.email)
    tokens = (await verify(client, challenge["mfa_token"], codes[0])).json()
    after = await client.get(
        "/auth/mfa", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert after.json()["recovery_codes_remaining"] == 9

    await _promote(sql, user, "verifier")
    assert (await user.get("/auth/mfa")).json()["required"] is True
    assert (await client.get("/auth/mfa")).status_code == 401


# ---------------------------------------------------------------- low recovery codes warning


async def test_token_response_warns_when_few_recovery_codes_remain(
    client: httpx.AsyncClient, make_actor: Any, clock: dict[str, float]
) -> None:
    user = await make_actor("Few")
    secret, codes = await enrol(user, clock["now"])
    seen: list[int | None] = []
    for code in codes[:9]:
        challenge = await login(client, user.email)
        r = await verify(client, challenge["mfa_token"], code)
        assert r.status_code == 200, r.text
        seen.append(r.json().get("recovery_codes_remaining"))
    assert seen == [None] * 7 + [2, 1]

    # A TOTP sign-in never carries the field.
    clock["now"] += 30
    challenge = await login(client, user.email)
    r = await verify(client, challenge["mfa_token"], code_at(secret, clock["now"]))
    assert r.status_code == 200
    assert "recovery_codes_remaining" not in r.json()

    last = await login(client, user.email)
    final = await verify(client, last["mfa_token"], codes[9])
    assert final.json()["recovery_codes_remaining"] == 0


# ---------------------------------------------------------------- staff privileges need MFA


class World:
    def __init__(self, staff: Any, own_pid: str, other_pid: str, alert_id: str) -> None:
        self.staff, self.own_pid, self.other_pid, self.alert_id = (
            staff,
            own_pid,
            other_pid,
            alert_id,
        )


async def _world(make_actor: Any, sql: Any, role: str) -> World:
    """A staff account that also administers its own organisation's project, and a submitted
    project of another organisation with an open alert."""
    ngo = await make_actor("Other NGO")
    org = (await ngo.post("/orgs", json={"name": "Sundarbans Trust", "org_type": "ngo"})).json()
    other_pid = (await ngo.post("/projects", json=project_payload(org["id"]))).json()["id"]
    await sql("UPDATE projects SET status='submitted' WHERE id=:id", id=other_pid)

    staff = await make_actor("Staff")
    own_org = (await staff.post("/orgs", json={"name": "Staff Home Org", "org_type": "ngo"})).json()
    own = await staff.post(
        "/projects",
        json=project_payload(own_org["id"], boundary=_far_square()),
    )
    assert own.status_code == 201, own.text
    await sql("UPDATE projects SET status='submitted' WHERE id=:id", id=own.json()["id"])
    await _promote(sql, staff, role)

    alert_id = uuid.uuid4()
    await sql(
        "INSERT INTO alerts (id, project_id, kind, metric, observed_on, observed_value, expected, "
        "severity, message) VALUES (:id, :p, 'vegetation_loss', 'ndvi_p50', DATE '2024-01-20', "
        "0.4, CAST('{\"p10\": 0.7}' AS jsonb), 'high', 'NDVI below envelope')",
        id=alert_id,
        p=other_pid,
    )
    return World(staff, own.json()["id"], other_pid, str(alert_id))


def _far_square() -> dict[str, Any]:
    return square(86.95, 20.80)  # does not overlap the default test boundary


@pytest.mark.parametrize("role", ["verifier", "registry_admin"])
async def test_staff_without_mfa_read_like_ordinary_users(
    make_actor: Any, sql: Any, clock: dict[str, float], role: str
) -> None:
    w = await _world(make_actor, sql, role)
    staff = w.staff

    # Projects list: only the staff member's own organisation.
    listed = (await staff.get("/projects")).json()
    assert [p["id"] for p in listed] == [w.own_pid]
    # Project read: own project yes, other organisation's project hidden.
    assert (await staff.get(f"/projects/{w.own_pid}")).status_code == 200
    assert (await staff.get(f"/projects/{w.other_pid}")).status_code == 404
    # Analysis read.
    assert (await staff.get(f"/projects/{w.other_pid}/analysis-runs")).status_code == 404
    assert (await staff.get(f"/projects/{w.own_pid}/analysis-runs")).status_code == 200
    # Alerts acknowledge: the project is invisible, so the alert is not found.
    assert (await staff.post(f"/alerts/{w.alert_id}/acknowledge")).status_code == 404
    # Staff-only endpoints still answer 403 with the MFA requirement.
    if role == "registry_admin":
        denied = await staff.get("/audit/verify")
        assert denied.status_code == 403
        assert "Two-factor authentication is required" in denied.json()["detail"]

    # After enrolling, staff privileges apply again.
    await enrol(staff, clock["now"])
    listed = (await staff.get("/projects")).json()
    assert {p["id"] for p in listed} == {w.own_pid, w.other_pid}
    assert (await staff.get(f"/projects/{w.other_pid}")).status_code == 200
    assert (await staff.get(f"/projects/{w.other_pid}/analysis-runs")).status_code == 200
    ack = await staff.post(f"/alerts/{w.alert_id}/acknowledge")
    assert ack.status_code == 200, ack.text


@pytest.mark.parametrize("settings_overrides", [{"mfa_required_for_staff": False}])
async def test_staff_read_all_when_mfa_not_required(
    make_actor: Any, sql: Any, settings_overrides: dict[str, Any]
) -> None:
    w = await _world(make_actor, sql, "verifier")
    assert (await w.staff.get(f"/projects/{w.other_pid}")).status_code == 200
    status = (await w.staff.get("/auth/mfa")).json()
    assert status["required"] is False
