"""`Idempotency-Key` for create endpoints the field app retries after a lost response.

Contract (submission create, plot-measurement create):

* The header is optional; when present it must be a UUID (422 otherwise).
* The first *successful* response is stored for (user, key) together with a SHA-256 of the method,
  path and canonical JSON body. A retry with the same key within `IDEMPOTENCY_TTL_H` (24 h) gets
  that stored response back (same status code and body, header `Idempotent-Replayed: true`)
  instead of `409 Capture nonce already used` / `409 already measured`. Nothing is re-done: no
  second row, no second worker task.
* Submissions ignore `object_key` in that comparison: an app that lost the response may upload
  the photo again (new presigned key) before retrying, and the photo is identified by `sha256`
  anyway. The replay then returns the original submission; the second upload stays unused.
* The same key with a different request (another path or body) is refused with 422
  `idempotency_key_reused`; error responses are never stored, so a failed request can be retried
  with the same key.
* Concurrent requests with one key are serialised by a transaction-level advisory lock, so the
  second waits for the first and then replays it.
* Keys are scoped to the caller: another user's key never matches. After the TTL a key is
  forgotten (the row is replaced) and the request runs normally again.
"""

import hashlib
import json
import uuid
from datetime import timedelta
from typing import Annotated, Any

from fastapi import Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from aqua_core.models import IdempotencyRecord
from aqua_core.models._types import utcnow

IdempotencyKey = Annotated[
    uuid.UUID | None,
    Header(
        alias="Idempotency-Key",
        description="UUID chosen by the client per logical request; a retry within 24 h with the "
        "same key and body returns the original response.",
    ),
]
REPLAYED_HEADER = "Idempotent-Replayed"
KEY_REUSED = {
    "code": "idempotency_key_reused",
    "message": "This Idempotency-Key was already used for a different request",
}


def request_fingerprint(
    request: Request, body: BaseModel, *, ignore: frozenset[str] = frozenset()
) -> str:
    """SHA-256 over method, path and the canonical JSON body (minus `ignore`d fields)."""
    canonical = json.dumps(
        body.model_dump(mode="json", exclude=set(ignore)), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(f"{request.method} {request.url.path}\n{canonical}".encode()).hexdigest()


async def replay(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    key: uuid.UUID,
    fingerprint: str,
    ttl_h: int,
) -> JSONResponse | None:
    """Lock the key for this transaction; the stored response if it is a replay, else None."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"idempotency:{user_id}:{key}"},
    )
    row = await session.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.user_id == user_id,
            IdempotencyRecord.key == key,
            IdempotencyRecord.created_at > func.now() - timedelta(hours=ttl_h),
        )
    )
    if row is None:
        return None
    if row.request_sha256 != fingerprint:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, KEY_REUSED)
    return JSONResponse(row.response, row.status_code, headers={REPLAYED_HEADER: "true"})


async def remember(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    key: uuid.UUID,
    scope: str,
    fingerprint: str,
    status_code: int,
    response: BaseModel,
    ttl_h: int,
) -> None:
    """Store the response in the same transaction as the created row (commit both or neither).
    Expired rows of this user are purged on the way."""
    await session.execute(
        delete(IdempotencyRecord).where(
            IdempotencyRecord.user_id == user_id,
            (IdempotencyRecord.key == key)
            | (IdempotencyRecord.created_at <= func.now() - timedelta(hours=ttl_h)),
        )
    )
    body: dict[str, Any] = response.model_dump(mode="json")
    session.add(
        IdempotencyRecord(
            user_id=user_id,
            key=key,
            scope=scope,
            request_sha256=fingerprint,
            status_code=status_code,
            response=body,
            created_at=utcnow(),
        )
    )
