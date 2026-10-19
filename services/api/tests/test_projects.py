from typing import Any

import pytest
from api_testlib import BHITARKANIKA, project_payload, square
from conftest import FakeStorage

ALL_FOREST_DOCS = ("forest_dept_agreement", "crz_clearance", "carbon_rights_agreement")


async def _ngo(make_actor: Any, name: str = "Mangrove NGO Admin") -> tuple[Any, str]:
    admin = await make_actor(name)
    r = await admin.post("/orgs", json={"name": "Odisha Coastal Trust", "org_type": "ngo"})
    assert r.status_code == 201, r.text
    return admin, r.json()["id"]


async def _attach(admin: Any, storage: FakeStorage, project_id: str, kind: str) -> Any:
    ticket = await admin.post(
        f"/projects/{project_id}/documents/upload-url",
        json={"kind": kind, "filename": f"{kind}.pdf", "content_type": "application/pdf"},
    )
    assert ticket.status_code == 200, ticket.text
    key = ticket.json()["object_key"]
    sha = storage.put(key, f"%PDF-1.7 {kind} {project_id}".encode())
    return await admin.post(
        f"/projects/{project_id}/documents",
        json={"object_key": key, "kind": kind, "filename": f"{kind}.pdf", "sha256": sha},
    )


async def test_create_project_computes_geodesic_area(make_actor: Any) -> None:
    admin, org_id = await _ngo(make_actor)
    r = await admin.post("/projects", json=project_payload(org_id))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "draft"
    # 0.02 deg x 0.02 deg at ~20.7N is about 2.08 km x 2.21 km, i.e. ~460 ha
    assert 440 < body["area_ha"] < 480
    assert body["boundary"]["type"] == "MultiPolygon"


@pytest.mark.parametrize(
    ("boundary", "code"),
    [
        (square(-0.1, 51.5), "outside_aoi"),  # London
        (square(20.7, 86.85), "outside_aoi"),  # lat/lon swapped
        (
            {  # self-intersecting "bow tie"
                "type": "Polygon",
                "coordinates": [
                    [[86.85, 20.7], [86.87, 20.72], [86.87, 20.7], [86.85, 20.72], [86.85, 20.7]]
                ],
            },
            "invalid_geometry",
        ),
        (square(86.85, 20.70, size_deg=0.0005), "area_too_small"),
        (square(86.5, 20.5, size_deg=0.5), "area_too_large"),
    ],
)
async def test_boundary_validation(make_actor: Any, boundary: dict[str, Any], code: str) -> None:
    admin, org_id = await _ngo(make_actor)
    r = await admin.post("/projects", json=project_payload(org_id, boundary=boundary))
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == code


async def test_overlapping_boundary_is_rejected_across_orgs(make_actor: Any) -> None:
    admin_a, org_a = await _ngo(make_actor, "Admin A")
    admin_b, org_b = await _ngo(make_actor, "Admin B")
    first = await admin_a.post("/projects", json=project_payload(org_a))
    assert first.status_code == 201

    lon, lat = BHITARKANIKA
    shifted = square(lon + 0.01, lat + 0.01)  # overlaps a quarter of the first project
    r = await admin_b.post("/projects", json=project_payload(org_b, boundary=shifted))
    assert r.status_code == 409
    conflict = r.json()["detail"]["conflicts"][0]
    assert conflict["project_id"] == first.json()["id"]
    assert 100 < conflict["overlap_ha"] < 130

    # Touching edges only (no shared area) is allowed.
    touching = square(lon + 0.02, lat)
    ok = await admin_b.post("/projects", json=project_payload(org_b, boundary=touching))
    assert ok.status_code == 201, ok.text


async def test_only_org_admins_of_eligible_orgs_create_projects(make_actor: Any) -> None:
    admin, org_id = await _ngo(make_actor)
    agent = await make_actor("Field Agent")
    await admin.post(f"/orgs/{org_id}/members", json={"email": agent.email, "role": "field_agent"})
    r = await agent.post("/projects", json=project_payload(org_id))
    assert r.status_code == 403

    buyer = await make_actor("Buyer")
    company = await buyer.post("/orgs", json={"name": "Konkan Steel", "org_type": "company"})
    r = await buyer.post("/projects", json=project_payload(company.json()["id"]))
    assert r.status_code == 422


async def test_project_visibility(make_actor: Any, sql: Any) -> None:
    admin, org_id = await _ngo(make_actor)
    project_id = (await admin.post("/projects", json=project_payload(org_id))).json()["id"]

    stranger = await make_actor("Stranger")
    assert (await stranger.get(f"/projects/{project_id}")).status_code == 404
    assert (await stranger.get("/projects")).json() == []

    verifier = await make_actor("Verifier")
    await sql("UPDATE users SET platform_role='verifier' WHERE id=:id", id=verifier.id)
    # Verifiers do not see drafts...
    assert (await verifier.get(f"/projects/{project_id}")).status_code == 404
    await sql("UPDATE projects SET status='submitted' WHERE id=:id", id=project_id)
    # ...but do see submitted projects.
    assert (await verifier.get(f"/projects/{project_id}")).status_code == 200


async def test_submission_requires_tenure_evidence_and_ccts_rules(
    make_actor: Any, storage: FakeStorage
) -> None:
    admin, org_id = await _ngo(make_actor)
    payload = project_payload(org_id, planned_mangrove_share_pct=85, start_date="2024-12-01")
    project_id = (await admin.post("/projects", json=payload)).json()["id"]

    blocked = await admin.post(f"/projects/{project_id}/submit")
    assert blocked.status_code == 422
    codes = {i["code"] for i in blocked.json()["detail"]["issues"]}
    assert {"ccts_mangrove_share", "ccts_start_date", "missing_document"} <= codes

    for kind in ALL_FOREST_DOCS:
        assert (await _attach(admin, storage, project_id, kind)).status_code == 201
    fix = await admin.patch(
        f"/projects/{project_id}",
        json={"planned_mangrove_share_pct": 96, "start_date": "2026-06-01"},
    )
    assert fix.status_code == 200, fix.text

    submitted = await admin.post(f"/projects/{project_id}/submit")
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "submitted"
    assert len(submitted.json()["documents"]) == 3

    # Submitted projects are frozen.
    frozen = await admin.patch(f"/projects/{project_id}", json={"name": "Renamed"})
    assert frozen.status_code == 409


async def test_document_hash_must_match_stored_bytes(make_actor: Any, storage: FakeStorage) -> None:
    admin, org_id = await _ngo(make_actor)
    project_id = (await admin.post("/projects", json=project_payload(org_id))).json()["id"]
    ticket = await admin.post(
        f"/projects/{project_id}/documents/upload-url",
        json={"kind": "crz_clearance", "filename": "crz.pdf", "content_type": "application/pdf"},
    )
    key = ticket.json()["object_key"]
    storage.put(key, b"%PDF actual bytes")
    r = await admin.post(
        f"/projects/{project_id}/documents",
        json={
            "object_key": key,
            "kind": "crz_clearance",
            "filename": "crz.pdf",
            "sha256": "0" * 64,
        },
    )
    assert r.status_code == 422
    assert "mismatch" in r.json()["detail"]

    # Keys belonging to another project are refused.
    other = (
        await admin.post("/projects", json=project_payload(org_id, boundary=square(86.90, 20.75)))
    ).json()["id"]
    r = await admin.post(
        f"/projects/{other}/documents",
        json={
            "object_key": key,
            "kind": "crz_clearance",
            "filename": "crz.pdf",
            "sha256": "0" * 64,
        },
    )
    assert r.status_code == 422
