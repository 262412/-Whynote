"""Independent synthetic checks of the real exploration orchestration."""

import contextlib
from pathlib import Path

import pytest
from test_explore import prepared as prepared

from whynote import explore_run as run
from whynote import windows_isolation
from whynote.explore_report import report
from whynote.replay_laya import ReplayError


@pytest.mark.parametrize("expires_before_start", [False, True])
def test_retention_expiry_stops_before_next_payload(prepared, monkeypatch, expires_before_start, record_property):
    output, _ = prepared
    calls = []
    expired = expires_before_start

    class Worker:
        def __init__(self, *args):
            pass

        def invoke(self, payload):
            nonlocal expired
            calls.append(payload["input_id"])
            expired = True
            return {"error": "timeout"}

        def close(self):
            pass

    # Replace clock/hardware only. Keep actual real_run, execute, guard, journal and SQLite.
    monkeypatch.setattr(run, "datetime_valid", lambda _: not expired)
    monkeypatch.setattr(run, "Worker", Worker)
    monkeypatch.setattr(run, "model_lock", contextlib.nullcontext)
    monkeypatch.setattr(run, "SHA256", {})
    monkeypatch.setattr(run, "runtime_hash", lambda *args: "synthetic-runtime")
    monkeypatch.setattr(windows_isolation, "isolated_profile", lambda *args: contextlib.nullcontext(object()))
    config = {
        "python": str(Path(__file__).resolve()),
        "base_python": str(output.parent),
        "model_dir": str(output.parent),
        "device": "cpu",
        "load_timeout": 60,
        "request_timeout": 60,
    }
    if expires_before_start:
        with pytest.raises(ReplayError, match="source_changed_or_expired"):
            run.real_run(output, config)
        assert not calls and not (output / "runtime.json").exists()
        return
    result = run.real_run(output, config)
    summary = report(output)
    record_property("actual_calls", len(calls))
    record_property("execution_status", result["status"])
    record_property("not_started", summary["buckets"]["not_started"])
    assert len(calls) == 1, "Q-40: retention expiry must prevent the next model payload"
    assert result["status"] == "STOPPED"
    assert summary["buckets"]["not_started"] == 101
