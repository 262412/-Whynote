"""Q-21 lifecycle checks against the actual Action and persistent EventStore."""

import asyncio
import runpy
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from whynote.domain import Principal
from whynote.store import EventStore

HELPERS = runpy.run_path(str(Path(__file__).with_name("test_openwebui_s0.py")))
OWNER = Principal("isolated-test-instance", "alice")


def host(monkeypatch, tmp_path):
    action = HELPERS["s0_action"](monkeypatch, tmp_path)
    chat, body, _ = HELPERS["s0_chat"]()
    body["whynote_click_id"] = str(uuid.uuid4())

    async def owned(_, user_id):
        return chat if user_id == "alice" else None

    action._owned_chat = owned
    return action, body, chat


async def choose(menu):
    return HELPERS["selected_value"](menu, "事实有误")


async def never(_):
    pytest.fail("A retry must not invoke the browser or a notification")


def run(action, body, callback=choose, emitter=None, user="alice"):
    return asyncio.run(action.action(body, __user__={"id": user}, __event_call__=callback, __event_emitter__=emitter))


@pytest.mark.parametrize("label", ["事实有误", "都不是", "暂时跳过", "不愿说明", "关闭", None])
def test_completed_operations_survive_restart(monkeypatch, tmp_path, label):
    action, body, _ = host(monkeypatch, tmp_path)

    async def respond(menu):
        if label is None:
            return False
        # Use the contract labels for operations, avoiding notification wording.
        labels = {
            "都不是": "reason_none_matched",
            "暂时跳过": "reason_skipped",
            "不愿说明": "reason_declined",
            "关闭": "reason_menu_closed",
        }
        actual = action.action.__func__.__globals__["MANUAL_OPERATIONS"].get(labels.get(label), label)
        return {"value": HELPERS["selected_value"](menu, actual), "timing": {"active_ms": 10**400}}

    first = run(action, body, respond)
    before = action.store.get_events(OWNER, first["event_id"])
    restarted = HELPERS["s0_action"](monkeypatch, tmp_path)
    restarted._owned_chat = action._owned_chat
    assert run(restarted, {**body, "session_id": "reconnected"}, never, never) == first
    assert action.store.get_events(OWNER, first["event_id"]) == before


def test_pending_retries_keep_original_menu_and_completed_retry_keeps_new_menu(monkeypatch, tmp_path):
    action, body, _ = host(monkeypatch, tmp_path)
    other = HELPERS["s0_action"](monkeypatch, tmp_path)
    other._owned_chat = action._owned_chat

    async def scenario():
        opened, release = asyncio.Event(), asyncio.Event()

        async def pending(menu):
            opened.set()
            await release.wait()
            return await choose(menu)

        task = asyncio.create_task(action.action(body, __user__={"id": "alice"}, __event_call__=pending))
        await opened.wait()
        retries = await asyncio.gather(
            *(
                other.action({**body, "session_id": str(i)}, __user__={"id": "alice"}, __event_call__=never)
                for i in range(8)
            )
        )
        assert all(item["result"] == "click_pending" for item in retries)
        release.set()
        first = await task
        opened.clear()
        release.clear()
        new_body = {**body, "whynote_click_id": str(uuid.uuid4())}
        task = asyncio.create_task(other.action(new_body, __user__={"id": "alice"}, __event_call__=pending))
        await opened.wait()
        assert await action.action(body, __user__={"id": "alice"}, __event_call__=never) == first
        release.set()
        edited = await task
        assert edited["event_id"] == first["event_id"]
        assert edited["attribution_status"] == "edited"
        assert await other.action(body, __user__={"id": "alice"}, __event_call__=never) == first
        assert len(action.store.get_events(OWNER, first["event_id"])) == 5

    asyncio.run(scenario())


def test_response_committed_before_notification_failure(monkeypatch, tmp_path):
    action, body, _ = host(monkeypatch, tmp_path)

    async def broken(_):
        raise ConnectionError("synthetic notification disconnected")

    with pytest.raises(ConnectionError):
        run(action, body, emitter=broken)
    result = run(action, body, never, never)
    assert result["attribution_status"] == "selected"
    assert len(action.store.get_events(OWNER, result["event_id"])) == 3


@pytest.mark.parametrize("retract", [False, True])
def test_pending_click_cannot_return_after_replacement_or_retraction(monkeypatch, tmp_path, retract):
    action, body, _ = host(monkeypatch, tmp_path)

    async def scenario():
        opened, release = asyncio.Event(), asyncio.Event()

        async def pending(menu):
            opened.set()
            await release.wait()
            return await choose(menu)

        task = asyncio.create_task(action.action(body, __user__={"id": "alice"}, __event_call__=pending))
        await opened.wait()
        event_id = action.store.next_gate_message()["event_id"]
        if retract:
            action.store.retract_action(OWNER, event_id, "retract")
        newer = await action.action(
            {**body, "whynote_click_id": str(uuid.uuid4())}, __user__={"id": "alice"}, __event_call__=choose
        )
        assert (newer["event_id"] != event_id) == retract
        before = action.store.get_events(OWNER, event_id)
        retry = await action.action(body, __user__={"id": "alice"}, __event_call__=never)
        assert retry["result"] == ("retracted" if retract else "click_superseded")
        release.set()
        with pytest.raises(ValueError):
            await task
        assert action.store.get_events(OWNER, event_id) == before

    asyncio.run(scenario())


def test_disconnected_expired_and_superseded_retries_do_not_reopen(monkeypatch, tmp_path):
    action, body, _ = host(monkeypatch, tmp_path)
    clock = [1000.0]
    action.action.__func__.__globals__["time"] = SimpleNamespace(time=lambda: clock[0], monotonic=lambda: clock[0])

    async def disconnected(_):
        return {"error": "synthetic disconnect"}

    first = run(action, body, disconnected)
    assert first["result"] == "client_unavailable"
    assert run(action, body, never)["result"] == "click_pending"
    clock[0] += 60
    assert run(action, body, never)["result"] == "ticket_expired"
    second = run(action, {**body, "whynote_click_id": str(uuid.uuid4())})
    assert second["event_id"] == first["event_id"]
    assert run(action, body, never)["result"] == "click_superseded"
    assert len(action.store.get_events(OWNER, first["event_id"])) == 3


def test_completed_retry_rechecks_owner_and_target(monkeypatch, tmp_path):
    action, body, chat = host(monkeypatch, tmp_path)
    first = run(action, body)
    before = action.store.get_events(OWNER, first["event_id"])
    with pytest.raises(ValueError, match="unavailable"):
        run(action, body, never, user="bob")
    chat.chat["history"]["messages"][body["id"]]["content"] = "changed synthetic answer"
    with pytest.raises(ValueError, match="does not match"):
        run(action, body, never)
    assert action.store.get_events(OWNER, first["event_id"]) == before


def test_pre_upgrade_click_does_not_guess_history(monkeypatch, tmp_path):
    action, body, _ = host(monkeypatch, tmp_path)
    first = run(action, body)
    before = action.store.get_events(OWNER, first["event_id"])
    with sqlite3.connect(action.store.path) as db:
        db.execute("DROP TABLE host_clicks")  # Synthetic pre-upgrade schema; events stay intact.
    restarted = HELPERS["s0_action"](monkeypatch, tmp_path)
    restarted._owned_chat = action._owned_chat
    assert run(restarted, body, never)["result"] == "legacy_click_unavailable"
    assert restarted.store.get_events(OWNER, first["event_id"]) == before
    assert run(restarted, {**body, "whynote_click_id": str(uuid.uuid4())})["attribution_status"] == "edited"


def test_concurrent_writers_reserve_once_and_rollback_together(tmp_path):
    path = tmp_path / "events.db"
    store = EventStore(path)
    target = {"object_type": "answer", "object_id": "synthetic", "object_version": "1"}
    metadata = {"interaction_contract": "manual-v1"}

    def reserve(_):
        return EventStore(path).begin_host_click(OWNER, target, metadata, "click", str(uuid.uuid4()), 1000, 1060)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(reserve, range(8)))
    assert sum(replay is None for _, replay in results) == 1
    assert len({state["event_id"] for state, _ in results}) == 1
    with sqlite3.connect(path) as db:
        for table in ("actions", "events", "outbox", "idempotency", "host_clicks", "display_tickets"):
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1
        db.execute("CREATE TRIGGER reject_click BEFORE INSERT ON host_clicks BEGIN SELECT RAISE(ABORT, 'test'); END;")
    with pytest.raises(sqlite3.IntegrityError):
        store.begin_host_click(Principal("other", "bob"), target, metadata, "new", "new", 1000, 1060)
    with sqlite3.connect(path) as db:
        for table in ("actions", "events", "outbox", "idempotency", "host_clicks", "display_tickets"):
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1
