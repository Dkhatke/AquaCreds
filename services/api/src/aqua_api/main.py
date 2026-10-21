"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from aqua_api import __version__
from aqua_api.middleware import SecurityHeadersMiddleware
from aqua_api.queue import enqueue
from aqua_api.ratelimit import RateLimiter, RedisRateLimitBackend
from aqua_api.routers import (
    analysis,
    audit,
    auth,
    chain,
    classifications,
    field,
    forward_fund,
    health,
    marketplace,
    orgs,
    projects,
    public,
    reports,
    stewardship,
    system,
)
from aqua_core.db import create_engine, session_factory
from aqua_core.logging import configure_logging, get_logger
from aqua_core.settings import Settings, get_settings
from aqua_core.storage import S3ObjectStorage


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.assert_production_safe()
    configure_logging(settings.log_level, json=settings.log_json)
    log = get_logger("aqua_api")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        log.info("api.startup", env=settings.app_env, version=__version__)
        try:
            yield
        finally:
            await app.state.rate_limiter.backend.close()
            await app.state.engine.dispose()
            log.info("api.shutdown")

    app = FastAPI(
        title="AquaCreds API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if settings.app_env != "production" else None,
    )
    # Engine creation does not connect; connections are opened lazily on first use.
    app.state.settings = settings
    app.state.engine = create_engine(settings.database_url)
    app.state.sessionmaker = session_factory(app.state.engine)
    app.state.storage = S3ObjectStorage(settings)
    app.state.enqueue = enqueue  # replaced by a recorder in tests
    app.state.rate_limiter = RateLimiter(settings, RedisRateLimitBackend(settings.redis_url))

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type"],
        )

    # Added last = outermost: headers are applied to every response, including CORS preflights.
    app.add_middleware(
        SecurityHeadersMiddleware,
        csp_exempt_paths=(
            app.docs_url or "",
            app.redoc_url or "",
            app.swagger_ui_oauth2_redirect_url or "",
        ),
        no_store_prefix="/api/v1/auth",
    )

    app.include_router(health.router)
    for module in (
        auth,
        orgs,
        projects,
        field,
        analysis,
        classifications,
        reports,
        stewardship,
        audit,
        chain,
        forward_fund,
        system,
        public,
        marketplace,
    ):
        app.include_router(module.router, prefix="/api/v1")
    return app


app = create_app()
