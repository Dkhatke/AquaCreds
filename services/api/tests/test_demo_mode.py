"""Explore Demo: read-only demo sessions on the synthetic demo org (see aqua_api.demo_mode).

The all-routes tests walk every route of the app, so a new endpoint is covered automatically: every
non-GET route must refuse a demo token, and every authenticated GET route that is not on the demo
allow-list must answer 403."""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
import pytest
from api_testlib import app_client, project_payload, square
from conftest import FakeStorage
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute, iter_route_contexts
from starlette.requests import Request

from aqua_api import demo_mode
from aqua_api.deps import get_current_user
from aqua_api.main import create_app
from aqua_api.security import (
    ACCESS_AUDIENCE,
    DEMO_AUDIENCE,
    create_access_token,
    create_demo_token,
    decode_access_token,
    decode_demo_token,
    looks_like_demo_token,
)
from aqua_core.settings import Settings

DEMO_ORG = uuid.UUID("00000000-0000-4000-8000-00000000de01")
DEMO_ORG_NAME = "Demo Mangrove Collective (synthetic, seed 1)"
VISITOR_EMAIL = "demo.visitor.s1@example.org"
SECRET = "t" * 48
SAFE = {"GET", "HEAD", "OPTIONS"}


@pytest.fixture
def settings_overrides() -> dict[str, Any]:
    return {"demo_mode_enabled": True, "demo_org_id": str(DEMO_ORG)}


async def _seed_world(client: httpx.AsyncClient, make_actor: Any, sql: Any) -> dict[str, Any]:
    """Demo org (synthetic label) with one project + its visitor; a real org with one project."""
    await sql(
        "INSERT INTO organizations (id, name, slug, org_type) VALUES (:id, :n, 'demo-s1', 'ngo')",
        id=DEMO_ORG,
        n=DEMO_ORG_NAME,
    )
    demo_admin = await make_actor("Demo Admin")
    await sql(
        "INSERT INTO memberships (user_id, org_id, role) VALUES (:u, :o, 'admin')",
        u=demo_admin.id,
        o=DEMO_ORG,
    )
    r = await demo_admin.post("/projects", json=project_payload(str(DEMO_ORG)))
    assert r.status_code == 201, r.text
    demo_project = r.json()["id"]

    r = await client.post(
        "/auth/register",
        json={"email": VISITOR_EMAIL, "full_name": "Demo Visitor", "password": "x" * 16},
    )
    assert r.status_code == 201, r.text
    visitor_id = r.json()["id"]
    await sql(
        "INSERT INTO memberships (user_id, org_id, role) VALUES (:u, :o, 'member')",
        u=visitor_id,
        o=DEMO_ORG,
    )

    real = await make_actor("Real Admin")
    org = (await real.post("/orgs", json={"name": "Real Mangrove NGO", "org_type": "ngo"})).json()
    r = await real.post("/projects", json=project_payload(org["id"], boundary=square(86.95, 20.75)))
    assert r.status_code == 201, r.text
    return {
        "demo_project": demo_project,
        "visitor_id": visitor_id,
        "real_org": org["id"],
        "real_project": r.json()["id"],
        "real": real,
    }


async def _session(client: httpx.AsyncClient) -> dict[str, Any]:
    r = await client.post("/demo/session")
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------- contract


async def test_status_is_public_and_cacheable(client: httpx.AsyncClient) -> None:
    r = await client.get("/demo/status")
    assert r.status_code == 200
    assert r.json() == {"enabled": True, "banner": "Demonstration data — synthetic, testnet"}
    assert r.headers["cache-control"] == "public, max-age=60"


async def test_session_contract_claims_and_audit(
    client: httpx.AsyncClient, make_actor: Any, sql: Any
) -> None:
    world = await _seed_world(client, make_actor, sql)
    r = await client.post("/demo/session")
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    body = r.json()
    assert set(body) == {"access_token", "expires_in", "demo", "org_id", "project_ids", "banner"}
    assert body["demo"] is True
    assert body["expires_in"] == 3600
    assert body["org_id"] == str(DEMO_ORG)
    assert body["project_ids"] == [world["demo_project"]]
    assert body["banner"] == demo_mode.BANNER

    claims = jwt.decode(body["access_token"], SECRET, algorithms=["HS256"], audience=DEMO_AUDIENCE)
    assert claims["demo"] is True
    assert claims["scope"] == "read"
    assert claims["sub"] == world["visitor_id"]
    assert claims["org"] == str(DEMO_ORG)
    assert claims["exp"] - claims["iat"] == 3600

    rows = await sql(
        "SELECT actor_id, entity_id, data FROM audit_log WHERE action = 'demo.session_created'"
    )
    assert len(rows) == 1
    actor, entity, data = rows[0]
    assert (str(actor), entity) == (world["visitor_id"], str(DEMO_ORG))
    assert data["jti"] == claims["jti"]
    assert len(data["ip_hash"]) == 32
    assert "127.0.0.1" not in str(data)  # only a keyed hash of the address is kept


async def test_reads_are_scoped_to_the_demo_org(
    client: httpx.AsyncClient, make_actor: Any, sql: Any
) -> None:
    world = await _seed_world(client, make_actor, sql)
    h = _auth((await _session(client))["access_token"])

    me = (await client.get("/auth/me", headers=h)).json()
    assert me["email"] == VISITOR_EMAIL
    assert me["platform_role"] == "user"
    assert [m["role"] for m in me["memberships"]] == ["member"]
    assert [o["id"] for o in (await client.get("/orgs", headers=h)).json()] == [str(DEMO_ORG)]
    assert (await client.get(f"/orgs/{DEMO_ORG}", headers=h)).status_code == 200
    projects = (await client.get("/projects", headers=h)).json()
    assert [p["id"] for p in projects] == [world["demo_project"]]
    assert (await client.get(f"/projects/{world['demo_project']}", headers=h)).status_code == 200
    plots = await client.get(f"/projects/{world['demo_project']}/plots", headers=h)
    assert plots.status_code == 200
    assert (await client.get("/notifications", headers=h)).status_code == 200

    # Anything outside the demo org: 404, exactly as for any outsider.
    for url in (
        f"/projects/{world['real_project']}",
        f"/orgs/{world['real_org']}",
        f"/projects/{world['real_project']}/periods",
        f"/projects/{world['real_project']}/plots",
        f"/projects/{world['real_project']}/chain",
        f"/projects/{world['real_project']}/stewardship",
        f"/orgs/{world['real_org']}/stewardship",
    ):
        r = await client.get(url, headers=h)
        assert r.status_code == 404, (url, r.status_code, r.text)
    filtered = await client.get("/projects", params={"org_id": world["real_org"]}, headers=h)
    assert filtered.json() == []


async def test_staff_admin_and_account_endpoints_are_forbidden(
    client: httpx.AsyncClient, make_actor: Any, sql: Any
) -> None:
    await _seed_world(client, make_actor, sql)
    h = _auth((await _session(client))["access_token"])
    for url in (
        "/audit",
        "/audit/verify",
        "/chain/status",
        f"/orgs/{DEMO_ORG}/members",
        "/auth/mfa",
        "/forward-fund/requests",
        "/notifications/preferences",
    ):
        r = await client.get(url, headers=h)
        assert r.status_code == 403, (url, r.text)
        assert r.json()["detail"]["code"] == "demo_forbidden"


async def test_writes_uploads_and_chain_actions_are_refused(
    client: httpx.AsyncClient, make_actor: Any, sql: Any
) -> None:
    world = await _seed_world(client, make_actor, sql)
    h = _auth((await _session(client))["access_token"])
    pid = world["demo_project"]
    calls = [
        ("POST", "/projects", {"json": project_payload(str(DEMO_ORG))}),
        ("PATCH", f"/projects/{pid}", {"json": {"name": "Hijacked"}}),
        (
            "POST",
            f"/projects/{pid}/documents/upload-url",
            {"json": {"doc_type": "crz_clearance", "content_type": "application/pdf"}},
        ),
        ("POST", f"/projects/{pid}/submit", {}),
        ("POST", f"/projects/{pid}/chain/register", {}),
        ("POST", f"/projects/{pid}/periods", {"json": {}}),
        ("POST", "/auth/logout", {"json": {"refresh_token": "x"}}),
        ("POST", "/auth/mfa/setup", {}),
        ("PUT", "/notifications/preferences", {"json": {}}),
        ("POST", "/demo/session", {}),
        ("POST", "/public/verify-hash", {"json": {}}),
        ("DELETE", f"/projects/{pid}/forward-fund/offers/1", {}),
    ]
    for method, url, kw in calls:
        r = await client.request(method, url, headers=h, **kw)
        assert r.status_code == 403, (method, url, r.text)
        assert r.json()["detail"]["code"] == "demo_read_only"
    rows = await sql("SELECT name FROM projects WHERE id = :id", id=pid)
    assert rows[0][0] != "Hijacked"
    assert await sql("SELECT count(*) FROM projects") == [(2,)]


async def test_demo_tokens_never_pass_as_ordinary_tokens(
    client: httpx.AsyncClient, make_actor: Any, sql: Any
) -> None:
    world = await _seed_world(client, make_actor, sql)
    token = (await _session(client))["access_token"]
    settings = Settings(_env_file=None, jwt_secret=SECRET)
    with pytest.raises(jwt.InvalidAudienceError):
        decode_access_token(token, settings)
    assert decode_demo_token(token, settings)["scope"] == "read"
    visitor = uuid.UUID(world["visitor_id"])

    # A normal-audience token that merely claims demo=true is treated as a demo token: refused.
    now = datetime.now(UTC)
    forged = jwt.encode(
        {
            "sub": str(visitor),
            "aud": ACCESS_AUDIENCE,
            "exp": now + timedelta(minutes=5),
            "demo": True,
            "scope": "read",
            "org": str(DEMO_ORG),
        },
        SECRET,
        algorithm="HS256",
    )
    assert (await client.get("/auth/me", headers=_auth(forged))).status_code == 401
    # Wrong scope, wrong key, expired: all 401.
    for claims, key in (
        ({"scope": "write"}, SECRET),
        ({}, "k" * 48),
        ({"exp": now - timedelta(minutes=1)}, SECRET),
    ):
        payload = {
            "sub": str(visitor),
            "aud": DEMO_AUDIENCE,
            "exp": now + timedelta(minutes=5),
            "demo": True,
            "scope": "read",
            "org": str(DEMO_ORG),
            **claims,
        }
        bad = jwt.encode(payload, key, algorithm="HS256")
        assert (await client.get("/auth/me", headers=_auth(bad))).status_code == 401
    # A demo token for an ordinary user (not the visitor) is refused.
    other, _, _ = create_demo_token(world["real"].id, DEMO_ORG, settings)
    assert (await client.get("/auth/me", headers=_auth(other))).status_code == 401
    # Ordinary tokens are unaffected by demo mode.
    assert looks_like_demo_token(create_access_token(visitor, settings)[0]) is False
    assert looks_like_demo_token("not-a-jwt") is False


async def test_visitor_and_org_are_rechecked_on_every_request(
    client: httpx.AsyncClient, make_actor: Any, sql: Any
) -> None:
    world = await _seed_world(client, make_actor, sql)
    h = _auth((await _session(client))["access_token"])
    assert (await client.get("/auth/me", headers=h)).status_code == 200

    # The visitor gains a second membership: no longer a pure demo visitor.
    await sql(
        "INSERT INTO memberships (user_id, org_id, role) VALUES (:u, :o, 'member')",
        u=world["visitor_id"],
        o=world["real_org"],
    )
    assert (await client.get("/auth/me", headers=h)).status_code == 401
    await sql("DELETE FROM memberships WHERE org_id = :o AND user_id = :u",
              o=world["real_org"], u=world["visitor_id"])  # fmt: skip
    assert (await client.get("/auth/me", headers=h)).status_code == 200

    # The demo org loses its synthetic label: existing tokens die, no new sessions.
    await sql("UPDATE organizations SET name = 'Real Collective' WHERE id = :o", o=DEMO_ORG)
    assert (await client.get("/auth/me", headers=h)).status_code == 401
    r = await client.post("/demo/session")
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "demo_unavailable"


async def test_unavailable_without_visitor_or_org(
    client: httpx.AsyncClient, make_actor: Any, sql: Any, database_url: str, storage: FakeStorage
) -> None:
    r = await client.post("/demo/session")  # org not seeded
    assert r.status_code == 503
    world = await _seed_world(client, make_actor, sql)
    await sql("UPDATE users SET is_active = false WHERE id = :u", u=world["visitor_id"])
    assert (await client.post("/demo/session")).status_code == 503
    await sql("UPDATE users SET is_active = true WHERE id = :u", u=world["visitor_id"])
    # by slug instead of id
    async with app_client(
        database_url, storage, demo_mode_enabled=True, demo_org_slug="demo-s1"
    ) as other:
        assert (await other.post("/demo/session")).status_code == 200
    async with app_client(database_url, storage, demo_mode_enabled=True) as unset:
        assert (await unset.post("/demo/session")).status_code == 503


async def test_disabled_demo_mode_hides_everything(
    client: httpx.AsyncClient, make_actor: Any, sql: Any, database_url: str, storage: FakeStorage
) -> None:
    await _seed_world(client, make_actor, sql)
    token = (await _session(client))["access_token"]
    async with app_client(database_url, storage, demo_org_id=str(DEMO_ORG)) as off:
        assert (await off.post("/demo/session")).status_code == 404
        status = await off.get("/demo/status")
        assert status.json() == {"enabled": False, "banner": demo_mode.BANNER}
        # Tokens issued while it was on stop working once it is off.
        assert (await off.get("/auth/me", headers=_auth(token))).status_code == 401
        # ...and still can't write anything.
        r = await off.post("/projects", headers=_auth(token), json={})
        assert r.status_code == 403


async def test_session_creation_is_rate_limited_per_ip(
    client: httpx.AsyncClient, make_actor: Any, sql: Any, database_url: str, storage: FakeStorage
) -> None:
    await _seed_world(client, make_actor, sql)
    async with app_client(
        database_url,
        storage,
        demo_mode_enabled=True,
        demo_org_id=str(DEMO_ORG),
        rate_limit_enabled=True,
        rate_limit_demo_session_ip="2/hour",
        demo_session_ttl_min=5,
    ) as limited:
        first = await limited.post("/demo/session")
        assert first.json()["expires_in"] == 300
        assert (await limited.post("/demo/session")).status_code == 200
        r = await limited.post("/demo/session")
        assert r.status_code == 429
        assert int(r.headers["retry-after"]) >= 1


# ---------------------------------------------------------------- every route, automatically


def _uses_current_user(dependant: Dependant) -> bool:
    return any(d.call is get_current_user or _uses_current_user(d) for d in dependant.dependencies)


def _api_routes() -> Iterator[tuple[str, APIRoute]]:
    app = create_app(Settings(_env_file=None, jwt_secret=SECRET))
    for ctx in iter_route_contexts(app.routes):
        route = ctx.original_route
        if isinstance(route, APIRoute):
            yield str(ctx.path), route


def _fill(path: str, route: APIRoute) -> str:
    """Concrete URL for a route template: valid values for each path parameter's type."""
    for field in route.dependant.path_params:
        annotation = field.field_info.annotation
        value = (
            str(uuid.uuid4()) if annotation is uuid.UUID else "1" if annotation is int else "png"
        )
        path = path.replace("{" + field.name + "}", value)
    return path


def test_route_walk_finds_the_whole_api() -> None:
    routes = list(_api_routes())
    paths = {p for p, _ in routes}
    assert len(routes) > 80
    assert {"/api/v1/projects", "/api/v1/demo/session", "/api/v1/public/vintages"} <= paths


def test_allow_list_names_real_get_endpoints() -> None:
    by_name = {demo_mode.endpoint_name(r.endpoint): r for _, r in _api_routes()}
    for name in demo_mode.DEMO_READABLE_ENDPOINTS:
        assert name in by_name, f"stale demo allow-list entry {name}"
        route = by_name[name]
        assert route.methods <= SAFE, name
        assert _uses_current_user(route.dependant), name


async def test_every_non_get_route_refuses_demo_tokens(
    client: httpx.AsyncClient, make_actor: Any, sql: Any
) -> None:
    await _seed_world(client, make_actor, sql)
    h = _auth((await _session(client))["access_token"])
    checked = 0
    for path, route in _api_routes():
        for method in sorted(route.methods - SAFE):
            url = _fill(path, route).removeprefix("/api/v1")
            r = await client.request(method, url, headers=h, json={})
            assert r.status_code == 403, (method, path, r.status_code, r.text)
            assert r.json()["detail"]["code"] == "demo_read_only", (method, path)
            checked += 1
    assert checked > 30


async def test_every_authenticated_get_outside_the_allow_list_is_forbidden(
    client: httpx.AsyncClient, make_actor: Any, sql: Any
) -> None:
    await _seed_world(client, make_actor, sql)
    h = _auth((await _session(client))["access_token"])
    checked = 0
    for path, route in _api_routes():
        if not route.methods & {"GET"} or not _uses_current_user(route.dependant):
            continue
        if demo_mode.endpoint_name(route.endpoint) in demo_mode.DEMO_READABLE_ENDPOINTS:
            continue
        r = await client.get(_fill(path, route).removeprefix("/api/v1"), headers=h)
        assert r.status_code == 403, (path, r.status_code, r.text)
        assert r.json()["detail"]["code"] == "demo_forbidden", path
        checked += 1
    assert checked >= 5


async def test_dependency_refuses_writes_even_without_the_middleware(
    client: httpx.AsyncClient, make_actor: Any, sql: Any
) -> None:
    """Defence in depth: `authenticate_demo` itself refuses non-GET requests."""
    await _seed_world(client, make_actor, sql)
    token = (await _session(client))["access_token"]
    settings = Settings(
        _env_file=None, jwt_secret=SECRET, demo_mode_enabled=True, demo_org_id=DEMO_ORG
    )
    request = Request({"type": "http", "method": "POST", "headers": [], "path": "/x"})
    with pytest.raises(Exception) as caught:
        await demo_mode.authenticate_demo(request, token, None, settings)  # type: ignore[arg-type]
    assert getattr(caught.value, "status_code", None) == 403
    assert getattr(caught.value, "detail", {})["code"] == "demo_read_only"
