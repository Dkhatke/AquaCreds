"""Integration-test fixtures: a throwaway PostGIS container (testcontainers), migrated once per
session; tables truncated between tests; an in-memory fake for object storage."""

import hashlib
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from aqua_api.main import create_app
from aqua_core.settings import Settings
from aqua_core.storage import StoredObject

POSTGIS_IMAGE = "imresamu/postgis:17-3.6"
ANVIL_IMAGE = "ghcr.io/foundry-rs/foundry:v1.7.0"
TABLES = (
    "mrv_reports, chain_events, chain_cursors, chain_transactions, org_wallets, "
    "audit_log, alerts, scene_observations, analysis_runs, "
    "plot_measurements, field_submissions, capture_nonces, plots, monitoring_periods, "
    "tenure_documents, projects, refresh_tokens, memberships, organizations, users"
)


def _docker_available() -> bool:
    try:
        import docker

        docker.from_env().ping()
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    if not _docker_available():
        pytest.skip("Docker is required for integration tests")
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer(POSTGIS_IMAGE, driver="asyncpg") as pg:
        url = pg.get_connection_url()
        cfg = Config()
        cfg.set_main_option("script_location", "aqua_api:migrations")
        cfg.set_main_option("sqlalchemy.url", url)
        command.upgrade(cfg, "head")
        yield url


@dataclass
class FakeStorage:
    objects: dict[str, tuple[bytes, str]] = field(default_factory=dict)

    def presign_put(self, key: str, content_type: str) -> str:
        return f"http://storage.test/{key}?signature=fake"

    def presign_get(self, key: str, filename: str | None = None) -> str:
        return f"http://storage.test/{key}?download={filename or ''}&signature=fake"

    def describe(self, key: str) -> StoredObject | None:
        if key not in self.objects:
            return None
        data, content_type = self.objects[key]
        return StoredObject(
            size_bytes=len(data), content_type=content_type, sha256=hashlib.sha256(data).hexdigest()
        )

    def get_bytes(self, key: str, max_bytes: int) -> bytes:
        data, _ = self.objects[key]
        if len(data) > max_bytes:
            raise ValueError("too large")
        return data

    def put(self, key: str, data: bytes, content_type: str = "application/pdf") -> str:
        self.objects[key] = (data, content_type)
        return hashlib.sha256(data).hexdigest()

    def put_bytes(self, key: str, data: bytes, content_type: str) -> str:
        return self.put(key, data, content_type)


@pytest.fixture
def storage() -> FakeStorage:
    return FakeStorage()


@pytest.fixture
def enqueued() -> list[tuple[str, tuple[str, ...], str]]:
    """Tasks the API tried to send to workers (recorded instead of using a broker)."""
    return []


@pytest.fixture(scope="session")
def anvil_url() -> Iterator[str]:
    """A local Anvil chain (chain id 31337, the public dev mnemonic's accounts prefunded)."""
    if not _docker_available():
        pytest.skip("Docker is required for chain integration tests")
    from testcontainers.core.container import DockerContainer
    from testcontainers.core.wait_strategies import LogMessageWaitStrategy

    container = (
        DockerContainer(ANVIL_IMAGE, entrypoint="anvil", command="--host 0.0.0.0 --chain-id 31337")
        .with_exposed_ports(8545)
        .waiting_for(LogMessageWaitStrategy("Listening on"))
    )
    with container:
        yield f"http://{container.get_container_host_ip()}:{container.get_exposed_port(8545)}"


@pytest.fixture
def settings_overrides() -> dict[str, Any]:
    """Extra Settings for the app under test; override in a test module (e.g. chain settings)."""
    return {}


@pytest.fixture
async def client(
    database_url: str,
    storage: FakeStorage,
    enqueued: list[tuple[str, tuple[str, ...], str]],
    settings_overrides: dict[str, Any],
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        Settings(
            _env_file=None,
            app_env="test",
            database_url=database_url,
            jwt_secret="t" * 48,
            **settings_overrides,
        )
    )
    app.state.storage = storage
    app.state.enqueue = lambda name, *args, queue="default": enqueued.append((name, args, queue))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test/api/v1"
    ) as c:
        yield c
    await app.state.engine.dispose()
    engine = create_async_engine(database_url)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
    await engine.dispose()


@pytest.fixture
async def sql(database_url: str) -> AsyncIterator[Any]:
    """Run raw SQL in tests (e.g. promote a user, tamper with the audit log)."""
    engine = create_async_engine(database_url)

    async def run(statement: str, **params: Any) -> Any:
        async with engine.begin() as conn:
            result = await conn.execute(text(statement), params)
            return result.fetchall() if result.returns_rows else result.rowcount

    yield run
    await engine.dispose()


class Actor:
    """A registered + logged-in user with convenience HTTP helpers."""

    def __init__(self, client: httpx.AsyncClient, email: str, token: str, user_id: uuid.UUID):
        self.client, self.email, self.token, self.id = client, email, token, user_id

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    async def get(self, url: str, **kw: Any) -> httpx.Response:
        return await self.client.get(url, headers=self.headers, **kw)

    async def post(self, url: str, **kw: Any) -> httpx.Response:
        return await self.client.post(url, headers=self.headers, **kw)

    async def patch(self, url: str, **kw: Any) -> httpx.Response:
        return await self.client.patch(url, headers=self.headers, **kw)


@pytest.fixture
def make_actor(client: httpx.AsyncClient) -> Any:
    async def _make(name: str = "User") -> Actor:
        email = f"{name.lower().replace(' ', '.')}.{uuid.uuid4().hex[:6]}@example.org"
        password = "correct-horse-battery"
        r = await client.post(
            "/auth/register", json={"email": email, "full_name": name, "password": password}
        )
        assert r.status_code == 201, r.text
        login = await client.post("/auth/login", json={"email": email, "password": password})
        assert login.status_code == 200, login.text
        return Actor(client, email, login.json()["access_token"], uuid.UUID(r.json()["id"]))

    return _make
