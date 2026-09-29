"""Independent supervisor stop and journal checks; only synthetic metadata and input."""

import io
import json
import sys
from types import SimpleNamespace

import pytest
from test_controlled_replay import prepare
from test_self_review import bundle as bundle

from whynote.controlled_replay import execute_slots
from whynote.replay_laya import ReplayError


@pytest.mark.parametrize("transport", ["envelope", "exception", "prediction"])
def test_uncaught_error_stops_before_the_next_slot(bundle, tmp_path, transport):
    setup, projections = prepare(bundle)
    output = tmp_path / "run"
    calls = []

    def invoke(raw):
        payload = json.loads(raw)
        calls.append((payload["input_id"], payload["scheme"]))
        if transport == "exception":
            raise ReplayError("uncaught_error")
        if transport == "envelope":
            return {"error": "uncaught_error"}
        sample = next(s for s in setup["manifest"]["samples"] if s["input_id"] == payload["input_id"])
        return {
            "prediction": {
                "input_id": payload["input_id"],
                "state_sha256": sample["state_sha256"],
                "scheme": payload["scheme"],
                "status": "error",
                "error": "uncaught_error",
                "route": None,
                "routed_ids": [],
                "initial_routed_ids": [],
                "available_ids": [],
                "reason_ids": [],
            }
        }

    try:
        execute_slots(setup, projections, invoke, output)
    except ReplayError:
        pass
    journal = [json.loads(line) for line in (output / "journal.jsonl").read_text(encoding="utf-8").splitlines()]
    started = [entry for entry in journal if entry["event"] == "started"]
    assert len(calls) == len(started) == 1, "Q-38: an uncaught failure must stop before any next slot"
    assert journal[-1]["event"] == "stopped"
    assert any(
        entry.get("prediction", {}).get("error") == "uncaught_error" or entry.get("error") == "uncaught_error"
        for entry in journal
    ), "The first failure must remain identifiable in the journal"
    assert not (output / "bundle.json").exists()


@pytest.mark.parametrize("phase", ["before_first", "after_first", "before_second"])
def test_guard_rejection_preserves_exact_partial_journal(bundle, tmp_path, phase):
    setup, projections = prepare(bundle)
    output = tmp_path / "run"
    guards, calls = [], []
    fail_at = {"before_first": 1, "after_first": 2, "before_second": 3}[phase]

    def guard():
        guards.append(None)
        if len(guards) == fail_at:
            raise ReplayError("source_changed")

    def invoke(payload):
        calls.append(payload)
        return {"error": "timeout"}

    with pytest.raises(ReplayError, match="^source_changed$"):
        execute_slots(setup, projections, invoke, output, guard=guard)
    journal = [json.loads(line) for line in (output / "journal.jsonl").read_text(encoding="utf-8").splitlines()]
    expected = {
        "before_first": ["stopped"],
        "after_first": ["started", "stopped"],
        "before_second": ["started", "completed", "stopped"],
    }
    assert [entry["event"] for entry in journal] == expected[phase]
    assert len(calls) == (0 if phase == "before_first" else 1)
    assert not (output / "bundle.json").exists() and not (output / "report.json").exists()


def test_error_detail_is_never_copied_into_partial_journal(bundle, tmp_path):
    setup, projections = prepare(bundle)
    output = tmp_path / "run"

    def invoke(_):
        raise RuntimeError("SYNTHETIC_PRIVATE_EXCEPTION_CONTENT")

    with pytest.raises(RuntimeError):
        execute_slots(setup, projections, invoke, output)
    assert "SYNTHETIC_PRIVATE_EXCEPTION_CONTENT" not in (output / "journal.jsonl").read_text(encoding="utf-8")
    assert not (output / "bundle.json").exists()


@pytest.mark.parametrize("stage,expected", [("load", "model_load_failed"), ("policy", "uncaught_error")])
def test_worker_classifies_unexpected_failure_at_its_actual_stage(monkeypatch, capsys, tmp_path, stage, expected):
    import whynote.controlled_worker as worker

    # Stage injection only: no SDK/model/data access. Keep the real worker main handler.
    def fail(*args, **kwargs):
        raise RuntimeError("SYNTHETIC_PRIVATE_FAILURE")

    agent = SimpleNamespace(device="cpu", cfg={"max_len": 1024, "head_max_len": 256})
    monkeypatch.setitem(sys.modules, "laya", SimpleNamespace(load=fail if stage == "load" else lambda *a, **k: agent))
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(manual_seed=lambda _: None))
    monkeypatch.setattr(worker, "load_tokenizer", lambda _: object())
    monkeypatch.setattr(worker, "infer_scheme", fail)
    monkeypatch.setattr(sys, "argv", ["worker", "--model-dir", str(tmp_path), "--device", "cpu"])
    payload = {
        "operation": "infer",
        "state": "SYNTHETIC ONLY",
        "input_id": "0" * 64,
        "scheme": "C",
        "evidence_kinds": ["request", "answer"],
    }
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(json.dumps(payload).encode())))
    worker.main()
    output = capsys.readouterr()
    assert "SYNTHETIC_PRIVATE_FAILURE" not in output.out + output.err
    assert json.loads(output.out) == {"error": expected}, "Q-39: a loaded model's policy crash is not a load failure"
