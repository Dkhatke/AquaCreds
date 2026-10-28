"""Explore Demo: read-only sessions on the seed-demo organisation, without an account.

Security model (see docs/DEMO.md, "Explore Demo"):

* Off by default (`DEMO_MODE_ENABLED`). Sessions are only issued for the organisation named by
  `DEMO_ORG_ID` / `DEMO_ORG_SLUG`, and only when its name carries the seed-demo synthetic label.
* A session is a short-lived JWT (audience `aquacreds-demo`, claims `demo: true`, `scope: read`,
  `org`) for that organisation's **visitor** account: a plain user (platform role `user`) whose only
  membership is `member` of the demo org, with a random password nobody knows. No refresh token.
  Because the token belongs to a real, minimally privileged account, every existing access check
  (org membership, staff roles) scopes it exactly like any outsider: GETs outside the demo org
  answer 404, staff endpoints 403.
* Two central gates on top of that, so a new route is safe without anyone remembering demo mode:
  1. `DemoReadOnlyMiddleware` answers 403 `demo_read_only` to every request that is not GET /
     HEAD / OPTIONS and carries a token that *claims* to be a demo token (checked before routing,
     so it covers public and authenticated routes alike, and even when demo mode is off).
  2. `authenticate_demo` (called by `deps.get_current_user`) only lets a demo token reach the
     endpoints in `DEMO_READABLE_ENDPOINTS`, an explicit allow-list of org-scoped reads;
     everything else (staff and admin endpoints, cross-org lists, account settings) is 403
     `demo_forbidden`. New endpoints are therefore denied until reviewed and listed.
* Uploads are POSTs (presigned PUT URLs), so they are refused; presigned *downloads* of demo-org
  artifacts (report files, classification rasters) come from allow-listed GETs.
* Nothing in a demo session can sign a chain transaction: every relayed action is a POST.
* Session creation is rate-limited per IP and audit-logged with a keyed hash of the IP.
"""

import hashlib
import hmac
import uuid
from typing import Any

import jwt
from fastapi import HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from aqua_api.security import decode_demo_token, looks_like_demo_token
from aqua_core.logging import get_logger
from aqua_core.models import Organization, OrgRole, PlatformRole, User
from aqua_core.settings import DEMO_SYNTHETIC_LABEL, Settings

log = get_logger("aqua_api.demo_mode")

BANNER = "Demonstration data — synthetic, testnet"
VISITOR_EMAIL_PREFIX = "demo.visitor."
VISITOR_EMAIL_DOMAIN = "@example.org"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

READ_ONLY = {
    "code": "demo_read_only",
    "message": "Demo sessions are read-only: sign in with a real account to make changes",
}
FORBIDDEN = {
    "code": "demo_forbidden",
    "message": "This endpoint is not available in a demo session",
}

# Org-scoped reads a demo session may call (`module.function` of the endpoint). Every one of them
# checks organisation membership (or reads only the caller's own rows), so the visitor sees the
# demo org and nothing else. Keep it minimal; test_demo_mode checks each entry is a GET route.
DEMO_READABLE_ENDPOINTS = frozenset(
    {
        "aqua_api.routers.auth.me",
        "aqua_api.routers.orgs.list_my_orgs",
        "aqua_api.routers.orgs.get_org",
        "aqua_api.routers.projects.list_projects",
        "aqua_api.routers.projects.get_project",
        "aqua_api.routers.field.list_periods",
        "aqua_api.routers.field.list_plots",
        "aqua_api.routers.field.list_submissions",
        "aqua_api.routers.field.get_submission",
        "aqua_api.routers.analysis.list_runs",
        "aqua_api.routers.analysis.get_run",
        "aqua_api.routers.analysis.timeseries",
        "aqua_api.routers.analysis.list_alerts",
        "aqua_api.routers.classifications.list_classifications",
        "aqua_api.routers.classifications.get_classification",
        "aqua_api.routers.reports.list_reports",
        "aqua_api.routers.reports.get_report",
        "aqua_api.routers.reports.verify_report",
        "aqua_api.routers.stewardship.get_org_stewardship",
        "aqua_api.routers.stewardship.get_project_stewardship",
        "aqua_api.routers.chain.project_chain",
        "aqua_api.routers.chain.get_transaction",
        "aqua_api.routers.forward_fund.project_forward_fund",
        "aqua_api.routers.notifications.list_notifications",
        "aqua_api.routers.notifications.unread_count",
        # Map tiles: tilejson issues signed tile URLs bound to the (visible) project.
        "aqua_api.routers.tiles.project_tilejson",
        "aqua_api.routers.tiles.classification_legend",
    }
)


def endpoint_name(endpoint: Any) -> str:
    return f"{getattr(endpoint, '__module__', '?')}.{getattr(endpoint, '__qualname__', '?')}"


def _request_endpoint(request: Request) -> Any:
    route = request.scope.get("route")
    endpoint = getattr(route, "endpoint", None)
    return endpoint if endpoint is not None else request.scope.get("endpoint")


def has_synthetic_label(org: Organization) -> bool:
    return DEMO_SYNTHETIC_LABEL in org.name


def is_configured_demo_org(org: Organization, settings: Settings) -> bool:
    if settings.demo_org_id is not None:
        return org.id == settings.demo_org_id
    slug = (settings.demo_org_slug or "").strip()
    return bool(slug) and org.slug == slug


def is_visitor(user: User, org_id: uuid.UUID) -> bool:
    """The demo org's read-only visitor: an active plain user whose only membership is `member`
    of the demo org, with a seed-demo visitor address."""
    email = user.email.lower()
    return (
        user.is_active
        and user.platform_role == PlatformRole.USER
        and email.startswith(VISITOR_EMAIL_PREFIX)
        and email.endswith(VISITOR_EMAIL_DOMAIN)
        and len(user.memberships) == 1
        and user.memberships[0].org_id == org_id
        and user.memberships[0].role == OrgRole.MEMBER
    )


def hash_ip(ip: str, settings: Settings) -> str:
    """Keyed hash of a client IP for the audit log (not reversible by enumerating addresses)."""
    key = hashlib.sha256(b"aquacreds-demo-ip:" + settings.jwt_secret.get_secret_value().encode())
    return hmac.new(key.digest(), ip.encode(), hashlib.sha256).hexdigest()[:32]


_UNAUTHORIZED = HTTPException(
    status.HTTP_401_UNAUTHORIZED,
    "Invalid or expired demo session",
    headers={"WWW-Authenticate": "Bearer"},
)


async def authenticate_demo(
    request: Request, token: str, session: AsyncSession, settings: Settings
) -> User:
    """Resolve a demo token to the visitor account, or refuse. Called for every authenticated
    request that carries a demo token."""
    if not settings.demo_mode_enabled:
        raise _UNAUTHORIZED
    try:
        claims = decode_demo_token(token, settings)
        user_id = uuid.UUID(str(claims["sub"]))
        org_id = uuid.UUID(str(claims["org"]))
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise _UNAUTHORIZED from exc
    if request.method not in SAFE_METHODS:  # the middleware already answered; defence in depth
        raise HTTPException(status.HTTP_403_FORBIDDEN, READ_ONLY)
    name = endpoint_name(_request_endpoint(request))
    if name not in DEMO_READABLE_ENDPOINTS:
        log.info("demo.endpoint_refused", endpoint=name)
        raise HTTPException(status.HTTP_403_FORBIDDEN, FORBIDDEN)
    user = await session.get(User, user_id)
    if user is None or not is_visitor(user, org_id):
        raise _UNAUTHORIZED
    org = user.memberships[0].organization
    if not (is_configured_demo_org(org, settings) and has_synthetic_label(org)):
        raise _UNAUTHORIZED
    request.state.demo_session = True
    return user


def _bearer(headers: list[tuple[bytes, bytes]]) -> str | None:
    for name, value in headers:
        if name.lower() == b"authorization":
            scheme, _, credentials = value.decode("latin-1").partition(" ")
            if scheme.lower() == "bearer" and credentials.strip():
                return credentials.strip()
    return None


class DemoReadOnlyMiddleware:
    """403 `demo_read_only` for any non-GET/HEAD/OPTIONS request that carries a demo token, before
    routing (so no endpoint, public or not, ever runs a write for a demo session)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("method", "GET") not in SAFE_METHODS:
            token = _bearer(scope.get("headers", []))
            if token is not None and looks_like_demo_token(token):
                log.info("demo.write_refused", method=scope.get("method"), path=scope.get("path"))
                response = JSONResponse({"detail": READ_ONLY}, status.HTTP_403_FORBIDDEN)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
