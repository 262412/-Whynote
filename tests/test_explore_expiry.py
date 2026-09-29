"""Q-40: expiry preserves in-flight outcomes and closes every later send boundary."""

import contextlib
import hashlib
import json
from pathlib import Path

import pytest
from test_explore import prepared as prepared
from test_explore import records

from whynote import explore_inputs, windows_isolation, windows_session
from whynote import explore_run as run
from whynote.explore_prepare import prepare
from whynote.explore_report import report
from whynote.replay_laya import ReplayError, require


@pytest.fixture
def hardware(prepared, monkeypatch):
    output, _ = prepared
    monkeypatch.setattr(run, "model_lock", contextlib.nullcontext)
    monkeypatch.setattr(run, "SHA256", {})
    monkeypatch.setattr(run, "runtime_hash", lambda *args: "synthetic-runtime")
    monkeypatch.setattr(windows_isolation, "isolated_profile", lambda *args: contextlib.nullcontext(object()))
    return {
        "python": str(Path(__file__).resolve()),
        "base_python": str(output.parent),
        "model_dir": str(output.parent),
        "device": "cpu",
        "load_timeout": 60,
        "request_timeout": 60,
    }


@pytest.mark.parametrize("last", [1, 102])
@pytest.mark.parametrize("outcome", ["abstained", "timeout", "cancelled"])
def test_expiry_during_request_preserves_terminal_and_remaining(prepared, hardware, monkeypatch, last, outcome):
    output, _ = prepared
    calls, closed = [], []
    expired = False

    class Worker:
        def __init__(self, *args):
            pass

        def invoke(self, payload):
            nonlocal expired
            calls.append(payload["input_id"])
            expired = len(calls) == last
            if expired and outcome == "cancelled":
                raise KeyboardInterrupt
            if outcome == "timeout":
                return {"error": "timeout"}
            return {
                "input_id": payload["input_id"],
                "scheme": payload["scheme"],
                "bucket": "abstained",
                "state_sha256": hashlib.sha256(payload["state"].encode()).hexdigest(),
            }

        def close(self):
            closed.append(True)

    monkeypatch.setattr(run, "Worker", Worker)
    monkeypatch.setattr(run, "datetime_valid", lambda _: not expired)
    result = run.real_run(output, hardware)
    assert result["status"] == "STOPPED"
    assert result["error"] == ("cancelled" if outcome == "cancelled" else "source_expired")
    rows = records(output)
    terminal = [r for r in rows if r["event"] in {"completed", "failed", "interrupted"}]
    assert len(calls) == len(terminal) == last and closed == [True]
    assert (
        terminal[-1]["bucket"]
        == {
            "abstained": "abstained",
            "timeout": "technical_failure",
            "cancelled": "interrupted",
        }[outcome]
    )
    assert not any(r.get("error") == "uncaught_error" for r in rows)
    summary = report(output)
    assert summary["status"] == "STOPPED" and summary["buckets"]["not_started"] == 102 - last
    assert sum(summary["buckets"].values()) == 102
    before = {p.name: p.read_bytes() for p in output.iterdir() if p.is_file()}
    with pytest.raises(ReplayError, match="source_changed_or_expired"):
        run.real_run(output, hardware, resume=True)
    assert {p.name: p.read_bytes() for p in output.iterdir() if p.is_file()} == before
    assert len(calls) == last


def test_one_of_two_sources_expiring_stops_whole_segment(prepared, hardware, monkeypatch):
    output, manifest = prepared
    source = output.parent / "wildfb.jsonl"
    source.write_text(
        json.dumps(
            {
                "history": [],
                "user_feedback": {"role": "user", "content": "synthetic feedback"},
                "messages": [
                    {"role": "user", "content": "synthetic request"},
                    {"role": "assistant", "content": "synthetic answer"},
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    pin = ("THU-KEG/WildFB", "2" * 40, "test.jsonl", source.stat().st_size, explore_inputs.file_hash(source))
    monkeypatch.setitem(explore_inputs.SOURCES, "wildfb", pin)
    second = manifest["sources"][0] | dict(zip(("repo", "revision", "file", "bytes", "sha256"), pin, strict=True))
    second.update(source="wildfb", path=str(source), expires_at="2099-02-01T00:00:00Z")
    manifest["sources"].append(second)
    output = output.parent / "two-sources"
    plan = prepare(manifest, output, records=2)
    assert len(plan["sources"]) == 2 and plan["targets"] == 5
    calls = []

    class Worker:
        def __init__(self, *args):
            pass

        def invoke(self, payload):
            calls.append(payload)
            return {"error": "timeout"}

        def close(self):
            pass

    monkeypatch.setattr(run, "Worker", Worker)
    # Only the second source expires while the first source is being processed.
    monkeypatch.setattr(run, "datetime_valid", lambda expiry: not calls or expiry != second["expires_at"])
    assert run.real_run(output, hardware)["error"] == "source_expired"
    assert len(calls) == 1 and report(output)["buckets"]["not_started"] == 4


@pytest.mark.parametrize("during_load", [False, True])
def test_actual_worker_checks_expiry_at_send_and_closes(prepared, hardware, monkeypatch, during_load):
    output, _ = prepared
    expired, sent, closed = False, [], []

    class Session:
        def __init__(self, *args):
            pass

        def receive(self, *args):
            nonlocal expired
            expired = during_load
            return json.dumps({"kind": "ready", "source_sha256": run.source_hash(), "metrics": {}})

        def request(self, payload, *args):
            nonlocal expired
            sent.append(payload)
            expired = True
            return '{"error":"timeout"}'

        def close(self):
            closed.append(True)

    monkeypatch.setattr(run, "trusted_command", lambda *args: ["synthetic"])
    monkeypatch.setattr(windows_session, "ResidentSession", Session)
    monkeypatch.setattr(run, "datetime_valid", lambda _: not expired)
    result = run.real_run(output, hardware)
    assert result["error"] == "source_expired" and closed == [True]
    assert len(sent) == (0 if during_load else 1)
    rows = records(output)
    assert any(r["event"] == "model_loaded" for r in rows)
    summary = report(output)
    assert summary["buckets"]["not_started"] == 101
    assert summary["buckets"]["skipped" if during_load else "technical_failure"] == 1


def test_expiry_check_before_next_plaintext_read(prepared):
    import sqlite3

    output, _ = prepared
    calls = []
    # Make the second payload unreadable: no parse/read-dependent failure may replace expiry.
    with sqlite3.connect(output / "inputs.sqlite3") as db:
        db.execute(
            "UPDATE inputs SET payload='invalid-json' WHERE ordinal=(SELECT ordinal FROM inputs ORDER BY ordinal LIMIT 1 OFFSET 1)"
        )
    plan_path = output / "manifest.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["inputs_sha256"] = explore_inputs.file_hash(output / "inputs.sqlite3")
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    def admission():
        require(not calls, "source_expired")

    from test_explore import factory_for

    def invoke(payload):
        calls.append(payload)
        return {"error": "timeout"}

    result = run.execute(output, factory_for(invoke), {}, admission_guard=admission, progress=lambda _: None)
    assert result["error"] == "source_expired" and len(calls) == 1
    assert len([r for r in records(output) if r["event"] == "started"]) == 1
