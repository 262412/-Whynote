import contextlib
import gzip
import io
import json
import sqlite3
import sys
from types import SimpleNamespace

import pytest

from whynote import explore_inputs as inputs
from whynote.explore_prepare import SCHEMA, prepare
from whynote.explore_report import report
from whynote.explore_run import append, execute
from whynote.replay_laya import ReplayError


def hs(i=0):
    return {
        "context": [{"role": "user", "content": f"SYNTHETIC request {i}"}],
        "response1": "one",
        "response2": "two",
        "feedback1": ["FEEDBACK_SECRET"],
        "feedback2": ["OTHER_SECRET"],
        "domain": "code",
        "language": "python",
    }


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    source = tmp_path / "source.jsonl.gz"
    with gzip.open(source, "wt", encoding="utf-8") as stream:
        for i in range(51):
            stream.write(json.dumps(hs(i)) + "\n")
    pin = (
        "nvidia/HelpSteer3",
        "1" * 40,
        "feedback/validation.jsonl.gz",
        source.stat().st_size,
        inputs.file_hash(source),
    )
    monkeypatch.setitem(inputs.SOURCES, "helpsteer3", pin)
    admission = dict(zip(("repo", "revision", "file", "bytes", "sha256"), pin, strict=True)) | {
        "source": "helpsteer3",
        "status": "ADMITTED_FOR_EXPLORATION",
        "path": str(source),
        "purpose": "local_unlabelled_exploration",
        "approval": "synthetic_test",
        "access": ["project_owner", "local_codex"],
        "backup": False,
        "expires_at": "2099-01-01T00:00:00Z",
        "max_source_records": 1000,
        "max_targets": 1000,
        "allow_all_scan": True,
    }
    manifest = {
        "schema_version": SCHEMA,
        "research_root": str(tmp_path),
        "project_root": str(tmp_path),
        "sources": [admission],
    }
    output = tmp_path / "run"
    prepare(manifest, output, records=51)
    return output, manifest


def factory_for(function):
    class Fake:
        def invoke(self, payload):
            return function(payload)

    @contextlib.contextmanager
    def factory():
        yield Fake()

    return factory


def records(output):
    return [json.loads(line) for line in (output / "journal.jsonl").read_text().splitlines()]


def test_no_labels_non_60_default_c_and_metadata_only(prepared):
    output, _ = prepared
    seen = []

    def invoke(payload):
        seen.append(payload)
        assert set(payload) == {"input_id", "scheme", "state", "evidence_kinds"}
        assert "SECRET" not in json.dumps(payload)
        return {"error": "timeout"}

    result = execute(output, factory_for(invoke), {"test": 1}, progress=lambda _: None)
    summary = report(output)
    assert result["status"] == "COMPLETED"
    assert len(seen) == 102 and {p["scheme"] for p in seen} == {"C"}
    assert summary["planned_slots"] == 102 and summary["buckets"]["technical_failure"] == 102
    assert sum(summary["buckets"].values()) == 102
    assert summary["quality"] == "NOT_EVALUATED" and summary["accuracy"] is None
    for filename in ("journal.jsonl", "results.jsonl", "summary.json", "report.html"):
        text = (output / filename).read_text(encoding="utf-8")
        assert "FEEDBACK_SECRET" not in text and "SYNTHETIC request" not in text


@pytest.mark.parametrize("form", ["envelope", "exception", "prediction"])
def test_uncaught_immediate_stop_and_resume_only_unstarted(prepared, form):
    output, _ = prepared
    calls = []

    def invoke(payload):
        calls.append(payload["input_id"])
        if len(calls) != 7:
            return {"error": "timeout"}
        if form == "exception":
            raise ReplayError("uncaught_error")
        if form == "prediction":
            return {
                "input_id": payload["input_id"],
                "scheme": payload["scheme"],
                "state_sha256": __import__("hashlib").sha256(payload["state"].encode()).hexdigest(),
                "bucket": "technical_failure",
                "error": "uncaught_error",
            }
        return {"error": "uncaught_error"}

    assert execute(output, factory_for(invoke), {"test": 1}, progress=lambda _: None)["status"] == "STOPPED"
    assert len(calls) == 7
    summary = report(output)
    assert summary["buckets"]["technical_failure"] == 7 and summary["buckets"]["not_started"] == 95
    initial = list(calls)
    execute(output, factory_for(invoke), {"test": 1}, resume=True, progress=lambda _: None)
    assert len(calls) == 102 and len(set(calls)) == 102 and calls[:7] == initial


def test_crash_started_without_result_is_not_retried(prepared):
    output, _ = prepared
    (output / "runtime.json").write_text('{"test": 1}')
    with sqlite3.connect(output / "inputs.sqlite3") as db:
        first = db.execute("SELECT input_id FROM inputs ORDER BY ordinal").fetchone()[0]
    append(output / "journal.jsonl", {"event": "started", "input_id": first, "scheme": "C", "attempt_id": "pending"})
    calls = []

    def invoke(payload):
        calls.append(payload["input_id"])
        return {"error": "worker_failed"}

    execute(output, factory_for(invoke), {"test": 1}, resume=True, progress=lambda _: None)
    result = report(output)
    assert len(calls) == 101 and first not in calls
    assert result["buckets"]["interrupted"] == 1 and result["buckets"]["technical_failure"] == 101
    assert result["status"] == "PARTIAL"


def test_cancel_current_result_unknown_no_next_call(prepared):
    output, _ = prepared

    def invoke(_):
        raise KeyboardInterrupt

    result = execute(output, factory_for(invoke), {}, progress=lambda _: None)
    assert result["status"] == "STOPPED"
    summary = report(output)
    assert summary["buckets"]["interrupted"] == 1 and summary["buckets"]["not_started"] == 101


def test_resume_changed_configuration_rejected_before_calls(prepared):
    output, _ = prepared
    execute(output, factory_for(lambda _: {"error": "uncaught_error"}), {"seed": 1}, progress=lambda _: None)
    with pytest.raises(ReplayError, match="resume_fingerprint_changed"):
        execute(output, factory_for(lambda _: pytest.fail("invoked")), {"seed": 2}, resume=True)


@pytest.mark.parametrize("field,value", [("status", "HOLD"), ("backup", True), ("expires_at", "2000-01-01T00:00:00Z")])
def test_missing_usage_rejected(prepared, field, value):
    output, manifest = prepared
    manifest["sources"][0][field] = value
    with pytest.raises(ReplayError):
        prepare(manifest, output.parent / "denied")
    assert not (output.parent / "denied").exists()


def test_wildfb_actual_schema_reference_isolated():
    row = {
        "history": [{"role": "user", "content": "before"}, {"role": "assistant", "content": "prior answer"}],
        "messages": [{"role": "user", "content": "request"}, {"role": "assistant", "content": "target"}],
        "user_feedback": {"role": "user", "content": "FUTURE_SECRET"},
        "label": 1,
        "text": "IGNORE_SECRET",
    }
    result, errors = inputs.map_record("wildfb", {"revision": "r", "sha256": "s"}, 0, row)
    assert not errors and len(result) == 1
    state = json.loads(result[0]["state"])
    assert state["context"] == row["history"] + row["messages"][:1] and state["answer"] == "target"
    assert "SECRET" not in result[0]["state"] and result[0]["reference"]["feedback"] == ["FUTURE_SECRET"]
    row["messages"].append(row["user_feedback"])
    assert not inputs.map_record("wildfb", {"revision": "r", "sha256": "s"}, 0, row)[0]


def utterance(number, content="text"):
    return {
        "UtterranceId": number,
        "TurnId": number // 2 + 1,
        "Role": "User" if number % 2 == 0 else "Agent",
        "Content": content,
        "Summary": "ANNOTATION_SECRET",
        "State": "ANNOTATION_SECRET",
    }


def test_wildfeedback_contiguous_reset_and_no_annotation_leak():
    mapper, batch = inputs.WildFeedbackMapper(), {"revision": "r", "sha256": "s"}
    assert mapper.map(batch, 0, utterance(0))[0] == []
    first = mapper.map(batch, 1, utterance(1))[0][0]
    assert "SECRET" not in first["state"]
    mapper.map(batch, 2, utterance(2))
    second = mapper.map(batch, 3, utterance(3))[0][0]
    assert first["source_conversation_id"] == second["source_conversation_id"]
    assert mapper.map(batch, 4, utterance(5))[1][0]["error"] == "ambiguous_conversation_boundary"
    assert mapper.map(batch, 5, utterance(6))[1]
    mapper.map(batch, 6, utterance(0))
    third = mapper.map(batch, 7, utterance(1))[0][0]
    assert third["source_conversation_id"] != first["source_conversation_id"]


def test_array_parser_bounded_oversize_and_escaped_delimiters():
    raw = json.dumps([{"a": "x" * 500}, {"a": '}["escape"'}, {"a": "ok"}]).encode()
    rows = list(inputs.array_records(io.BytesIO(raw), limit=100))
    assert rows[0][2] == "record_oversized" and rows[1][1] == {"a": '}["escape"'} and rows[2][1] == {"a": "ok"}


@pytest.mark.parametrize("raw", [b'[{"a":1}', b"[{},]", b"[{} {}]", b"{}", b"[{}] trailing"])
def test_array_parser_rejects_broken_boundary(raw):
    with pytest.raises(ReplayError):
        list(inputs.array_records(io.BytesIO(raw)))


def test_helpsteer_sibling_failure_preserves_other_answer():
    row = hs()
    row["response1"] = ""
    mapped, rejected = inputs.map_record("helpsteer3", {"revision": "r", "sha256": "s"}, 0, row)
    assert len(mapped) == len(rejected) == 1 and mapped[0]["target_id"] == "response2"


def test_optional_schemes_and_holdout_accounted(prepared):
    output, manifest = prepared
    second = output.parent / "abc"
    prepare(manifest, second, records=3, schemes=("A", "B", "C"), holdout_percent=99)
    execute(second, factory_for(lambda _: {"error": "timeout"}), {}, progress=lambda _: None)
    result = report(second)
    assert result["planned_slots"] == 18 and sum(result["buckets"].values()) == 18
    assert result["buckets"]["skipped"] > 0


def test_duplicate_completion_rejected(prepared):
    output, _ = prepared
    execute(output, factory_for(lambda _: {"error": "uncaught_error"}), {}, progress=lambda _: None)
    failed = next(r for r in records(output) if r["event"] == "failed")
    append(output / "journal.jsonl", failed)
    with pytest.raises(ReplayError, match="duplicate_or_missing_attempt"):
        report(output)


def test_payload_reference_drift_refused(prepared):
    output, _ = prepared
    with sqlite3.connect(output / "inputs.sqlite3") as db:
        db.execute("UPDATE inputs SET excluded='x'")
    with pytest.raises(ReplayError, match="prepared_inputs_changed"):
        execute(output, factory_for(lambda _: pytest.fail("invoked")), {})


@pytest.mark.parametrize(
    "stage,expected", [("load", "model_load_failed"), ("policy", "uncaught_error"), ("metrics", "uncaught_error")]
)
def test_resident_stage_errors_and_no_second_request(monkeypatch, tmp_path, capsys, stage, expected):
    from whynote import explore_worker as worker

    def fail(*args, **kwargs):
        raise RuntimeError("PRIVATE_FAILURE")

    engine = SimpleNamespace(torch=None, infer=fail)
    monkeypatch.setattr(worker, "Engine", fail if stage == "load" else lambda *args: engine)
    monkeypatch.setattr(worker, "memory", fail if stage == "metrics" else lambda *args: {})
    monkeypatch.setattr(sys, "argv", ["worker", "--model-dir", str(tmp_path)])
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b"{}\n{}\n")))
    worker.main()
    out = capsys.readouterr()
    rows = [json.loads(line) for line in out.out.splitlines()]
    assert rows[-1] == {"error": expected}
    assert len(rows) == (2 if stage == "policy" else 1)
    assert "PRIVATE_FAILURE" not in out.out + out.err


@pytest.mark.parametrize("value", [[], {}, None, "wrong"])
def test_malformed_error_stays_fixed(prepared, value):
    output, _ = prepared
    execute(output, factory_for(lambda _: {"error": value}), {}, progress=lambda _: None)
    result = report(output)
    assert result["error_codes"] == {"invalid_response": 102}


def test_unknown_mode_cli_rejected():
    import subprocess

    run = subprocess.run(
        [sys.executable, "-m", "whynote.explore", "--mode", "evaluate", "report", "--output", "."], capture_output=True
    )
    assert run.returncode == 2 and b"invalid choice" in run.stderr


def test_disk_and_scope_checks_happen_before_creation(prepared, monkeypatch):
    from whynote import explore_prepare as module

    output, manifest = prepared
    with pytest.raises(ReplayError, match="target_scope_exceeded"):
        prepare(manifest, output.parent / "all", records=None)
    monkeypatch.setattr(module.shutil, "disk_usage", lambda _: SimpleNamespace(free=1))
    with pytest.raises(ReplayError, match="insufficient_research_disk"):
        prepare(manifest, output.parent / "full")
    assert not (output.parent / "full").exists()


def test_one_source_hold_keeps_other_source_available(prepared):
    output, manifest = prepared
    manifest["sources"].append({"source": "wildfb", "status": "ADMITTED_FOR_EXPLORATION", "sha256": "wrong"})
    result = prepare(manifest, output.parent / "partial", records=2)
    assert result["targets"] == 4
    assert result["holds"] == [{"source": "wildfb", "status": "HOLD", "reason": "source_identity_changed"}]


def test_all_requires_separate_scope_then_accepts(prepared):
    output, manifest = prepared
    manifest["sources"][0]["allow_all_targets"] = True
    result = prepare(manifest, output.parent / "all_allowed", records=None)
    assert result["targets"] == 102


def test_budget_coverage_does_not_count_unmeasured_load_failures(prepared):
    output, _ = prepared
    execute(output, factory_for(lambda _: {"error": "model_load_failed"}), {}, progress=lambda _: None)
    result = report(output)
    assert all(s["runnable"]["numerator"] == 0 for s in result["strata"])


def test_report_can_read_without_replacing_execution_index(prepared):
    output, _ = prepared
    execute(output, factory_for(lambda _: {"error": "uncaught_error"}), {}, progress=lambda _: None)
    before = (output / "index.sqlite3").read_bytes()
    report(output)
    assert (output / "index.sqlite3").read_bytes() == before


def test_explicit_target_limit_cannot_bypass_scope_with_small_record_count(prepared):
    output, manifest = prepared
    manifest["sources"][0]["max_targets"] = 1
    with pytest.raises(ReplayError, match="target_scope_exceeded"):
        prepare(manifest, output.parent / "too_many", records=2, targets=2)
    assert not (output.parent / "too_many").exists()


def test_default_smoke_respects_smaller_source_target_cap(prepared):
    output, manifest = prepared
    manifest["sources"][0]["max_targets"] = 1
    result = prepare(manifest, output.parent / "small_cap", records=2)
    assert result["targets"] == 1
    assert result["source_counts"]["helpsteer3"]["targets_not_selected"] == 1


def test_all_scan_still_obeys_source_record_scope(prepared):
    output, manifest = prepared
    manifest["sources"][0]["max_source_records"] = 1
    result = prepare(manifest, output.parent / "bounded_all", records=None, targets=100)
    assert result["targets"] == 2
    assert result["source_counts"]["helpsteer3"]["scanned"] == 1


@pytest.mark.parametrize("limit", [0, -1, True, "100"])
def test_invalid_source_scope_is_held_before_output_creation(prepared, limit):
    output, manifest = prepared
    manifest["sources"][0]["max_targets"] = limit
    with pytest.raises(ReplayError, match="no_admitted_sources"):
        prepare(manifest, output.parent / "invalid_scope", records=2)
    assert not (output.parent / "invalid_scope").exists()
