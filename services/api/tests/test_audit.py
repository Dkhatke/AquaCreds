import itertools
from typing import Any

from api_testlib import project_payload


async def test_audit_chain_verifies_and_detects_tampering(make_actor: Any, sql: Any) -> None:
    admin = await make_actor("Registry Admin")
    await sql("UPDATE users SET platform_role='registry_admin' WHERE id=:id", id=admin.id)
    org = (await admin.post("/orgs", json={"name": "Trust", "org_type": "ngo"})).json()
    await admin.post("/projects", json=project_payload(org["id"]))

    entries = (await admin.get("/audit")).json()
    actions = [e["action"] for e in entries]
    assert {"user.registered", "org.created", "project.created"} <= set(actions)
    # Each entry links to its predecessor.
    ordered = sorted(entries, key=lambda e: e["id"])
    for prev, cur in itertools.pairwise(ordered):
        assert cur["prev_hash"] == prev["hash"]

    ok = (await admin.get("/audit/verify")).json()
    assert ok == {"ok": True, "entries_checked": len(entries), "first_broken_id": None}

    # Rewrite history: change the data of the project.created entry.
    target = next(e for e in ordered if e["action"] == "project.created")
    await sql(
        "UPDATE audit_log SET data = jsonb_set(data, '{area_ha}', '9999') WHERE id=:id",
        id=target["id"],
    )
    broken = (await admin.get("/audit/verify")).json()
    assert broken["ok"] is False
    assert broken["first_broken_id"] == target["id"]


async def test_audit_requires_registry_admin(make_actor: Any) -> None:
    user = await make_actor("Plain User")
    assert (await user.get("/audit")).status_code == 403
    assert (await user.get("/audit/verify")).status_code == 403
