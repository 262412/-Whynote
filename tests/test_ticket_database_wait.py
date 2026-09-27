"""Q-22: distinguish reservation, dispatch, callback arrival, and later writes."""

import asyncio
import re
import runpy
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from whynote.domain import ConflictError, NotFoundError, Principal
from whynote.store import EventStore

HELPERS = runpy.run_path(str(Path(__file__).with_name("test_host_clicks.py")))
OWNER = HELPERS["OWNER"]


def controlled_clock(action, monkeypatch):
    started, offset = time.monotonic(), [0.0]

    def now():
        return 1000.9 + time.monotonic() - started + offset[0]

    monkeypatch.setitem(action.action.__func__.__globals__, "time", SimpleNamespace(time=now, monotonic=now))
    return started, offset, now


@pytest.mark.parametrize("explicit", [False, True])
def test_sqlite_commit_wait_precedes_menu_clock(monkeypatch, tmp_path, explicit):
    action, body, _ = HELPERS["host"](monkeypatch, tmp_path)
    if not explicit:
        body.pop("whynote_click_id")
    ready, proceed = threading.Event(), threading.Event()

    def reader():
        with sqlite3.connect(action.store.path) as db:
            db.execute("BEGIN")
            db.execute("SELECT COUNT(*) FROM actions").fetchone()
            ready.set()
            assert proceed.wait(5)
            time.sleep(0.75)  # A SHARED lock allows writes but delays their commit.

    worker = threading.Thread(target=reader)
    worker.start()
    assert ready.wait(5)
    started, offset, _ = controlled_clock(action, monkeypatch)

    async def choose(menu):
        assert time.monotonic() - started >= 0.75
        offset[0] += 59.5
        return await HELPERS["choose"](menu)

    proceed.set()
    try:
        result = HELPERS["run"](action, body, choose)
    finally:
        worker.join(5)
    assert result["result"] == "reason_submitted"
    assert len(action.store.get_events(OWNER, result["event_id"])) == 3


def test_deadline_write_wait_does_not_expire_an_arrived_response(monkeypatch, tmp_path):
    action, body, _ = HELPERS["host"](monkeypatch, tmp_path)
    _, offset, now = controlled_clock(action, monkeypatch)
    publishing, locked, returned = (threading.Event() for _ in range(3))
    publish = action.store.set_host_click_deadline
    captured = []

    def delayed_publish(*args):
        publishing.set()
        assert locked.wait(5)
        publish(*args)

    monkeypatch.setattr(action.store, "set_host_click_deadline", delayed_publish)

    def writer():
        with sqlite3.connect(action.store.path) as db:
            db.execute("BEGIN IMMEDIATE")
            locked.set()
            assert returned.wait(5)
            time.sleep(0.75)

    worker = threading.Thread(target=writer)

    async def choose(menu):
        assert await asyncio.to_thread(publishing.wait, 5)
        worker.start()
        assert await asyncio.to_thread(locked.wait, 5)
        value = await HELPERS["choose"](menu)
        deadline = float(re.fullmatch(r"s0t1\.[^.]+\.(.+)\.factual_error\.[^.]+", value).group(1))
        captured.append(deadline)
        assert deadline - now() > 59
        offset[0] += 59.5
        returned.set()
        return value

    try:
        result = HELPERS["run"](action, body, choose)
    finally:
        worker.join(5)
    assert now() >= captured[0], "The later database write must actually finish after the ticket deadline"
    assert result["result"] == "reason_submitted", "Judge expiry when the callback arrives, before later writes"
    with sqlite3.connect(action.store.path) as db:
        assert db.execute("SELECT expires_at FROM host_clicks").fetchone()[0] == captured[0]
    assert len(action.store.get_events(OWNER, result["event_id"])) == 3


def test_deadline_failure_preserves_reservation_and_requires_new_click(monkeypatch, tmp_path):
    action, body, _ = HELPERS["host"](monkeypatch, tmp_path)
    publish = action.store.set_host_click_deadline

    def failed(*_):
        raise sqlite3.OperationalError("synthetic deadline persistence failure")

    monkeypatch.setattr(action.store, "set_host_click_deadline", failed)
    with pytest.raises(sqlite3.OperationalError):
        HELPERS["run"](action, body)
    monkeypatch.setattr(action.store, "set_host_click_deadline", publish)
    result = HELPERS["run"](action, body, HELPERS["never"])
    assert result["result"] == "click_pending"
    assert len(action.store.get_events(OWNER, result["event_id"])) == 1
    new = HELPERS["run"](action, {**body, "whynote_click_id": str(uuid.uuid4())})
    assert new["result"] == "reason_submitted" and new["event_id"] == result["event_id"]


def test_unpublished_deadline_survives_restart_and_cannot_be_renewed(tmp_path):
    path = tmp_path / "events.db"
    store = EventStore(path)
    target = {"object_type": "answer", "object_id": "synthetic", "object_version": "1"}
    metadata = {"interaction_contract": "manual-v1"}
    state, replay = store.begin_host_click(OWNER, target, metadata, "key", "display", lambda: 1000)
    assert replay is None
    store = EventStore(path)
    _, replay = store.begin_host_click(OWNER, target, metadata, "key", "ignored", lambda: 10000)
    assert replay["result"] == "click_pending"
    eid = state["event_id"]
    with pytest.raises(NotFoundError):
        store.set_host_click_deadline(Principal(OWNER.tenant_ref, "bob"), eid, "key", "display", 1060)
    with pytest.raises(ConflictError):
        store.set_host_click_deadline(OWNER, eid, "key", "wrong", 1060)
    store.set_host_click_deadline(OWNER, eid, "key", "display", 1060)
    store.set_host_click_deadline(OWNER, eid, "key", "display", 1060)
    with pytest.raises(ConflictError):
        store.set_host_click_deadline(OWNER, eid, "key", "display", 1100)
    _, replay = store.begin_host_click(OWNER, target, metadata, "key", "ignored", lambda: 1060)
    assert replay["result"] == "ticket_expired"
    assert len(store.get_events(OWNER, eid)) == 1


@pytest.mark.parametrize("wall,mono", [(59.999, 59.999), (60, 60), (61, 1), (-5, 61)])
def test_explicit_ticket_keeps_both_clock_boundaries(monkeypatch, tmp_path, wall, mono):
    action, body, _ = HELPERS["host"](monkeypatch, tmp_path)
    clock = [1000.9, 2000.0]
    monkeypatch.setitem(
        action.action.__func__.__globals__, "time", SimpleNamespace(time=lambda: clock[0], monotonic=lambda: clock[1])
    )

    async def choose(menu):
        clock[0] += wall
        clock[1] += mono
        return await HELPERS["choose"](menu)

    result = HELPERS["run"](action, body, choose)
    assert result["result"] == ("reason_submitted" if wall < 60 and mono < 60 else "ticket_expired")
