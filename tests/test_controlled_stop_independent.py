"""Independent durability and last-slot checks for the Q-38 fix."""

import json

import pytest
from test_controlled_replay import prepare
from test_self_review import bundle as bundle

from whynote import controlled_replay as replay
from whynote.replay_laya import ReplayError


@pytest.mark.parametrize("at", [1, 180])
@pytest.mark.parametrize("transport", ["envelope", "exception", "prediction"])
def test_fatal_slot_is_synced_before_stop_and_cannot_be_overwritten(bundle, tmp_path, monkeypatch, at, transport):
    setup, projections = prepare(bundle)
    output = tmp_path / "run"
    calls, synced = [], []
    fsync = replay.os.fsync

    def observe_sync(fd):
        fsync(fd)
        entries = (output / "journal.jsonl").read_text(encoding="utf-8").splitlines()
        synced.append(json.loads(entries[-1]))

    monkeypatch.setattr(replay.os, "fsync", observe_sync)

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

    with pytest.raises(ReplayError, match="^uncaught_error$"):
        replay.execute_slots(setup, projections, invoke, output)
    assert len(calls) == at
    assert [row["event"] for row in synced] == ["started", "completed"] * at + ["stopped"]
    assert synced[-2]["prediction"]["error"] == synced[-1]["error"] == "uncaught_error"
    assert synced[-1]["completed_slots"] == at
    assert not (output / "bundle.json").exists() and not (output / "report.json").exists()
    before = {p.name: p.read_bytes() for p in output.iterdir()}
    with pytest.raises(FileExistsError):
        replay.execute_slots(setup, projections, invoke, output)
    assert len(calls) == at
    assert {p.name: p.read_bytes() for p in output.iterdir()} == before
