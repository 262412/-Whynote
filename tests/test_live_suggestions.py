"""Synthetic host integration for real-model provenance and fail-closed admission."""

import asyncio
import copy
import json
from pathlib import Path

import pytest
from test_replay import Backend
from test_suggestions import trial as trial
from test_template_suggestions import client, invoke
from test_template_suggestions import host as host

from whynote import live_suggestions as live
from whynote import suggestions
from whynote.domain import project
from whynote.research import validate_config


def result(**changes):
    return {
        "version": live.VERSION,
        "model": live.MODEL,
        "route": "general",
        "fallback_used": False,
        "outcome": "suggested",
        "reason_ids": ["general.style"],
        **changes,
    }


@pytest.fixture
def enabled(host):
    f = host
    target = f.action._target(f.chat_record, f.template_answer.body)
    f.config.update(
        suggestion_backend="laya_local",
        suggestion_model_revision=live.REVISION,
        suggestion_python=str(Path(__file__).resolve()),
        suggestion_model_dir=str(Path(__file__).parent.resolve()),
        suggestion_synthetic_targets={target["object_id"]: target["object_version"]},
    )
    f.config_path.write_text(json.dumps(f.config), encoding="utf-8")
    return f


def stub(monkeypatch, f, *, reply=None, error=None, change=None):
    calls = []

    async def evaluate(config, question, answer):
        event_id = f.store.current_action(f.principal, f.action._target(f.chat_record, f.template_answer.body))[
            "event_id"
        ]
        current = f.store.get_events(f.principal, event_id)
        assert project(current)["action_status"] == "active"
        assert not any(e["event_type"] == "m52_suggestion_generated" for e in current)
        calls.append((question, answer))
        if change:
            change(event_id)
        if error:
            raise live.ReplayError(error)
        return result() if reply is None else reply

    monkeypatch.setattr(live, "evaluate", evaluate)
    return calls


def test_live_confirmation_provenance_and_same_click_replay(enabled, monkeypatch):
    f = enabled
    f.config["suggestion_fixture"] = {"invalid": "must not be read"}
    f.config_path.write_text(json.dumps(f.config), encoding="utf-8")
    calls = stub(monkeypatch, f)
    callback = client(f, [("yes", "general.style"), ("done", None)])
    first = invoke(f, callback)
    records = f.store.get_events(f.principal, first["event_id"])
    generated = next(e for e in records if e["event_type"] == "m52_suggestion_generated")
    assert generated["source"] == "model_inferred_unconfirmed"
    assert generated["payload"]["binding"]["model_version"] == live.MODEL
    assert f.display["cards"][0]["quotes"] == []
    assert suggestions.project_suggestions(records)["reason_id"] == "general.style"
    second = invoke(f, callback)
    assert second["replayed"] is True and len(calls) == 1
    assert f.store.get_events(f.principal, first["event_id"]) == records


@pytest.mark.parametrize("outcome", ["unknown", "no_match"])
def test_live_abstention_falls_back_without_confirmation(enabled, monkeypatch, outcome):
    f = enabled
    stub(monkeypatch, f, reply=result(outcome=outcome, reason_ids=[]))
    actual = invoke(f, client(f, []))
    records = f.store.get_events(f.principal, actual["event_id"])
    assert actual["suggestion"]["result"] == "abstained"
    assert not any(e["event_type"] in {"m52_render_reported", "m52_response_recorded"} for e in records)
    assert project(records)["action_status"] == "active"


@pytest.mark.parametrize("code", ["timeout", "backend_unavailable", "input_tokens_exceeded", "invalid_response"])
def test_live_failure_preserves_action_and_menu(enabled, monkeypatch, code):
    f = enabled
    stub(monkeypatch, f, error=code)
    actual = invoke(f, client(f, []))
    assert actual["suggestion"]["failure_code"] == code
    assert "input" in f.client_events
    records = f.store.get_events(f.principal, actual["event_id"])
    assert project(records)["action_status"] == "active"
    assert not any(e["event_type"] == "m52_suggestion_generated" for e in records)


@pytest.mark.parametrize("boundary", ["retract", "revoke", "replace", "stop"])
def test_inflight_model_result_rechecks_host(enabled, monkeypatch, boundary):
    f = enabled

    def change(event_id):
        if boundary == "retract":
            f.store.retract_action(f.principal, event_id, "undo")
        elif boundary == "revoke":
            f.store.invalidate(f.chat_record.id, revoke=True)
        elif boundary == "replace":
            f.chat_record.chat["history"]["messages"][f.template_answer.message_id]["content"] = "Changed"
        else:
            f.config["suggestion_backend"] = "fixture"
            f.config_path.write_text(json.dumps(f.config), encoding="utf-8")

    stub(monkeypatch, f, change=change)
    actual = invoke(f, client(f, []))
    assert actual["suggestion"]["result"] == "superseded"
    records = f.store.get_events(f.principal, actual["event_id"])
    assert not any(e["event_type"] == "m52_suggestion_generated" for e in records)


def test_unlisted_target_does_not_call_model(enabled, monkeypatch):
    f = enabled
    f.config["suggestion_synthetic_targets"] = {"another/target": "another-version"}
    f.config_path.write_text(json.dumps(f.config), encoding="utf-8")
    calls = stub(monkeypatch, f)
    actual = invoke(f, client(f, []))
    assert actual["suggestion"]["result"] == "superseded" and calls == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("mode", "cloud"),
        ("suggestion_model_revision", "b" * 40),
        ("suggestion_backend", "cloud"),
        ("suggestion_synthetic_targets", {}),
        ("suggestion_python", "relative"),
        ("suggestion_template_enabled", False),
    ],
)
def test_live_configuration_rejects_widening(enabled, field, value):
    config = {**enabled.config, field: value}
    with pytest.raises(ValueError):
        validate_config(config)


@pytest.mark.parametrize(
    "change",
    [
        {"model": "foreign"},
        {"route": "invented"},
        {"outcome": "unknown"},
        {"reason_ids": ["code.interface_changed"]},
        {"citations": {}},
        {"reason_ids": ["general.style", "general.instruction_not_followed"]},
    ],
)
def test_invalid_prediction_cannot_create_presentation(change):
    with pytest.raises(ValueError):
        live.presentation("request without code", "answer", "v1", result(**change))


def test_actual_c_scheme_uses_evidence_and_fallback():
    backend = Backend(route="general", positive="code.interface_changed")
    backend.metadata = {"model": live.MODEL, "backend": "test_stub"}
    question = "Rewrite preserving interface.\n```python\ndef f():\n    return 1\n```"
    reply = live.infer(backend, question, "def g(): return 1")
    assert reply["fallback_used"] is True and reply["reason_ids"] == ["code.interface_changed"]
    live.presentation(question, "def g(): return 1", "v1", reply)
    missing = live.infer(backend, "Rewrite missing code", "def g(): return 1")
    assert not missing["fallback_used"] and missing["reason_ids"] == []


def test_process_offline_and_oversize_input(enabled):
    config = copy.deepcopy(enabled.config)
    config["suggestion_python"] = str(Path(config["suggestion_python"]).parent / "absent-runtime.exe")
    with pytest.raises(live.ReplayError, match="backend_unavailable"):
        asyncio.run(live.evaluate(config, "question", "answer"))
    with pytest.raises(live.ReplayError, match="input_bytes_exceeded"):
        asyncio.run(live.evaluate(config, "界" * 2800, "answer"))


@pytest.mark.parametrize("cancel", [False, True])
def test_process_timeout_or_cancellation_reaps_worker(enabled, monkeypatch, cancel):
    class Process:
        returncode = None
        killed = False
        waited = False

        async def communicate(self, payload):
            assert payload == b'{"request":"question","answer":"answer"}'
            if cancel:
                raise asyncio.CancelledError
            raise TimeoutError

        def kill(self):
            self.killed = True

        async def wait(self):
            self.waited = True
            self.returncode = 1

    process = Process()

    async def spawn(*args, **kwargs):
        assert kwargs["stderr"] == asyncio.subprocess.DEVNULL
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(asyncio.CancelledError if cancel else live.ReplayError):
        asyncio.run(live.evaluate(enabled.config, "question", "answer"))
    assert process.killed and process.waited
