"""Project boundary validation, overlap (double-counting) detection and submission rules."""

import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

from geoalchemy2.elements import WKBElement
from geoalchemy2.shape import from_shape, to_shape
from shapely import force_2d
from shapely.geometry import MultiPolygon, Polygon, mapping, shape
from shapely.validation import explain_validity
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from aqua_core.models import (
    Ecosystem,
    Methodology,
    Project,
    ProjectStatus,
)
from aqua_core.models.enums import ALWAYS_REQUIRED_DOCUMENTS, REQUIRED_TENURE_DOCUMENTS
from aqua_core.settings import Settings

# Rough envelope of India's coast incl. Andaman & Nicobar and Lakshadweep (lon_min, lat_min,
# lon_max, lat_max). Phase 1 replaces this with a proper coastline-buffer AOI check.
INDIA_COASTAL_BBOX = (68.0, 6.0, 97.5, 24.8)

CCTS_EARLIEST_START = date(2025, 1, 1)
CCTS_MIN_MANGROVE_SHARE_PCT = 90.0
CCTS_MAX_SOIL_DISTURBANCE_PCT = 10.0

EDITABLE_STATUSES = {ProjectStatus.DRAFT, ProjectStatus.CHANGES_REQUESTED}


class BoundaryError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Overlap:
    project_id: uuid.UUID
    name: str
    overlap_ha: float


@dataclass(frozen=True)
class Issue:
    code: str
    message: str


def parse_boundary(geojson: dict[str, Any]) -> MultiPolygon:
    """Validate a GeoJSON Polygon/MultiPolygon and normalise it to a 2D MultiPolygon."""
    geom = force_2d(shape(geojson))
    if isinstance(geom, Polygon):
        geom = MultiPolygon([geom])
    if not isinstance(geom, MultiPolygon) or geom.is_empty:
        raise BoundaryError("invalid_geometry", "Boundary must be a non-empty (Multi)Polygon")
    if not geom.is_valid:
        raise BoundaryError("invalid_geometry", f"Invalid boundary: {explain_validity(geom)}")
    minx, miny, maxx, maxy = geom.bounds
    bx0, by0, bx1, by1 = INDIA_COASTAL_BBOX
    if minx < bx0 or miny < by0 or maxx > bx1 or maxy > by1:
        raise BoundaryError(
            "outside_aoi",
            "Boundary must lie within India's coastal region (coordinates must be lon, lat)",
        )
    return geom


async def geodesic_area_ha(session: AsyncSession, geom: MultiPolygon) -> float:
    result = await session.scalar(
        text("SELECT ST_Area(ST_GeomFromText(:wkt, 4326)::geography) / 10000.0"),
        {"wkt": geom.wkt},
    )
    return float(result or 0.0)


def check_area(area_ha: float, settings: Settings) -> None:
    if area_ha < settings.project_min_area_ha:
        raise BoundaryError(
            "area_too_small", f"Area {area_ha:.2f} ha is below {settings.project_min_area_ha} ha"
        )
    if area_ha > settings.project_max_area_ha:
        raise BoundaryError(
            "area_too_large",
            f"Area {area_ha:.1f} ha exceeds the MVP limit of {settings.project_max_area_ha} ha",
        )


async def find_overlaps(
    session: AsyncSession,
    geom: MultiPolygon,
    settings: Settings,
    exclude_project_id: uuid.UUID | None = None,
) -> list[Overlap]:
    """Non-rejected projects whose boundary shares more than the tolerance area with `geom`."""
    rows = await session.execute(
        text(
            """
            WITH q AS (SELECT ST_GeomFromText(:wkt, 4326) AS g)
            SELECT p.id, p.name,
                   ST_Area(ST_Intersection(p.boundary, q.g)::geography) / 10000.0 AS overlap_ha
            FROM projects p, q
            WHERE ST_Intersects(p.boundary, q.g)
              AND p.status <> :rejected
              AND (CAST(:exclude AS uuid) IS NULL OR p.id <> CAST(:exclude AS uuid))
            """
        ),
        {
            "wkt": geom.wkt,
            "rejected": ProjectStatus.REJECTED.value,
            "exclude": str(exclude_project_id) if exclude_project_id else None,
        },
    )
    return [
        Overlap(project_id=r.id, name=r.name, overlap_ha=round(float(r.overlap_ha), 4))
        for r in rows
        if float(r.overlap_ha) > settings.project_overlap_tolerance_ha
    ]


def to_db_geometry(geom: MultiPolygon) -> WKBElement:
    element: WKBElement = from_shape(geom, srid=4326)
    return element


def boundary_geojson(project: Project) -> dict[str, Any]:
    return dict(mapping(to_shape(project.boundary)))


def submission_issues(project: Project) -> list[Issue]:
    """Rule checks that must pass before a project leaves draft (methodology-specific)."""
    issues: list[Issue] = []
    if project.status not in EDITABLE_STATUSES:
        issues.append(
            Issue("invalid_status", f"Cannot submit a project in status {project.status}")
        )

    if project.methodology == Methodology.CCTS_BM_FR05_001:
        if project.ecosystem != Ecosystem.MANGROVE:
            issues.append(
                Issue("ccts_ecosystem", "BM FR05.001 applies only to degraded mangrove habitats")
            )
        if project.start_date < CCTS_EARLIEST_START:
            issues.append(
                Issue("ccts_start_date", "CCTS offset projects must start on or after 2025-01-01")
            )
        if project.planned_mangrove_share_pct <= CCTS_MIN_MANGROVE_SHARE_PCT:
            issues.append(
                Issue(
                    "ccts_mangrove_share",
                    "More than 90% of the project area must be planted with mangrove species "
                    "(BM FR05.001 §3.2(b))",
                )
            )
        if project.soil_disturbance_pct > CCTS_MAX_SOIL_DISTURBANCE_PCT:
            issues.append(
                Issue(
                    "ccts_soil_disturbance",
                    "Soil disturbance must not exceed 10% of the area (BM FR05.001 §3.2(c))",
                )
            )

    present = {doc.kind for doc in project.documents}
    required = REQUIRED_TENURE_DOCUMENTS[project.tenure_type] | ALWAYS_REQUIRED_DOCUMENTS
    for kind in sorted(required - present):
        issues.append(Issue("missing_document", f"Required tenure document missing: {kind}"))
    return issues
