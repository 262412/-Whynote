"""Independent M5-3b boundaries; all identities and content are synthetic."""

import asyncio
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_live_suggestions import enabled as enabled
from test_live_suggestions import stub
from test_suggestions import ref
from test_suggestions import trial as trial
from test_template_suggestions import client, invoke
from test_template_suggestions import host as host

from whynote import live_suggestions as live
from whynote import suggestions
from whynote.domain import project
from whynote.research_report import report


@pytest.mark.parametrize("source", ["self_natural", "public_replay"])
def test_whitelisted_non_scripted_source_never_reaches_model(enabled, monkeypatch, source):
    f = enabled
    f.chat_record = f.chat("non-scripted", source)
    f.template_answer = f.answer(f.chat_record, "non-scripted-answer")
    target = f.action._target(f.chat_record, f.template_answer.body)
    f.config["suggestion_synthetic_targets"] = {target["object_id"]: target["object_version"]}
    f.config_path.write_text(json.dumps(f.config), encoding="utf-8")
    calls = stub(monkeypatch, f)
    actual = invoke(f, client(f, []))
    records = f.store.get_events(f.principal, actual["event_id"])
    assert actual["suggestion"]["result"] == "superseded"
    assert not calls and "whynote:suggestion-render" not in f.client_events
    assert project(records)["action_status"] == "active"
    assert [e["event_type"] for e in records] == ["negative_feedback_action_recorded"]


@pytest.mark.parametrize("phase", ["render", "response"])
@pytest.mark.parametrize("boundary", ["whitelist", "protocol", "runtime", "retract"])
def test_late_admission_change_does_not_accept_callback(enabled, monkeypatch, phase, boundary):
    f = enabled
    stub(monkeypatch, f)
    original = client(f, [("yes", "general.style")])

    async def callback(event):
        value = await original(event)
        if event["type"] == "whynote:suggestion-" + phase:
            if boundary == "retract":
                f.store.retract_action(f.principal, f.template_event, "late-retract")
            else:
                key, value_change = {
                    "whitelist": ("suggestion_synthetic_targets", {"different/target": "v2"}),
                    "protocol": ("research_protocol_ref", ref("new-protocol")),
                    "runtime": ("suggestion_python", str(f.config_path.resolve())),
                }[boundary]
                f.config[key] = value_change
                f.config_path.write_text(json.dumps(f.config), encoding="utf-8")
        return value

    actual = invoke(f, callback)
    records = f.store.get_events(f.principal, actual["event_id"])
    kinds = [e["event_type"] for e in records]
    assert actual["suggestion"]["result"] == "superseded"
    assert kinds.count("m52_suggestion_generated") == 1
    assert kinds.count("m52_render_reported") == (phase == "response")
    assert "m52_response_recorded" not in kinds
    assert suggestions.project_suggestions(records)["reason_id"] is None


def test_backend_rollback_keeps_original_source_and_report_history(enabled, monkeypatch):
    f = enabled
    calls = stub(monkeypatch, f)
    first = invoke(f, client(f, [("yes", "general.style"), ("done", None)]))
    before = f.store.get_events(f.principal, first["event_id"])
    f.config["suggestion_backend"] = "fixture"
    f.config_path.write_text(json.dumps(f.config), encoding="utf-8")
    assert invoke(f, client(f, []))["replayed"] is True
    assert len(calls) == 1 and f.store.get_events(f.principal, first["event_id"]) == before
    invoke(f, client(f, []), {**f.template_answer.body, "whynote_click_id": ref("undo-live")})
    second = invoke(
        f,
        client(f, [("none_matched", None)]),
        {**f.template_answer.body, "whynote_click_id": ref("fixture-click")},
    )
    newer = f.store.get_events(f.principal, second["event_id"])
    generated = next(e for e in newer if e["event_type"] == "m52_suggestion_generated")
    assert generated["source"] == "synthetic_model"
    assert "model_source" not in generated["payload"]["binding"]
    older = f.store.get_events(f.principal, first["event_id"])
    assert older[: len(before)] == before
    assert (
        next(e for e in before if e["event_type"] == "m52_suggestion_generated")["source"]
        == "model_inferred_unconfirmed"
    )
    digest = hashlib.sha256(Path(f.store.path).read_bytes()).hexdigest()
    stats = report(f.store.path, f.principal, "2026-09-01T00:00:00Z", f.now())
    assert report(f.store.path, f.principal, stats["since"], stats["as_of"]) == stats
    assert hashlib.sha256(Path(f.store.path).read_bytes()).hexdigest() == digest
    assert stats["groups"]["scripted"]["suggestions"]["generated"] == 2
    with sqlite3.connect(f.store.path) as db:
        assert db.execute("SELECT count(*) FROM outbox WHERE event_id=?", (second["event_id"],)).fetchone()[0] >= 1


@pytest.mark.parametrize("cancel", [False, True])
def test_real_child_is_reaped_on_timeout_or_task_cancel(enabled, monkeypatch, cancel):
    """Replace the worker command, retaining real OS process/pipes/kill/wait."""
    spawn, wait_for = asyncio.create_subprocess_exec, asyncio.wait_for
    children = []

    async def sleeping_worker(*args, **kwargs):
        child = await spawn(sys.executable, "-c", "import time; time.sleep(120)", **kwargs)
        children.append(child)
        return child

    async def short_deadline(awaitable, timeout):
        assert timeout == 60
        return await wait_for(awaitable, 0.05)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", sleeping_worker)
    if not cancel:
        monkeypatch.setattr(asyncio, "wait_for", short_deadline)

    async def run():
        task = asyncio.create_task(live.evaluate(enabled.config, "synthetic question", "synthetic answer"))
        if cancel:
            while not children:
                await asyncio.sleep(0.01)
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else live.ReplayError):
            await task
        assert len(children) == 1 and children[0].returncode is not None
        assert await children[0].wait() == children[0].returncode

    asyncio.run(run())


@pytest.mark.parametrize("missing", ["model_python", "model_dir"])
def test_provision_rejects_missing_model_options_before_host_writes(tmp_path, monkeypatch, missing):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "qa"))
    import s1_browser_host

    from qa import m53_live_host

    args = SimpleNamespace(
        data_dir=tmp_path,
        base_url="http://127.0.0.1:1",
        model_python=Path(sys.executable),
        model_dir=tmp_path,
    )
    setattr(args, missing, None)

    def forbidden_write(_):
        pytest.fail("Q-28: missing model option reached host provisioning before validation")

    monkeypatch.setattr(s1_browser_host, "provision", forbidden_write)
    with pytest.raises((ValueError, SystemExit)):
        m53_live_host.provision(args)
