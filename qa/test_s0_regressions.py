"""Explicit acceptance probes: run `uv run pytest qa/test_s0_regressions.py -q -s`.

These assert the required behavior, so unresolved Q-11/Q-15 fail rather than xfail.
Only host ownership lookup and the menu callback are simulated; EventStore is real.
"""

import asyncio
import json
import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest

from whynote.domain import Principal
from whynote.store import EventStore

HELPERS = runpy.run_path(str(Path(__file__).parents[1] / "tests/test_openwebui_s0.py"))


def setup_action(monkeypatch, tmp_path):
    action = HELPERS["s0_action"](monkeypatch, tmp_path)
    chat, body, _ = HELPERS["s0_chat"]()

    async def owned(chat_id, user_id):
        return chat if chat_id == body["chat_id"] and user_id == "alice" else None

    action._owned_chat = owned
    return action, body


def evidence(action, result):
    principal = Principal("isolated-test-instance", "alice")
    events = action.store.get_events(principal, result["event_id"])
    reopened = EventStore(action.store.path)
    assert reopened.get_events(principal, result["event_id"]) == events
    assert reopened.get_action(principal, result["event_id"]) == action.store.get_action(principal, result["event_id"])
    print(json.dumps({"result": result, "events": events}, ensure_ascii=False))
    return events


@pytest.mark.parametrize("elapsed", [0, 59.0, 59.5, 59.999, 60.001])
def test_full_ticket_lifetime(monkeypatch, tmp_path, elapsed):
    action, body = setup_action(monkeypatch, tmp_path)
    clock = {"wall": 1000.9, "mono": 2000.0}
    # Replace only this Action module's clock; leave asyncio's scheduler untouched.
    action.action.__func__.__globals__["time"] = SimpleNamespace(
        time=lambda: clock["wall"], monotonic=lambda: clock["mono"]
    )

    async def selected(menu):
        clock.update(wall=1000.9 + elapsed, mono=2000.0 + elapsed)
        return HELPERS["selected_value"](menu, "事实错误")

    result = asyncio.run(action.action(body, __user__={"id": "alice"}, __event_call__=selected))
    events = evidence(action, result)
    if elapsed < 60:
        assert result["result"] == "reason_submitted"
        assert events[-1]["event_type"] == "reason_selected"
    else:
        assert result["result"] == "ticket_expired"
        assert [event["event_type"] for event in events] == ["negative_feedback_action_recorded"]


@pytest.mark.parametrize("new_reply", ["cancel", "select"])
@pytest.mark.parametrize("separate_instance", [False, True])
def test_new_menu_supersedes_pending_callback(monkeypatch, tmp_path, new_reply, separate_instance):
    action, body = setup_action(monkeypatch, tmp_path)
    newer, _ = setup_action(monkeypatch, tmp_path) if separate_instance else (action, body)
    newer._owned_chat = action._owned_chat

    async def scenario():
        opened = asyncio.Event()
        release = asyncio.Event()

        async def old(menu):
            opened.set()
            await release.wait()
            return HELPERS["selected_value"](menu, "事实错误")

        async def new(menu):
            return False if new_reply == "cancel" else HELPERS["selected_value"](menu, "内容不相关")

        pending = asyncio.create_task(action.action(body, __user__={"id": "alice"}, __event_call__=old))
        await opened.wait()
        current = await newer.action(body, __user__={"id": "alice"}, __event_call__=new)
        assert current["result"] == ("no_reason_submitted" if new_reply == "cancel" else "reason_submitted")
        before = action.store.get_events(Principal("isolated-test-instance", "alice"), current["event_id"])
        release.set()
        try:
            stale = await pending
        except ValueError as error:
            stale = {"event_id": current["event_id"], "rejected": type(error).__name__}
        after = evidence(action, stale)
        assert current["event_id"] == stale["event_id"]
        assert after == before, "Superseded callback must not append a display or reason"

    asyncio.run(scenario())
