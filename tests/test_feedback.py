import sqlite3

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from whynote.api import create_app
from whynote.domain import GateSignals, Principal, REASON_CODES, decide_gate, validate_jev_response
from whynote.store import process_one_gate


@pytest.fixture
def client(tmp_path):
    def authenticate(request):
        token = request.headers.get("Authorization")
        if token == "Bearer alice":
            return Principal("tenant-a", "alice")
        if token == "Bearer bob":
            return Principal("tenant-a", "bob")
        raise HTTPException(401, "missing identity")

    app = create_app(tmp_path / "feedback.db", authenticate, lambda principal, target: target["object_id"] == "answer-1")
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
    invalidate = http.post(
        f"/v1/feedback-actions/{event_id}/attribution-events",
        json={"user_action": "attribution_invalidated", "explicit_submission": True},
        headers=_headers(key="invalidate"),
    )
    assert invalidate.status_code == 200
    assert invalidate.json()["action_status"] == "active"
    assert invalidate.json()["attribution_status"] == "invalidated"
    assert invalidate.json()["reason_code"] is None

    retract = http.post(f"/v1/feedback-actions/{event_id}/retract", headers=_headers(key="retract"))
    assert retract.status_code == 200
    assert retract.json()["action_status"] == "retracted"
    again = http.post(f"/v1/feedback-actions/{event_id}/retract", headers=_headers(key="retract"))
    assert again.status_code == 200
    kinds = [event["event_type"] for event in store.get_events(Principal("tenant-a", "alice"), event_id)]
    assert kinds.count("attribution_invalidated") == 1
    assert kinds.count("action_retracted") == 1


def test_only_explicit_user_action_can_become_confirmed(client):
    http, store = client
    event_id = http.post("/v1/feedback-actions", json=_action(), headers=_headers()).json()["event_id"]
    endpoint = f"/v1/feedback-actions/{event_id}/attribution-events"
    confirmation = {"user_action": "reason_confirmed", "reason_code": "incomplete", "explicit_submission": True}
    assert http.post(endpoint, json=confirmation, headers=_headers(key="confirm-1")).status_code == 409
    with store._transaction() as db:
        store._append(db, event_id, "model_suggestion_recorded", {"reason_code": "incomplete"}, "model")
    assert http.get(f"/v1/feedback-actions/{event_id}", headers=_headers()).json()["attribution_status"] == "model_inferred_unconfirmed"
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
    selected = http.post(
        endpoint,
        json={"user_action": "reason_selected", "reason_code": "style", "explicit_submission": True},
        headers=_headers(key="select"),
    )
    assert selected.status_code == 200
    with store._transaction() as db:
        store._append(db, event_id, "model_suggestion_recorded", {"reason_code": "incomplete"}, "model")
    current = http.get(f"/v1/feedback-actions/{event_id}", headers=_headers()).json()
    assert current["reason_code"] == "style"
    assert current["attribution_source"] == "user_selected"


def test_jev_contract_rejects_unpinned_or_invalid_distribution():
    codes = sorted(REASON_CODES)
    probabilities = {code: 0.0 for code in codes}
    probabilities["incomplete"] = 1.0
    response = {
        "model": "jev-pinned",
        "answers": {
            "primary_reason": {"type": "choice", "choice": "incomplete", "confidence": 0.9, "probabilities": probabilities},
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
