"""Projects: boundary registration with double-counting checks, tenure evidence, submission."""

import re
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from aqua_api.deps import (
    CurrentUser,
    SessionDep,
    SettingsDep,
    StorageDep,
    can_read_all_projects,
    effective_platform_role,
    org_role,
)
from aqua_api.schemas.project import (
    DocumentConfirm,
    ProjectCreate,
    ProjectOut,
    ProjectUpdate,
    TenureDocumentOut,
    UploadRequest,
    UploadTicket,
)
from aqua_api.services import audit
from aqua_api.services.projects import (
    EDITABLE_STATUSES,
    BoundaryError,
    boundary_geojson,
    check_area,
    find_overlaps,
    geodesic_area_ha,
    parse_boundary,
    submission_issues,
    to_db_geometry,
)
from aqua_core.models import (
    Membership,
    Organization,
    OrgRole,
    OrgType,
    PlatformRole,
    Project,
    ProjectStatus,
    TenureDocument,
    User,
)
from aqua_core.models._types import utcnow
from aqua_core.settings import Settings

router = APIRouter(prefix="/projects", tags=["projects"])

PROJECT_ORG_TYPES = {OrgType.NGO, OrgType.PANCHAYAT, OrgType.GOVERNMENT}


def _out(project: Project) -> ProjectOut:
    return ProjectOut(
        id=project.id,
        org_id=project.org_id,
        name=project.name,
        description=project.description,
        state=project.state,
        ecosystem=project.ecosystem,
        methodology=project.methodology,
        status=project.status,
        tenure_type=project.tenure_type,
        boundary=boundary_geojson(project),
        area_ha=round(project.area_ha, 4),
        start_date=project.start_date,
        planned_mangrove_share_pct=project.planned_mangrove_share_pct,
        soil_disturbance_pct=project.soil_disturbance_pct,
        submitted_at=project.submitted_at,
        created_at=project.created_at,
        documents=[TenureDocumentOut.model_validate(d) for d in project.documents],
    )


def _boundary_http_error(err: BoundaryError) -> HTTPException:
    return HTTPException(
        status.HTTP_422_UNPROCESSABLE_CONTENT, {"code": err.code, "message": err.message}
    )


async def _validated_boundary(
    session: AsyncSession,
    geojson: dict[str, Any],
    settings: Settings,
    exclude: uuid.UUID | None = None,
) -> tuple[Any, float]:
    try:
        geom = parse_boundary(geojson)
        area_ha = await geodesic_area_ha(session, geom)
        check_area(area_ha, settings)
    except BoundaryError as err:
        raise _boundary_http_error(err) from err
    overlaps = await find_overlaps(session, geom, settings, exclude_project_id=exclude)
    if overlaps:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "code": "boundary_overlap",
                "message": "Boundary overlaps an existing project (double counting is not allowed)",
                "conflicts": [
                    {"project_id": str(o.project_id), "name": o.name, "overlap_ha": o.overlap_ha}
                    for o in overlaps
                ],
            },
        )
    return geom, area_ha


async def _load(
    session: AsyncSession, project_id: uuid.UUID, user: User, settings: Settings
) -> Project:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    is_member = org_role(user, project.org_id) is not None
    role = effective_platform_role(user, settings)
    staff_visible = can_read_all_projects(user, settings) and (
        project.status != ProjectStatus.DRAFT or role == PlatformRole.REGISTRY_ADMIN
    )
    if not (is_member or staff_visible):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return project


def _require_org_admin(user: User, project: Project) -> None:
    if org_role(user, project.org_id) != OrgRole.ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Organisation admin required")


def _require_editable(project: Project) -> None:
    if project.status not in EDITABLE_STATUSES:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Project is {project.status} and can no longer be edited"
        )


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectCreate, user: CurrentUser, session: SessionDep, settings: SettingsDep
) -> ProjectOut:
    if org_role(user, body.org_id) != OrgRole.ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Organisation admin required")
    org = await session.get(Organization, body.org_id)
    if org is None or org.org_type not in PROJECT_ORG_TYPES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Projects can be registered by NGOs, panchayats or government bodies only",
        )
    geom, area_ha = await _validated_boundary(session, body.boundary.model_dump(), settings)
    project = Project(
        org_id=body.org_id,
        created_by=user.id,
        name=body.name,
        description=body.description,
        state=body.state,
        ecosystem=body.ecosystem,
        methodology=body.methodology,
        tenure_type=body.tenure_type,
        boundary=to_db_geometry(geom),
        area_ha=area_ha,
        start_date=body.start_date,
        planned_mangrove_share_pct=body.planned_mangrove_share_pct,
        soil_disturbance_pct=body.soil_disturbance_pct,
    )
    session.add(project)
    await session.flush()
    await audit.record(
        session,
        actor_id=user.id,
        action="project.created",
        entity_type="project",
        entity_id=project.id,
        data={"name": project.name, "area_ha": round(area_ha, 4)},
    )
    await session.commit()
    await session.refresh(project, ["documents"])
    return _out(project)


@router.get("", response_model=list[ProjectOut])
async def list_projects(
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    org_id: uuid.UUID | None = None,
    status_filter: Annotated[ProjectStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ProjectOut]:
    query = select(Project).order_by(Project.created_at.desc()).limit(limit).offset(offset)
    role = effective_platform_role(user, settings)
    if role == PlatformRole.REGISTRY_ADMIN:
        pass
    elif role == PlatformRole.VERIFIER:
        query = query.where(Project.status != ProjectStatus.DRAFT)
    else:
        my_orgs = select(Membership.org_id).where(Membership.user_id == user.id)
        query = query.where(Project.org_id.in_(my_orgs))
    if org_id is not None:
        query = query.where(Project.org_id == org_id)
    if status_filter is not None:
        query = query.where(Project.status == status_filter)
    return [_out(p) for p in await session.scalars(query)]


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: uuid.UUID, user: CurrentUser, session: SessionDep, settings: SettingsDep
) -> ProjectOut:
    return _out(await _load(session, project_id, user, settings))


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: uuid.UUID,
    body: ProjectUpdate,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
) -> ProjectOut:
    project = await _load(session, project_id, user, settings)
    _require_org_admin(user, project)
    _require_editable(project)
    changes = body.model_dump(exclude_unset=True, exclude={"boundary"})
    if body.boundary is not None:
        geom, area_ha = await _validated_boundary(
            session, body.boundary.model_dump(), settings, exclude=project.id
        )
        project.boundary = to_db_geometry(geom)
        project.area_ha = area_ha
        changes["area_ha"] = round(area_ha, 4)
    for field, value in body.model_dump(exclude_unset=True, exclude={"boundary"}).items():
        setattr(project, field, value)
    await audit.record(
        session,
        actor_id=user.id,
        action="project.updated",
        entity_type="project",
        entity_id=project.id,
        data={"fields": sorted(changes)},
    )
    await session.commit()
    await session.refresh(project)
    return _out(project)


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


@router.post("/{project_id}/documents/upload-url", response_model=UploadTicket)
async def request_document_upload(
    project_id: uuid.UUID,
    body: UploadRequest,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    storage: StorageDep,
) -> UploadTicket:
    project = await _load(session, project_id, user, settings)
    _require_org_admin(user, project)
    _require_editable(project)
    safe = _SAFE_NAME.sub("_", body.filename)[-120:] or "document"
    key = f"projects/{project.id}/tenure/{uuid.uuid4().hex}/{safe}"
    url = await run_in_threadpool(storage.presign_put, key, body.content_type)
    return UploadTicket(
        object_key=key,
        upload_url=url,
        headers={"Content-Type": body.content_type},
        expires_in=settings.upload_url_ttl_s,
    )


@router.post(
    "/{project_id}/documents",
    response_model=TenureDocumentOut,
    status_code=status.HTTP_201_CREATED,
)
async def confirm_document(
    project_id: uuid.UUID,
    body: DocumentConfirm,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    storage: StorageDep,
) -> TenureDocument:
    project = await _load(session, project_id, user, settings)
    _require_org_admin(user, project)
    _require_editable(project)
    if not body.object_key.startswith(f"projects/{project.id}/tenure/"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Object key not for this project"
        )
    stored = await run_in_threadpool(storage.describe, body.object_key)
    if stored is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Upload not found in storage")
    if stored.size_bytes > settings.upload_max_bytes:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "File exceeds the upload limit")
    if stored.sha256 != body.sha256:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "SHA-256 mismatch: stored file differs from what the client hashed",
        )
    if any(d.sha256 == stored.sha256 for d in project.documents):
        raise HTTPException(status.HTTP_409_CONFLICT, "This document was already attached")
    doc = TenureDocument(
        project_id=project.id,
        kind=body.kind,
        object_key=body.object_key,
        filename=body.filename,
        content_type=stored.content_type,
        size_bytes=stored.size_bytes,
        sha256=stored.sha256,
        uploaded_by=user.id,
    )
    session.add(doc)
    await session.flush()
    await audit.record(
        session,
        actor_id=user.id,
        action="project.document_attached",
        entity_type="project",
        entity_id=project.id,
        data={"document_id": str(doc.id), "kind": doc.kind.value, "sha256": doc.sha256},
    )
    await session.commit()
    await session.refresh(doc)
    return doc


@router.post("/{project_id}/submit", response_model=ProjectOut)
async def submit_project(
    project_id: uuid.UUID, user: CurrentUser, session: SessionDep, settings: SettingsDep
) -> ProjectOut:
    project = await _load(session, project_id, user, settings)
    _require_org_admin(user, project)
    issues = [{"code": i.code, "message": i.message} for i in submission_issues(project)]
    overlaps = await find_overlaps(
        session, parse_boundary(boundary_geojson(project)), settings, exclude_project_id=project.id
    )
    issues += [
        {"code": "boundary_overlap", "message": f"Overlaps {o.name} by {o.overlap_ha} ha"}
        for o in overlaps
    ]
    if issues:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            {"code": "submission_blocked", "issues": issues},
        )
    project.status = ProjectStatus.SUBMITTED
    project.submitted_at = utcnow()
    await audit.record(
        session,
        actor_id=user.id,
        action="project.submitted",
        entity_type="project",
        entity_id=project.id,
        data={"documents": sorted(d.sha256 for d in project.documents)},
    )
    await session.commit()
    await session.refresh(project)
    return _out(project)
