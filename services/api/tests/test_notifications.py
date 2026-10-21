"""Notification fan-out for every hooked event, the inbox API and email preferences."""

import uuid
from collections.abc import AsyncIterator
from datetime import date
from typing import Any

import pyotp
import pytest
from api_testlib import project_payload
from conftest import FakeStorage
from reports_testlib import make_verifier, request_and_build, setup_project
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from test_field import FAR_OUTSIDE, _check, _photo, _submit, field  # noqa: F401 (fixture)
from test_projects import ALL_FOREST_DOCS, _attach

from aqua_core import notifications
from aqua_core.db import session_factory
from aqua_core.models import (
    AlertKind,
    AlertSeverity,
    ChainSigner,
    ChainTransaction,
    ChainTxPurpose,
    ChainTxStatus,
    NotificationKind,
    OrgWallet,
)
from aqua_worker.alerts import AlertCandidate
from aqua_worker.tasks import geo, ml

Enqueued = list[tuple[str, tuple[str, ...], str]]
ZERO = "0x" + "0" * 40


@pytest.fixture
async def sessionmaker(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(database_url)
    yield session_factory(engine)
    await engine.dispose()


async def inbox(actor: Any, **params: Any) -> list[dict[str, Any]]:
    r = await actor.get("/notifications", params=params)
    assert r.status_code == 200, r.text
    items: list[dict[str, Any]] = r.json()["items"]
    return items


async def kinds(actor: Any) -> list[str]:
    return [n["kind"] for n in await inbox(actor)]


# ---------------------------------------------------------------- reports


async def test_report_review_fan_out_respects_conflict_of_interest(
    make_actor: Any, sql: Any, storage: FakeStorage, enqueued: Enqueued, sessionmaker: Any
) -> None:
    s = await setup_project(make_actor, sql)
    insider = await make_verifier(make_actor, sql, "Insider Verifier")
    r = await s.admin.post(
        f"/orgs/{s.org_id}/members", json={"email": insider.email, "role": "member"}
    )
    assert r.status_code == 201, r.text
    second = await make_verifier(make_actor, sql, "Second Verifier")
    inactive = await make_verifier(make_actor, sql, "Inactive Verifier")
    await sql("UPDATE users SET is_active=false WHERE id=:id", id=inactive.id)
    registry = await make_actor("Registry")
    await sql("UPDATE users SET platform_role='registry_admin' WHERE id=:id", id=registry.id)

    await request_and_build(s, storage, enqueued, sessionmaker)
    report_id = (await s.admin.get(f"/periods/{s.period_id}/reports")).json()[0]["id"]
    for verifier in (s.verifier, second):
        [n] = await inbox(verifier)
        assert n["kind"] == "report_submitted_for_review"
        assert n["link"] == f"/reports/{report_id}"
        assert (n["entity_type"], n["entity_id"]) == ("mrv_report", report_id)
        assert n["read_at"] is None
    assert await kinds(insider) == []  # member of the project's organisation
    assert await kinds(s.admin) == []
    assert await kinds(registry) == []
    assert await sql("SELECT 1 FROM notifications WHERE user_id=:u", u=inactive.id) == []

    r = await s.verifier.post(
        f"/reports/{report_id}/review",
        json={"decision": "request_changes", "comment": "Re-measure plot S02"},
    )
    assert r.status_code == 200, r.text
    [n] = await inbox(s.admin)
    assert n["kind"] == "changes_requested"
    assert "Re-measure plot S02" in n["body"]
    assert n["org_id"] == s.org_id
    # Nothing is emailed: email is off by default.
    assert not any(name == notifications.EMAIL_TASK for name, _, _ in enqueued)


async def test_baseline_approval_notifies_decision_and_registration(
    make_actor: Any, sql: Any, storage: FakeStorage, enqueued: Enqueued, sessionmaker: Any
) -> None:
    s = await setup_project(make_actor, sql, kind="baseline", project_status="baseline_ready")
    await request_and_build(s, storage, enqueued, sessionmaker)
    report_id = (await s.admin.get(f"/periods/{s.period_id}/reports")).json()[0]["id"]
    r = await s.verifier.post(f"/reports/{report_id}/review", json={"decision": "approve"})
    assert r.status_code == 200, r.text
    assert sorted(await kinds(s.admin)) == ["project_registered", "report_decided"]
    decided = next(n for n in await inbox(s.admin) if n["kind"] == "report_decided")
    assert "approved" in decided["title"]


async def test_monitoring_rejection_is_a_decision_without_registration(
    make_actor: Any, sql: Any, storage: FakeStorage, enqueued: Enqueued, sessionmaker: Any
) -> None:
    s = await setup_project(make_actor, sql, project_status="registered")
    await request_and_build(s, storage, enqueued, sessionmaker)
    report_id = (await s.admin.get(f"/periods/{s.period_id}/reports")).json()[0]["id"]
    r = await s.verifier.post(
        f"/reports/{report_id}/review", json={"decision": "reject", "comment": "Fabricated"}
    )
    assert r.status_code == 200, r.text
    [n] = await inbox(s.admin)
    assert (n["kind"], "rejected" in n["title"]) == ("report_decided", True)


# ---------------------------------------------------------------- projects


async def test_project_submission_notifies_independent_registry_admins(
    make_actor: Any, sql: Any, storage: FakeStorage
) -> None:
    admin = await make_actor("NGO Admin")
    org = (await admin.post("/orgs", json={"name": "Coast Trust", "org_type": "ngo"})).json()
    outside = await make_actor("Registry")
    inside = await make_actor("Registry Insider")
    for user in (outside, inside):
        await sql("UPDATE users SET platform_role='registry_admin' WHERE id=:id", id=user.id)
    await admin.post(f"/orgs/{org['id']}/members", json={"email": inside.email, "role": "member"})
    project_id = (await admin.post("/projects", json=project_payload(org["id"]))).json()["id"]
    for kind in ALL_FOREST_DOCS:
        assert (await _attach(admin, storage, project_id, kind)).status_code == 201
    assert (await admin.post(f"/projects/{project_id}/submit")).status_code == 200
    [n] = await inbox(outside)
    assert (n["kind"], n["link"]) == ("project_submitted", f"/projects/{project_id}")
    assert await kinds(inside) == []


# ---------------------------------------------------------------- field evidence


async def test_rejected_submission_notifies_the_field_agent(
    field: Any,  # noqa: F811
    storage: FakeStorage,
    database_url: str,
) -> None:
    sub = (await _submit(field, storage, _photo(3, FAR_OUTSIDE), at=FAR_OUTSIDE)).json()["id"]
    assert (await _check(database_url, storage, sub))["status"] == "rejected"
    [n] = await inbox(field.agent)
    assert n["kind"] == "submission_rejected"
    assert "geofence" in n["body"]
    assert n["link"] == f"/field/submissions/{sub}"
    assert await kinds(field.admin) == []

    ok = (await _submit(field, storage, _photo(4))).json()["id"]
    assert (await _check(database_url, storage, ok))["status"] == "checked"
    assert len(await inbox(field.agent)) == 1  # accepted photos do not notify


# ---------------------------------------------------------------- alerts


def _candidate(severity: AlertSeverity, day: int, kind: AlertKind) -> AlertCandidate:
    return AlertCandidate(
        kind=kind,
        metric="ndvi_p50",
        observed_on=date(2024, 1, day),
        observed_value=0.3,
        severity=severity,
        expected={"n": 15},
        message=f"NDVI dropped ({severity.value})",
    )


async def test_alerts_notify_org_admins_and_email_only_high_severity(
    make_actor: Any, sql: Any, sessionmaker: Any
) -> None:
    admin = await make_actor("NGO Admin")
    org = (await admin.post("/orgs", json={"name": "Alert Trust", "org_type": "ngo"})).json()
    agent = await make_actor("Field Agent")
    await admin.post(
        f"/orgs/{org['id']}/members", json={"email": agent.email, "role": "field_agent"}
    )
    project_id = uuid.UUID(
        (await admin.post("/projects", json=project_payload(org["id"]))).json()["id"]
    )
    run_id = uuid.uuid4()
    await sql(
        "INSERT INTO analysis_runs (id, project_id, kind, status, params, outputs) VALUES "
        "(:id, :p, 'classification', 'succeeded', CAST('{}' AS jsonb), CAST('{}' AS jsonb))",
        id=run_id,
        p=project_id,
    )
    sent: list[tuple[str, tuple[str, ...], str]] = []
    notifications.set_default_email_dispatch(
        lambda name, *args, queue="default": sent.append((name, args, queue)), enabled=True
    )
    try:
        async with sessionmaker() as session:
            created = await geo._store_alerts(
                session,
                project_id,
                None,
                [
                    _candidate(AlertSeverity.HIGH, 5, AlertKind.VEGETATION_LOSS),
                    _candidate(AlertSeverity.MEDIUM, 6, AlertKind.VEGETATION_LOSS),
                ],
            )
        assert len(created) == 2
        async with sessionmaker() as session:  # the classifier's area-loss alerts too
            alert_id = await ml._store_alert(
                session,
                project_id,
                run_id,
                _candidate(AlertSeverity.HIGH, 7, AlertKind.MANGROVE_AREA_LOSS),
            )
            assert alert_id is not None
            await session.rollback()  # rolled back: no notification, no email
    finally:
        notifications.set_default_email_dispatch(None, enabled=False)

    items = await inbox(admin)
    assert sorted((n["kind"], n["severity"]) for n in items) == [
        ("alert_raised", "high"),
        ("alert_raised", "medium"),
    ]
    high = next(n for n in items if n["severity"] == "high")
    assert high["title"].startswith("High-severity alert")
    assert high["link"] == f"/projects/{project_id}/alerts"
    assert await kinds(agent) == []
    # Only the committed high-severity alert was handed to the email task.
    assert sent == [(notifications.EMAIL_TASK, (high["id"],), "default")]


# ---------------------------------------------------------------- chain


async def _tx(
    sessionmaker: Any,
    purpose: ChainTxPurpose,
    status: ChainTxStatus,
    entity_id: str,
    args: list[Any],
    **kw: Any,
) -> uuid.UUID:
    async with sessionmaker() as session:
        tx = ChainTransaction(
            chain_id=31337,
            purpose=purpose,
            entity_type="project",
            entity_id=entity_id,
            signer=ChainSigner.ORG_WALLET,
            from_address=ZERO,
            to_address=ZERO,
            contract="AquaForwardFund",
            function="submitOffer",
            args=args,
            status=status,
            **kw,
        )
        session.add(tx)
        await session.commit()
        return tx.id


async def test_chain_outcomes_notify_org_admins_once(make_actor: Any, sessionmaker: Any) -> None:
    admin = await make_actor("NGO Admin")
    org = (await admin.post("/orgs", json={"name": "Chain Trust", "org_type": "ngo"})).json()
    project_id = (await admin.post("/projects", json=project_payload(org["id"]))).json()["id"]
    wallet = "0x" + "ab" * 20
    async with sessionmaker() as session:
        session.add(OrgWallet(org_id=uuid.UUID(org["id"]), derivation_index=1000, address=wallet))
        await session.commit()

    issued = await _tx(
        sessionmaker,
        ChainTxPurpose.ISSUE_CREDITS,
        ChainTxStatus.CONFIRMED,
        project_id,
        [1, 0, wallet.upper().replace("0X", "0x"), 120, 0],
    )
    offer = await _tx(
        sessionmaker,
        ChainTxPurpose.FORWARD_FUND_OFFER,
        ChainTxStatus.REVERTED,
        project_id,
        [7, 100],
        error_name="OfferTooHigh",
    )
    pending = await _tx(
        sessionmaker, ChainTxPurpose.FORWARD_FUND_OFFER, ChainTxStatus.SUBMITTED, project_id, []
    )
    unknown_wallet = await _tx(
        sessionmaker,
        ChainTxPurpose.ISSUE_CREDITS,
        ChainTxStatus.CONFIRMED,
        project_id,
        [1, 0, "0x" + "cd" * 20, 5, 0],
    )
    other = await _tx(
        sessionmaker, ChainTxPurpose.REGISTER_PROJECT, ChainTxStatus.CONFIRMED, project_id, []
    )
    for tx_id in (issued, offer, pending, unknown_wallet, other, issued, offer, uuid.uuid4()):
        await notifications.chain_tx_settled(sessionmaker, tx_id)

    items = {n["kind"]: n for n in await inbox(admin)}
    assert len(await inbox(admin)) == 2  # repeated calls are deduplicated
    assert "120 credit units" in items["credits_issued"]["body"]
    ff = items["forward_fund_offer_status"]
    assert "reverted" in ff["title"]
    assert "OfferTooHigh" in ff["body"]
    assert ff["link"] == f"/projects/{project_id}/forward-fund"


# ---------------------------------------------------------------- MFA


async def test_low_recovery_codes_notify_the_user(
    client: Any, make_actor: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aqua_api import mfa

    monkeypatch.setattr(mfa, "RECOVERY_CODE_COUNT", 4)
    user = await make_actor("Staff")
    secret = (await user.post("/auth/mfa/setup")).json()["secret"]
    enabled = await user.post("/auth/mfa/enable", json={"code": pyotp.TOTP(secret).now()})
    codes = enabled.json()["recovery_codes"]
    for expected, code in zip((0, 1, 2), codes, strict=False):
        challenge = (
            await client.post(
                "/auth/login", json={"email": user.email, "password": "correct-horse-battery"}
            )
        ).json()
        r = await client.post(
            "/auth/mfa/verify", json={"mfa_token": challenge["mfa_token"], "code": code}
        )
        assert r.status_code == 200, r.text
        lows = [n for n in await inbox(user) if n["kind"] == "mfa_recovery_codes_low"]
        assert len(lows) == expected  # 3 left: none; 2 left: first; 1 left: second
    assert "1 unused recovery code" in (await inbox(user))[0]["body"]


# ---------------------------------------------------------------- inbox API


async def test_inbox_pagination_read_state_and_isolation(
    make_actor: Any, sessionmaker: Any
) -> None:
    alice = await make_actor("Alice")
    bob = await make_actor("Bob")
    async with sessionmaker() as session:
        for i in range(5):
            await notifications.notify(
                session,
                [alice.id],
                kind=NotificationKind.PROJECT_REGISTERED,
                title=f"n{i}",
                body="b",
            )
        await notifications.notify(
            session, [], kind=NotificationKind.CREDITS_ISSUED, title="", body=""
        )
        await session.commit()

    page = (await alice.get("/notifications", params={"limit": 2, "offset": 0})).json()
    assert (page["total"], page["unread"], len(page["items"])) == (5, 5, 2)
    assert [n["title"] for n in page["items"]] == ["n4", "n3"]  # newest first
    first = page["items"][0]["id"]

    assert (await bob.post(f"/notifications/{first}/read")).status_code == 404
    assert (await alice.post(f"/notifications/{uuid.uuid4()}/read")).status_code == 404
    read = await alice.post(f"/notifications/{first}/read")
    assert read.status_code == 200
    stamp = read.json()["read_at"]
    assert stamp is not None
    assert (await alice.post(f"/notifications/{first}/read")).json()["read_at"] == stamp
    assert (await alice.get("/notifications/unread-count")).json() == {"unread": 4}
    unread = (await alice.get("/notifications", params={"unread": True})).json()
    assert unread["total"] == 4
    assert first not in [n["id"] for n in unread["items"]]

    assert (await bob.post("/notifications/read-all")).json() == {"updated": 0}
    assert (await alice.post("/notifications/read-all")).json() == {"updated": 4}
    assert (await alice.get("/notifications/unread-count")).json() == {"unread": 0}
    assert (await bob.get("/notifications")).json()["total"] == 0
    assert (await alice.client.get("/notifications")).status_code == 401


async def test_email_preferences(make_actor: Any) -> None:
    user = await make_actor("Prefs")
    prefs = (await user.get("/notifications/preferences")).json()
    assert prefs["email_enabled"] is False
    by_kind = {p["kind"]: p for p in prefs["preferences"]}
    assert set(by_kind) == {k.value for k in NotificationKind}
    assert by_kind["alert_raised"]["note"] == "Only high-severity alerts are emailed"
    assert by_kind["credits_issued"] == {
        "kind": "credits_issued",
        "email": True,
        "default": True,
        "customised": False,
        "note": None,
    }

    r = await user.client.put(
        "/notifications/preferences",
        headers=user.headers,
        json={"email": {"credits_issued": False, "submission_rejected": True}},
    )
    assert r.status_code == 200, r.text
    by_kind = {p["kind"]: p for p in r.json()["preferences"]}
    assert (by_kind["credits_issued"]["email"], by_kind["credits_issued"]["customised"]) == (
        False,
        True,
    )
    assert by_kind["submission_rejected"]["email"] is True
    # update an existing choice, reset another to its default
    r = await user.client.put(
        "/notifications/preferences",
        headers=user.headers,
        json={"email": {"credits_issued": True, "submission_rejected": None}},
    )
    by_kind = {p["kind"]: p for p in r.json()["preferences"]}
    assert by_kind["credits_issued"]["customised"] is True
    assert by_kind["submission_rejected"] == {**by_kind["submission_rejected"], "email": False}
    assert by_kind["submission_rejected"]["customised"] is False
    bad = await user.client.put(
        "/notifications/preferences", headers=user.headers, json={"email": {"nope": True}}
    )
    assert bad.status_code == 422


def test_email_wanted_rules() -> None:
    w = notifications.email_wanted
    assert w(NotificationKind.ALERT_RAISED, AlertSeverity.HIGH, None) is True
    assert w(NotificationKind.ALERT_RAISED, AlertSeverity.MEDIUM, True) is False
    assert w(NotificationKind.ALERT_RAISED, AlertSeverity.HIGH, False) is False
    assert w(NotificationKind.SUBMISSION_REJECTED, None, None) is False
    assert w(NotificationKind.SUBMISSION_REJECTED, None, True) is True
    assert set(notifications.EMAIL_DEFAULTS) == set(NotificationKind)
