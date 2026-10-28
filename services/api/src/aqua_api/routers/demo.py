"""Explore Demo (SIH judging): read-only sessions on the synthetic seed-demo organisation without
creating an account. Off unless `DEMO_MODE_ENABLED`; the security model is in
`aqua_api.demo_mode` and docs/DEMO.md."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select

from aqua_api import demo_mode
from aqua_api.deps import SessionDep, SettingsDep
from aqua_api.ratelimit import RateLimiter, client_ip, get_rate_limiter
from aqua_api.schemas.demo import DemoSessionOut, DemoStatusOut
from aqua_api.security import create_demo_token
from aqua_api.services import audit
from aqua_core.logging import get_logger
from aqua_core.models import Membership, Organization, OrgRole, Project, User

router = APIRouter(prefix="/demo", tags=["demo"])
log = get_logger("aqua_api.demo")

STATUS_MAX_AGE_S = 60
_UNAVAILABLE = HTTPException(
    status.HTTP_503_SERVICE_UNAVAILABLE,
    {"code": "demo_unavailable", "message": "The demo data is not available right now"},
)


@router.get("/status", response_model=DemoStatusOut)
async def demo_status(settings: SettingsDep, response: Response) -> DemoStatusOut:
    """Whether Explore Demo is switched on (public, cacheable)."""
    response.headers["Cache-Control"] = f"public, max-age={STATUS_MAX_AGE_S}"
    return DemoStatusOut(enabled=settings.demo_mode_enabled, banner=demo_mode.BANNER)


@router.post(
    "/session",
    response_model=DemoSessionOut,
    responses={404: {"description": "Demo mode is off"}, 503: {"description": "No demo data"}},
)
async def create_demo_session(
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
) -> DemoSessionOut:
    """A short-lived, read-only token for the demo organisation's visitor account (no account
    creation, no refresh token). 404 when demo mode is off; 429 over the per-IP limit."""
    if not settings.demo_mode_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    await limiter.enforce_ip(request, "demo_session", settings.rate_limit_demo_session_ip)

    query = select(Organization)
    if settings.demo_org_id is not None:
        query = query.where(Organization.id == settings.demo_org_id)
    elif (settings.demo_org_slug or "").strip():
        query = query.where(Organization.slug == (settings.demo_org_slug or "").strip())
    else:
        log.error("demo.not_configured", reason="DEMO_ORG_ID / DEMO_ORG_SLUG unset")
        raise _UNAVAILABLE
    org = await session.scalar(query)
    if org is None:
        log.error("demo.org_missing", demo_org_id=str(settings.demo_org_id))
        raise _UNAVAILABLE
    if not demo_mode.has_synthetic_label(org):
        # Never hand out sessions on a real organisation, whatever the configuration says.
        log.error("demo.org_not_synthetic", org_id=str(org.id))
        raise _UNAVAILABLE
    candidates = await session.scalars(
        select(User)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.org_id == org.id, Membership.role == OrgRole.MEMBER)
        .order_by(User.created_at)
    )
    visitor = next((u for u in candidates if demo_mode.is_visitor(u, org.id)), None)
    if visitor is None:
        log.error("demo.visitor_missing", org_id=str(org.id), hint="re-run aqua-api seed-demo")
        raise _UNAVAILABLE
    project_ids = list(
        await session.scalars(
            select(Project.id).where(Project.org_id == org.id).order_by(Project.created_at)
        )
    )
    token, ttl, jti = create_demo_token(visitor.id, org.id, settings)
    ip_hash = demo_mode.hash_ip(client_ip(request, limiter.trusted), settings)
    await audit.record(
        session,
        actor_id=visitor.id,
        action="demo.session_created",
        entity_type="organization",
        entity_id=org.id,
        data={"jti": jti, "ip_hash": ip_hash, "expires_in": ttl},
    )
    await session.commit()
    log.info("demo.session_created", org_id=str(org.id), jti=jti, ip_hash=ip_hash)
    response.headers["Cache-Control"] = "no-store"
    return DemoSessionOut(
        access_token=token,
        expires_in=ttl,
        demo=True,
        org_id=org.id,
        project_ids=project_ids,
        banner=demo_mode.BANNER,
    )
