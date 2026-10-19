"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from aqua_api import __version__
from aqua_api.routers import health, system
from aqua_core.db import create_engine
from aqua_core.logging import configure_logging, get_logger
from aqua_core.settings import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.assert_production_safe()
    configure_logging(settings.log_level, json=settings.log_json)
    log = get_logger("aqua_api")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.engine = create_engine(settings.database_url)
        log.info("api.startup", env=settings.app_env, version=__version__)
        try:
            yield
        finally:
            await app.state.engine.dispose()
            log.info("api.shutdown")

    app = FastAPI(
        title="AquaCreds API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if settings.app_env != "production" else None,
    )
    app.state.settings = settings

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type"],
        )

    app.include_router(health.router)
    app.include_router(system.router, prefix="/api/v1")
    return app


app = create_app()
