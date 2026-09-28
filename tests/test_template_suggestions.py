"""Synthetic host -> single action -> client callbacks -> ledger/report checks."""

import asyncio
import json
import sqlite3
import sys
from pathlib import Path

import pytest
from test_suggestions import events, generate, ref, render, respond
from test_suggestions import trial as trial

from whynote import suggestions as s
from whynote.domain import ConflictError, NotFoundError, project
from whynote.research import validate_config
from whynote.research_report import report
from whynote.template_suggestions import digest, prepare

sys.path.insert(0, str(Path(__file__).parents[1]))
from integrations.openwebui.local_chain_action import Action


def fixture_config(question="虚构研究问题"):
    return {
        "tasks": ["general"],
        "outcome": "suggested",
        "reason_ids": ["general.style", "general.instruction_not_followed"],
        "citations": {
            "general.style": [
                {"source": "request", "start": 0, "end": len(question), "source_sha256": digest(question)}
            ]
        },
    }


def test_exact_quotes_and_category_only():
    question = "不要修改签名\n```python\ndef f(x):\n    return x\n```"
    config = fixture_config(question)
    config.update(tasks=["code_rewrite"], reason_ids=["code.interface_changed", "general.style"])
    config["citations"]["code.interface_changed"] = config["citations"]["general.style"]
    del config["citations"]["general.style"]
    candidates, selection, cards, metadata = prepare(question, "新的回答", "v1", config)
    assert "original_code" in candidates.evidence_kinds
    assert cards[0]["quotes"][0]["text"] == question
    assert cards[0]["template"] and cards[1]["template"] is None
    assert question not in json.dumps(metadata, ensure_ascii=False)
    assert metadata["cards"][0]["references"][0]["object_version"] == "v1"
    assert selection["reason_ids"] == config["reason_ids"]


@pytest.mark.parametrize(
    "change",
    [
        {"source": "history"},
        {"start": -1},
        {"end": 9999},
        {"start": True},
        {"source_sha256": "0" * 64},
        {"text": "invented"},
    ],
)
def test_unreliable_quote_downgrades_to_category(change):
    config = fixture_config()
    config["citations"]["general.style"][0].update(change)
    _, _, cards, metadata = prepare("虚构研究问题", "回答", "v1", config)
    assert cards[0]["quotes"] == [] and cards[0]["template"] is None
    assert metadata["cards"][0]["references"] == []


def test_missing_original_code_cannot_select_code_reason():
    config = fixture_config()
    config.update(tasks=["code_rewrite"], reason_ids=["code.interface_changed"])
    with pytest.raises(ValueError, match="reason_not_selectable"):
        prepare("没有原代码", "回答", "v1", config)


@pytest.mark.parametrize(
    "flag,value",
    [
        ("research_enabled", False),
        ("suggestion_research_enabled", False),
        ("mode", "cloud"),
        ("suggestion_template_enabled", "true"),
    ],
)
def test_template_scope_cannot_bypass_mock(trial, flag, value):
    config = {**trial.config, "suggestion_template_enabled": True, flag: value}
    with pytest.raises(ValueError):
        validate_config(config)


@pytest.mark.parametrize("terminal", ["expired", "superseded", "invalidated"])
def test_old_generation_retry_is_rejected(trial, monkeypatch, terminal):
    generate(trial)
    if terminal == "expired":
        trial.clock += 61
    elif terminal == "superseded":
        generate(trial, "two", display="two-display")
    else:
        s.invalidate(trial.store, trial.principal, trial.event_id, ref("invalid"), ref("one"))
    before = events(trial)
    with pytest.raises(ConflictError):
        generate(trial)
    assert events(trial) == before


def test_retraction_clears_current_confirmation_pointer(trial):
    render(trial, generate(trial))
    confirmed = respond(trial)
    trial.store.retract_action(trial.principal, trial.event_id, "retract")
    state = s.project_suggestions(events(trial))
    assert state["response_id"] is None and state["last_response_id"] == confirmed


@pytest.fixture
def host(trial, monkeypatch):
    f = trial
    f.config.update(suggestion_template_enabled=True, suggestion_fixture=fixture_config())
    f.config_path.write_text(json.dumps(f.config), encoding="utf-8")
    monkeypatch.setenv("WHYNOTE_S1_CONFIG", str(f.config_path))
    monkeypatch.setenv("WHYNOTE_LOCAL_CHAIN", "1")
    monkeypatch.setenv("WHYNOTE_TEMPLATE_SYNTHETIC", "1")
    f.template_answer = f.answer(f.chat_record, "template-answer")
    f.action = Action()
    f.host_reads = 0
    f.notifications = []

    async def owned(chat_id, user_id):
        f.host_reads += 1
        return f.chats.get(chat_id) if user_id == f.principal.actor_ref else None

    async def emitter(event):
        f.notifications.append(event)

    f.action._owned_chat = owned
    f.emitter = emitter

    async def forbidden_model(*args):
        raise AssertionError("Synthetic template path called a real model")

    monkeypatch.setattr("integrations.openwebui.laya_action.local_suggestion", forbidden_model)
    return f


def invoke(f, callback, body=None):
    return asyncio.run(
        f.action.action(body or f.template_answer.body, {"id": f.principal.actor_ref}, f.emitter, callback)
    )


def client(f, operations, *, interrupt=None):
    sequence = iter(operations)
    f.client_events = []
    f.display = None

    async def callback(event):
        kind, data = event["type"], event["data"]
        f.client_events.append(kind)
        if kind == "whynote:suggestion-dismiss":
            return True
        if kind == "input":
            assert not any(e["type"] == "provider" for e in f.notifications)
            return False
        if kind == "whynote:suggestion-render":
            f.display = data
            f.template_event = data["binding"]["event_id"]
            current = f.store.get_events(f.principal, f.template_event)
            assert project(current)["action_status"] == "active"
            assert not any(e["event_type"] == "m52_render_reported" for e in current)
            with sqlite3.connect(f.store.path) as db:
                assert (
                    db.execute("SELECT count(*) FROM outbox WHERE event_id=?", (f.template_event,)).fetchone()[0] == 1
                )
            if interrupt:
                return await interrupt(event)
            return {"binding": data["binding"]}
        assert kind == "whynote:suggestion-response"
        operation, reason = next(sequence)
        f.clock += 1
        return {
            "operation": operation,
            "reason_id": reason,
            "timing": {
                "version": "active-v1",
                "display_id": data["display_id"],
                "session_ref": f.display["session_ref"],
                "active_ms": 100,
                "elapsed_ms": 500,
            },
        }

    return callback


def test_single_button_confirmation_correction_replay_and_retraction(host):
    f = host
    callback = client(f, [("yes", "general.style"), ("correct", "general.instruction_not_followed"), ("done", None)])
    result = invoke(f, callback)
    assert result["suggestion"]["result"] == "confirmed"
    event_list = f.store.get_events(f.principal, result["event_id"])
    assert s.project_suggestions(event_list)["reason_id"] == "general.instruction_not_followed"
    assert s.project_suggestions(event_list)["status"] == "corrected"
    assert project(event_list)["reason_code"] is None
    serialized = json.dumps(event_list, ensure_ascii=False)
    assert "虚构研究问题" not in serialized and "虚构研究回答" not in serialized
    stats = report(f.store.path, f.principal, "2026-09-01T00:00:00Z", f.now())["groups"]["scripted"]["suggestions"]
    assert (stats["generated"], stats["render_reported"], stats["confirmed"]) == (1, 1, 1)
    assert stats["active_ms"]["n"] == 2
    count = len(f.client_events)
    assert invoke(f, callback)["replayed"] is True
    assert len(f.client_events) == count
    retract = invoke(f, callback, {**f.template_answer.body, "whynote_click_id": ref("toggle-again")})
    assert retract["result"] == "retracted"
    assert s.project_suggestions(f.store.get_events(f.principal, result["event_id"]))["response_id"] is None


@pytest.mark.parametrize("operation", ["no", "none_matched", "manual", "skip", "decline", "close"])
def test_nonconfirmation_and_manual_fallback(host, operation):
    f = host
    callback = client(f, [(operation, "general.style" if operation == "no" else None)])
    result = invoke(f, callback)
    current = f.store.get_events(f.principal, result["event_id"])
    assert s.project_suggestions(current)["reason_id"] is None
    assert project(current)["action_status"] == "active"
    assert ("input" in f.client_events) == (operation in {"no", "none_matched", "manual"})
    assert sum(e["event_type"] == "negative_feedback_action_recorded" for e in current) == 1


@pytest.mark.parametrize("boundary", ["retract", "disable", "replace", "revoke", "late", "bad_binding", "disconnect"])
def test_interrupted_display_never_confirms(host, boundary):
    f = host

    async def interrupt(event):
        binding = event["data"]["binding"]
        if boundary == "retract":
            f.store.retract_action(f.principal, binding["event_id"], "during-render")
        elif boundary == "disable":
            f.config["suggestion_template_enabled"] = False
            f.config_path.write_text(json.dumps(f.config), encoding="utf-8")
        elif boundary == "replace":
            f.chat_record.chat["history"]["messages"][f.template_answer.message_id]["content"] = "换版"
        elif boundary == "revoke":
            f.store.invalidate(f.chat_record.id, revoke=True)
        elif boundary == "late":
            f.clock += 61
        elif boundary == "bad_binding":
            return {"binding": {**binding, "display_id": ref("wrong")}}
        elif boundary == "disconnect":
            raise ConnectionError("synthetic socket closed")
        return {"binding": binding}

    result = invoke(f, client(f, [], interrupt=interrupt))
    current = f.store.get_events(f.principal, result["event_id"])
    assert not any(e["event_type"] in {"m52_render_reported", "m52_response_recorded"} for e in current)


def test_disabled_before_host_read(host):
    host.config["suggestion_template_enabled"] = False
    host.config_path.write_text(json.dumps(host.config), encoding="utf-8")
    with pytest.raises(NotFoundError):
        invoke(host, client(host, []))
    assert host.host_reads == 0


@pytest.mark.parametrize("outcome", ["unknown", "no_match", "invalid"])
def test_synthetic_abstention_or_failure_keeps_action_and_menu(host, outcome):
    host.config["suggestion_fixture"].update(outcome=outcome, reason_ids=[])
    host.config_path.write_text(json.dumps(host.config), encoding="utf-8")
    result = invoke(host, client(host, []))
    assert "input" in host.client_events
    assert project(host.store.get_events(host.principal, result["event_id"]))["action_status"] == "active"
    assert "whynote:suggestion-render" not in host.client_events


def test_on_time_response_survives_slow_host_recheck(host):
    f = host
    callback = client(f, [("yes", "general.style")])
    original_owned = f.action._owned_chat
    response_arrived = False

    async def receive(event):
        nonlocal response_arrived
        value = await callback(event)
        if event["type"] == "whynote:suggestion-response":
            response_arrived = True
        return value

    async def slow_owned(*args):
        nonlocal response_arrived
        if response_arrived:
            f.clock += 61
            response_arrived = False
        return await original_owned(*args)

    f.action._owned_chat = slow_owned
    result = invoke(f, receive)
    assert result["suggestion"]["result"] == "confirmed"
    current = f.store.get_events(f.principal, result["event_id"])
    expiry = next(e["payload"]["expires_at"] for e in current if e["event_type"] == "m52_suggestion_generated")
    response = next(e["payload"] for e in current if e["event_type"] == "m52_response_recorded")
    assert response["received_at"] < expiry < f.clock
    assert "input" not in f.client_events


@pytest.mark.parametrize("active", [-1, True, float("nan"), float("inf"), 2000])
def test_invalid_timing_is_missing_not_a_duration(host, active):
    f = host
    callback = client(f, [("yes", "general.style"), ("done", None)])

    async def receive(event):
        response = await callback(event)
        if event["type"] == "whynote:suggestion-response":
            response["timing"]["active_ms"] = active
        return response

    result = invoke(f, receive)
    current = f.store.get_events(f.principal, result["event_id"])
    measurements = [e["payload"]["measurement"] for e in current if e["event_type"] == "m52_response_recorded"]
    assert measurements[0]["active_ms"] is None
    assert measurements[0]["timing_status"] == "invalid_duration"


def test_raw_text_cannot_be_written_as_presentation(trial):
    trial.config["suggestion_template_enabled"] = True
    before = events(trial)
    with pytest.raises(ValueError, match="Invalid presentation"):
        s.generate(
            trial.store,
            trial.principal,
            trial.event_id,
            ref("raw-request"),
            ref("raw-suggestion"),
            trial.candidates,
            trial.selection,
            display_id=ref("raw-display"),
            presentation={"text": "raw question"},
        )
    assert events(trial) == before
