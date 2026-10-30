"""Model cards over the API (`/models/...`, `ClassificationOut.model_card`): every number must equal
the model card's metrics JSON in ml/model_cards, so the web app can stop copying it."""

import json
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from api_testlib import project_payload

from aqua_core.ml_models import (
    CURRENT_ECOSYSTEM_CLASSIFIER,
    ECOSYSTEM_CLASSIFIER_V1,
    ECOSYSTEM_CLASSIFIER_V1_1,
    FAMILIES,
    PHOTO_VERIFIER_V1,
    ModelManifest,
)

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("manifest", [ECOSYSTEM_CLASSIFIER_V1, ECOSYSTEM_CLASSIFIER_V1_1])
def test_classifier_card_fields_match_the_metrics_json(manifest: ModelManifest) -> None:
    doc = json.loads((ROOT / manifest.metrics_file).read_text())
    assert doc["model_version"] == manifest.version
    assert doc["winner"] == manifest.algorithm
    model = doc["models"][manifest.algorithm]
    assert manifest.heldout_per_class == model["heldout"]["per_class"]
    cv = model["cv"]
    assert manifest.cv_metrics["folds"] == len(cv["per_fold"])
    assert manifest.cv_metrics["macro_f1_mean"] == cv["macro_f1"]["mean"]
    assert manifest.cv_metrics["macro_f1_std"] == cv["macro_f1"]["std"]
    assert manifest.cv_metrics["accuracy_mean"] == cv["accuracy"]["mean"]
    assert manifest.cv_metrics["mangrove_vs_rest_f1_mean"] == cv["mangrove_vs_rest_f1"]["mean"]
    assert (ROOT / manifest.model_card).is_file()


def test_every_family_lists_real_cards() -> None:
    for family, (versions, current) in FAMILIES.items():
        assert current in versions
        for m in versions:
            assert m.family == family
            assert (ROOT / m.model_card).is_file(), m.model_card
            assert (ROOT / m.metrics_file).is_file(), m.metrics_file
    assert not ECOSYSTEM_CLASSIFIER_V1.files_available  # lost: provenance only
    assert ECOSYSTEM_CLASSIFIER_V1_1.files_available


async def test_model_endpoints(client: httpx.AsyncClient) -> None:
    r = await client.get("/models")
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "public, max-age=3600"
    families = {f["family"]: f for f in r.json()}
    assert set(families) == {"ecosystem-classifier", "photo-verifier"}
    eco = families["ecosystem-classifier"]
    assert eco["current_version"] == CURRENT_ECOSYSTEM_CLASSIFIER.version
    assert [v["version"] for v in eco["versions"]] == [
        "ecosystem_classifier_v1",
        "ecosystem_classifier_v1.1",
    ]

    one = await client.get("/models/ecosystem-classifier/ecosystem_classifier_v1.1")
    assert one.status_code == 200
    card = one.json()
    assert card["current"] is True
    assert card["files_available"] is True
    assert card["heldout_metrics"] == dict(ECOSYSTEM_CLASSIFIER_V1_1.heldout_metrics)
    assert card["heldout_per_class"]["mangrove"]["f1"] == 0.9451
    assert card["cv_metrics"]["macro_f1_mean"] == 0.7596
    assert card["metrics_file"] == "ml/model_cards/ecosystem_classifier_v1_1_metrics.json"
    assert card["model_card_url"].endswith("ml/model_cards/ecosystem_classifier_v1_1.md")
    assert card["sha256"] == dict(ECOSYSTEM_CLASSIFIER_V1_1.sha256)
    v1 = (await client.get("/models/ecosystem-classifier/ecosystem_classifier_v1")).json()
    assert (v1["current"], v1["files_available"]) == (False, False)

    photo = await client.get(f"/models/photo-verifier/{PHOTO_VERIFIER_V1.version}")
    assert photo.status_code == 200
    assert photo.json()["heldout_metrics"]["test_state_f1"] == 0.962
    fam = await client.get("/models/photo-verifier")
    assert fam.json()["current_version"] == "photo_verifier_v1"

    assert (await client.get("/models/ecosystem-classifier/v9")).status_code == 404
    assert (await client.get("/models/unknown-family")).status_code == 422
    etag = one.headers["etag"]
    cached = await client.get(
        "/models/ecosystem-classifier/ecosystem_classifier_v1.1", headers={"If-None-Match": etag}
    )
    assert cached.status_code == 304


async def _classification(sql: Any, pid: str, version: str, year: int) -> uuid.UUID:
    cid = uuid.uuid4()
    await sql(
        "INSERT INTO classifications (id, project_id, season_start_year, model_version, "
        "model_sha256, features_sha256, provider, scenes, dem_items, epsg, pixel_area_m2, "
        "areal_scale, polygon_pixels, classified_pixels, unclassified_pixels, classes, "
        "class_areas_ha, mangrove_ha, mangrove_mean_confidence, low_confidence_threshold, "
        "low_confidence_share, classes_cog_key, classes_cog_sha256, probability_cog_key, "
        "probability_cog_sha256) VALUES (:id, :pid, :year, :v, :m, :m, 'planetary_computer', "
        "'[]'::jsonb, '[]'::jsonb, 32645, 100.0, 1.0, 10, 10, 0, '[\"mangrove\"]'::jsonb, "
        "'{\"mangrove\": 1.0}'::jsonb, 1.0, 0.9, 0.6, 0.0, :k, :m, :k, :m)",
        id=cid,
        pid=pid,
        year=year,
        v=version,
        m="1" * 64,
        k=f"projects/{pid}/c/{cid}.tif",
    )
    return cid


async def test_classifications_carry_their_model_card(make_actor: Any, sql: Any) -> None:
    admin = await make_actor("Card Admin")
    org = (await admin.post("/orgs", json={"name": "Card NGO", "org_type": "ngo"})).json()
    pid = (await admin.post("/projects", json=project_payload(org["id"]))).json()["id"]
    known = await _classification(sql, pid, "ecosystem_classifier_v1.1", 2021)
    unknown = await _classification(sql, pid, "ecosystem_classifier_v0_experimental", 2020)

    listed = (await admin.get(f"/projects/{pid}/classifications")).json()
    by_id = {c["id"]: c for c in listed}
    card = by_id[str(known)]["model_card"]
    assert card["version"] == "ecosystem_classifier_v1.1"
    assert card["heldout_metrics"]["macro_f1"] == 0.6378
    assert by_id[str(unknown)]["model_card"] is None  # never another version's numbers

    detail = await admin.get(f"/classifications/{known}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["model_card"] == card
