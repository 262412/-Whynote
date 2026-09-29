"""Q-30 backend identity changes at the real ledger boundary."""

import hashlib
import sqlite3
from pathlib import Path

import pytest
from test_live_suggestions import enabled as enabled
from test_suggestions import events, generate, render, respond, snapshot
from test_suggestions import trial as trial
from test_template_suggestions import host as host

from whynote import suggestions
from whynote.domain import ConflictError, project


@pytest.fixture
def ledger(enabled):
    f = enabled
    target = f.action._target(f.chat_record, f.answer_record.body)
    f.config["suggestion_synthetic_targets"] = {target["object_id"]: target["object_version"]}
    f.store.config = f.config
    return f


@pytest.mark.parametrize("callback", ["render", "respond"])
def test_fixture_receipts_rejected_after_enabling_live(ledger, callback):
    f = ledger
    f.config["suggestion_backend"] = "fixture"
    binding = generate(f)
    assert "model_source" not in binding
    if callback == "respond":
        render(f, binding)
    before = events(f)
    f.config["suggestion_backend"] = "laya_local"
    with pytest.raises(ConflictError, match="versions"):
        render(f, binding) if callback == "render" else respond(f)
    assert events(f) == before
    assert suggestions.project_suggestions(events(f))["reason_id"] is None


@pytest.mark.parametrize("backend", ["fixture", "laya_local"])
@pytest.mark.parametrize("callback", ["render", "respond"])
def test_receipt_retry_after_backend_switch_is_rejected_without_append(ledger, backend, callback):
    f = ledger
    f.config["suggestion_backend"] = backend
    binding = generate(f)
    render(f, binding)
    if callback == "respond":
        respond(f)
    before = events(f)
    projection = suggestions.project_suggestions(before)
    f.config["suggestion_backend"] = "fixture" if backend == "laya_local" else "laya_local"
    with pytest.raises(ConflictError, match="versions"):
        render(f, binding) if callback == "render" else respond(f)
    assert events(f) == before
    assert suggestions.project_suggestions(events(f)) == projection


@pytest.mark.parametrize("backend", ["fixture", "laya_local"])
def test_unchanged_backend_keeps_generation_render_and_response_idempotent(ledger, backend):
    f = ledger
    f.config["suggestion_backend"] = backend
    binding = generate(f)
    if backend == "fixture":
        # This is the existing fixture schema: do not backfill backend fields.
        assert not {"model_source", "model_version", "inference_version"} & binding.keys()
        del f.config["suggestion_backend"]
    assert generate(f) == binding
    first_render = render(f, binding)
    assert render(f, binding) == first_render
    first_response = respond(f)
    before = events(f)
    assert respond(f) == first_response and events(f) == before
    assert len(before) == 4


def test_rollback_new_fixture_preserves_history_outbox_and_read_only_report(ledger):
    f = ledger
    live_binding = generate(f)
    render(f, live_binding)
    first = respond(f)
    history = events(f)
    f.config["suggestion_backend"] = "fixture"
    assert suggestions.project_suggestions(events(f))["response_id"] == first
    fresh = generate(f, "fixture", display="fixture-display")
    assert "model_source" not in fresh
    render(f, fresh, "fixture-display")
    respond(
        f, name="fixture-yes", suggestion="fixture", display="fixture-display", reason="general.style", previous=first
    )
    records = events(f)
    assert records[: len(history)] == history
    assert [e["source"] for e in records if e["event_type"] == "m52_suggestion_generated"] == [
        "model_inferred_unconfirmed",
        "synthetic_model",
    ]
    assert project(records)["action_status"] == "active"
    assert suggestions.project_suggestions(records)["reason_id"] == "general.style"
    with sqlite3.connect(f.store.path) as db:
        assert db.execute("SELECT count(*) FROM outbox").fetchone()[0] == 1
    before = hashlib.sha256(Path(f.store.path).read_bytes()).hexdigest()
    report = snapshot(f)
    assert snapshot(f) == report and hashlib.sha256(Path(f.store.path).read_bytes()).hexdigest() == before
    assert report["groups"]["scripted"]["suggestions"]["generated"] == 2
