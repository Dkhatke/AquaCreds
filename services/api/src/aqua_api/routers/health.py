"""Liveness (`/healthz`) and readiness (`/readyz`) probes."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

import redis.asyncio as aioredis
from fastapi import APIRouter, Request, Response, status
from starlette.concurrency import run_in_threadpool

from aqua_api import __version__
from aqua_core.db import check_database
from aqua_core.settings import Settings
from aqua_core.storage import check_storage

router = APIRouter(tags=["health"])

CHECK_TIMEOUT_S = 3.0


async def check_db(request: Request) -> str:
    return await check_database(request.app.state.engine)


async def check_redis(request: Request) -> str:
    settings: Settings = request.app.state.settings
    client = aioredis.from_url(settings.redis_url)
    try:
        await client.ping()
        return "pong"
    finally:
        await client.aclose()


async def check_s3(request: Request) -> str:
    settings: Settings = request.app.state.settings
    await run_in_threadpool(check_storage, settings)
    return settings.s3_bucket


CHECKS: dict[str, Callable[[Request], Awaitable[str]]] = {
    "database": check_db,
    "redis": check_redis,
    "storage": check_s3,
}


async def _run(name: str, request: Request) -> tuple[str, dict[str, Any]]:
    started = time.perf_counter()
    try:
        detail = await asyncio.wait_for(CHECKS[name](request), timeout=CHECK_TIMEOUT_S)
        result: dict[str, Any] = {"ok": True, "detail": detail}
    except Exception as exc:
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]}
    result["ms"] = round((time.perf_counter() - started) * 1000, 1)
    return name, result


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "aqua-api", "version": __version__}


@router.get("/readyz")
async def readyz(request: Request, response: Response) -> dict[str, Any]:
    results = dict(await asyncio.gather(*(_run(name, request) for name in CHECKS)))
    ready = all(r["ok"] for r in results.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ready" if ready else "not_ready", "checks": results}
