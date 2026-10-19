"""Shared helpers for API integration tests."""

from typing import Any


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
