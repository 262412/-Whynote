"""Synthetic source sentinels and fixed-slot failure accounting, never real labels."""

import gzip
import hashlib
import json
import subprocess
import sys

import pytest
from test_self_review import bundle as bundle_fixture
from test_self_review import repin

from whynote.controlled_inputs import group_hash, project_helpsteer, selected_rows
from whynote.controlled_replay import execute_real, execute_synthetic, prediction, source_hash
from whynote.replay_laya import ReplayError
from whynote.replay_runtime import model_lock, plain_synthetic_process
from whynote.source_mapping import REVISIONS, digest

bundle = bundle_fixture


def source_row():
    return {
        "context": [{"role": "user", "content": "Rewrite:\n```python\nx=1\n```"}],
        "response1": "FIRST_ANSWER",
        "response2": "SECOND_ANSWER",
        "feedback1": ["FEEDBACK_ONE"],
        "feedback2": ["FEEDBACK_TWO"],
        "domain": "code",
        "language": "python",
    }


@pytest.mark.parametrize("target", ["response1", "response2"])
def test_target_association_and_no_reference_leak(target):
    row = source_row()
    actual = project_helpsteer(row, target)
    state = json.loads(actual["state"])
    assert state == {"context": row["context"], "answer": row[target]}
    assert (
        "FEEDBACK" not in actual["state"]
        and row["response2" if target == "response1" else "response1"] not in actual["state"]
    )
    assert actual["feedback_origin"] == "evaluator"
    assert actual["evidence_kinds"] == ["request", "answer", "original_code"]
    assert actual["state_sha256"] == hashlib.sha256(actual["state"].encode()).hexdigest()


def test_budget_material_does_not_infer_code_from_domain():
    row = source_row()
    row["context"][0]["content"] = "Create new Python code."
    assert "original_code" not in project_helpsteer(row, "response1")["evidence_kinds"]


def test_bounded_reader_does_not_parse_row_201(tmp_path):
    path = tmp_path / "source.gz"
    with gzip.open(path, "wb") as stream:
        stream.write((json.dumps(source_row()).encode() + b"\n") * 200 + b"INVALID UNADMITTED ROW\n")
    assert len(selected_rows(path, set(range(200)), maximum_row=199)) == 200
    with pytest.raises(ReplayError, match="invalid_selected_rows"):
        selected_rows(path, {200}, maximum_row=199)


def test_normalized_group_matches_fullwidth_and_spacing():
    assert group_hash([{"role": "user", "content": "Ａ   B\nC"}]) == group_hash([{"role": "user", "content": "A B C"}])


def prepare(bundle):
    projections = {}
    for sample in bundle["manifest"]["samples"]:
        state = "SYNTHETIC ONLY " + sample["input_id"]
        sample["state_sha256"] = hashlib.sha256(state.encode()).hexdigest()
        projections[sample["input_id"]] = {"state": state, "evidence_kinds": ["request", "answer"]}
    repin(bundle)
    return {k: bundle[k] for k in ("manifest", "labels", "run")}, projections


def test_180_slots_no_retry_fixed_failure_and_no_raw_output(bundle, tmp_path):
    setup, projections = prepare(bundle)
    calls = []

    def invoke(payload):
        value = json.loads(payload)
        assert set(value) == {"operation", "state", "input_id", "scheme", "evidence_kinds"}
        calls.append((value["input_id"], value["scheme"]))
        if len(calls) % 3 == 0:
            raise ReplayError("timeout")
        return {"error": "model_load_failed"}

    output = tmp_path / "run"
    result = execute_synthetic(setup, projections, invoke, output)
    assert len(calls) == len(set(calls)) == 180
    assert calls == [(s["input_id"], k) for s in setup["run"]["schedule"] for k in s["schemes"]]
    journal = [json.loads(line) for line in (output / "journal.jsonl").read_text().splitlines()]
    assert [r["event"] for r in journal] == ["started", "completed"] * 180
    assert sum(r["prediction"]["error"] == "timeout" for r in journal if r["event"] == "completed") == 60
    assert result["production_approved"] is False
    assert "SYNTHETIC ONLY " not in (output / "journal.jsonl").read_text()
    with pytest.raises(FileExistsError):
        execute_synthetic(setup, projections, invoke, output)
    assert len(calls) == 180


def test_ineligible_slots_remain_without_worker_call(bundle, tmp_path):
    setup, projections = prepare(bundle)
    for sample in setup["manifest"]["samples"]:
        sample["material_ready"] = False
    repin(bundle)
    result = execute_synthetic(setup, projections, lambda _: pytest.fail("worker called"), tmp_path / "run")
    assert result["by_scheme"]["C"]["counts"]["input_material_missing"] == 60


@pytest.mark.parametrize("failure", [KeyboardInterrupt, RuntimeError])
def test_cancel_and_unknown_failure_preserve_partial_journal(bundle, tmp_path, failure):
    setup, projections = prepare(bundle)
    count = 0

    def invoke(_):
        nonlocal count
        count += 1
        if count == 2:
            raise failure()
        return {"error": "timeout"}

    with pytest.raises(failure):
        execute_synthetic(setup, projections, invoke, tmp_path / "run")
    journal = [json.loads(line) for line in (tmp_path / "run/journal.jsonl").read_text().splitlines()]
    assert [r["event"] for r in journal] == ["started", "completed", "started", "stopped"]
    assert journal[-1]["completed_slots"] == 1
    with model_lock():
        pass


def test_synthetic_entry_rejects_real_before_output(bundle, tmp_path):
    setup, projections = prepare(bundle)
    setup["manifest"]["mode"] = "real"
    with pytest.raises(ReplayError, match="synthetic_cannot_execute_real"):
        execute_synthetic(setup, projections, lambda _: pytest.fail(), tmp_path / "run")
    assert not (tmp_path / "run").exists()


def test_real_review_only_admission_stops_before_model(bundle, tmp_path):
    setup, _ = prepare(bundle)
    setup["manifest"]["mode"] = "real"
    setup["manifest"]["admissions"][0]["status"] = "ADMITTED_FOR_REVIEW_ONLY"
    with pytest.raises(ReplayError, match="batch_not_admitted"):
        execute_real(setup, {}, {}, tmp_path / "run")
    assert not (tmp_path / "run").exists()


@pytest.mark.parametrize("bad_error", [[], {}, "private text", None])
def test_malformed_error_is_fixed_classification(bundle, bad_error):
    sample = bundle["manifest"]["samples"][0]
    assert prediction(sample, "C", bundle["run"], {"error": bad_error}, 1)["error"] == "invalid_response"


def test_a_unknown_is_abstention_not_a_label(bundle):
    sample = bundle["manifest"]["samples"][0]
    old = {
        "input_id": sample["input_id"],
        "state_sha256": sample["state_sha256"],
        "scheme": "A",
        "status": "ok",
        "outcome": "unknown",
        "routed_ids": ["other_or_unknown"],
        "available_ids": ["other_or_unknown"],
        "reason_ids": [],
    }
    row = prediction(sample, "A", bundle["run"], {"prediction": old}, 1)
    assert row["status"] == "ok" and row["outcome"] == "unknown" and row["routed_ids"] == []


def test_material_drift_stops_before_child(bundle, tmp_path):
    setup, projections = prepare(bundle)
    for projection in projections.values():
        projection["state"] += "changed"
    with pytest.raises(ReplayError, match="prediction_material_changed"):
        execute_synthetic(setup, projections, lambda _: pytest.fail(), tmp_path / "run")


def test_source_hash_sees_untracked_module(tmp_path, monkeypatch):
    import whynote.controlled_replay as module

    monkeypatch.setattr(module, "__file__", str(tmp_path / "controlled_replay.py"))
    first = source_hash()
    (tmp_path / "untracked_route.py").write_text("print('different')")
    assert source_hash() != first


def test_lock_rejects_other_process_and_releases():
    code = "from whynote.replay_runtime import model_lock\nwith model_lock(): print('acquired')"
    with model_lock():
        process = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True)
        assert process.returncode != 0 and b"model_busy" in process.stderr
    process = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True)
    assert process.returncode == 0 and b"acquired" in process.stdout


def test_child_deadline():
    with pytest.raises(ReplayError, match="timeout"):
        plain_synthetic_process([sys.executable, "-I", "-c", "import time;time.sleep(30)"], b"", timeout=0.1)


def test_fixed_successful_policy_replay_and_abstentions(bundle, tmp_path):
    from whynote.replay import infer_scheme, length_bucket

    setup, projections = prepare(bundle)

    class Backend:
        metadata = {"backend": "test_stub"}

        def predict(self, state, questions):
            answers = {}
            for key, question in questions.items():
                if question["type"] == "noul":
                    answers[key] = {"noul": 0.0}
                    continue
                choice = "other_or_unknown" if key == "primary_reason" else "general" if key == "route" else "no"
                answers[key] = {
                    "choice": choice,
                    "probabilities": {k: float(k == choice) for k in question["criteria"]},
                }
            return {"answers": answers, "budget": {}}

    def invoke(raw):
        payload = json.loads(raw)
        state = payload["state"]
        item = {
            "input_id": payload["input_id"],
            "state": state,
            "source": "synthetic",
            "task": "unknown",
            "language": "unknown",
            "partition": "validation",
            "input_error": None,
            "state_sha256": hashlib.sha256(state.encode()).hexdigest(),
            "input_utf8_bytes": len(state.encode()),
            "length_bucket": length_bucket(len(state.encode())),
            "evidence_kinds": payload["evidence_kinds"],
        }
        return {"prediction": infer_scheme(Backend(), item, payload["scheme"])}

    result = execute_synthetic(setup, projections, invoke, tmp_path / "run")
    rows = json.loads((tmp_path / "run/bundle.json").read_text())["predictions"]
    assert len(rows) == 180 and all(row["status"] == "ok" for row in rows)
    # A chooses its unknown sentinel; B/C keep unknown because reference evidence is absent.
    assert sum(row["outcome"] == "unknown" for row in rows) == 180
    assert result["production_approved"] is False


@pytest.mark.parametrize(
    "mutation,expected",
    [
        ("none", None),
        ("exposed", "validation_already_exposed"),
        ("target", "source_identity_changed"),
        ("group", "conversation_group_changed"),
        ("state", "prediction_material_changed"),
        ("ledger", "exposure_ledger_changed"),
    ],
)
def test_real_projection_binding_and_exposure(tmp_path, mutation, expected):
    from whynote.controlled_inputs import input_identity, load_projections

    path = tmp_path / "source.gz"
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        stream.write(json.dumps(source_row()) + "\n")
    projection = project_helpsteer(source_row(), "response1")
    exposure = {"schema_version": "m54a-exposed-groups-v1", "groups": []}
    if mutation == "exposed":
        exposure["groups"] = [projection["normalized_group_sha256"]]
    batch = {
        "source": "helpsteer3",
        "revision": REVISIONS["helpsteer3"],
        "file_path": str(path),
        "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "batch_id": "test",
        "row_end": 0,
        "exclusions_sha256": digest(exposure),
    }
    sample = {
        "input_id": input_identity(batch, 0, "response1"),
        "batch_id": "test",
        "row_id": 0,
        "target_key": "response1",
        "partition": "validation",
        "state_sha256": projection["state_sha256"],
        "input_utf8_bytes": projection["input_utf8_bytes"],
        "group_ids": {"conversation": projection["normalized_group_sha256"]},
    }
    if mutation == "target":
        sample["target_key"] = "response2"
    if mutation == "group":
        sample["group_ids"]["conversation"] = "0" * 64
    if mutation == "state":
        sample["state_sha256"] = "0" * 64
    if mutation == "ledger":
        exposure["groups"] = ["0" * 64]
    manifest = {"admissions": [batch], "samples": [sample]}
    if expected:
        with pytest.raises(ReplayError, match=expected):
            load_projections(manifest, {"test": exposure})
    else:
        assert load_projections(manifest, {"test": exposure})[sample["input_id"]] == projection


def test_sdk_failure_is_not_model_load_failure(monkeypatch):
    import whynote.controlled_worker as worker

    class Agent:
        def predict(self, state, questions):
            raise RuntimeError("SYNTHETIC PRIVATE FAILURE")

    monkeypatch.setattr(worker, "check_budget", lambda *args: {})
    with pytest.raises(ReplayError, match="^worker_failed$"):
        worker.checked_prediction(Agent(), "synthetic", {})


def test_timeout_is_unavailable_route_not_false_no_attempt(bundle):
    sample = bundle["manifest"]["samples"][0]
    assert prediction(sample, "C", bundle["run"], {"error": "timeout"}, 60001)["route_status"] == "failed"
    assert (
        prediction(sample, "C", bundle["run"], {"error": "model_load_failed"}, 100)["route_status"] == "not_attempted"
    )
