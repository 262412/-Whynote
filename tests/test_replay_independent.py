"""Independent synthetic M5-1 checks; these are not quality labels."""

import copy
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from whynote import replay
from whynote.replay_laya import ReplayError, validate_response
from whynote.replay_metrics import quality_metrics

FIXTURES = Path(__file__).parents[1] / "fixtures"


class ScriptedBackend:
    metadata = {"backend": "test_stub"}

    def __init__(self, *, fail_at=None, yes=False):
        self.calls = []
        self.fail_at = fail_at
        self.yes = yes

    def predict(self, state, questions):
        self.calls.append((state, copy.deepcopy(questions)))
        if len(self.calls) == self.fail_at:
            raise ReplayError("timeout")
        answers = {}
        for key, question in questions.items():
            if question["type"] == "noul":
                answers[key] = {"type": "noul", "noul": 0.5}
                continue
            choice = (
                "general" if key == "route" else "style" if key == "primary_reason" else "yes" if self.yes else "no"
            )
            answers[key] = {
                "type": "choice",
                "choice": choice,
                "probabilities": {name: float(name == choice) for name in question["criteria"]},
            }
        return {"answers": validate_response({"model": "laya-rl-agent", "answers": answers}, questions)}


def fixture_copy(tmp_path):
    for name in ("m5-synthetic", "m5-task-reasons"):
        shutil.copytree(FIXTURES / name, tmp_path / name)
    shutil.copyfile(FIXTURES / "m5-replay.json", tmp_path / "m5-replay.json")
    return tmp_path / "m5-replay.json"


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def repin(path, prefix):
    value = json.loads(path.read_bytes())
    value[prefix + "_sha256"] = hashlib.sha256((path.parent / value[prefix + "_path"]).read_bytes()).hexdigest()
    save(path, value)


def test_developer_annotations_cannot_change_prediction_state(tmp_path):
    manifest = fixture_copy(tmp_path)
    before, _ = replay.load_samples(manifest)
    exploration = tmp_path / "m5-task-reasons/exploration.json"
    data = json.loads(exploration.read_bytes())
    for row in data["cases"]:
        row.update(
            task_types=["other"],
            developer_review="SYNTHETIC_HIDDEN_LABEL",
            reason_ids=["general.outdated"],
            outcome="unknown",
            evidence_kinds=["reference"],
            fallback_tasks=["general"],
        )
    save(exploration, data)
    repin(manifest, "exploration")
    after, _ = replay.load_samples(manifest)
    assert [s["state"] for s in before] == [s["state"] for s in after]
    assert [s["state_sha256"] for s in before] == [s["state_sha256"] for s in after]
    assert [s["evidence_kinds"] for s in before] == [s["evidence_kinds"] for s in after]
    assert all(s["task"] == "other" for s in after[4:])
    assert all("SYNTHETIC_HIDDEN_LABEL" not in s["state"] for s in after)


def test_incomplete_screening_retains_three_failures_without_model_input(tmp_path):
    manifest = fixture_copy(tmp_path)
    review_path = tmp_path / "m5-synthetic/wildfb-reviews.json"
    reviews = json.loads(review_path.read_bytes())
    reviews["0"]["targets"]["conversation"]["screening_ref"] = None
    save(review_path, reviews)
    batch_path = tmp_path / "m5-synthetic/batch.json"
    batch = json.loads(batch_path.read_bytes())
    batch["sources"][0]["reviews_sha256"] = hashlib.sha256(review_path.read_bytes()).hexdigest()
    save(batch_path, batch)
    repin(manifest, "source_batch")
    samples, inputs = replay.load_samples(manifest)
    assert len(samples) == 11
    assert samples[0]["state"] is None and samples[0]["length_bucket"] == "unknown"
    backend = ScriptedBackend()
    report = replay.run_replay(samples, inputs, tmp_path / "output", backend)
    assert all(state is not None for state, _ in backend.calls)
    assert all(stats["attempts"] == 11 and stats["valid_outputs"] == 10 for stats in report["by_scheme"].values())
    for stats in report["strata"]["length_bucket"]["unknown"].values():
        assert stats["statuses"] == {"source_review_incomplete": 1}
    rows = [json.loads(line) for line in (tmp_path / "output/predictions.jsonl").read_text().splitlines()]
    rejected = [row for row in rows if row["source"] == "wildfb"]
    assert len(rejected) == 3
    assert all(row["stages"] == [] and row["reason_ids"] == [] for row in rejected)


@pytest.mark.parametrize("fail_at", [1, 2, 3])
def test_each_c_stage_failure_preserves_attempt_and_never_selects(fail_at):
    item = replay.load_samples(FIXTURES / "m5-replay.json")[0][4]
    backend = ScriptedBackend(fail_at=fail_at)
    row = replay.infer_scheme(backend, item, "C")
    assert row["status"] == "error" and row["error"] == "timeout"
    assert row["reason_ids"] == [] and row["outcome"] is None
    assert len(row["stages"]) == len(backend.calls) == fail_at
    assert row["stages"][-1]["error"] == "timeout"
    assert all(state == item["state"] for state, _ in backend.calls)
    counts = replay.summarize([row])["by_scheme"]["C"]
    assert counts["attempts"] == 1 and counts["valid_outputs"] == 0
    assert counts["statuses"] == {"timeout": 1}


def test_tied_positive_scores_use_frozen_order_without_unneeded_fallback():
    item = replay.load_samples(FIXTURES / "m5-replay.json")[0][4]
    row = replay.infer_scheme(ScriptedBackend(yes=True), item, "C")
    assert row["reason_ids"] == ["general.instruction_not_followed"]
    assert not row["fallback_used"]
    assert [stage["stage"] for stage in row["stages"]] == ["route", "reason"]
    assert row["attribution_source"] == "test_stub" and row["calibrated"] is False


@pytest.mark.parametrize(
    "size,bucket",
    [
        (1024, "0-1024"),
        (1025, "1025-4096"),
        (4096, "1025-4096"),
        (4097, "4097-8192"),
        (8192, "4097-8192"),
        (8193, "over-8192"),
    ],
)
def test_utf8_length_boundaries_count_failures_in_all_three_schemes(size, bucket):
    overhead = len(replay.state_text({"request": "", "answer": "x"}).encode())
    filler, remainder = divmod(size - overhead, 3)
    item = replay.sample(
        {"request": "中" * filler + "x" * remainder, "answer": "x"},
        identity={"synthetic_size": size},
        source="task_examples",
        task="general",
        language="zh",
        evidence=["request", "answer"],
        partition="exploration",
    )
    assert item["input_utf8_bytes"] == size
    backend = ScriptedBackend(fail_at=1)
    rows = [replay.infer_scheme(backend, item, scheme) for scheme in ("A", "B", "C")]
    groups = replay.summarize(rows)["strata"]["length_bucket"]
    assert set(groups) == {bucket}
    assert all(stats["attempts"] == 1 for stats in groups[bucket].values())
    if size > 8192:
        assert not backend.calls
        assert all(row["error"] == "input_bytes_exceeded" for row in rows)
    else:
        assert rows[0]["error"] == "timeout"


def test_strata_and_percentiles_include_failed_attempts_exactly_once():
    samples, _ = replay.load_samples(FIXTURES / "m5-replay.json")
    rows = [
        replay.infer_scheme(ScriptedBackend(fail_at=1), item, scheme) for item in samples for scheme in ("A", "B", "C")
    ]
    for index, row in enumerate(rows):
        row["elapsed_ms"] = (index // 3 + 1) * 10
    report = replay.summarize(rows)
    assert report["quality_metrics"] is None and report["user_cost_metrics"] is None
    for scheme in ("A", "B", "C"):
        counts = report["by_scheme"][scheme]
        assert counts["attempts"] == 11 and counts["valid_outputs"] == 0
        assert counts["elapsed_ms"] == {"p50": 60, "p95": 110}
        for dimension in ("source", "task", "language", "length_bucket"):
            assert sum(group[scheme]["attempts"] for group in report["strata"][dimension].values()) == 11


def test_quality_accounting_keeps_errors_no_defect_and_unjudgeable_separate():
    # Fictional adjudication metadata tests arithmetic only, never real model evidence.
    samples = []
    rows = []
    labels = {"schema_version": "m5-replay-labels-v1", "items": []}
    for index, verdict in enumerate(("defect", "defect", "no_defect", "unjudgeable")):
        item = replay.sample(
            {"request": "Synthetic explicit request", "answer": "Synthetic answer"},
            identity={"qa_accounting_case": index},
            source="task_examples",
            task="general",
            language="en",
            evidence=["request", "answer"],
            partition="holdout",
        )
        samples.append(item)
        for scheme in ("A", "B", "C"):
            rows.append(replay.infer_scheme(ScriptedBackend(fail_at=1 if index == 1 else None, yes=True), item, scheme))
        labels["items"].append(
            {
                "input_id": item["input_id"],
                "partition": "holdout",
                "review_origin": "independent_adjudicated",
                "reviewer_refs": ["00000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-000000000002"],
                "adjudication_ref": "00000000-0000-4000-8000-000000000003",
                "verdict": verdict,
                "reason_ids": ["general.instruction_not_followed"] if verdict == "defect" else [],
                "legacy_reason_codes": ["style"] if verdict == "defect" else [],
                "reference_task": "general",
                "reference_domain": "general",
            }
        )
    manifest = {
        "schema_version": replay.VERSION,
        "protocol": copy.deepcopy(replay.PROTOCOL),
        "protocol_sha256": replay.digest(replay.PROTOCOL),
        "samples": [{k: v for k, v in item.items() if k != "state"} for item in samples],
        "backend": {"backend": "laya_local", "model": replay.MODEL},
    }
    manifest["run_id"] = replay.digest(manifest)
    for row in rows:
        row["run_id"] = manifest["run_id"]
    metrics = quality_metrics(rows, samples, labels, run_manifest=manifest)["C"]
    assert metrics["candidate_hit"] == {"numerator": 1, "denominator": 2, "value": 0.5}
    assert metrics["misleading"] == {"numerator": 1, "denominator": 2, "value": 0.5}
    assert metrics["judgment_error"] == {"numerator": 0, "denominator": 1, "value": 0.0}
    assert metrics["routing_omission"] == {"numerator": 0, "denominator": 2, "value": 0.0}
    assert metrics["runtime_errors"] == metrics["routing_unavailable"] == metrics["unjudgeable"] == 1
    labels["items"].pop()
    with pytest.raises(ReplayError, match="^incomplete_holdout_labels$"):
        quality_metrics(rows, samples, labels, run_manifest=manifest)
