"""Chain endpoints when the relayer is not configured / the RPC is unreachable (no Anvil)."""

from typing import Any

import pytest
from api_testlib import project_payload

from aqua_worker.celery_app import app as celery_app


@pytest.fixture
def settings_overrides() -> dict[str, Any]:
    # Nothing listens on port 9 (discard); keeps the status probe fast and deterministic.
    return {"chain_rpc_url": "http://127.0.0.1:9", "chain_rpc_timeout_s": 2.0}


async def test_register_returns_503_when_chain_not_configured(
    make_actor: Any, sql: Any, enqueued: list[Any]
) -> None:
    admin = await make_actor("NGO Admin")
    org = (await admin.post("/orgs", json={"name": "Trust", "org_type": "ngo"})).json()
    pid = (await admin.post("/projects", json=project_payload(org["id"]))).json()["id"]
    await sql("UPDATE projects SET status='submitted' WHERE id=:id", id=pid)
    registry = await make_actor("Registry Admin")
    await sql("UPDATE users SET platform_role='registry_admin' WHERE id=:id", id=registry.id)

    r = await registry.post(f"/projects/{pid}/chain/register")
    assert r.status_code == 503
    assert "RELAYER_MNEMONIC" in r.json()["detail"]
    assert enqueued == []

    view = await admin.get(f"/projects/{pid}/chain")
    assert view.status_code == 200
    assert view.json()["registered"] is False
    assert view.json()["transactions"] == []

    status = (await registry.get("/chain/status")).json()
    assert status["configured"] is False
    assert status["connected"] is False
    assert status["signers"] == []
    assert status["error"]


def test_chain_tasks_are_registered_on_the_chain_queue() -> None:
    names = {
        "aqua.chain.register_project",
        "aqua.chain.submit_report",
        "aqua.chain.approve_report",
        "aqua.chain.issue_credits",
        "aqua.chain.send",
        "aqua.chain.index_events",
    }
    assert names <= set(celery_app.tasks)
    assert celery_app.conf.task_routes["aqua.chain.*"] == {"queue": "chain"}
