import asyncio
import importlib
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from whynote.domain import ConflictError, NotFoundError, Principal
from whynote.s1 import TrialStore
from whynote.store import EventStore

with pytest.MonkeyPatch.context() as imports:
    imports.syspath_prepend(str(Path(__file__).parents[1]))
    module = importlib.import_module("integrations.openwebui.local_chain_action")
    laya = importlib.import_module("integrations.openwebui.laya_action")

P = Principal("test", "alice")
TARGET = {"object_type": "message", "object_id": "chat/answer", "object_version": "v1"}
META = {"interaction_contract": "manual-v1"}


def test_atomic_toggle_replay_restart_and_conflict(tmp_path):
    store = EventStore(tmp_path / "events.db")
    first, fresh = store.toggle_action(P, TARGET, META, "one")
    assert fresh and first["action_status"] == "active"
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: store.toggle_action(P, TARGET, META, "two"), range(4)))
    assert sum(fresh for _, fresh in results) == 1
    assert all(state["action_status"] == "retracted" for state, _ in results)
    third, fresh = store.toggle_action(P, TARGET, META, "three")
    assert fresh and third["action_status"] == "active" and third["event_id"] != first["event_id"]
    assert EventStore(store.path).toggle_action(P, TARGET, META, "one")[1] is False
    with pytest.raises(ConflictError):
        store.toggle_action(P, {**TARGET, "object_version": "v2"}, META, "one")
    with store._transaction() as db:
        assert db.execute("SELECT count(*) FROM events").fetchone()[0] == 3
        assert db.execute("SELECT count(*) FROM outbox").fetchone()[0] == 3
    other, _ = store.toggle_action(Principal("test", "bob"), TARGET, META, "one")
    assert other["action_status"] == "active"
    assert store.get_action(P, third["event_id"])["action_status"] == "active"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("WHYNOTE_LOCAL_CHAIN", "1")
    action = object.__new__(module.Action)
    action.tenant = "test"
    store = EventStore(tmp_path / "events.db")
    store.current_action = lambda p, t: TrialStore.current_action(store, p, t)
    action._store_for_target = lambda p, t: store
    target = dict(TARGET)
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

    async def owned(chat_id, user_id):
        if user_id != "alice":
            raise NotFoundError()
        return chat

    action._owned_chat = owned
    action._target = lambda c, b: dict(target)
    messages, calls = [], []

    async def emit(message):
        messages.append(message)

    async def suggest(*args):
        calls.append(args)
        assert store.current_action(P, target)["action_status"] == "active"
        return "factual_error"

    monkeypatch.setattr(laya, "local_suggestion", suggest)

    def body():
        return {"chat_id": "chat", "id": "answer", "whynote_click_id": str(uuid.uuid4())}

    return action, store, messages, calls, emit, body, target


def test_first_click_suggests_second_retracts_third_creates(setup):
    action, store, messages, calls, emit, body, _ = setup

    async def run():
        first_body = body()
        first = await action.action(first_body, {"id": "alice"}, emit)
        assert first["suggestion"]["reason"] == "factual_error"
        assert len(calls) == 1 and "未确认" in messages[-1]["data"]["content"]
        assert (await action.action(first_body, {"id": "alice"}, emit))["replayed"]
        assert len(calls) == 1
        second = await action.action(body(), {"id": "alice"}, emit)
        assert second["result"] == "retracted" and len(calls) == 1
        third = await action.action(body(), {"id": "alice"}, emit)
        assert third["event_id"] != first["event_id"] and len(calls) == 2
        assert store.get_action(P, third["event_id"])["attribution_status"] == "none"

    asyncio.run(run())


@pytest.mark.parametrize("case", ["disabled", "other_user", "missing_click", "null_click", "missing_session"])
def test_gate_zero_writes_and_model_calls(setup, monkeypatch, case):
    action, store, _, calls, emit, body, _ = setup
    request, user = body(), {"id": "alice"}
    if case == "disabled":
        monkeypatch.delenv("WHYNOTE_LOCAL_CHAIN")
    elif case == "other_user":
        user = {"id": "bob"}
    elif case == "missing_click":
        del request["whynote_click_id"]
    elif case == "null_click":
        request["whynote_click_id"] = None
    else:
        emit = None
    with pytest.raises((NotFoundError, ValueError)):
        asyncio.run(action.action(request, user, emit))
    assert not calls
    with store._transaction() as db:
        assert db.execute("SELECT count(*) FROM events").fetchone()[0] == 0


@pytest.mark.parametrize("change", ["retract", "version", "disable", "failure"])
def test_inflight_changes_never_publish_stale_suggestion(setup, monkeypatch, change):
    action, store, messages, _, emit, body, target = setup

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()

        async def suggest(*args):
            entered.set()
            await release.wait()
            if change == "failure":
                raise httpx.ConnectError("private text")
            return "factual_error"

        monkeypatch.setattr(laya, "local_suggestion", suggest)
        pending = asyncio.create_task(action.action(body(), {"id": "alice"}, emit))
        await asyncio.wait_for(entered.wait(), timeout=5)
        assert store.current_action(P, TARGET)["action_status"] == "active"
        if change == "retract":
            assert (await action.action(body(), {"id": "alice"}, emit))["result"] == "retracted"
        elif change == "version":
            target["object_version"] = "v2"
        elif change == "disable":
            monkeypatch.delenv("WHYNOTE_LOCAL_CHAIN")
        release.set()
        result = await pending
        assert result.get("suggestion", {}).get("result") != "model_inferred_unconfirmed"
        assert not any("知因原因分析" in m["data"]["content"] for m in messages)
        assert "private text" not in str(messages)
        assert store.current_action(P, TARGET)["action_status"] == ("retracted" if change == "retract" else "active")

    asyncio.run(run())
