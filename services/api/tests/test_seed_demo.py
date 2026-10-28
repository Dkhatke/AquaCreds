"""`aqua-api seed-demo`: labelled synthetic data, idempotent re-runs, production / chain refusals."""

import io
import json
from datetime import UTC, datetime
from typing import Any

import pytest
from api_testlib import app_client
from chain_testlib import Deployment, deploy_suite
from conftest import FakeStorage
from PIL import Image

from aqua_api import cli
from aqua_api.demo import (
    DEMO_SITES,
    DEMO_TAG,
    SIDE_DEG,
    SYNTHETIC_PROVIDER,
    DemoSeedError,
    boundary,
    plots,
    print_result,
    seed_demo,
    synthetic_photo,
    trees,
)
from aqua_api.services.projects import INDIA_COASTAL_BBOX
from aqua_core.settings import Settings

COUNTS = {
    "users": "SELECT count(*) FROM users",
    "orgs": "SELECT count(*) FROM organizations",
    "members": "SELECT count(*) FROM memberships",
    "projects": "SELECT count(*) FROM projects",
    "documents": "SELECT count(*) FROM tenure_documents",
    "periods": "SELECT count(*) FROM monitoring_periods",
    "plots": "SELECT count(*) FROM plots",
    "measurements": "SELECT count(*) FROM plot_measurements",
    "photos": "SELECT count(*) FROM field_submissions",
    "scenes": "SELECT count(*) FROM scene_observations",
    "reports": "SELECT count(*) FROM mrv_reports",
    "chain_txs": "SELECT count(*) FROM chain_transactions",
}


def _settings(database_url: str, **extra: Any) -> Settings:
    return Settings(
        _env_file=None,
        app_env="development",
        database_url=database_url,
        jwt_secret="t" * 48,
        rate_limit_enabled=False,
        **extra,
    )


async def _counts(sql: Any) -> dict[str, int]:
    return {k: (await sql(q))[0][0] for k, q in COUNTS.items()}


def test_synthetic_generators_are_deterministic() -> None:
    assert boundary(7) == boundary(7)
    assert boundary(1) != boundary(2)
    assert plots(3) == plots(3)
    assert trees(3, "D01", "dense", True) == trees(3, "D01", "dense", True)
    for seed in range(len(DEMO_SITES)):
        ring = boundary(seed)["coordinates"][0]
        for _, lon, lat in plots(seed).values():  # every plot inside its boundary
            assert ring[0][0] < lon < ring[2][0]
            assert ring[0][1] < lat < ring[2][1]


def test_demo_boundary_is_the_gmw_checked_mangrove_square() -> None:
    # Default seed 42 → the square that GMW v4 2020 maps as 99.66 % mangrove (see DEMO_SITES).
    assert boundary(42) == {
        "type": "Polygon",
        "coordinates": [
            [[86.837, 20.702], [86.856, 20.702], [86.856, 20.721], [86.837, 20.721], [86.837, 20.702]]
        ],
    }
    assert boundary(42 + len(DEMO_SITES)) == boundary(42)  # sites repeat after len(DEMO_SITES)
    bx0, by0, bx1, by1 = INDIA_COASTAL_BBOX
    boxes = [(lon, lat, lon + SIDE_DEG, lat + SIDE_DEG) for lon, lat in DEMO_SITES]
    for x0, y0, x1, y1 in boxes:  # inside the India coast check and in Bhitarkanika
        assert bx0 < x0 < x1 < bx1 and by0 < y0 < y1 < by1
        assert 86.75 <= x0 and x1 <= 86.98 and 20.60 <= y0 and y1 <= 20.78
    for i, a in enumerate(boxes):  # different sites never overlap (cross-org overlap rule)
        for b in boxes[i + 1 :]:
            assert a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1]


async def test_seed_is_labelled_and_idempotent(
    client: Any, database_url: str, storage: FakeStorage, sql: Any
) -> None:
    settings = _settings(database_url)
    first = await seed_demo(settings, seed=5, storage=storage, password="demo-password-123")
    counts = await _counts(sql)
    assert counts["users"] == 6  # 5 demo logins + the read-only Explore Demo visitor
    assert counts["projects"] == counts["orgs"] == 1
    assert counts["periods"] == 2
    assert counts["measurements"] == 2 * counts["plots"] == 10
    assert counts["photos"] == 5
    assert counts["reports"] == 2
    assert counts["chain_txs"] == 0
    assert {r for r, _, _ in first.logins} == {"admin", "field", "verifier", "registry", "buyer"}
    assert all(email.endswith("@example.org") for _, email, _ in first.logins)
    assert all(secret == "demo-password-123" for _, _, secret in first.logins)

    # labelled synthetic everywhere
    rows = await sql("SELECT name FROM organizations")
    assert "(synthetic" in rows[0][0]
    rows = await sql("SELECT name, description, status FROM projects")
    assert "(synthetic" in rows[0][0]
    assert DEMO_TAG in rows[0][1]
    assert rows[0][2] == "monitoring"
    ((area_ha,),) = await sql("SELECT area_ha FROM projects")
    assert 416.0 < area_ha < 416.7  # 0.019° square at ~20.7°N, geodesic (PostGIS geography)
    assert await sql("SELECT DISTINCT provider FROM scene_observations") == [(SYNTHETIC_PROVIDER,)]
    assert await sql("SELECT DISTINCT status, trust_score FROM field_submissions") == [
        ("checked", 100)
    ]
    assert await sql("SELECT DISTINCT status FROM mrv_reports") == [("approved",)]
    (key,) = (await sql("SELECT json_key FROM mrv_reports ORDER BY created_at DESC LIMIT 1"))[0]
    record = json.loads(storage.objects[key][0])
    assert DEMO_TAG in json.dumps(record)
    assert SYNTHETIC_PROVIDER in json.dumps(record)

    # re-run: nothing duplicated, passwords untouched
    again = await seed_demo(settings, seed=5, storage=storage)
    assert again.created == []
    assert again.project_id == first.project_id
    assert await _counts(sql) == counts
    assert all("unchanged" in secret for _, _, secret in again.logins)
    lines: list[str] = []
    print_result(first, out=lines.append)
    assert "SYNTHETIC" in lines[0]
    assert any(
        "demo.verifier.s5@example.org" in line and "demo-password-123" in line for line in lines
    )

    # the admin can sign in with the printed password
    r = await client.post(
        "/auth/login", json={"email": "demo.admin.s5@example.org", "password": "demo-password-123"}
    )
    assert r.status_code == 200, r.text
    assert any(f"DEMO_ORG_ID={first.org_id}" in line for line in lines)

    # Explore Demo on the seeded data: a read-only visitor session sees the reports and gets
    # presigned downloads of their files, but can neither upload nor review.
    async with app_client(
        database_url, storage, demo_mode_enabled=True, demo_org_id=first.org_id
    ) as demo:
        session = (await demo.post("/demo/session")).json()
        assert session["project_ids"] == [first.project_id]
        h = {"Authorization": f"Bearer {session['access_token']}"}
        periods = (await demo.get(f"/projects/{first.project_id}/periods", headers=h)).json()
        assert len(periods) == 2
        report_id = first.report_ids["monitoring"]
        report = await demo.get(f"/reports/{report_id}", headers=h)
        assert report.status_code == 200, report.text
        assert "signature=fake" in report.json()["downloads"]["pdf_url"]
        review = await demo.post(
            f"/reports/{report_id}/review", headers=h, json={"decision": "approve"}
        )
        assert review.status_code == 403
        upload = await demo.post(
            f"/periods/{periods[0]['id']}/photos/upload-url", headers=h, json={}
        )
        assert upload.status_code == 403
        login = await demo.post(
            "/auth/login", json={"email": "demo.visitor.s5@example.org", "password": "x" * 12}
        )
        assert login.status_code == 401  # the visitor has no usable password


def test_synthetic_photo_has_exif() -> None:
    data = synthetic_photo(1, "x", 86.86, 20.71, datetime(2026, 10, 21, 6, tzinfo=UTC))
    exif = Image.open(io.BytesIO(data)).getexif()
    assert exif[0x010F] == "AquaCreds demo generator"  # Make
    assert exif[0x010E] == DEMO_TAG  # ImageDescription


async def test_refuses_production_and_foreign_chains(database_url: str) -> None:
    prod = _settings(database_url).model_copy(update={"app_env": "production"})
    with pytest.raises(DemoSeedError, match="production"):
        await seed_demo(prod)
    amoy = _settings(database_url, chain_id=80002)
    with pytest.raises(DemoSeedError, match="31337"):
        await seed_demo(amoy, with_chain=True)
    mfa = _settings(database_url, mfa_required_for_staff=True)
    with pytest.raises(DemoSeedError, match="MFA"):
        await seed_demo(mfa)
    with pytest.raises(DemoSeedError, match="10 characters"):
        await seed_demo(_settings(database_url), password="short")


def test_cli_refuses_production(monkeypatch: pytest.MonkeyPatch, database_url: str) -> None:
    prod = _settings(database_url).model_copy(update={"app_env": "production"})
    monkeypatch.setattr(cli, "get_settings", lambda: prod)
    with pytest.raises(SystemExit, match="refuses to run with APP_ENV=production"):
        cli.main(["seed-demo", "--seed", "3"])


@pytest.fixture
async def deployment(anvil_url: str) -> Deployment:
    return await deploy_suite(anvil_url)


async def test_seed_with_chain_anchors_and_issues_once(
    client: Any,
    database_url: str,
    storage: FakeStorage,
    sql: Any,
    anvil_url: str,
    deployment: Deployment,
) -> None:
    settings = _settings(database_url, **deployment.settings(anvil_url))
    first = await seed_demo(settings, seed=6, storage=storage, with_chain=True)
    assert "issue_credits" in first.created
    lccc = first.chain["lccc_issued"]
    assert lccc > 0
    assert first.chain["issue_tx_hash"].startswith("0x")
    again = await seed_demo(settings, seed=6, storage=storage, with_chain=True)
    assert again.created == []
    issues = await sql("SELECT count(*) FROM chain_transactions WHERE purpose='issue_credits'")
    assert issues == [(1,)]
    assert await sql("SELECT count(*) FROM mrv_reports WHERE anchored_at IS NOT NULL") == [(2,)]
    vintages = (await client.get("/public/vintages")).json()["items"]
    assert [(v["issued"], v["outstanding"]) for v in vintages] == [(lccc, lccc)]
