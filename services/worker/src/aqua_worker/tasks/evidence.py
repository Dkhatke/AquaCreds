"""Celery task: run evidence checks on a field submission (queue `ml`)."""

import asyncio
import uuid
from typing import Any

from celery import shared_task
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aqua_core import audit
from aqua_core.db import create_engine, session_factory
from aqua_core.geometry import to_point
from aqua_core.models import CaptureNonce, FieldSubmission, SubmissionStatus
from aqua_core.models._types import utcnow
from aqua_core.settings import get_settings
from aqua_core.storage import ObjectStorage, S3ObjectStorage
from aqua_worker import evidence as ev


async def _geofence_distance_m(session: AsyncSession, sub: FieldSubmission) -> float:
    """0 if the point is inside the project boundary, else the geodesic distance to it (m)."""
    result = await session.scalar(
        text(
            """
            SELECT CASE WHEN ST_Covers(p.boundary, s.location) THEN 0.0
                        ELSE ST_Distance(p.boundary::geography, s.location::geography) END
            FROM field_submissions s JOIN projects p ON p.id = s.project_id
            WHERE s.id = :id
            """
        ),
        {"id": sub.id},
    )
    return float(result)


async def _duplicates(
    session: AsyncSession, sub: FieldSubmission, phash: str
) -> tuple[int, int | None]:
    exact = await session.scalar(
        select(func.count())
        .select_from(FieldSubmission)
        .where(FieldSubmission.sha256 == sub.sha256, FieldSubmission.id != sub.id)
    )
    nearest = await session.scalar(
        text(
            """
            SELECT min(bit_count(('x' || phash)::bit(64) # ('x' || :ph)::bit(64)))
            FROM field_submissions WHERE phash IS NOT NULL AND id <> :id
            """
        ),
        {"ph": phash, "id": sub.id},
    )
    return int(exact or 0), (int(nearest) if nearest is not None else None)


async def run_checks(
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: ObjectStorage,
    submission_id: uuid.UUID,
    max_bytes: int,
) -> dict[str, Any]:
    async with sessionmaker() as session:
        sub = await session.get(FieldSubmission, submission_id, with_for_update=True)
        if sub is None:
            raise LookupError(f"submission {submission_id} not found")

        data = await asyncio.to_thread(storage.get_bytes, sub.object_key, max_bytes)
        exif = ev.read_exif(data)
        phash = ev.perceptual_hash(data)
        point = to_point(sub.location)

        checks: list[ev.Check] = []
        checks += ev.check_exif(exif, point.y, point.x, sub.gps_accuracy_m, sub.captured_at)

        nonce = await session.get(CaptureNonce, sub.nonce_id) if sub.nonce_id else None
        checks.append(
            ev.check_nonce(
                None
                if nonce is None
                else {
                    "user_id": str(nonce.user_id),
                    "period_id": str(nonce.period_id),
                    "issued_at": nonce.issued_at,
                    "expires_at": nonce.expires_at,
                    "used_by": str(sub.id),  # consumed by this submission at upload time
                },
                user_id=str(sub.submitted_by),
                period_id=str(sub.period_id),
                captured_at=sub.captured_at,
                submission_id=str(sub.id),
            )
        )
        checks.append(
            ev.check_geofence(await _geofence_distance_m(session, sub), sub.gps_accuracy_m)
        )
        checks += ev.check_duplicates(*await _duplicates(session, sub, phash))
        checks.append(
            ev.not_yet_available("photo_model", "Photo verifier model not deployed yet (Phase 1f)")
        )
        checks.append(
            ev.not_yet_available("satellite_consistency", "Satellite consistency runs in Phase 4")
        )

        trust, rejected = ev.score(checks)
        sub.exif = {k: v for k, v in exif.items() if isinstance(v, (str, int, float))}
        sub.phash = phash
        sub.checks = [c.to_dict() for c in checks]
        sub.trust_score = trust
        sub.status = SubmissionStatus.REJECTED if rejected else SubmissionStatus.CHECKED
        sub.checked_at = utcnow()
        await audit.record(
            session,
            actor_id=None,
            action="submission.checked",
            entity_type="field_submission",
            entity_id=sub.id,
            data={"trust_score": trust, "status": sub.status.value, "sha256": sub.sha256},
        )
        await session.commit()
        return {"id": str(sub.id), "status": sub.status.value, "trust_score": trust}


async def _run(submission_id: str) -> dict[str, Any]:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    try:
        return await run_checks(
            session_factory(engine),
            S3ObjectStorage(settings),
            uuid.UUID(submission_id),
            settings.upload_max_bytes,
        )
    finally:
        await engine.dispose()


@shared_task(
    name="aqua.ml.check_submission",
    autoretry_for=(ConnectionError, TimeoutError, OSError),
    retry_backoff=True,
    max_retries=5,
)
def check_submission(submission_id: str) -> dict[str, Any]:
    return asyncio.run(_run(submission_id))
