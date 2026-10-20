from collections.abc import AsyncIterator

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import Request

from aqua_api.main import create_app
from aqua_api.routers import health
from aqua_core.settings import Settings


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(Settings(_env_file=None))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _ok(_: Request) -> str:
    return "ok"


async def _fail(_: Request) -> str:
    raise ConnectionError("connection refused")


async def test_healthz_is_always_ok(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_readyz_ready_when_all_checks_pass(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(health, "CHECKS", {"database": _ok, "redis": _ok, "storage": _ok})
    response = await client.get("/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert all(check["ok"] for check in body["checks"].values())


async def test_readyz_503_and_reports_failing_dependency(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(health, "CHECKS", {"database": _ok, "redis": _fail, "storage": _ok})
    response = await client.get("/readyz")
    assert response.status_code == 503
    checks = response.json()["checks"]
    assert checks["database"]["ok"] is True
    assert checks["redis"]["ok"] is False
    assert "connection refused" in checks["redis"]["error"]


async def test_docs_hidden_in_production() -> None:
    app = create_app(
        Settings(
            _env_file=None,
            app_env="production",
            jwt_secret="y" * 40,
            s3_secret_access_key="z",
            # BIP-39 test vector (no funds); only checks that a non-dev mnemonic is configured.
            relayer_mnemonic=" ".join(["abandon"] * 11 + ["about"]),
            mfa_encryption_key=Fernet.generate_key().decode(),
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        assert (await c.get("/docs")).status_code == 404
        # Strict CSP everywhere in production (no docs exemption needed).
        health = await c.get("/healthz")
        assert "default-src 'none'" in health.headers["content-security-policy"]
