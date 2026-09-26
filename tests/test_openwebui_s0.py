import asyncio
import copy
import json
import runpy
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from whynote.domain import Principal

ACTION_PATH = Path(__file__).parents[1] / "integrations" / "openwebui" / "s0_action.py"
PIPE_PATH = Path(__file__).parents[1] / "integrations" / "openwebui" / "s0_pipe.py"
FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "openwebui-s0-v1.json"


def s0_action(monkeypatch, tmp_path):
    fixture_copy = tmp_path / "fixture.json"
    fixture_copy.write_bytes(FIXTURE_PATH.read_bytes())
    monkeypatch.setenv("WHYNOTE_S0_TENANT", "isolated-test-instance")
    monkeypatch.setenv("WHYNOTE_S0_VERSION_KEY", "synthetic-only-key-with-at-least-32-chars")
    monkeypatch.setenv("WHYNOTE_S0_FIXTURE", str(fixture_copy))
    monkeypatch.setenv("WHYNOTE_S0_DB", str(tmp_path / "events.db"))
    return runpy.run_path(str(ACTION_PATH))["Action"]()


def s0_chat():
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    chat_id, prompt_id, response_id = (str(uuid.uuid4()) for _ in range(3))
    chat = SimpleNamespace(
        chat={
            "history": {
                "messages": {
                    prompt_id: {"id": prompt_id, "role": "user", "content": fixture["prompt"]},
                    response_id: {
                        "id": response_id,
                        "role": "assistant",
                        "content": fixture["response"],
                        "parentId": prompt_id,
                        "model": "whynote_s0_pipe",
                        "timestamp": 1,
                    },
                }
            }
        }
    )
    body = {
        "chat_id": chat_id,
        "id": response_id,
        "model": "whynote_s0_pipe",
        "messages": [
            {"id": prompt_id, "role": "user", "content": fixture["prompt"]},
            {"id": response_id, "role": "assistant", "content": fixture["response"]},
        ],
        "session_id": "browser-session-a",
    }
    return chat, body, fixture


def selected_value(menu, label):
    return next(option["value"] for option in menu["data"]["input"]["options"] if option["label"] == label)


def test_openwebui_s0_action_select_edit_and_keep_content_out_of_events(monkeypatch, tmp_path):
    action = s0_action(monkeypatch, tmp_path)
    chat, body, fixture = s0_chat()

    async def owned(chat_id, user_id):
        return chat if chat_id == body["chat_id"] and user_id == "alice" else None

    async def selected(menu):
        options = menu["data"]["input"]["options"]
        assert [option["label"] for option in options] == ["事实错误", "内容不相关", "表达方式"]
        assert all(option["value"].startswith("s0t1.") for option in options)
        assert all(fixture["prompt"] not in option["value"] for option in options)
        return selected_value(menu, "事实错误")

    async def edited(menu):
        return selected_value(menu, "内容不相关")

    notifications = []

    async def emit(event):
        notifications.append(event)

    action._owned_chat = owned
    first = asyncio.run(action.action(body, __user__={"id": "alice"}, __event_call__=selected, __event_emitter__=emit))
    second = asyncio.run(action.action(body, __user__={"id": "alice"}, __event_call__=edited))
    assert first["event_id"] == second["event_id"]
    assert first["attribution_status"] == "selected"
    assert second["attribution_status"] == "edited"
    assert notifications == [
        {"type": "notification", "data": {"type": "success", "content": "知因 S0 反馈与原因已记录"}}
    ]
    principal = Principal("isolated-test-instance", "alice")
    events = action.store.get_events(principal, first["event_id"])
    assert [event["event_type"] for event in events] == [
        "negative_feedback_action_recorded",
        "reason_displayed",
        "reason_selected",
        "reason_displayed",
        "reason_edited",
    ]
    assert (
        action.store.record_display(
            principal,
            first["event_id"],
            first["display"]["display_id"],
            "manual_menu",
            ["factual_error", "irrelevant", "style"],
            "openwebui-s0-select-v1",
        )["receipt_status"]
        == "historical"
    )
    stored = (tmp_path / "events.db").read_bytes()
    assert fixture["prompt"].encode() not in stored
    assert fixture["response"].encode() not in stored
    assert b"WHYNOTE_S0_PROMPT" not in stored
    assert b"WHYNOTE_S0_RESPONSE" not in stored


def test_openwebui_s0_display_ticket_rejects_tamper_and_replay(monkeypatch, tmp_path):
    action = s0_action(monkeypatch, tmp_path)
    chat, body, _ = s0_chat()

    async def owner(chat_id, user_id):
        return chat if chat_id == body["chat_id"] and user_id == "alice" else None

    action._owned_chat = owner
    captured = None

    async def capture(menu):
        nonlocal captured
        captured = selected_value(menu, "事实错误")
        return False

    first = asyncio.run(action.action(body, __user__={"id": "alice"}, __event_call__=capture))
    assert first["result"] == "no_reason_submitted"
    assert captured is not None

    async def tampered(_):
        return captured[:-1] + ("0" if captured[-1] != "0" else "1")

    with pytest.raises(ValueError, match="menu response is invalid"):
        asyncio.run(action.action(body, __user__={"id": "alice"}, __event_call__=tampered))

    second_session = {**body, "session_id": "browser-session-b"}

    async def replay(_):
        return captured

    with pytest.raises(ValueError, match="menu response is invalid"):
        asyncio.run(action.action(second_session, __user__={"id": "alice"}, __event_call__=replay))

    principal = Principal("isolated-test-instance", "alice")
    assert [event["event_type"] for event in action.store.get_events(principal, first["event_id"])] == [
        "negative_feedback_action_recorded",
        "reason_displayed",
    ]
    assert b"s0t1." not in (tmp_path / "events.db").read_bytes()


def test_openwebui_s0_action_rejects_other_user_and_changed_version(monkeypatch, tmp_path):
    action = s0_action(monkeypatch, tmp_path)
    chat, body, _ = s0_chat()
    permitted = True

    async def owned(chat_id, user_id):
        return chat if permitted and chat_id == body["chat_id"] and user_id == "alice" else None

    async def revoked(menu):
        nonlocal permitted
        permitted = False
        return selected_value(menu, "事实错误")

    action._owned_chat = owned
    with pytest.raises(ValueError, match="unavailable"):
        asyncio.run(action.action(body, __user__={"id": "bob"}, __event_call__=revoked))
    stale_body = copy.deepcopy(body)
    stale_body["messages"][-1]["content"] = "stale browser message"
    with pytest.raises(ValueError, match="displayed version"):
        asyncio.run(action.action(stale_body, __user__={"id": "alice"}, __event_call__=revoked))
    assert action.store.next_gate_message() is None
    with pytest.raises(ValueError, match="unavailable"):
        asyncio.run(action.action(body, __user__={"id": "alice"}, __event_call__=revoked))
    principal = Principal("isolated-test-instance", "alice")
    message = action.store.next_gate_message()
    assert message is not None
    assert [event["event_type"] for event in action.store.get_events(principal, message["event_id"])] == [
        "negative_feedback_action_recorded"
    ]
    permitted = True

    async def changed(menu):
        chat.chat["history"]["messages"][body["id"]]["content"] = "changed while menu was open"
        return selected_value(menu, "事实错误")

    with pytest.raises(ValueError, match="displayed version"):
        asyncio.run(action.action(body, __user__={"id": "alice"}, __event_call__=changed))
    assert [event["event_type"] for event in action.store.get_events(principal, message["event_id"])] == [
        "negative_feedback_action_recorded"
    ]


def test_openwebui_s0_action_expired_ticket_has_no_display(monkeypatch, tmp_path):
    action = s0_action(monkeypatch, tmp_path)
    chat, body, _ = s0_chat()

    async def owned(chat_id, user_id):
        return chat if chat_id == body["chat_id"] and user_id == "alice" else None

    async def selected(menu):
        return selected_value(menu, "事实错误")

    action._owned_chat = owned
    action.action.__func__.__globals__["TICKET_SECONDS"] = -1
    result = asyncio.run(action.action(body, __user__={"id": "alice"}, __event_call__=selected))
    assert result["result"] == "ticket_expired"
    events = action.store.get_events(Principal("isolated-test-instance", "alice"), result["event_id"])
    assert [event["event_type"] for event in events] == ["negative_feedback_action_recorded"]


def test_openwebui_s0_rechecks_permission_before_reason_write(monkeypatch, tmp_path):
    action = s0_action(monkeypatch, tmp_path)
    chat, body, _ = s0_chat()
    permitted = True

    async def owned(chat_id, user_id):
        return chat if permitted and chat_id == body["chat_id"] and user_id == "alice" else None

    async def selected(menu):
        return selected_value(menu, "事实错误")

    original_record_display = action.store.record_display

    def revoke_after_display(*args):
        nonlocal permitted
        receipt = original_record_display(*args)
        permitted = False
        return receipt

    action._owned_chat = owned
    action.store.record_display = revoke_after_display
    with pytest.raises(ValueError, match="unavailable"):
        asyncio.run(action.action(body, __user__={"id": "alice"}, __event_call__=selected))
    message = action.store.next_gate_message()
    events = action.store.get_events(Principal("isolated-test-instance", "alice"), message["event_id"])
    assert [event["event_type"] for event in events] == ["negative_feedback_action_recorded", "reason_displayed"]


def test_openwebui_s0_cancel_is_not_decline_and_disconnection_is_not_display(monkeypatch, tmp_path):
    action = s0_action(monkeypatch, tmp_path)
    chat, body, _ = s0_chat()

    async def owned(chat_id, user_id):
        return chat if chat_id == body["chat_id"] and user_id == "alice" else None

    async def cancelled(_):
        return False

    async def disconnected(_):
        return {"error": "Client session disconnected."}

    action._owned_chat = owned
    first = asyncio.run(action.action(body, __user__={"id": "alice"}, __event_call__=cancelled))
    second = asyncio.run(action.action(body, __user__={"id": "alice"}, __event_call__=disconnected))
    assert first["event_id"] == second["event_id"]
    assert first["result"] == "no_reason_submitted"
    assert second["result"] == "client_unavailable"
    events = action.store.get_events(Principal("isolated-test-instance", "alice"), first["event_id"])
    assert [event["event_type"] for event in events] == ["negative_feedback_action_recorded", "reason_displayed"]


def test_openwebui_s0_pipe_only_answers_fixture(monkeypatch, tmp_path):
    fixture_copy = tmp_path / "fixture.json"
    fixture_copy.write_bytes(FIXTURE_PATH.read_bytes())
    monkeypatch.setenv("WHYNOTE_S0_FIXTURE", str(fixture_copy))
    pipe = runpy.run_path(str(PIPE_PATH))["Pipe"]()
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert asyncio.run(pipe.pipe({"messages": [{"role": "user", "content": fixture["prompt"]}]})) == fixture["response"]
    assert asyncio.run(pipe.pipe({"messages": [{"role": "user", "content": "real text"}]})) == (
        "S0 只接受固定虚构问题。"
    )
