"""Shared helpers for API integration tests."""

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from typing import Any

import httpx

from aqua_api.main import create_app
from aqua_api.ratelimit import MemoryRateLimitBackend
from aqua_core.settings import Settings


@asynccontextmanager
async def app_client(
    database_url: str, storage: Any, **overrides: Any
) -> AsyncIterator[httpx.AsyncClient]:
    """An extra app on the test database with other settings (the `client` fixture truncates)."""
    options: dict[str, Any] = {
        "app_env": "test",
        "database_url": database_url,
        "jwt_secret": "t" * 48,
        "rate_limit_enabled": False,
        **overrides,
    }
    app = create_app(Settings(_env_file=None, **options))
    app.state.rate_limiter.backend = MemoryRateLimitBackend()
    app.state.storage = storage
    app.state.enqueue = lambda *a, **k: None
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test/api/v1"
        ) as c:
            yield c
    finally:
        await app.state.engine.dispose()


def square(lon: float, lat: float, size_deg: float = 0.02) -> dict[str, Any]:
    """A GeoJSON square polygon (~2.2 km per 0.02 deg side near 20°N → ~470 ha)."""
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [lon, lat],
                [lon + size_deg, lat],
                [lon + size_deg, lat + size_deg],
                [lon, lat + size_deg],
                [lon, lat],
            ]
        ],
    }


BHITARKANIKA = (86.85, 20.70)


def project_payload(org_id: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "org_id": org_id,
        "name": "Bhitarkanika Fringe Restoration",
        "state": "Odisha",
        "tenure_type": "forest_land",
        "boundary": square(*BHITARKANIKA),
        "start_date": "2026-06-01",
        "planned_mangrove_share_pct": 95,
        "soil_disturbance_pct": 3,
    }
    payload.update(overrides)
    return payload


async def insert_sar_observation(
    sql: Any,
    project_id: Any,
    day: str | date,
    *,
    vh_db: float = -15.0,
    rvi: float = 0.6,
    valid_fraction: float = 1.0,
    relative_orbit: int = 85,
    key: str | None = None,
) -> str:
    """One `sar_observations` row with VH/VV dB, ratio and RVI statistics around the given values."""
    day = date.fromisoformat(day) if isinstance(day, str) else day
    key = key or f"S1A_{day:%Y%m%d}_120000"

    def summary(v: float) -> dict[str, float]:
        return {"valid_pixels": 360, "mean": v, "p10": v - 1.0, "p50": v, "p90": v + 1.0}

    stats = {
        "vh_db": summary(vh_db),
        "vv_db": summary(vh_db + 7.0),
        "vh_vv_db": summary(-7.0),
        "vh_vv_ratio": summary(0.2),
        "rvi": summary(rvi),
    }
    await sql(
        "INSERT INTO sar_observations (id, project_id, acquisition_key, item_ids, acquired_at, "
        "acquired_on, platform, orbit_state, relative_orbit, trigger, provider, polygon_pixels, "
        "valid_pixels, valid_fraction, stats) VALUES (:id, :p, :k, ARRAY[:item], :at, :d, 'S1A', "
        "'ascending', :orbit, 'monsoon', 'fake-sar', 360, :valid_px, :vf, CAST(:s AS jsonb))",
        id=uuid.uuid4(),
        p=project_id,
        k=key,
        item=f"{key}_rtc",
        at=datetime(day.year, day.month, day.day, 12, tzinfo=UTC),
        d=day,
        orbit=relative_orbit,
        valid_px=round(360 * valid_fraction),
        vf=valid_fraction,
        s=json.dumps(stats),
    )
    return key
