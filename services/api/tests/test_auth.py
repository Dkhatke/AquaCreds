from typing import Any

import httpx

PASSWORD = "correct-horse-battery"


async def _register_and_login(client: httpx.AsyncClient, email: str) -> dict[str, Any]:
    r = await client.post(
        "/auth/register", json={"email": email, "full_name": "Asha Rao", "password": PASSWORD}
    )
    assert r.status_code == 201, r.text
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    tokens: dict[str, Any] = login.json()
    return tokens


async def test_register_login_me(client: httpx.AsyncClient) -> None:
    tokens = await _register_and_login(client, "Asha@Example.org")
    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert me.status_code == 200
    body = me.json()
    assert body["email"] == "asha@example.org"  # normalised to lower case
    assert body["platform_role"] == "user"
    assert "password" not in str(body)


async def test_duplicate_email_rejected(client: httpx.AsyncClient) -> None:
    await _register_and_login(client, "dup@example.org")
    r = await client.post(
        "/auth/register",
        json={"email": "DUP@example.org", "full_name": "Someone", "password": PASSWORD},
    )
    assert r.status_code == 409


async def test_wrong_password_and_unknown_user_look_identical(client: httpx.AsyncClient) -> None:
    await _register_and_login(client, "a@example.org")
    wrong = await client.post("/auth/login", json={"email": "a@example.org", "password": "x" * 12})
    unknown = await client.post(
        "/auth/login", json={"email": "no@example.org", "password": "x" * 12}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


async def test_protected_route_requires_valid_token(client: httpx.AsyncClient) -> None:
    assert (await client.get("/auth/me")).status_code == 401
    bad = await client.get("/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert bad.status_code == 401


async def test_refresh_rotation_and_reuse_detection(client: httpx.AsyncClient) -> None:
    tokens = await _register_and_login(client, "rot@example.org")
    first = tokens["refresh_token"]

    rotated = await client.post("/auth/refresh", json={"refresh_token": first})
    assert rotated.status_code == 200
    second = rotated.json()["refresh_token"]
    assert second != first

    # Replaying the already-used token is treated as theft: the whole family is revoked...
    replay = await client.post("/auth/refresh", json={"refresh_token": first})
    assert replay.status_code == 401
    # ...so even the legitimately rotated token stops working.
    assert (await client.post("/auth/refresh", json={"refresh_token": second})).status_code == 401


async def test_logout_revokes_refresh_token(client: httpx.AsyncClient) -> None:
    tokens = await _register_and_login(client, "out@example.org")
    assert (
        await client.post("/auth/logout", json={"refresh_token": tokens["refresh_token"]})
    ).status_code == 204
    assert (
        await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    ).status_code == 401
