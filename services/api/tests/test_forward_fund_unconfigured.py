"""Forward-fund endpoints when CHAIN_FORWARD_FUND_ADDRESS is unset (no Anvil needed)."""

from typing import Any

from api_testlib import project_payload


async def test_forward_fund_endpoints_answer_503_when_not_configured(
    make_actor: Any, enqueued: list[Any]
) -> None:
    admin = await make_actor("NGO Admin")
    org = (await admin.post("/orgs", json={"name": "Trust", "org_type": "ngo"})).json()
    pid = (await admin.post("/projects", json=project_payload(org["id"]))).json()["id"]

    for r in (
        await admin.get("/forward-fund/requests"),
        await admin.get("/forward-fund/requests/1"),
        await admin.get(f"/projects/{pid}/forward-fund"),
        await admin.post(
            f"/projects/{pid}/forward-fund/offers",
            json={"request_id": 1, "price_per_unit": 1, "quantity": 1},
        ),
    ):
        assert r.status_code == 503
        assert "CHAIN_FORWARD_FUND_ADDRESS" in r.json()["detail"]
    assert enqueued == []
    anonymous = await admin.client.get("/forward-fund/requests")
    assert anonymous.status_code == 401
