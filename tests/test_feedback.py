import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from whynote.api import create_app
from whynote.domain import (
    REASON_CODES,
    ConflictError,
    GateSignals,
    Principal,
    decide_gate,
    project,
    validate_jev_response,
)
from whynote.store import EventStore, process_one_gate


@pytest.fixture
def client(tmp_path):
    def authenticate(request):
        token = request.headers.get("Authorization")
        if token == "Bearer alice":
            return Principal("tenant-a", "alice")
        if token == "Bearer bob":
            return Principal("tenant-a", "bob")
        if token == "Bearer mallory":
            return Principal("tenant-b", "mallory")
        raise HTTPException(401, "missing identity")

    app = create_app(
        tmp_path / "feedback.db", authenticate, lambda principal, target: target["object_id"] == "answer-1"
    )
    with TestClient(app) as http:
        yield http, app.state.store


def _action(object_id="answer-1"):
    return {
        "target_ref": {"object_type": "assistant_response", "object_id": object_id, "object_version": "v7"},
        "action_type": "negative_feedback",
        "channel": "chat",
        "locale": "zh-CN",
    }


def _headers(actor="alice", key="key-1"):
    return {"Authorization": f"Bearer {actor}", "Idempotency-Key": key}


def _display(store, event_id, mode, codes, actor="alice"):
    display_id = str(uuid.uuid4())
    store.record_display(Principal("tenant-a", actor), event_id, display_id, mode, codes, "ui-v1")
    return display_id


def test_action_is_durable_before_gate_and_retries_are_idempotent(client):
    http, store = client
    first = http.post("/v1/feedback-actions", json=_action(), headers=_headers())
    assert first.status_code == 202
    event_id = first.json()["event_id"]
    assert first.json()["inference_status"] == "gate_pending"
    assert first.json()["reason_code"] is None

    duplicate = http.post("/v1/feedback-actions", json=_action(), headers=_headers())
    assert duplicate.status_code == 202
    assert duplicate.json()["event_id"] == event_id
    assert len(store.get_events(Principal("tenant-a", "alice"), event_id)) == 1
    event = store.get_events(Principal("tenant-a", "alice"), event_id)[0]
    assert event["event_id"] == event_id
    assert event["record_id"] != event_id
    assert event["tenant_ref"] == "tenant-a"
    assert event["trace_id"]
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT count(*) FROM outbox WHERE event_id=?", (event_id,)).fetchone()[0] == 1

    changed = _action()
    changed["channel"] = "other"
    assert http.post("/v1/feedback-actions", json=changed, headers=_headers()).status_code == 409


def test_ownership_and_input_boundary(client):
    http, _ = client
    assert http.post("/v1/feedback-actions", json=_action("other"), headers=_headers()).status_code == 403
    body = _action()
    body["user_query"] = "private text must not be accepted"
    assert http.post("/v1/feedback-actions", json=body, headers=_headers()).status_code == 422
    event_id = http.post("/v1/feedback-actions", json=_action(), headers=_headers()).json()["event_id"]
    assert http.get(f"/v1/feedback-actions/{event_id}", headers=_headers("bob")).status_code == 404
    assert http.get(f"/v1/feedback-actions/{event_id}", headers=_headers("mallory")).status_code == 404
    assert (
        http.post(f"/v1/feedback-actions/{event_id}/retract", headers=_headers("mallory", "foreign")).status_code == 404
    )
    assert (
        http.post(
            f"/v1/feedback-actions/{event_id}/attribution-events",
            json={
                "user_action": "reason_selected",
                "reason_code": "style",
                "display_id": "foreign",
                "explicit_submission": True,
            },
            headers=_headers("mallory", "foreign-attribution"),
        ).status_code
        == 404
    )


def test_revoked_target_access_blocks_existing_action_routes(tmp_path, monkeypatch):
    access = {"allowed": True}
    target = _action()["target_ref"]

    def authenticate(request):
        if request.headers.get("Authorization") == "Bearer alice":
            return Principal("tenant-a", "alice")
        raise HTTPException(401, "missing identity")

    def authorize_target(principal, stored_target):
        assert principal == Principal("tenant-a", "alice")
        assert stored_target == target
        return access["allowed"]

    app = create_app(tmp_path / "access.db", authenticate, authorize_target)
    with TestClient(app) as http:
        store = app.state.store
        principal = Principal("tenant-a", "alice")
        event_id = http.post("/v1/feedback-actions", json=_action(), headers=_headers()).json()["event_id"]
        display_id = _display(store, event_id, "manual_menu", ["style"])
        endpoint = f"/v1/feedback-actions/{event_id}"
        selection = {
            "user_action": "reason_selected",
            "reason_code": "style",
            "display_id": display_id,
            "explicit_submission": True,
        }
        events_before = store.get_events(principal, event_id)
        access["allowed"] = False
        with monkeypatch.context() as denied:

            def no_event_read(*_):
                raise AssertionError("denied requests must not read the event stream")

            denied.setattr(store, "_events", no_event_read)
            assert http.get(endpoint, headers=_headers()).status_code == 404
            assert (
                http.post(
                    f"{endpoint}/attribution-events", json=selection, headers=_headers(key="selection")
                ).status_code
                == 404
            )
            assert http.post(f"{endpoint}/retract", headers=_headers(key="retract")).status_code == 404
            assert http.post("/v1/feedback-actions", json=_action(), headers=_headers()).status_code == 403
        assert store.get_events(principal, event_id) == events_before

        access["allowed"] = True
        selected = http.post(f"{endpoint}/attribution-events", json=selection, headers=_headers(key="selection"))
        assert selected.status_code == 200
        assert selected.json()["reason_code"] == "style"
        assert http.post(f"{endpoint}/retract", headers=_headers(key="retract")).status_code == 200


@pytest.mark.parametrize(
    "signals,reason",
    [
        ((False, True, True, True), "authorization"),
        ((True, False, True, True), "scenario"),
        ((True, True, False, True), "sampling"),
        ((True, True, True, False), "budget"),
    ],
)
def test_each_gate_rejection_preserves_action_without_reason(client, signals, reason):
    http, store = client
    event_id = http.post("/v1/feedback-actions", json=_action(), headers=_headers()).json()["event_id"]

    def evaluate(_):
        return GateSignals(*signals, policy_version="d1", scenario_id="chat")

    result = process_one_gate(store, evaluate)
    assert result["event_id"] == event_id
    assert result["inference_status"] == "denied"
    assert result["reason_code"] is None
    assert reason in result["gate"]["deny_reasons"]
    assert process_one_gate(store, evaluate) is None


def test_allowed_gate_abstains_until_pipeline_is_approved(client):
    http, store = client
    http.post("/v1/feedback-actions", json=_action(), headers=_headers())
    result = process_one_gate(
        store,
        lambda _: GateSignals(True, True, True, True, "d1", "chat", "sample-v1", "sample-1", 0.25),
    )
    assert result["inference_status"] == "abstained"
    assert result["abstain_reason"] == "pipeline_unconfigured"
    assert result["gate"]["inverse_probability_weight"] == 4
    assert result["reason_code"] is None


def test_authorization_failure_discards_sampling_metadata():
    decision = decide_gate(GateSignals(False, True, True, True, "d1", "chat", "sample-v1", "sample-1", 0.25))
    assert decision["gate_outcome"] == "deny"
    assert decision["sample_id"] is None
    assert decision["inclusion_probability"] is None
    assert decision["inverse_probability_weight"] is None


def test_budget_rejection_does_not_claim_a_valid_weight():
    decision = decide_gate(GateSignals(True, True, True, False, "d1", "chat", "sample-v1", "sample-1", 0.25))
    assert decision["inverse_probability_weight"] is None
    assert decision["weight_status"] == "not_applicable_gate_denied"


def test_retract_before_gate_prevents_gate_result(client):
    http, store = client
    event_id = http.post("/v1/feedback-actions", json=_action(), headers=_headers()).json()["event_id"]
    http.post(f"/v1/feedback-actions/{event_id}/retract", headers=_headers(key="retract"))

    def must_not_run(_):
        raise AssertionError("retracted actions must not be evaluated")

    result = process_one_gate(store, must_not_run)
    assert result["action_status"] == "retracted"
    assert result["gate"] is None
    assert result["inference_status"] == "cancelled"


def test_retract_and_attribution_invalidation_are_distinct(client):
    http, store = client
    event_id = http.post("/v1/feedback-actions", json=_action(), headers=_headers()).json()["event_id"]
    with store._transaction() as db:
        store._append(db, event_id, "model_suggestion_recorded", {"reason_code": "incomplete"}, "model")
    display_id = _display(store, event_id, "model_suggestion", ["incomplete"])
    invalidate = http.post(
        f"/v1/feedback-actions/{event_id}/attribution-events",
        json={"user_action": "attribution_invalidated", "display_id": display_id, "explicit_submission": True},
        headers=_headers(key="invalidate"),
    )
    assert invalidate.status_code == 200
    assert invalidate.json()["action_status"] == "active"
    assert invalidate.json()["attribution_status"] == "invalidated"
    assert invalidate.json()["reason_code"] is None

    retract = http.post(f"/v1/feedback-actions/{event_id}/retract", headers=_headers(key="retract"))
    assert retract.status_code == 200
    assert retract.json()["action_status"] == "retracted"
    assert retract.json()["attribution_status"] == "none"
    again = http.post(f"/v1/feedback-actions/{event_id}/retract", headers=_headers(key="retract"))
    assert again.status_code == 200
    kinds = [event["event_type"] for event in store.get_events(Principal("tenant-a", "alice"), event_id)]
    assert kinds.count("attribution_invalidated") == 1
    assert kinds.count("action_retracted") == 1


def test_only_explicit_user_action_can_become_confirmed(client):
    http, store = client
    event_id = http.post("/v1/feedback-actions", json=_action(), headers=_headers()).json()["event_id"]
    endpoint = f"/v1/feedback-actions/{event_id}/attribution-events"
    confirmation = {
        "user_action": "reason_confirmed",
        "reason_code": "incomplete",
        "display_id": "forged",
        "explicit_submission": True,
    }
    assert http.post(endpoint, json=confirmation, headers=_headers(key="confirm-1")).status_code == 409
    with store._transaction() as db:
        store._append(db, event_id, "model_suggestion_recorded", {"reason_code": "incomplete"}, "model")
    assert (
        http.get(f"/v1/feedback-actions/{event_id}", headers=_headers()).json()["attribution_status"]
        == "model_inferred_unconfirmed"
    )
    confirmation["display_id"] = _display(store, event_id, "model_suggestion", ["incomplete"])
    confirmation["explicit_submission"] = False
    assert http.post(endpoint, json=confirmation, headers=_headers(key="confirm-2")).status_code == 409
    confirmation["explicit_submission"] = True
    response = http.post(endpoint, json=confirmation, headers=_headers(key="confirm-3"))
    assert response.status_code == 200
    assert response.json()["attribution_status"] == "confirmed"
    assert response.json()["attribution_source"] == "user_confirmed"


def test_late_model_result_never_overwrites_user_reason(client):
    http, store = client
    event_id = http.post("/v1/feedback-actions", json=_action(), headers=_headers()).json()["event_id"]
    endpoint = f"/v1/feedback-actions/{event_id}/attribution-events"
    display_id = _display(store, event_id, "manual_menu", ["style"])
    selected = http.post(
        endpoint,
        json={
            "user_action": "reason_selected",
            "reason_code": "style",
            "display_id": display_id,
            "explicit_submission": True,
        },
        headers=_headers(key="select"),
    )
    assert selected.status_code == 200
    with store._transaction() as db:
        store._append(db, event_id, "model_suggestion_recorded", {"reason_code": "incomplete"}, "model")
    current = http.get(f"/v1/feedback-actions/{event_id}", headers=_headers()).json()
    assert current["reason_code"] == "style"
    assert current["attribution_source"] == "user_manual"


def test_selection_requires_owned_rendered_display_and_shown_reason(client):
    http, store = client
    first = http.post("/v1/feedback-actions", json=_action(), headers=_headers()).json()["event_id"]
    endpoint = f"/v1/feedback-actions/{first}/attribution-events"
    request = {
        "user_action": "reason_selected",
        "reason_code": "style",
        "display_id": "forged",
        "explicit_submission": True,
    }
    assert http.post(endpoint, json=request, headers=_headers(key="forged")).status_code == 409
    assert http.post(endpoint, json={**request, "display_id": ""}, headers=_headers(key="empty")).status_code == 422

    second = http.post("/v1/feedback-actions", json=_action(), headers=_headers(key="second")).json()["event_id"]
    request["display_id"] = _display(store, second, "manual_menu", ["style"])
    assert http.post(endpoint, json=request, headers=_headers(key="other-action")).status_code == 409
    with pytest.raises(LookupError):
        store.record_display(Principal("tenant-a", "bob"), first, "owned-by-bob", "manual_menu", ["style"], "ui-v1")

    request["display_id"] = _display(store, first, "manual_menu", ["style"])
    assert (
        http.post(
            endpoint, json={**request, "reason_code": "incomplete"}, headers=_headers(key="not-shown")
        ).status_code
        == 409
    )
    response = http.post(endpoint, json=request, headers=_headers(key="selected"))
    assert response.status_code == 200
    assert response.json()["attribution_source"] == "user_manual"
    assert http.post(endpoint, json=request, headers=_headers(key="selected")).status_code == 200
    assert http.post(endpoint, json=request, headers=_headers(key="new-key")).status_code == 409
    events = store.get_events(Principal("tenant-a", "alice"), first)
    assert [event["event_type"] for event in events].count("reason_selected") == 1
    assert events[-1]["payload"]["ui_version"] == "ui-v1"
    assert events[-1]["event_version"] == 2


@pytest.mark.parametrize("user_action", ["reason_declined", "reason_skipped"])
def test_nonresponse_cannot_erase_submitted_reason_and_retract_masks_it(client, user_action):
    http, store = client
    event_id = http.post("/v1/feedback-actions", json=_action(), headers=_headers()).json()["event_id"]
    endpoint = f"/v1/feedback-actions/{event_id}/attribution-events"
    selected_display = _display(store, event_id, "manual_menu", ["style"])
    selected = http.post(
        endpoint,
        json={
            "user_action": "reason_selected",
            "reason_code": "style",
            "display_id": selected_display,
            "explicit_submission": True,
        },
        headers=_headers(key="select"),
    )
    assert selected.status_code == 200
    edit_display = _display(store, event_id, "edit_menu", ["style", "incomplete"])
    rejected = http.post(
        endpoint,
        json={"user_action": user_action, "display_id": edit_display, "explicit_submission": True},
        headers=_headers(key=user_action),
    )
    assert rejected.status_code == 409
    assert http.get(f"/v1/feedback-actions/{event_id}", headers=_headers()).json()["reason_code"] == "style"
    assert not any(
        event["event_type"] == user_action for event in store.get_events(Principal("tenant-a", "alice"), event_id)
    )

    retracted = http.post(f"/v1/feedback-actions/{event_id}/retract", headers=_headers(key="retract"))
    assert retracted.status_code == 200
    assert retracted.json()["projection_version"] == "3"
    assert retracted.json()["attribution_status"] == "none"
    assert retracted.json()["attribution_source"] == "none"
    assert retracted.json()["reason_code"] is None
    assert any(
        event["event_type"] == "reason_selected" for event in store.get_events(Principal("tenant-a", "alice"), event_id)
    )


def test_retracted_confirmed_action_has_no_current_attribution(client):
    http, store = client
    event_id = http.post("/v1/feedback-actions", json=_action(), headers=_headers()).json()["event_id"]
    with store._transaction() as db:
        store._append(db, event_id, "model_suggestion_recorded", {"reason_code": "incomplete"}, "model")
    display_id = _display(store, event_id, "model_suggestion", ["incomplete"])
    endpoint = f"/v1/feedback-actions/{event_id}/attribution-events"
    confirmed = http.post(
        endpoint,
        json={
            "user_action": "reason_confirmed",
            "reason_code": "incomplete",
            "display_id": display_id,
            "explicit_submission": True,
        },
        headers=_headers(key="confirm"),
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["attribution_status"] == "confirmed"
    retracted = http.post(f"/v1/feedback-actions/{event_id}/retract", headers=_headers(key="retract"))
    assert retracted.json()["action_status"] == "retracted"
    assert retracted.json()["attribution_status"] == "none"
    assert retracted.json()["reason_code"] is None
    assert any(
        event["event_type"] == "reason_confirmed"
        for event in store.get_events(Principal("tenant-a", "alice"), event_id)
    )


def test_display_ack_is_idempotent_and_stale_displays_cannot_submit(client):
    http, store = client
    event_id = http.post("/v1/feedback-actions", json=_action(), headers=_headers()).json()["event_id"]
    principal = Principal("tenant-a", "alice")
    first = _display(store, event_id, "manual_menu", ["style"])
    assert store.record_display(principal, event_id, first, "manual_menu", ["style"], "ui-v1") == {
        "display_id": first,
        "receipt_status": "current",
        "actionable": True,
    }
    with pytest.raises(ConflictError):
        store.record_display(principal, event_id, first, "manual_menu", ["incomplete"], "ui-v1")
    second = _display(store, event_id, "manual_menu", ["style"])
    endpoint = f"/v1/feedback-actions/{event_id}/attribution-events"
    request = {"user_action": "reason_selected", "reason_code": "style", "explicit_submission": True}
    assert http.post(endpoint, json={**request, "display_id": first}, headers=_headers(key="stale")).status_code == 409
    assert (
        http.post(endpoint, json={**request, "display_id": second}, headers=_headers(key="current")).status_code == 200
    )
    assert [event["event_type"] for event in store.get_events(principal, event_id)].count("reason_displayed") == 2


def test_edit_requires_new_display_and_keeps_user_source(client):
    http, store = client
    event_id = http.post("/v1/feedback-actions", json=_action(), headers=_headers()).json()["event_id"]
    endpoint = f"/v1/feedback-actions/{event_id}/attribution-events"
    manual = _display(store, event_id, "manual_menu", ["style"])
    assert (
        http.post(
            endpoint,
            json={
                "user_action": "reason_selected",
                "reason_code": "style",
                "display_id": manual,
                "explicit_submission": True,
            },
            headers=_headers(key="first"),
        ).status_code
        == 200
    )
    edit = _display(store, event_id, "edit_menu", ["incomplete"])
    response = http.post(
        endpoint,
        json={
            "user_action": "reason_edited",
            "reason_code": "incomplete",
            "display_id": edit,
            "explicit_submission": True,
        },
        headers=_headers(key="edit"),
    )
    assert response.status_code == 200
    assert response.json()["attribution_status"] == "edited"
    assert response.json()["attribution_source"] == "user_edited"
    assert response.json()["reason_code"] == "incomplete"


def test_edit_menu_rejects_decline_of_unconfirmed_suggestion(client):
    http, store = client
    principal = Principal("tenant-a", "alice")
    event_id = http.post("/v1/feedback-actions", json=_action(), headers=_headers()).json()["event_id"]
    with store._transaction() as db:
        store._append(db, event_id, "model_suggestion_recorded", {"reason_code": "incomplete"}, "model")
    display_id = _display(store, event_id, "edit_menu", ["style", "incomplete"])
    endpoint = f"/v1/feedback-actions/{event_id}/attribution-events"
    declined = http.post(
        endpoint,
        json={"user_action": "reason_declined", "display_id": display_id, "explicit_submission": True},
        headers=_headers(key="decline-edit"),
    )
    assert declined.status_code == 409
    assert not any(event["event_type"] == "reason_declined" for event in store.get_events(principal, event_id))
    assert store.get_action(principal, event_id)["attribution_status"] == "model_inferred_unconfirmed"
    skipped = http.post(
        endpoint,
        json={"user_action": "reason_skipped", "display_id": display_id, "explicit_submission": True},
        headers=_headers(key="skip-edit"),
    )
    assert skipped.status_code == 200
    assert skipped.json()["attribution_status"] == "skipped"


def test_retract_race_with_selection_has_consistent_projection(client):
    http, store = client
    event_id = http.post("/v1/feedback-actions", json=_action(), headers=_headers()).json()["event_id"]
    display_id = _display(store, event_id, "manual_menu", ["style"])
    principal = Principal("tenant-a", "alice")
    barrier = Barrier(2)

    def select():
        barrier.wait()
        try:
            store.record_user_action(principal, event_id, "reason_selected", "style", display_id, True, "race-select")
        except ConflictError:
            pass

    def retract():
        barrier.wait()
        store.retract_action(principal, event_id, "race-retract")

    with ThreadPoolExecutor(max_workers=2) as pool:
        selection = pool.submit(select)
        retraction = pool.submit(retract)
        selection.result()
        retraction.result()
    state = store.get_action(principal, event_id)
    assert state["action_status"] == "retracted"
    assert state["attribution_status"] == "none"
    assert state["reason_code"] is None
    events = store.get_events(principal, event_id)
    assert [event["event_type"] for event in events].count("action_retracted") == 1
    assert [event["event_type"] for event in events].count("reason_selected") <= 1


@pytest.mark.parametrize(
    "timestamp",
    ["2026-09-24T06:00:00", "2026-09-24 06:00:00Z", 1790230000, "2026-09-24T06:00:00+25:00"],
)
def test_client_time_requires_rfc3339_timezone(client, timestamp):
    http, _ = client
    body = _action()
    body["client_occurred_at"] = timestamp
    assert http.post("/v1/feedback-actions", json=body, headers=_headers()).status_code == 422


def test_client_time_is_normalized_to_utc_in_event(client):
    http, store = client
    body = _action()
    body["client_occurred_at"] = "2026-09-24T14:00:00+08:00"
    event_id = http.post("/v1/feedback-actions", json=body, headers=_headers()).json()["event_id"]
    event = store.get_events(Principal("tenant-a", "alice"), event_id)[0]
    assert event["payload"]["client_occurred_at"] == "2026-09-24T06:00:00Z"
    assert event["event_id"] == event_id
    assert event["occurred_at"].endswith("Z")
    assert event["recorded_at"].endswith("Z")


def test_legacy_attribution_events_replay_without_display():
    events = [
        {"event_type": "negative_feedback_action_recorded", "payload": {"target_ref": {"object_id": "old"}}},
        {"event_type": "reason_selected", "payload": {"reason_code": "style"}},
    ]
    assert project(events)["attribution_source"] == "user_selected"
    events.append({"event_type": "action_retracted", "payload": {}})
    state = project(events)
    assert state["action_status"] == "retracted"
    assert state["attribution_status"] == "none"
    assert state["reason_code"] is None


@pytest.mark.parametrize(
    "late_event", ["reason_declined", "reason_skipped", "reason_unresponded", "attribution_invalidated"]
)
def test_legacy_nonresponse_cannot_override_submitted_reason(late_event):
    events = [
        {"event_type": "negative_feedback_action_recorded", "payload": {"target_ref": {"object_id": "old"}}},
        {"event_type": "reason_selected", "payload": {"reason_code": "style"}},
        {"event_type": late_event, "payload": {}},
    ]
    state = project(events)
    assert state["projection_version"] == "3"
    assert state["attribution_status"] == "selected"
    assert state["attribution_source"] == "user_selected"
    assert state["reason_code"] == "style"
    events.append({"event_type": "reason_edited", "payload": {"reason_code": "incomplete"}})
    edited = project(events)
    assert edited["attribution_status"] == "edited"
    assert edited["reason_code"] == "incomplete"


def test_existing_sqlite_events_replay_after_reopen(tmp_path):
    path = tmp_path / "existing.db"
    principal = Principal("tenant-a", "alice")
    store = EventStore(path)
    event_id = store.create_action(
        principal,
        {"object_type": "assistant_response", "object_id": "old", "object_version": "v1"},
        {"channel": "chat", "locale": "zh-CN", "action_type": "negative_feedback"},
        "old-action",
    )["event_id"]
    with store._transaction() as db:
        store._append(db, event_id, "reason_selected", {"reason_code": "style"}, "user")
        store._append(db, event_id, "reason_declined", {}, "user")
    reopened = EventStore(path)
    replayed = reopened.get_action(principal, event_id)
    assert replayed["projection_version"] == "3"
    assert replayed["attribution_status"] == "selected"
    assert replayed["attribution_source"] == "user_selected"
    assert replayed["reason_code"] == "style"
    state = reopened.retract_action(principal, event_id, "old-retract")
    assert state["attribution_status"] == "none"
    assert state["reason_code"] is None
    assert any(event["event_type"] == "reason_selected" for event in reopened.get_events(principal, event_id))
    assert any(event["event_type"] == "reason_declined" for event in reopened.get_events(principal, event_id))


def test_jev_contract_rejects_unpinned_or_invalid_distribution():
    codes = sorted(REASON_CODES)
    probabilities = {code: 0.0 for code in codes}
    probabilities["incomplete"] = 1.0
    response = {
        "model": "jev-pinned",
        "answers": {
            "primary_reason": {
                "type": "choice",
                "choice": "incomplete",
                "confidence": 0.9,
                "probabilities": probabilities,
            },
            "factual_error_signal": {"type": "noul", "noul": 0.1},
        },
        "usage": {"input_tokens": 30, "output_tokens": 5},
    }
    assert validate_jev_response(response, "jev-pinned")["primary_reason"] == "incomplete"
    with pytest.raises(ValueError, match="model"):
        validate_jev_response(response, "another-version")
    response["answers"]["primary_reason"]["probabilities"]["incomplete"] = float("nan")
    with pytest.raises(ValueError, match="probability"):
        validate_jev_response(response, "jev-pinned")


def test_unconfigured_http_boundary_fails_closed(tmp_path):
    app = create_app(tmp_path / "closed.db")
    with TestClient(app) as http:
        assert http.post("/v1/feedback-actions", json=_action(), headers=_headers()).status_code == 503
