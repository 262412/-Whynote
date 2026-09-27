"""Independent manual-v1 contract checks; failures remain failures, never xfail."""

import asyncio
import hashlib
import json
import runpy
import sqlite3
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from whynote.api import create_app
from whynote.domain import MANUAL_OPERATIONS, MANUAL_REASONS, MANUAL_UI_VERSION, Principal
from whynote.measurement import report, timing_payload
from whynote.store import EventStore

HELPERS = runpy.run_path(str(Path(__file__).parents[1] / "tests/test_openwebui_s0.py"))
OWNER = Principal("synthetic-qa", "alice")
TARGET = {"object_type": "answer", "object_id": "fiction", "object_version": "1"}


def emit_evidence(case, **values):
    print(json.dumps({"case": case, **values}, ensure_ascii=False))


def host(monkeypatch, tmp_path):
    action = HELPERS["s0_action"](monkeypatch, tmp_path)
    chat, body, _ = HELPERS["s0_chat"]()

    async def owned(chat_id, user_id):
        return chat if chat_id == body["chat_id"] and user_id == "alice" else None

    action._owned_chat = owned
    return action, body


def create(store):
    return store.create_action(OWNER, TARGET, {"interaction_contract": "manual-v1"}, "create")["event_id"]


def display(store, eid, did, mode="manual_menu"):
    store.issue_display_ticket(OWNER, eid, did)
    store.record_display(OWNER, eid, did, mode, [c for c, _ in MANUAL_REASONS], MANUAL_UI_VERSION, "session")


def respond(store, eid, did, kind, code=None, mode="manual_menu"):
    display(store, eid, did, mode)
    return store.record_user_action(OWNER, eid, kind, code, did, True, did)


def test_host_new_click_after_retraction(monkeypatch, tmp_path):
    action, body = host(monkeypatch, tmp_path)

    async def choose(menu):
        return HELPERS["selected_value"](menu, "事实有误")

    first = asyncio.run(action.action(body, __user__={"id": "alice"}, __event_call__=choose))
    principal = Principal(action.tenant, "alice")
    action.store.retract_action(principal, first["event_id"], "synthetic-retract")
    second = asyncio.run(action.action(body, __user__={"id": "alice"}, __event_call__=choose))
    with sqlite3.connect(action.store.path) as db:
        counts = {
            "actions": db.execute("SELECT COUNT(*) FROM actions").fetchone()[0],
            "gate_outbox": db.execute("SELECT COUNT(*) FROM outbox WHERE topic='feedback.gate.requested'").fetchone()[
                0
            ],
        }
    emit_evidence(
        "post_retraction", same_event=first["event_id"] == second["event_id"], result=second["result"], **counts
    )
    assert second["event_id"] != first["event_id"], "A new deliberate click must create a new intent"
    assert counts == {"actions": 2, "gate_outbox": 2}


@pytest.mark.parametrize("label", [*MANUAL_OPERATIONS.values(), None])
def test_no_reason_notification_is_accurate(monkeypatch, tmp_path, label):
    action, body = host(monkeypatch, tmp_path)
    notifications = []

    async def choose(menu):
        return False if label is None else HELPERS["selected_value"](menu, label)

    async def notify(event):
        notifications.append(event["data"]["content"])

    result = asyncio.run(action.action(body, __user__={"id": "alice"}, __event_call__=choose, __event_emitter__=notify))
    state = action.store.get_action(Principal(action.tenant, "alice"), result["event_id"])
    emit_evidence("no_reason_notification", label=label, reason_code=state["reason_code"], notifications=notifications)
    assert state["reason_code"] is None
    assert all("原因已记录" not in message for message in notifications), (
        "Null reason must not be confirmed as recorded"
    )


@pytest.mark.parametrize("field", ["active_ms", "elapsed_ms"])
def test_invalid_optional_timing_does_not_block_http_feedback(monkeypatch, tmp_path, field):
    monkeypatch.setattr("whynote.store._now", lambda: "2026-09-01T00:00:00Z")
    app = create_app(tmp_path / "events.db", authenticate=lambda _: OWNER, authorize_target=lambda *_: True)
    store = app.state.store
    eid = create(store)
    display(store, eid, "display")
    before = store.get_events(OWNER, eid)
    timing = {
        "version": "active-v1",
        "display_id": "display",
        "session_ref": "session",
        "active_ms": 1,
        "elapsed_ms": 1,
        field: 10**400,
    }
    payload = {
        "user_action": "reason_selected",
        "reason_code": "style",
        "display_id": "display",
        "explicit_submission": True,
        "timing": timing,
    }
    with TestClient(app, raise_server_exceptions=False) as api:
        response = api.post(
            f"/v1/feedback-actions/{eid}/attribution-events", headers={"Idempotency-Key": "response"}, json=payload
        )
        after = store.get_events(OWNER, eid)
        emit_evidence(
            "oversized_timing",
            field=field,
            status=response.status_code,
            events_before=len(before),
            events_after=len(after),
        )
        assert response.status_code == 200, "Optional invalid timing must not reject feedback"
        assert after[-1]["payload"]["measurement"]["timing_status"] == "invalid_duration"
        assert after[-1]["payload"]["measurement"]["active_ms"] is None
        assert len(after) == len(before) + 1
        assert (
            api.post(
                f"/v1/feedback-actions/{eid}/attribution-events", headers={"Idempotency-Key": "response"}, json=payload
            ).status_code
            == 200
        )
        assert store.get_events(OWNER, eid) == after


def test_late_retraction_is_separately_reported(monkeypatch, tmp_path):
    path = tmp_path / "events.db"
    clock = ["2026-09-01T00:00:00Z"]
    monkeypatch.setattr("whynote.store._now", lambda: clock[0])
    store = EventStore(path)
    eid = create(store)
    display(store, eid, "seen")
    at_deadline = report(path, OWNER.tenant_ref, [eid], "2026-09-02T00:00:00Z")
    clock[0] = "2026-09-02T01:00:00Z"
    store.retract_action(OWNER, eid, "late-retract")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    result = report(path, OWNER.tenant_ref, [eid], clock[0])
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert report(path, OWNER.tenant_ref, [eid], "2026-09-02T00:00:00Z") == at_deadline
    row = result["rows"][0]
    emit_evidence(
        "late_retraction",
        denominator=result["denominator"],
        window_status=row["window_status"],
        current=row["current_state"]["action_status"],
        aggregate=result["window_status_counts"],
    )
    assert result["denominator"] == 1
    assert row["current_state"]["action_status"] == "retracted"
    # Preserve the frozen window but require an explicit current retraction aggregate.
    assert result.get("retracted_action_count") == 1, (
        "Current retractions need a separate count, including those after 24h"
    )


def test_repeated_operation_counts_reconcile(monkeypatch, tmp_path):
    path = tmp_path / "events.db"
    monkeypatch.setattr("whynote.store._now", lambda: "2026-09-01T00:00:00Z")
    store = EventStore(path)
    eid = create(store)
    respond(store, eid, "first", "reason_selected", "style")
    respond(store, eid, "edit1", "reason_edited", "irrelevant", "edit_menu")
    respond(store, eid, "edit2", "reason_edited", "style", "edit_menu")
    result = report(path, OWNER.tenant_ref, [eid], "2026-09-02T00:00:00Z")
    counts = result["rows"][0]["response_counts_in_window"]
    # The existing response_action_counts field counts unique actions. Record its unit
    # explicitly; do not mislabel it as operation occurrences or assert a new meaning.
    emit_evidence(
        "repeat_counts",
        per_action_occurrences=counts,
        aggregate_unique_actions=result["response_action_counts_in_window"],
    )
    assert counts["reason_edited"] == 2
    assert result["response_action_counts_in_window"]["reason_edited"] == 1
    assert result["filled_numerator"] == result["denominator"] == 1


@pytest.mark.parametrize("reconnect", [False, True])
def test_completed_click_retry_does_not_reopen_or_append(monkeypatch, tmp_path, reconnect):
    """Same click ID is a request retry even while its action remains active."""
    action, body = host(monkeypatch, tmp_path)
    body["whynote_click_id"] = str(uuid.uuid4())
    opened = []

    async def choose(menu):
        opened.append(menu["data"]["input"]["measurement"]["display_id"])
        return HELPERS["selected_value"](menu, "事实有误")

    first = asyncio.run(action.action(body, __user__={"id": "alice"}, __event_call__=choose))
    principal = Principal(action.tenant, "alice")
    before = action.store.get_events(principal, first["event_id"])
    retry_body = {**body, "session_id": "reconnected"} if reconnect else body.copy()
    second = asyncio.run(action.action(retry_body, __user__={"id": "alice"}, __event_call__=choose))
    after = action.store.get_events(principal, first["event_id"])
    emit_evidence(
        "completed_click_retry",
        reconnect=reconnect,
        menus=len(opened),
        before_types=[e["event_type"] for e in before],
        after_types=[e["event_type"] for e in after],
        first_status=first["attribution_status"],
        retry_status=second["attribution_status"],
    )
    assert len(opened) == 1, "A completed click retry must reuse its result without reopening a menu"
    assert after == before
    assert second == first


@pytest.mark.parametrize("value", [-(10**400), 10**400, False, "1", None, float("inf"), float("nan")])
def test_additional_invalid_duration_boundaries(value):
    result = timing_payload(
        {"version": "active-v1", "session_ref": "s", "display_id": "d", "active_ms": value, "elapsed_ms": 2},
        {"client_session_ref": "s", "display_id": "d"},
        10,
    )
    assert result == {"active_ms": None, "timing_status": "invalid_duration", "server_total_ms": 10}
