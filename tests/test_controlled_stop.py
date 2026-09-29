"""Q-38/Q-39 developer boundary checks; independent failure assertions stay unchanged."""

import contextlib
import io
import json
import sys
from types import SimpleNamespace

import pytest
from test_controlled_replay import prepare
from test_self_review import bundle as bundle

from whynote.controlled_replay import execute_synthetic
from whynote.replay_laya import ReplayError


@pytest.mark.parametrize("at", [7, 179])
@pytest.mark.parametrize("transport", ["envelope", "exception", "prediction"])
def test_late_fatal_error_is_durable_and_stops(bundle, tmp_path, at, transport):
    setup, projections = prepare(bundle)
    calls = []

    def invoke(raw):
        payload = json.loads(raw)
        calls.append(payload)
        if len(calls) != at:
            return {"error": "timeout"}
        if transport == "exception":
            raise ReplayError("uncaught_error")
        if transport == "envelope":
            return {"error": "uncaught_error"}
        sample = next(s for s in setup["manifest"]["samples"] if s["input_id"] == payload["input_id"])
        return {
            "prediction": {
                "input_id": sample["input_id"],
                "state_sha256": sample["state_sha256"],
                "scheme": payload["scheme"],
                "status": "error",
                "error": "uncaught_error",
                "route": None,
                "routed_ids": [],
                "available_ids": [],
                "reason_ids": [],
            }
        }

    output = tmp_path / "run"
    with pytest.raises(ReplayError, match="^uncaught_error$"):
        execute_synthetic(setup, projections, invoke, output)
    journal = [json.loads(line) for line in (output / "journal.jsonl").read_text().splitlines()]
    assert len(calls) == at
    assert [row["event"] for row in journal] == ["started", "completed"] * at + ["stopped"]
    assert journal[-2]["prediction"]["error"] == journal[-1]["error"] == "uncaught_error"
    assert journal[-1]["completed_slots"] == at
    assert not (output / "bundle.json").exists() and not (output / "report.json").exists()
    before = (output / "journal.jsonl").read_bytes()
    with pytest.raises(FileExistsError):
        execute_synthetic(setup, projections, invoke, output)
    assert (output / "journal.jsonl").read_bytes() == before and len(calls) == at


@pytest.mark.parametrize("error", ["timeout", "model_load_failed", "route_failed", "invalid_response", "worker_failed"])
def test_planned_failures_continue_with_fixed_counts(bundle, tmp_path, error):
    setup, projections = prepare(bundle)
    calls = []

    def invoke(raw):
        calls.append(raw)
        raise ReplayError(error)

    execute_synthetic(setup, projections, invoke, tmp_path / "run")
    journal = [json.loads(line) for line in (tmp_path / "run/journal.jsonl").read_text().splitlines()]
    assert len(calls) == 180 and len(journal) == 360
    assert all(row["prediction"]["error"] == error for row in journal if row["event"] == "completed")


def inject_worker(monkeypatch, tmp_path, stage):
    import whynote.controlled_worker as worker

    def fail(*args, **kwargs):
        raise RuntimeError("SYNTHETIC_PRIVATE_FAILURE")

    agent = SimpleNamespace(device="cpu", cfg={"max_len": 1024, "head_max_len": 256})
    monkeypatch.setattr(worker, "load_tokenizer", fail if stage == "tokenizer_load" else lambda _: object())
    monkeypatch.setattr(worker, "measure_budget", fail)
    monkeypatch.setitem(
        sys.modules, "laya", SimpleNamespace(load=fail if stage == "model_load" else lambda *a, **k: agent)
    )
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(manual_seed=fail if stage == "seed" else lambda _: None))
    monkeypatch.setattr(worker, "infer_scheme", fail)
    monkeypatch.setattr(sys, "argv", ["worker", "--model-dir", str(tmp_path), "--device", "cpu"])

    def invoke(raw):
        monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(raw)))
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            worker.main()
        assert "SYNTHETIC_PRIVATE_FAILURE" not in stdout.getvalue() + stderr.getvalue()
        return json.loads(stdout.getvalue())

    return invoke


@pytest.mark.parametrize("stage", ["seed", "policy"])
def test_loaded_worker_fault_stops_actual_supervisor(bundle, tmp_path, monkeypatch, stage):
    setup, projections = prepare(bundle)
    worker = inject_worker(monkeypatch, tmp_path, stage)
    calls = []

    def invoke(raw):
        calls.append(raw)
        return worker(raw)

    with pytest.raises(ReplayError, match="^uncaught_error$"):
        execute_synthetic(setup, projections, invoke, tmp_path / "run")
    journal = [json.loads(line) for line in (tmp_path / "run/journal.jsonl").read_text().splitlines()]
    assert len(calls) == 1 and journal[1]["prediction"]["error"] == "uncaught_error"
    assert journal[-1]["event"] == "stopped" and not (tmp_path / "run/bundle.json").exists()


@pytest.mark.parametrize(
    "stage,operation,expected",
    [
        ("tokenizer_load", "measure", "model_load_failed"),
        ("measure", "measure", "uncaught_error"),
        ("model_load", "infer", "model_load_failed"),
    ],
)
def test_measurement_and_load_stage_classification(monkeypatch, tmp_path, stage, operation, expected):
    invoke = inject_worker(monkeypatch, tmp_path, stage)
    raw = json.dumps(
        {
            "operation": operation,
            "state": "SYNTHETIC ONLY",
            "input_id": "0" * 64,
            "scheme": "C",
            "evidence_kinds": ["request", "answer"],
        }
    ).encode()
    assert invoke(raw) == {"error": expected}
