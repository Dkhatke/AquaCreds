"""Map tiles for the console's monitoring map: classification and confidence XYZ PNG tiles, their
legend and TileJSON (see `aqua_api.tiles` for rendering, caching and the signed-URL scheme).

- `GET /projects/{id}/tilejson?layer=classification|confidence&cid=...`: TileJSON 3.0 with signed
  tile URLs (bearer auth + project visibility).
- `GET /projects/{id}/classifications/{cid}/tiles/{z}/{x}/{y}.png`: categorical classes.
- `GET /projects/{id}/classifications/{cid}/confidence/{z}/{x}/{y}.png`: highest class
  probability, single-hue ramp.
- `GET /projects/{id}/classifications/{cid}/legend`.

Tiles accept either the `token` query parameter from TileJSON or a bearer token. Responses: 200
PNG, 204 when the tile holds nothing visible, 304 on a matching `If-None-Match`, 404 outside the
zoom range / for a bad address, 403 for an invalid or expired token.
"""

import asyncio
import hashlib
import uuid
from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from geoalchemy2.shape import to_shape
from shapely import wkb
from shapely.geometry.base import BaseGeometry
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aqua_api import tiles
from aqua_api.deps import CurrentUser, SessionDep, SettingsDep, get_current_user
from aqua_api.routers.analysis import _project
from aqua_api.schemas.tiles import (
    ClassificationLegend,
    ConfidenceLegend,
    LegendClass,
    LegendNoData,
    LegendRampStop,
    TileJSON,
)
from aqua_core.logging import get_logger
from aqua_core.models import Classification, Project, User
from aqua_core.settings import Settings

router = APIRouter(tags=["map tiles"])
log = get_logger("aqua_api.tiles")

PNG = "image/png"
# Tiles of a classification never change (COGs are immutable, the render version is in the
# ETag). `private`: they are only for users who can see the project, so no shared cache.
TILE_CACHE_CONTROL = "private, max-age=86400, immutable"
SLOT_WAIT_S = 10.0
_bearer = HTTPBearer(auto_error=False)
_TILE_RESPONSES: dict[int | str, dict[str, object]] = {
    200: {"content": {PNG: {}}, "description": "PNG tile (256 x 256, RGBA)"},
    204: {"description": "Valid tile with nothing to draw"},
    304: {"description": "Not modified (If-None-Match)"},
    403: {"description": "Invalid or expired tile token"},
    404: {"description": "Unknown project/classification, or tile outside the zoom range"},
    503: {"description": "Tile renderer busy; retry"},
}


# ---------------------------------------------------------------- helpers


@lru_cache(maxsize=128)
def _footprints(wkb_bytes: bytes) -> tuple[BaseGeometry, BaseGeometry, str]:
    geom = wkb.loads(wkb_bytes)
    return geom, tiles.footprint_mercator(geom), hashlib.sha256(wkb_bytes).hexdigest()


def _footprint(project: Project) -> tuple[BaseGeometry, BaseGeometry, str]:
    shape = to_shape(project.boundary)
    return _footprints(shape.wkb)


async def _classification(
    session: AsyncSession, project_id: uuid.UUID, classification_id: uuid.UUID
) -> Classification:
    row = await session.get(Classification, classification_id)
    if row is None or row.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Classification not found")
    return row


def attribution(row: Classification) -> str:
    years = sorted({str(s["acquired_on"])[:4] for s in row.scenes if s.get("acquired_on")}) or [
        str(row.season_start_year),
        str(row.season_start_year + 1),
    ]
    span = years[0] if years[0] == years[-1] else f"{years[0]}-{years[-1]}"
    version = row.model_version.removeprefix("ecosystem_classifier_")
    return (
        f"Contains modified Copernicus Sentinel data {span}; "
        f"AquaCreds ecosystem classifier {version}"
    )


def _cog(row: Classification, layer: tiles.TileLayer) -> tuple[str, str]:
    if layer is tiles.TileLayer.CLASSIFICATION:
        key, sha = row.classes_cog_key, row.classes_cog_sha256
    else:
        key, sha = row.probability_cog_key, row.probability_cog_sha256
    if not tiles.valid_cog_key(key, row.project_id):
        log.error("tiles.unexpected_cog_key", classification_id=str(row.id), key=key)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Classification raster not available")
    return key, sha


async def _tile_user(
    session: AsyncSession,
    settings: Settings,
    *,
    token: str | None,
    credentials: HTTPAuthorizationCredentials | None,
    project_id: uuid.UUID,
    classification_id: uuid.UUID,
    layer: tiles.TileLayer,
) -> User:
    """The user a tile request acts for: from the signed token, else from the bearer token.
    Either way the user must be active and must still see the project (checked by the caller)."""
    if token is None:
        return await get_current_user(session, settings, credentials)
    try:
        user_id, _ = tiles.verify_tile_token(
            settings,
            token,
            project_id=project_id,
            classification_id=classification_id,
            layer=layer,
        )
    except tiles.TileTokenError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc).capitalize()) from exc
    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid tile token")
    return user


def _etag_matches(request: Request, etag: str) -> bool:
    header = request.headers.get("if-none-match")
    if not header:
        return False
    candidates = {part.strip().removeprefix("W/") for part in header.split(",")}
    return "*" in candidates or etag in candidates


async def _serve_tile(
    request: Request,
    session: AsyncSession,
    settings: Settings,
    *,
    layer: tiles.TileLayer,
    project_id: uuid.UUID,
    classification_id: uuid.UUID,
    z: int,
    x: int,
    y: int,
    token: str | None,
    credentials: HTTPAuthorizationCredentials | None,
) -> Response:
    user = await _tile_user(
        session,
        settings,
        token=token,
        credentials=credentials,
        project_id=project_id,
        classification_id=classification_id,
        layer=layer,
    )
    project = await _project(session, project_id, user, settings)
    row = await _classification(session, project_id, classification_id)
    footprint, footprint_merc, footprint_sha = _footprint(project)
    grid = tiles.TileGrid.for_footprint(footprint, row.pixel_area_m2)
    where = grid.check(z, x, y)
    if where is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tile outside this layer's zoom range")
    headers = {"Cache-Control": TILE_CACHE_CONTROL}
    if not where:
        return Response(status_code=status.HTTP_204_NO_CONTENT, headers=headers)
    key, sha = _cog(row, layer)
    spec = tiles.TileSpec(layer, key, sha, tuple(row.classes), footprint_sha, z, x, y)
    headers["ETag"] = spec.etag
    if _etag_matches(request, spec.etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)

    cache: tiles.TileCache = request.app.state.tile_cache
    hit, png = cache.get(spec)
    if not hit:
        png = await _render(request, spec, footprint_merc)
        cache.put(spec, png)
    if png is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT, headers=headers)
    return Response(content=png, media_type=PNG, headers=headers)


async def _render(
    request: Request, spec: tiles.TileSpec, footprint_merc: BaseGeometry
) -> bytes | None:
    state = request.app.state
    semaphore: asyncio.Semaphore = state.tile_semaphore
    try:
        async with asyncio.timeout(SLOT_WAIT_S):
            await semaphore.acquire()
    except TimeoutError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Tile renderer busy",
            headers={"Retry-After": "2"},
        ) from exc
    try:
        path = state.cog_locator(spec.cog_key)
        return await run_in_threadpool(
            tiles.render_tile, spec, path, footprint_merc, state.cog_access
        )
    except HTTPException:
        raise
    except Exception as exc:
        # Storage unreachable, corrupt COG, ...: an honest error, never a blank "success".
        log.error(
            "tiles.render_failed",
            cog_key=spec.cog_key,
            tile=f"{spec.z}/{spec.x}/{spec.y}",
            error=f"{type(exc).__name__}: {exc}",
        )
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Could not read the raster") from exc
    finally:
        semaphore.release()


# ---------------------------------------------------------------- routes


@router.get(
    "/projects/{project_id}/classifications/{classification_id}/tiles/{z}/{x}/{y}.png",
    response_class=Response,
    responses=_TILE_RESPONSES,
)
async def classification_tile(
    project_id: uuid.UUID,
    classification_id: uuid.UUID,
    z: int,
    x: int,
    y: int,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    token: Annotated[str | None, Query(max_length=200)] = None,
) -> Response:
    """Ecosystem classes as a 256 px PNG (palette in the legend), transparent outside the project
    polygon and where the classifier made no prediction."""
    return await _serve_tile(
        request,
        session,
        settings,
        layer=tiles.TileLayer.CLASSIFICATION,
        project_id=project_id,
        classification_id=classification_id,
        z=z,
        x=x,
        y=y,
        token=token,
        credentials=credentials,
    )


@router.get(
    "/projects/{project_id}/classifications/{classification_id}/confidence/{z}/{x}/{y}.png",
    response_class=Response,
    responses=_TILE_RESPONSES,
)
async def confidence_tile(
    project_id: uuid.UUID,
    classification_id: uuid.UUID,
    z: int,
    x: int,
    y: int,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    token: Annotated[str | None, Query(max_length=200)] = None,
) -> Response:
    """Highest class probability (0-1) as a single-hue ramp, same masking as the class tiles."""
    return await _serve_tile(
        request,
        session,
        settings,
        layer=tiles.TileLayer.CONFIDENCE,
        project_id=project_id,
        classification_id=classification_id,
        z=z,
        x=x,
        y=y,
        token=token,
        credentials=credentials,
    )


@router.get(
    "/projects/{project_id}/classifications/{classification_id}/legend",
    response_model=ClassificationLegend,
)
async def classification_legend(
    project_id: uuid.UUID,
    classification_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    response: Response,
) -> ClassificationLegend:
    """Palette and areas of the class layer, and the confidence ramp."""
    await _project(session, project_id, user, settings)
    row = await _classification(session, project_id, classification_id)
    response.headers["Cache-Control"] = "private, max-age=3600"
    classes = []
    for value, name in enumerate(row.classes):
        style = tiles.class_style(name)
        classes.append(
            LegendClass(
                value=value,
                class_name=name,
                label=style.label,
                color=style.color,
                area_ha=row.class_areas_ha.get(name),
            )
        )
    return ClassificationLegend(
        classification_id=row.id,
        project_id=row.project_id,
        season_start_year=row.season_start_year,
        model_version=row.model_version,
        attribution=attribution(row),
        classes=classes,
        nodata=LegendNoData(value=tiles.CLASS_NODATA, label=tiles.NODATA_LABEL),
        confidence=ConfidenceLegend(
            label="Highest class probability",
            min=0.0,
            max=1.0,
            stops=[LegendRampStop(value=v, color=c) for v, c in tiles.CONFIDENCE_STOPS],
            low_confidence_threshold=row.low_confidence_threshold,
        ),
        note=(
            "Classified 10 m pixels whose centres fall inside the project boundary. Context for "
            "monitoring only: credits come from field plots (estimated, pending accredited "
            "verification)."
        ),
    )


@router.get("/projects/{project_id}/tilejson", response_model=TileJSON)
async def project_tilejson(
    project_id: uuid.UUID,
    request: Request,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    response: Response,
    layer: Annotated[tiles.TileLayer, Query()] = tiles.TileLayer.CLASSIFICATION,
    cid: Annotated[uuid.UUID | None, Query(description="Classification id")] = None,
) -> TileJSON:
    """TileJSON 3.0 for one layer of a classification (default: the newest season's latest run),
    with tile URLs signed for this user until `expires_at`."""
    project = await _project(session, project_id, user, settings)
    if cid is None:
        cid = await session.scalar(
            select(Classification.id)
            .where(Classification.project_id == project_id)
            .order_by(Classification.season_start_year.desc(), Classification.created_at.desc())
            .limit(1)
        )
        if cid is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No classification for this project")
    row = await _classification(session, project_id, cid)
    _cog(row, layer)
    footprint, _, _ = _footprint(project)
    grid = tiles.TileGrid.for_footprint(footprint, row.pixel_area_m2)
    expires = tiles.token_expiry(settings)
    token = tiles.sign_tile_token(
        settings,
        project_id=project_id,
        classification_id=row.id,
        layer=layer,
        user_id=user.id,
        expires=expires,
    )
    base = (
        f"{str(request.base_url).rstrip('/')}/api/v1/projects/{project_id}/classifications/{row.id}"
    )
    segment = "tiles" if layer is tiles.TileLayer.CLASSIFICATION else "confidence"
    west, south, east, north = grid.bounds
    response.headers["Cache-Control"] = "private, no-store"  # carries a bearer-equivalent token
    label = (
        "Ecosystem classes" if layer is tiles.TileLayer.CLASSIFICATION else "Classifier confidence"
    )
    return TileJSON(
        name=f"{label}, dry season {row.season_start_year}-{(row.season_start_year + 1) % 100:02d}",
        description=(
            f"{label} from {row.model_version} on 10 m Sentinel-2 pixels inside the project "
            "boundary. Monitoring context only; not a crediting figure."
        ),
        attribution=attribution(row),
        tiles=[f"{base}/{segment}/{{z}}/{{x}}/{{y}}.png?token={token}"],
        minzoom=grid.minzoom,
        maxzoom=grid.maxzoom,
        bounds=[west, south, east, north],
        center=[(west + east) / 2, (south + north) / 2, float(min(grid.maxzoom, grid.minzoom + 6))],
        layer=layer.value,
        project_id=project_id,
        classification_id=row.id,
        season_start_year=row.season_start_year,
        model_version=row.model_version,
        expires_at=datetime.fromtimestamp(expires, UTC),
        legend_url=f"{base}/legend",
    )
