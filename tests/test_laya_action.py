import asyncio
from types import SimpleNamespace

import httpx
import pytest

from integrations.openwebui import laya_action
from whynote.domain import NotFoundError


def setup_action(monkeypatch):
    monkeypatch.setenv("WHYNOTE_LOCAL_CHAIN", "1")
    action = object.__new__(laya_action.Action)
    action.tenant = "test"
    chat = SimpleNamespace(
        chat={
            "history": {
                "messages": {
                    "answer": {"parentId": "question", "content": "synthetic answer"},
                    "question": {"content": "synthetic question"},
                }
            }
        }
    )
    target = {"object_version": "v1"}
    state = {"action_status": "active", "event_id": "one"}
    calls = []

    async def owned(chat_id, user_id):
        calls.append("owned")
        if user_id != "alice":
            raise NotFoundError()
        return chat

    async def suggest(question, answer):
        assert (question, answer) == ("synthetic question", "synthetic answer")
        calls.append("model")
        return "factual_error"

    action._owned_chat = owned
    action._target = lambda chat, body: dict(target)
    action._store_for_target = lambda principal, target: SimpleNamespace(current_action=lambda *args: dict(state))
    monkeypatch.setattr(laya_action, "local_suggestion", suggest)
    return action, target, state, calls


def execute(action, user=None):
    messages = []

    async def emit(message):
        messages.append(message)

    result = asyncio.run(action.action({"id": "answer", "chat_id": "chat"}, user, emit))
    return result, messages


@pytest.mark.parametrize("case", ["disabled", "unauthenticated", "other_user", "retracted"])
def test_gate_before_model(monkeypatch, case):
    action, _, state, calls = setup_action(monkeypatch)
    user = {"id": "alice"}
    if case == "disabled":
        monkeypatch.delenv("WHYNOTE_LOCAL_CHAIN")
    elif case == "unauthenticated":
        user = None
    elif case == "other_user":
        user = {"id": "bob"}
    else:
        state["action_status"] = "retracted"
    with pytest.raises(NotFoundError):
        execute(action, user)
    assert "model" not in calls
    if case in ("disabled", "unauthenticated"):
        assert calls == []


def test_readonly_suggestion_rechecks_and_labels_source(monkeypatch):
    action, _, state, calls = setup_action(monkeypatch)
    result, messages = execute(action, {"id": "alice"})
    assert result["result"] == "model_inferred_unconfirmed"
    assert "未确认" in messages[0]["data"]["content"]
    assert calls == ["owned", "model", "owned"]
    assert state == {"action_status": "active", "event_id": "one"}
    execute(action, {"id": "alice"})  # repeated explicit request has no feedback writer
    assert state["event_id"] == "one"


@pytest.mark.parametrize("change", ["version", "action", "retract", "disable"])
def test_discard_result_after_change(monkeypatch, change):
    action, target, state, _ = setup_action(monkeypatch)

    async def suggest(*args):
        if change == "version":
            target["object_version"] = "v2"
        elif change == "action":
            state["event_id"] = "two"
        elif change == "retract":
            state["action_status"] = "retracted"
        else:
            monkeypatch.delenv("WHYNOTE_LOCAL_CHAIN")
        return "factual_error"

    monkeypatch.setattr(laya_action, "local_suggestion", suggest)
    with pytest.raises(NotFoundError):
        execute(action, {"id": "alice"})


def test_model_failure_keeps_manual_feedback(monkeypatch):
    action, _, state, _ = setup_action(monkeypatch)

    async def fail(*args):
        raise httpx.ConnectError("private request body must not escape")

    monkeypatch.setattr(laya_action, "local_suggestion", fail)
    result, messages = execute(action, {"id": "alice"})
    assert result == {"result": "unavailable"}
    assert "private" not in str(messages)
    assert state["action_status"] == "active"


@pytest.mark.parametrize("case", ["ok", "null", "version", "busy", "redirect", "token"])
def test_loopback_protocol_and_no_retries(monkeypatch, case):
    calls = []

    def handle(request):
        calls.append(request)
        assert request.url.host == "127.0.0.1" and request.url.port == 8766
        assert "authorization" not in request.headers
        if request.method == "GET":
            return httpx.Response(200, text="none" if case == "token" else '<script nonce="synthetic-token">')
        assert request.headers["x-local-token"] == "synthetic-token"
        assert b'"feedback":""' in request.content
        if case == "busy":
            return httpx.Response(429)
        if case == "redirect":
            return httpx.Response(302, headers={"location": "https://example.invalid"})
        result = {
            "provider": "laya_local",
            "model_version": laya_action.MODEL,
            "prompt_version": laya_action.PROMPT_VERSION,
            "attribution_source": "model_inferred_unconfirmed",
            "calibrated": False,
            "primary_reason": "factual_error",
        }
        if case == "version":
            result["model_version"] = "other-model"
        return httpx.Response(200, json=None if case == "null" else result)

    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: client(transport=httpx.MockTransport(handle), **kw))
    if case == "ok":
        assert asyncio.run(laya_action.local_suggestion("question", "answer")) == "factual_error"
    else:
        with pytest.raises((ValueError, httpx.HTTPError)):
            asyncio.run(laya_action.local_suggestion("question", "answer"))
    assert len(calls) == (1 if case == "token" else 2)
