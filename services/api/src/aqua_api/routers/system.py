"""System diagnostics: verifies the API → queue → worker round trip."""

from typing import Any

from celery.exceptions import TimeoutError as CeleryTimeout
from fastapi import APIRouter, HTTPException, status
from starlette.concurrency import run_in_threadpool

from aqua_api.queue import celery_client

router = APIRouter(prefix="/system", tags=["system"])

PING_TIMEOUT_S = 10.0


@router.post("/ping-worker")
async def ping_worker() -> dict[str, Any]:
    result = celery_client().send_task("aqua.system.ping", queue="default")
    try:
        payload: dict[str, Any] = await run_in_threadpool(result.get, timeout=PING_TIMEOUT_S)
    except CeleryTimeout as exc:
        raise HTTPException(
            status.HTTP_504_GATEWAY_TIMEOUT, "No worker answered within 10 s"
        ) from exc
    return {"task_id": result.id, "result": payload}
