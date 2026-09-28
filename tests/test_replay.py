import copy
import hashlib
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from whynote import replay
from whynote.replay_laya import LayaReplay, ReplayError, check_budget, validate_response
from whynote.replay_metrics import quality_metrics

FIXTURES = Path(__file__).parents[1] / "fixtures"


class Backend:
    metadata = {"backend": "test_stub"}

    def __init__(self, route="general", positive="code.interface_changed", failure=None):
        self.route, self.positive, self.failure = route, positive, failure
        self.calls = []

    def predict(self, state, questions):
        self.calls.append((state, copy.deepcopy(questions)))
        if self.failure:
            raise ReplayError(self.failure)
        answers = {}
        for key, question in questions.items():
            if question["type"] == "noul":
                answers[key] = {"noul": 0.5}
            else:
                choice = (
                    self.route
                    if key == "route"
                    else ("factual_error" if key == "primary_reason" else "yes" if key == self.positive else "no")
                )
                answers[key] = {
                    "choice": choice,
                    "probabilities": {k: float(k == choice) for k in question["criteria"]},
                }
        return {"answers": answers, "budget": {"state_tokens": 10}}


def example():
    return replay.load_samples(FIXTURES / "m5-replay.json")[0][4]


def copied_manifest(tmp_path):
    shutil.copytree(FIXTURES / "m5-synthetic", tmp_path / "m5-synthetic")
    shutil.copytree(FIXTURES / "m5-task-reasons", tmp_path / "m5-task-reasons")
    shutil.copy(FIXTURES / "m5-replay.json", tmp_path / "m5-replay.json")
    return tmp_path / "m5-replay.json"


def pin(manifest_path, kind):
    manifest = json.loads(manifest_path.read_bytes())
    manifest[f"{kind}_sha256"] = hashlib.sha256(
        (manifest_path.parent / manifest[f"{kind}_path"]).read_bytes()
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def test_three_sources_and_exploration_inputs_exclude_all_post_answer_evidence():
    samples, inputs = replay.load_samples(FIXTURES / "m5-replay.json")
    assert len(samples) == 11
    assert [s["source"] for s in samples[:4]] == ["wildfb", "helpsteer3", "helpsteer3", "wildfeedback"]
    for item in samples:
        assert all(
            marker not in item["state"]
            for marker in ("FUTURE", "FEEDBACK", "developer_review", "reason_ids", "task_types")
        )
    assert all(s["evidence_kinds"] == ["request", "answer"] for s in samples[:4])
    assert inputs["source_report"]["quality_metrics"] is None


@pytest.mark.parametrize("status,mode", [("HOLD", "synthetic"), ("admitted", "public"), ("excluded", "synthetic")])
def test_unadmitted_source_not_opened_and_other_sources_continue(tmp_path, monkeypatch, status, mode):
    manifest = copied_manifest(tmp_path)
    batch_path = tmp_path / "m5-synthetic/batch.json"
    batch = json.loads(batch_path.read_bytes())
    batch["sources"][0].update(status=status, mode=mode)
    batch_path.write_text(json.dumps(batch), encoding="utf-8")
    pin(manifest, "source_batch")
    original = Path.open

    def guarded(path, *args, **kwargs):
        assert path.name not in ("wildfb.json", "wildfb-reviews.json")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    samples, inputs = replay.load_samples(manifest)
    assert len(samples) == 10
    assert inputs["source_report"]["sources"][0]["status"] == "HOLD"


def test_source_corruption_does_not_suppress_other_sources(tmp_path):
    manifest = copied_manifest(tmp_path)
    (tmp_path / "m5-synthetic/wildfb.json").write_text("SENSITIVE_CORRUPTION", encoding="utf-8")
    samples, inputs = replay.load_samples(manifest)
    assert len(samples) == 10
    assert inputs["source_report"]["sources"][0]["reason"] == "file_hash_mismatch"
    assert "SENSITIVE" not in json.dumps(inputs)


def test_a_keeps_old_interface_b_single_domain_c_can_recover_across_tasks():
    item = example()
    back = Backend()
    outputs = [replay.infer_scheme(back, item, scheme) for scheme in ("A", "B", "C")]
    assert outputs[0]["reason_ids"] == ["factual_error"]
    assert outputs[1]["routed_ids"] == replay.DOMAIN["general"]
    assert outputs[1]["reason_ids"] == []
    assert outputs[2]["reason_ids"] == ["code.interface_changed"]
    assert outputs[2]["fallback_used"] is True
    assert [s["stage"] for s in outputs[2]["stages"]] == ["route", "reason", "fallback_reason"]
    assert len({state for state, _ in back.calls}) == 1
    assert all(o["attribution_source"] == "test_stub" for o in outputs)
    assert back.calls[0][1] == replay.QUESTIONS


def test_missing_original_material_excludes_code_and_does_not_trigger_fallback():
    item = replay.load_samples(FIXTURES / "m5-replay.json")[0][0]
    result = replay.infer_scheme(Backend(route="mixed"), item, "C")
    assert set(replay.DOMAIN["code"]) <= set(result["unavailable_ids"])
    assert result["outcome"] == "unknown"
    assert not result["fallback_used"]
    assert not set(replay.DOMAIN["code"]) & set(result["available_ids"])


@pytest.mark.parametrize(
    "failure",
    [
        "timeout",
        "invalid_response",
        "reserved_token",
        "question_tokens_exceeded",
        "input_tokens_exceeded",
        "backend_unavailable",
    ],
)
def test_failures_keep_denominators_and_no_reason(failure):
    outputs = [replay.infer_scheme(Backend(failure=failure), example(), scheme) for scheme in ("A", "B", "C")]
    report = replay.summarize(outputs)
    for scheme in ("A", "B", "C"):
        stats = report["by_scheme"][scheme]
        assert stats["attempts"] == 1 and stats["valid_outputs"] == 0
        assert stats["statuses"] == {failure: 1}
        assert stats["elapsed_ms"]["p95"] is not None
    assert all(not r["reason_ids"] for r in outputs)


@pytest.mark.parametrize("error", ["source_review_incomplete", "input_bytes_exceeded"])
def test_pre_model_rejection_never_calls_backend(error):
    item = example()
    if error == "input_bytes_exceeded":
        item["state"] = "敏" * 2731
    else:
        item["input_error"] = error
    backend = Backend()
    result = replay.infer_scheme(backend, item, "C")
    assert result["error"] == error
    assert not backend.calls


def test_replay_outputs_no_raw_text_no_overwrite_and_complete_per_source_reports(tmp_path):
    samples, inputs = replay.load_samples(FIXTURES / "m5-replay.json")
    output = tmp_path / "run"
    report = replay.run_replay(samples, inputs, output, Backend())
    assert report["sample_count"] == 11 and report["quality_metrics"] is None
    predictions = [json.loads(line) for line in (output / "predictions.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(predictions) == 33
    raw = "".join(path.read_text(encoding="utf-8") for path in output.iterdir())
    assert "def parse(" not in raw and "SYNTHETIC_FUTURE" not in raw
    assert '"state":' not in raw
    assert len(list(output.glob("*-report.json"))) == 4
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir()}
    with pytest.raises(FileExistsError):
        replay.run_replay(samples, inputs, output, Backend())
    assert hashes == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir()}


def test_disabled_cli_rejects_before_input_read_or_model_load(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(replay, "load_samples", lambda _: pytest.fail("input read while disabled"))
    assert replay.main(["--manifest", "secret", "--model-dir", "secret", "--output", str(tmp_path / "out")]) == 1
    assert '"code": "model_disabled"' in capsys.readouterr().out
    assert not (tmp_path / "out").exists()


def test_load_failure_keeps_all_attempts_and_safe_evidence(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise ReplayError("model_load_failed")

    monkeypatch.setattr(replay, "LayaReplay", fail)
    output = tmp_path / "run"
    assert (
        replay.main(
            [
                "--manifest",
                str(FIXTURES / "m5-replay.json"),
                "--output",
                str(output),
                "--model-dir",
                "unused",
                "--enable-local-model",
            ]
        )
        == 2
    )
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["backend"]["model_loaded"] is False
    assert all(v["statuses"] == {"model_load_failed": 11} for v in report["by_scheme"].values())


@pytest.mark.parametrize(
    "change", ["missing", "model", "nan", "choice", "unknown_key", "negative", "bool", "sum", "type"]
)
def test_invalid_model_responses_cannot_escape(change):
    questions = replay.ROUTE_B
    response = {
        "model": "laya-rl-agent",
        "answers": {"route": {"type": "choice", "choice": "code", "probabilities": {"code": 0.8, "general": 0.2}}},
    }
    answer = response["answers"]["route"]
    if change == "missing":
        response["answers"] = {}
    elif change == "model":
        response["model"] = "another_model"
    elif change == "choice":
        answer["choice"] = "general"
    elif change == "unknown_key":
        answer["probabilities"]["sensitive"] = 0.1
    elif change == "type":
        answer["type"] = "noul"
    else:
        answer["probabilities"]["code"] = {"nan": float("nan"), "negative": -0.1, "bool": True, "sum": 0.1}[change]
    with pytest.raises(ReplayError, match="^invalid_response$"):
        validate_response(response, questions)


def test_timeout_terminates_worker_and_prevents_followup_send():
    backend = object.__new__(LayaReplay)
    backend.alive, backend.timeout = True, 0.01
    calls = []
    backend.connection = SimpleNamespace(poll=lambda _: False, close=lambda: calls.append("closed"))
    backend.process = SimpleNamespace(
        is_alive=lambda: "terminated" not in calls, terminate=lambda: calls.append("terminated"), join=lambda **_: None
    )
    with pytest.raises(ReplayError, match="^timeout$"):
        backend._receive()
    assert calls == ["terminated", "closed"]
    with pytest.raises(ReplayError, match="^backend_unavailable$"):
        backend.predict("secret", {})


@pytest.mark.parametrize(
    "state,option_size,ins_size,error",
    [
        ("x" * 701, 2, 2, "input_tokens_exceeded"),
        ("x", 49, 2, "question_tokens_exceeded"),
        ("x", 48, 161, "question_tokens_exceeded"),
        ("[MASK]", 2, 2, "reserved_token"),
        ("敏" * 2731, 2, 2, "input_bytes_exceeded"),
    ],
)
def test_budget_rejects_before_sdk_can_truncate(monkeypatch, state, option_size, ins_size, error):
    common = SimpleNamespace(
        render_options=lambda _: ["option", "option"],
        encode_text=lambda _, text, **kw: {
            "input_ids": [0]
            * (option_size if text.startswith(" option") else ins_size if "question:" in text else len(text))
        },
    )
    monkeypatch.setitem(sys.modules, "laya", SimpleNamespace(common=common))
    monkeypatch.setitem(sys.modules, "laya.common", common)
    agent = SimpleNamespace(
        tok=SimpleNamespace(all_special_tokens=["[MASK]"]),
        _to_internal=lambda q: {"t": q["type"], "ins": q["instructions"]},
    )
    with pytest.raises(ReplayError, match=error):
        check_budget(agent, state, replay.ROUTE_B)


def test_quality_metrics_keep_library_routing_material_and_judgment_separate():
    samples = [example()]
    samples[0]["partition"] = "holdout"
    rows = [replay.infer_scheme(Backend(), samples[0], scheme) for scheme in ("A", "B", "C")]
    labels = {
        "schema_version": "m5-replay-labels-v1",
        "items": [
            {
                "input_id": samples[0]["input_id"],
                "partition": "holdout",
                "review_origin": "independent_adjudicated",
                "reviewer_refs": ["00000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-000000000002"],
                "adjudication_ref": "00000000-0000-4000-8000-000000000003",
                "verdict": "defect",
                "reason_ids": ["code.interface_changed"],
                "legacy_reason_codes": [],
                "reference_task": "code_rewrite",
                "reference_domain": "code",
            }
        ],
    }
    assert quality_metrics(rows, samples, None) is None
    result = quality_metrics(rows, samples, labels)
    assert result["A"]["library_coverage"]["value"] == 0
    assert result["B"]["library_coverage"]["value"] == 1
    assert result["B"]["routing_omission"]["value"] == 1
    assert result["B"]["judgment_error"]["denominator"] == 0
    assert result["C"]["candidate_hit"]["value"] == 1
    assert result["C"]["initial_routing_omission"]["value"] == 1
    assert result["C"]["routing_omission"]["value"] == 0
    rows[2]["available_ids"] = []
    rows[2]["reason_ids"] = []
    material = quality_metrics(rows, samples, labels)["C"]
    assert material["material_unavailable"]["value"] == 1
    assert material["judgment_error"]["denominator"] == 0
    rows[2]["available_ids"] = ["code.interface_changed"]
    rows[2]["reason_ids"] = []
    rows[2]["outcome"] = "unknown"
    assert quality_metrics(rows, samples, labels)["C"]["judgment_error"]["value"] == 1
    rows[2]["status"] = "error"
    assert quality_metrics(rows, samples, labels)["C"]["judgment_error"]["denominator"] == 0
    assert quality_metrics(rows, samples, labels)["C"]["candidate_hit"]["denominator"] == 1
    rows[2]["route"] = None
    assert quality_metrics(rows, samples, labels)["C"]["routing_unavailable"] == 1
    labels["items"][0]["review_origin"] = "developer_synthetic"
    with pytest.raises(ReplayError, match="labels_not_independent_holdout"):
        quality_metrics(rows, samples, labels)
