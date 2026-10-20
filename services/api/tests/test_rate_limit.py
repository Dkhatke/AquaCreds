"""Auth rate limits (429 + Retry-After), proxy-aware client IP, and security headers."""

import ipaddress
from typing import Any

import httpx
import pytest
from starlette.requests import Request

from aqua_api.ratelimit import (
    MemoryRateLimitBackend,
    RateLimiter,
    RedisRateLimitBackend,
    client_ip,
)
from aqua_core.settings import Settings, parse_rate

PASSWORD = "correct-horse-battery"


@pytest.fixture
def settings_overrides() -> dict[str, Any]:
    return {
        "rate_limit_enabled": True,
        "rate_limit_login_ip": "4/minute",
        "rate_limit_login_account": "2/minute",
        "rate_limit_register_ip": "3/minute",
        "rate_limit_refresh_ip": "2/minute",
        "rate_limit_mfa_ip": "3/minute",
        "trusted_proxy_ips": "10.0.0.0/8",
    }


def _from(ip: str) -> dict[str, str]:
    # The ASGI test transport's peer is 127.0.0.1, which is not a trusted proxy here, so this
    # header must be IGNORED (a client can't pick its own rate-limit bucket).
    return {"X-Forwarded-For": ip}


async def _register(client: httpx.AsyncClient, email: str) -> httpx.Response:
    return await client.post(
        "/auth/register", json={"email": email, "full_name": "Rate Tester", "password": PASSWORD}
    )


def _assert_429(r: httpx.Response) -> None:
    assert r.status_code == 429, r.text
    assert 1 <= int(r.headers["retry-after"]) <= 61


async def test_login_limited_per_account(client: httpx.AsyncClient) -> None:
    assert (await _register(client, "acct@example.org")).status_code == 201
    for _ in range(2):
        r = await client.post(
            "/auth/login", json={"email": "acct@example.org", "password": "x" * 12}
        )
        assert r.status_code == 401
    # Third try for the same account is refused even with the right password.
    _assert_429(
        await client.post("/auth/login", json={"email": "ACCT@example.org", "password": PASSWORD})
    )
    # Other accounts are unaffected (until the per-IP limit).
    r = await client.post("/auth/login", json={"email": "other@example.org", "password": PASSWORD})
    assert r.status_code == 401


async def test_login_limited_per_ip_and_spoofed_xff_ignored(client: httpx.AsyncClient) -> None:
    for i in range(4):
        r = await client.post(
            "/auth/login",
            json={"email": f"u{i}@example.org", "password": PASSWORD},
            headers=_from(f"203.0.113.{i}"),
        )
        assert r.status_code == 401
    _assert_429(
        await client.post(
            "/auth/login",
            json={"email": "u9@example.org", "password": PASSWORD},
            headers=_from("198.51.100.7"),
        )
    )


async def test_register_and_refresh_limited_per_ip(client: httpx.AsyncClient) -> None:
    for i in range(3):
        assert (await _register(client, f"r{i}@example.org")).status_code == 201
    _assert_429(await _register(client, "r3@example.org"))
    for _ in range(2):
        r = await client.post("/auth/refresh", json={"refresh_token": "z" * 40})
        assert r.status_code == 401
    _assert_429(await client.post("/auth/refresh", json={"refresh_token": "z" * 40}))


async def test_mfa_verify_limited_per_ip(client: httpx.AsyncClient) -> None:
    for _ in range(3):
        r = await client.post("/auth/mfa/verify", json={"mfa_token": "x" * 40, "code": "123456"})
        assert r.status_code == 401
    _assert_429(
        await client.post("/auth/mfa/verify", json={"mfa_token": "x" * 40, "code": "123456"})
    )


async def test_recovery_code_regeneration_limited_per_ip(client: httpx.AsyncClient) -> None:
    email = "regen.limit@example.org"
    assert (await _register(client, email)).status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    for _ in range(3):
        r = await client.post("/auth/mfa/recovery-codes", json={"code": "123456"}, headers=headers)
        assert r.status_code == 409  # MFA not enabled; still counted
    _assert_429(
        await client.post("/auth/mfa/recovery-codes", json={"code": "123456"}, headers=headers)
    )


async def test_security_headers_on_json_responses(client: httpx.AsyncClient) -> None:
    r = await client.get("/auth/me")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert "default-src 'none'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"  # auth responses carry tokens
    health = await client.get("http://test/healthz")
    assert "cache-control" not in health.headers
    assert health.headers["x-frame-options"] == "DENY"


async def test_docs_page_keeps_working_without_strict_csp(client: httpx.AsyncClient) -> None:
    docs = await client.get("http://test/docs")
    assert docs.status_code == 200
    assert "swagger-ui" in docs.text
    assert "content-security-policy" not in docs.headers
    assert docs.headers["x-content-type-options"] == "nosniff"


# ---------------------------------------------------------------- units


def _request(peer: str, xff: str | None = None) -> Request:
    headers = [(b"x-forwarded-for", xff.encode())] if xff is not None else []
    return Request({"type": "http", "client": (peer, 1234), "headers": headers})


NETS = [ipaddress.ip_network("172.16.0.0/12"), ipaddress.ip_network("10.0.0.5/32")]


@pytest.mark.parametrize(
    ("peer", "xff", "expected"),
    [
        ("203.0.113.9", None, "203.0.113.9"),
        ("203.0.113.9", "1.2.3.4", "203.0.113.9"),  # untrusted peer: header ignored
        ("172.18.0.3", "1.2.3.4", "1.2.3.4"),  # Caddy on the Docker network
        ("172.18.0.3", "6.6.6.6, 1.2.3.4", "1.2.3.4"),  # client-forged left-most hop ignored
        ("172.18.0.3", "1.2.3.4, 10.0.0.5", "1.2.3.4"),  # chained trusted proxies skipped
        ("172.18.0.3", "not-an-ip", "172.18.0.3"),
        ("172.18.0.3", None, "172.18.0.3"),
        ("172.18.0.3", "172.18.0.9", "172.18.0.9"),  # every hop trusted
    ],
)
def test_client_ip(peer: str, xff: str | None, expected: str) -> None:
    assert client_ip(_request(peer, xff), NETS) == expected


def test_client_ip_without_trusted_proxies_uses_peer() -> None:
    assert client_ip(_request("172.18.0.3", "1.2.3.4"), []) == "172.18.0.3"


def test_parse_rate_and_settings_validation() -> None:
    assert parse_rate("10/minute") == (10, 60)
    assert parse_rate(" 3 / hour ") == (3, 3600)
    for bad in ("0/minute", "ten/minute", "5/week"):
        with pytest.raises(ValueError, match="invalid rate"):
            parse_rate(bad)
    with pytest.raises(ValueError, match="invalid rate"):
        Settings(_env_file=None, rate_limit_login_ip="lots")
    with pytest.raises(ValueError, match="does not appear to be an IPv4 or IPv6 network"):
        Settings(_env_file=None, trusted_proxy_ips="caddy")


async def test_window_resets_and_retry_after_counts_down() -> None:
    now = {"t": 1_000_040.0}  # 20 s into a minute window (16_667 × 60 = 1_000_020)
    backend = MemoryRateLimitBackend(clock=lambda: now["t"])
    limiter = RateLimiter(Settings(_env_file=None), backend, clock=lambda: now["t"])
    await limiter.enforce("s", "1/minute", "a")
    with pytest.raises(Exception) as exc:  # noqa: PT011 (HTTPException checked below)
        await limiter.enforce("s", "1/minute", "a")
    assert exc.value.headers == {"Retry-After": "40"}  # type: ignore[attr-defined]
    now["t"] += 41  # next window
    await limiter.enforce("s", "1/minute", "a")


async def test_limiter_fails_open_when_backend_is_down() -> None:
    limiter = RateLimiter(Settings(_env_file=None), RedisRateLimitBackend("redis://127.0.0.1:1/0"))
    for _ in range(3):
        await limiter.enforce("s", "1/minute", "a")  # no exception: logged, allowed
    await limiter.backend.close()


def _docker_available() -> bool:
    try:
        import docker

        docker.from_env().ping()
        return True
    except Exception:
        return False


async def test_redis_backend_against_valkey() -> None:
    if not _docker_available():
        pytest.skip("Docker is required")
    from testcontainers.community.redis import RedisContainer

    with RedisContainer("valkey/valkey:8.1-alpine") as valkey:
        url = f"redis://{valkey.get_container_host_ip()}:{valkey.get_exposed_port(6379)}/0"
        backend = RedisRateLimitBackend(url)
        try:
            assert [await backend.hit("k", 60) for _ in range(3)] == [1, 2, 3]
            ttl = await backend._client.ttl("k")
            assert 0 < ttl <= 61
            limiter = RateLimiter(Settings(_env_file=None), backend)
            await limiter.enforce("login", "2/minute", "x@example.org")
            await limiter.enforce("login", "2/minute", "x@example.org")
            with pytest.raises(Exception, match="Too many requests"):
                await limiter.enforce("login", "2/minute", "x@example.org")
            keys = [k.decode() for k in await backend._client.keys("aquacreds:rl:*")]
            assert keys
            assert not any("example.org" in k for k in keys)  # subjects are hashed
        finally:
            await backend.close()
