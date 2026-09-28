"""Q-27 developer regressions; the original independent QA test stays unchanged."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest
from test_suggestions import events, generate, ref, respond, snapshot
from test_suggestions import trial as trial
from test_template_suggestions import client, invoke
from test_template_suggestions import host as host

from whynote import suggestions as s
from whynote.domain import ConflictError, NotFoundError, project


@pytest.mark.parametrize("explicit_arrival", [False, True])
def test_timely_render_survives_sqlite_wait_without_extending_response_deadline(trial, monkeypatch, explicit_arrival):
    f = trial
    binding = generate(f)
    f.clock += 59.5
    arrival = f.clock
    waiting = threading.Event()
    transaction = f.store._transaction

    @contextmanager
    def blocked_transaction():
        waiting.set()
        with transaction() as db:
            yield db

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction():
            monkeypatch.setattr(f.store, "_transaction", blocked_transaction)
            future = pool.submit(
                s.render,
                f.store,
                f.principal,
                f.event_id,
                ref("render"),
                ref("display"),
                binding,
                **({"server_received_at": arrival} if explicit_arrival else {}),
            )
            assert waiting.wait(5)
            f.clock += 1
        record = future.result(timeout=10)
    assert events(f)[-1]["record_id"] == record
    before = events(f)
    with pytest.raises(ConflictError):
        respond(f)
    assert events(f) == before
    assert project(before)["action_status"] == "active"
    group = snapshot(f)["groups"]["scripted"]["suggestions"]
    assert (group["render_reported"], group["valid_response"]) == (1, 0)
    assert group["status_counts"] == {"unresponded": 1}


@pytest.mark.parametrize(
    "transition", ["stop", "revoke", "delete", "retract", "replace", "invalidate", "protocol", "model"]
)
def test_timely_queued_render_rechecks_current_state(trial, monkeypatch, transition):
    f = trial
    binding = generate(f)
    f.clock += 59.5
    arrived = threading.Event()
    proceed = threading.Event()
    transaction = f.store._transaction

    @contextmanager
    def queued():
        if threading.current_thread() is not threading.main_thread():
            arrived.set()
            assert proceed.wait(5)
        with transaction() as db:
            yield db

    monkeypatch.setattr(f.store, "_transaction", queued)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(s.render, f.store, f.principal, f.event_id, ref("render"), ref("display"), binding)
        assert arrived.wait(5)
        try:
            if transition == "stop":
                f.config["suggestion_research_enabled"] = False
            elif transition in {"revoke", "delete"}:
                f.store.invalidate(f.chat_record.id, revoke=transition == "revoke")
            elif transition == "retract":
                f.store.retract_action(f.principal, f.event_id, "undo")
            elif transition == "replace":
                generate(f, "new", display="new-display")
            elif transition == "invalidate":
                s.invalidate(f.store, f.principal, f.event_id, ref("invalidate"), binding["suggestion_id"])
            elif transition == "protocol":
                f.config["research_protocol_ref"] = ref("changed-protocol")
            else:
                f.config["suggestion_model_revision"] = "b" * 40
            before = events(f)
            f.clock += 1
        finally:
            proceed.set()
        with pytest.raises((ConflictError, NotFoundError)):
            future.result(timeout=10)
    assert events(f) == before
    assert not any(e["event_type"] == "m52_render_reported" for e in before)


@pytest.mark.parametrize("boundary", ["disable", "retract", "replace", "revoke"])
def test_arrived_render_rechecks_host_after_deadline(host, boundary):
    f = host
    callback = client(f, [])
    owned = f.action._owned_chat
    arrived = False
    before = None

    async def receive(event):
        nonlocal arrived
        value = await callback(event)
        if event["type"] == "whynote:suggestion-render":
            f.clock += 59.5
            arrived = True
        return value

    async def changed_host(*args):
        nonlocal arrived, before
        if arrived:
            arrived = False
            f.clock += 1
            if boundary == "disable":
                f.config["suggestion_template_enabled"] = False
                f.config_path.write_text(json.dumps(f.config), encoding="utf-8")
            elif boundary == "retract":
                f.store.retract_action(f.principal, f.template_event, "inflight-retract")
            elif boundary == "replace":
                f.chat_record.chat["history"]["messages"][f.template_answer.message_id]["content"] = "Changed answer"
            else:
                f.store.invalidate(f.chat_record.id, revoke=True)
            before = f.store.get_events(f.principal, f.template_event)
        return await owned(*args)

    f.action._owned_chat = changed_host
    result = invoke(f, receive)
    assert result["suggestion"]["result"] == "superseded"
    assert f.store.get_events(f.principal, f.template_event) == before
    assert not any(e["event_type"] == "m52_render_reported" for e in before)


@pytest.mark.parametrize("offset", [-1, 60, 60.5])
def test_explicit_arrival_must_be_within_original_lifetime(trial, offset):
    f = trial
    binding = generate(f)
    arrival = f.clock + offset
    f.clock += 61
    before = events(f)
    with pytest.raises(ConflictError):
        s.render(f.store, f.principal, f.event_id, ref("render"), ref("display"), binding, server_received_at=arrival)
    assert events(f) == before


@pytest.mark.parametrize("value", [True, "59.5", float("nan"), float("inf"), "future"])
def test_invalid_server_receipt_time_cannot_write(trial, value):
    f = trial
    binding = generate(f)
    if value == "future":
        value = f.clock + 1
    before = events(f)
    with pytest.raises(ValueError, match="Invalid server receipt time"):
        s.render(f.store, f.principal, f.event_id, ref("render"), ref("display"), binding, server_received_at=value)
    assert events(f) == before


def test_client_cannot_supply_render_arrival(host):
    f = host
    callback = client(f, [])

    async def receive(event):
        value = await callback(event)
        if event["type"] == "whynote:suggestion-render":
            return {**value, "server_received_at": f.clock}
        return value

    result = invoke(f, receive)
    current = f.store.get_events(f.principal, result["event_id"])
    assert not any(e["event_type"] == "m52_render_reported" for e in current)
    assert project(current)["action_status"] == "active"
