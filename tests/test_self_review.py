"""Hand-calculated synthetic accounting. No real labels or model-quality evidence."""

import copy
import hashlib
import json
import subprocess
import sys
from collections import Counter

import pytest

from whynote.laya_local import MODEL
from whynote.replay import POOL
from whynote.replay_laya import ReplayError
from whynote.self_review import report
from whynote.self_review_contract import (
    LEGACY,
    PROTOCOL_ID,
    repeat_ids,
    schedule,
    validate_batch,
    validate_labels,
)
from whynote.self_review_metrics import gates, ratio, summarize
from whynote.source_mapping import digest
from whynote.task_reasons import CATALOG_SHA256


def repin(bundle):
    run = bundle["run"]
    run["manifest_sha256"] = digest(bundle["manifest"])
    run["labels_sha256"] = digest(bundle["labels"])
    run["schedule"] = schedule(bundle["manifest"]["samples"])
    if run["freeze"]:
        for key in ("manifest_sha256", "labels_sha256", "protocol_sha256", "source_sha256", "environment_sha256"):
            run["freeze"][key] = run[key]
    run["run_id"] = digest({k: v for k, v in run.items() if k != "run_id"})
    for row in bundle["predictions"]:
        row["run_id"] = run["run_id"]


@pytest.fixture
def bundle(tmp_path):
    source = tmp_path / "synthetic.jsonl"
    source.write_text("SYNTHETIC ONLY\n", encoding="utf-8")
    admission = {
        "batch_id": "synthetic-batch",
        "source": "synthetic",
        "revision": "fixture-v1",
        "file_path": str(source),
        "file_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "controlled_directory": str(tmp_path),
        "accessors": ["self"],
        "purpose": "synthetic-accounting",
        "license_chain_ref": "synthetic",
        "retention_ref": "test-temp",
        "deletion_ref": "test-temp",
        "backup_ref": "none",
        "exclusions_sha256": digest([]),
        "row_start": 0,
        "row_end": 89,
        "status": "ADMITTED",
        "approver_ref": "self",
        "approved_at": "2026-09-01T00:00:00Z",
        "expires_at": "2026-10-01T00:00:00Z",
    }
    samples = []
    for i in range(90):
        samples.append(
            {
                "input_id": digest(["id", i]),
                "batch_id": admission["batch_id"],
                "row_id": i,
                "target_key": "answer",
                "group_ids": {k: digest([k, i]) for k in ("source", "conversation", "near_duplicate")},
                "state_sha256": digest(["synthetic-state", i]),
                "partition": "validation" if i < 60 else "exploration",
                "task": "code_rewrite" if i < 40 or 60 <= i < 80 else "other",
                "language": "zh_native" if i < 30 else "other",
                "exposure_flag": False,
                "input_utf8_bytes": 1500 if i < 10 else 100,
                "state_tokens": 300,
                "head_tokens": 100,
                "total_tokens": 404,
                "max_option_tokens": 20,
                "option_total_tokens": 60,
                "reserved_token": False,
                "material_ready": True,
                "budget_evidence_sha256": digest(["budget", i]),
            }
        )
    first = []
    for i, s in enumerate(samples[:60]):
        first.append(
            {
                "input_id": s["input_id"],
                "verdict": "defect" if i < 40 else "no_defect",
                "reason_ids": [POOL[0]] if i < 40 else [],
                "legacy_reason_codes": [LEGACY[0]] if i < 40 else [],
                "reference_task": s["task"],
                "reference_domain": "code" if i < 40 else "general",
                "labeled_at": "2026-09-02T00:00:00Z",
                "evidence_sha256": digest(["evidence", i]),
            }
        )
    repeat = [copy.deepcopy(row) for row in first if row["input_id"] in repeat_ids(samples)]
    for row in repeat:
        row["labeled_at"] = "2026-09-04T00:00:00Z"
    final = copy.deepcopy(first)
    for row in final:
        row["labeled_at"] = "2026-09-04T01:00:00Z"
    labels = {
        "schema_version": "m54a-self-reviewed-blind-v1",
        "review_origin": "self_reviewed_blind",
        "reviewer_ref": "self",
        "sealed_at": "2026-09-04T02:00:00Z",
        "first": first,
        "repeat": repeat,
        "final": final,
    }
    manifest = {
        "schema_version": "m54a-self-review-batch-v1",
        "mode": "synthetic",
        "protocol_id": PROTOCOL_ID,
        "package_sha256": CATALOG_SHA256,
        "package_frozen_at": "2026-09-01T01:00:00Z",
        "selected_at": "2026-09-01T02:00:00Z",
        "seed": 42,
        "admissions": [admission],
        "samples": samples,
    }
    run = {
        "schema_version": "m54a-self-review-run-v1",
        "mode": "synthetic",
        "manifest_sha256": None,
        "labels_sha256": None,
        "protocol_sha256": digest("protocol"),
        "source_sha256": digest("source"),
        "environment_sha256": digest("synthetic-env"),
        "model": MODEL,
        "started_at": "2026-09-04T03:00:00Z",
        "schedule": schedule(samples),
        "freeze": None,
        "run_id": None,
    }
    predictions = []
    for entry in run["schedule"]:
        i = next(i for i, s in enumerate(samples) if s["input_id"] == entry["input_id"])
        for scheme in entry["schemes"]:
            library = LEGACY if scheme == "A" else POOL
            predictions.append(
                {
                    "run_id": None,
                    "input_id": samples[i]["input_id"],
                    "scheme": scheme,
                    "state_sha256": samples[i]["state_sha256"],
                    "status": "ok",
                    "error": None,
                    "outcome": "suggested" if i < 40 else "no_match",
                    "route_status": "success",
                    "library_ids": list(library),
                    "initial_routed_ids": list(library),
                    "routed_ids": list(library),
                    "available_ids": list(library),
                    "reason_ids": [library[0]] if i < 40 else [],
                    "elapsed_ms": 1000,
                    "citation_present": False,
                    "citation_supported": None,
                    "safety_events": [],
                }
            )
    result = {"manifest": manifest, "labels": labels, "run": run, "predictions": predictions}
    repin(result)
    return result


def crows(bundle):
    return [r for r in bundle["predictions"] if r["scheme"] == "C"]


def defect_rows(bundle):
    return [r for r in crows(bundle) if r["reason_ids"]]


def test_baseline_complete_and_no_false_acceptance(bundle):
    result = report(bundle)
    assert result["status"] == "SYNTHETIC_VERIFIED"
    assert result["gate_status"] == "PASS_SELF_REVIEW_PILOT"
    assert result["scope"] == "synthetic_accounting_only"
    assert not result["independent_acceptance"] and not result["production_approved"]
    counts = result["by_scheme"]["C"]["counts"]
    assert (counts["N"], counts["J"], counts["D"], counts["S"]) == (60, 60, 40, 40)
    assert result["by_scheme"]["C"]["ratios"]["citation_support"]["status"] == "NA"
    assert result["strata"]["language"]["zh_native"]["C"]["counts"]["N"] == 30


def test_six_orders_and_repeat_strata_are_fixed(bundle):
    samples = bundle["manifest"]["samples"]
    orders = schedule(samples)
    assert Counter("".join(s["schemes"]) for s in orders) == dict.fromkeys(
        ("ABC", "ACB", "BAC", "BCA", "CAB", "CBA"), 10
    )
    assert schedule(list(reversed(samples))) == orders
    assert repeat_ids(list(reversed(samples))) == repeat_ids(samples)
    assert sum(s["task"] == "code_rewrite" for s in samples if s["input_id"] in repeat_ids(samples)) == 13


def test_hand_calculated_failure_material_wrong_and_abstention(bundle):
    rows = defect_rows(bundle)
    # 40 defects: two route failures, one omission, one lost to materials,
    # one wrong selection, one answerable abstention, one later timeout => 33 hits.
    for r in rows[:2]:
        r.update(
            status="error",
            error="route_failed",
            route_status="failed",
            initial_routed_ids=[],
            routed_ids=[],
            available_ids=[],
            reason_ids=[],
            outcome=None,
        )
    rows[2].update(
        initial_routed_ids=[POOL[1]], routed_ids=[POOL[1]], available_ids=[POOL[1]], reason_ids=[], outcome="no_match"
    )
    rows[3].update(available_ids=[], reason_ids=[], outcome="unknown")
    rows[4].update(reason_ids=[POOL[1]])
    rows[5].update(reason_ids=[], outcome="unknown")
    rows[6].update(status="error", error="timeout", reason_ids=[], outcome=None, elapsed_ms=60000)
    summary = report(bundle)["by_scheme"]["C"]
    expected = {
        "library_coverage": (40, 40),
        "routing_omission": (1, 38),
        "routing_unavailable": (2, 40),
        "material_unavailable": (1, 40),
        "end_to_end_hit": (33, 40),
        "wrong_selection": (1, 35),
        "answerable_abstention": (1, 35),
        "wrong_suggestion": (1, 34),
        "technical_failure": (3, 60),
    }
    for key, pair in expected.items():
        assert (summary["ratios"][key]["numerator"], summary["ratios"][key]["denominator"]) == pair
    assert summary["counts"]["route_failed"] == 2
    assert summary["latency"] == {"denominator": 60, "p95_ms": 1000}


@pytest.mark.parametrize("wrong,expected", [(1, "PASS"), (2, "FAIL")])
def test_five_percent_equality_twenty_suggestions(bundle, wrong, expected):
    rows = defect_rows(bundle)
    for r in rows[20:]:
        r.update(reason_ids=[], outcome="no_match")
    for r in rows[:wrong]:
        r["reason_ids"] = [POOL[1]]
    check = report(bundle)["checks"]["wrong_suggestion"]
    assert check["denominator"] == 20 and check["status"] == expected


@pytest.mark.parametrize(
    "key,minimum",
    [
        ("wrong_suggestion", 20),
        ("routing_omission", 20),
        ("answerable_abstention", 20),
        ("library_coverage", 30),
        ("end_to_end_hit", 30),
        ("technical_failure", 30),
    ],
)
def test_gate_minimum_denominators(bundle, key, minimum):
    # Direct handcrafted gate input isolates the boundary from other gates.
    summary = report(bundle)["by_scheme"]["C"]
    numerator = minimum if key in ("library_coverage", "end_to_end_hit") else 0
    for n, status in ((0, "INCONCLUSIVE"), (minimum - 1, "INCONCLUSIVE"), (minimum, "PASS")):
        summary["ratios"][key] = ratio(min(numerator, n), n)
        assert gates(summary, [], bundle["predictions"])["checks"][key]["status"] == status


def test_wilson_and_zero_denominator():
    assert ratio(0, 0)["wilson95"] is None
    assert ratio(0, 20)["wilson95"] == pytest.approx([0, 0.16112515805281938])
    assert ratio(30, 60)["wilson95"] == pytest.approx([0.3773502424, 0.6226497576])
    assert ratio(20, 20)["wilson95"] == pytest.approx([0.8388748419471806, 1])


def test_initial_omission_recovered_by_fallback(bundle):
    defect_rows(bundle)[0]["initial_routed_ids"] = [POOL[1]]
    ratios = report(bundle)["by_scheme"]["C"]["ratios"]
    assert ratios["initial_routing_omission"]["numerator"] == 1
    assert ratios["routing_omission"]["numerator"] == 0


def test_empty_candidates_material_loss_not_wrong_selection(bundle):
    for r in defect_rows(bundle):
        r.update(available_ids=[], reason_ids=[], outcome="unknown")
    result = report(bundle)
    stats = result["by_scheme"]["C"]
    assert stats["ratios"]["material_unavailable"]["numerator"] == 40
    assert stats["ratios"]["wrong_selection"]["denominator"] == 0
    assert stats["ratios"]["answerable_abstention"]["denominator"] == 0
    assert result["checks"]["end_to_end_hit"]["status"] == "FAIL"


def test_oversize_kept_in_defect_denominator(bundle):
    item = bundle["manifest"]["samples"][0]
    item["input_utf8_bytes"] = 9000
    for row in bundle["predictions"]:
        if row["input_id"] == item["input_id"]:
            row.update(
                status="ineligible",
                error="input_budget_exceeded",
                route_status="not_attempted",
                initial_routed_ids=[],
                routed_ids=[],
                available_ids=[],
                reason_ids=[],
                outcome=None,
                elapsed_ms=0,
            )
    repin(bundle)
    summary = report(bundle)["by_scheme"]["C"]
    assert summary["ratios"]["end_to_end_hit"]["value"] == 39 / 40
    assert summary["ratios"]["technical_failure"]["denominator"] == 59
    assert summary["ratios"]["input_applicability"]["value"] == 59 / 60


@pytest.mark.parametrize("count,status", [(4, "PASS_SELF_REVIEW_PILOT"), (5, "FAIL")])
def test_repeat_disagreement_boundary(bundle, count, status):
    for row in bundle["labels"]["repeat"][:count]:
        row.update(verdict="unjudgeable", reason_ids=[], legacy_reason_codes=[])
    repin(bundle)
    result = report(bundle)
    assert result["gate_status"] == status
    assert result["checks"]["repeat_disagreements"]["observed"] == count


def test_unjudgeable_suggestion_requires_review(bundle):
    for rows in (bundle["labels"]["final"],):
        rows[0].update(verdict="unjudgeable", reason_ids=[], legacy_reason_codes=[])
    repin(bundle)
    result = report(bundle)
    assert result["gate_status"] == "HOLD"
    assert result["by_scheme"]["C"]["ratios"]["unjudgeable_suggestion"]["value"] == 1


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("run_id", "different-run", "mixed_replay_runs"),
        ("state_sha256", "0" * 64, "prediction_input_mismatch"),
        ("elapsed_ms", float("nan"), "invalid_elapsed"),
        ("error", ["timeout"], "invalid_outcome"),
    ],
)
def test_invalid_prediction_bindings(bundle, field, value, code):
    bundle["predictions"][0][field] = value
    with pytest.raises(ReplayError, match=code):
        report(bundle)


@pytest.mark.parametrize(
    "mutation,code",
    [
        (lambda b: b["labels"]["first"].pop(), "incomplete_label_pass"),
        (lambda b: b["predictions"].pop(), "missing_predictions"),
        (lambda b: b["predictions"].append(copy.deepcopy(b["predictions"][0])), "duplicate_prediction"),
        (lambda b: b["predictions"].reverse(), "prediction_order_mismatch"),
        (lambda b: b["manifest"]["samples"][0].update(exposure_flag=True), "exposed_validation"),
        (lambda b: b["manifest"]["admissions"][0].update(status="HOLD"), "batch_not_admitted"),
        (lambda b: b["labels"].update(review_origin="independent_adjudicated"), "invalid_review_origin"),
        (lambda b: b["labels"]["repeat"][0].update(labeled_at="2026-09-03T23:59:59Z"), "repeat_too_early"),
        (lambda b: b["run"].update(started_at="2026-09-04T01:00:00Z"), "prediction_before_seal"),
        (lambda b: b["manifest"]["samples"][0].update(feedback="HIDDEN"), "invalid_sample"),
        (lambda b: b["labels"].update(reviewer_refs=["self", "fake-second"]), "invalid_labels"),
    ],
)
def test_contract_rejections(bundle, mutation, code):
    mutation(bundle)
    repin(bundle)
    with pytest.raises(ReplayError, match=code):
        report(bundle)


@pytest.mark.parametrize("kind", ["source", "conversation", "near_duplicate"])
def test_group_leakage_across_exploration_and_validation(bundle, kind):
    samples = bundle["manifest"]["samples"]
    samples[60]["group_ids"][kind] = samples[0]["group_ids"][kind]
    with pytest.raises(ReplayError, match="group_leakage"):
        validate_batch(bundle["manifest"])


def test_one_source_question_cannot_supply_two_answers(bundle):
    samples = bundle["manifest"]["samples"]
    samples[1].update(row_id=0, target_key="answer-two")
    with pytest.raises(ReplayError, match="duplicate_source_row"):
        validate_batch(bundle["manifest"])


def test_changed_labels_without_reseal_rejected(bundle):
    bundle["labels"]["final"][0]["reason_ids"] = []
    with pytest.raises(ReplayError, match="seal_mismatch"):
        report(bundle)


def test_actual_timeout_counts_for_technical_rate_and_p95(bundle):
    for row in defect_rows(bundle)[:4]:
        row.update(status="error", error="timeout", outcome=None, reason_ids=[], elapsed_ms=60000)
    result = report(bundle)
    assert result["by_scheme"]["C"]["latency"]["p95_ms"] == 60000
    assert result["checks"]["technical_failure"]["status"] == "FAIL"
    assert result["checks"]["latency"]["status"] == "FAIL"


def test_safety_in_diagnostic_scheme_stops_whole_run(bundle):
    next(r for r in bundle["predictions"] if r["scheme"] == "A")["safety_events"] = ["context_leak"]
    result = report(bundle)
    assert result["gate_status"] == "HOLD" and result["stop_required"]


def test_citation_pending_and_unsupported_never_pass(bundle):
    row = defect_rows(bundle)[0]
    row["citation_present"] = True
    assert report(bundle)["gate_status"] == "HOLD"
    row["citation_supported"] = False
    assert report(bundle)["gate_status"] == "HOLD"
    row["citation_supported"] = True
    assert report(bundle)["gate_status"] == "PASS_SELF_REVIEW_PILOT"


def make_real(bundle):
    bundle["manifest"]["mode"] = "real"
    run = bundle["run"]
    run["mode"] = "real"
    run["freeze"] = {
        "status": "FROZEN_FOR_SELF_REVIEW",
        "operator_ref": "self",
        "confirmed_at": "2026-09-04T02:30:00Z",
        "outbound_evidence_sha256": digest("outbound"),
        "concurrency_evidence_sha256": digest("concurrency"),
        "cancel_evidence_sha256": digest("cancel"),
    }
    repin(bundle)


def test_real_mode_requires_explicit_freeze_before_file_access(bundle):
    bundle["manifest"]["mode"] = bundle["run"]["mode"] = "real"
    repin(bundle)
    with pytest.raises(ReplayError, match="missing_execution_freeze"):
        report(bundle)


@pytest.mark.parametrize(
    "change,code",
    [
        (lambda b: b["manifest"]["admissions"][0].update(file_sha256="0" * 64), "admitted_file_changed"),
        (lambda b: b["run"]["freeze"].pop("outbound_evidence_sha256"), "missing_execution_freeze"),
        (
            lambda b: b["manifest"]["admissions"][0].update(expires_at="2026-09-03T00:00:00Z"),
            "admission_not_current_at_run",
        ),
    ],
)
def test_real_contract_still_checked_with_freeze(bundle, change, code):
    make_real(bundle)
    change(bundle)
    repin(bundle)
    with pytest.raises(ReplayError, match=code):
        report(bundle)


def test_real_metadata_path_uses_synthetic_bytes_only(bundle):
    make_real(bundle)
    result = report(bundle)
    assert result["status"] == "PASS_SELF_REVIEW_PILOT"
    assert not result["independent_acceptance"]


def test_cli_end_to_end_refuses_overwrite_and_does_not_echo_context(bundle, tmp_path):
    source, output = tmp_path / "bundle.json", tmp_path / "report.json"
    source.write_text(json.dumps(bundle), encoding="utf-8")
    command = [sys.executable, "-m", "whynote.self_review", str(source), str(output)]
    good = subprocess.run(command, capture_output=True, text=True)
    assert good.returncode == 0, good.stdout + good.stderr
    assert json.loads(output.read_bytes())["scope"] == "synthetic_accounting_only"
    before = output.read_bytes()
    assert subprocess.run(command, capture_output=True).returncode == 2
    assert output.read_bytes() == before
    source.write_text('{"PRIVATE_CONTEXT": "never echo"}', encoding="utf-8")
    failed = subprocess.run(command, capture_output=True, text=True)
    assert failed.returncode == 2 and "PRIVATE_CONTEXT" not in failed.stdout + failed.stderr


def test_old_independent_schema_still_rejects_self_labels(bundle):
    from test_replay import metric_fixture

    from whynote.replay_metrics import quality_metrics

    samples, rows, labels, manifest = metric_fixture()
    assert quality_metrics(rows, samples, labels, run_manifest=manifest) is not None
    with pytest.raises(ReplayError, match="invalid_labels"):
        quality_metrics(rows, samples, bundle["labels"], run_manifest=manifest)
    labels["items"][0]["review_origin"] = "self_reviewed_blind"
    with pytest.raises(ReplayError, match="labels_not_independent_holdout"):
        quality_metrics(rows, samples, labels, run_manifest=manifest)


def test_empty_slice_is_na_not_zero_accuracy(bundle):
    labels, _ = validate_labels(bundle["labels"], bundle["manifest"]["samples"])
    summary = summarize([], {}, labels)
    assert summary["ratios"]["wrong_selection"]["value"] is None
    assert summary["latency"] == {"denominator": 0, "p95_ms": None}


def test_out_of_library_defect_stays_defect(bundle):
    bundle["labels"]["final"][0]["reason_ids"] = []
    repin(bundle)
    ratios = report(bundle)["by_scheme"]["C"]["ratios"]
    assert ratios["library_coverage"]["value"] == 39 / 40
    assert ratios["end_to_end_hit"]["value"] == 39 / 40
    assert ratios["wrong_suggestion"]["numerator"] == 1


def test_no_defect_suggestion_counts_as_wrong(bundle):
    row = next(r for r in crows(bundle) if not r["reason_ids"])
    row.update(outcome="suggested", reason_ids=[POOL[0]])
    metric = report(bundle)["by_scheme"]["C"]["ratios"]["wrong_suggestion"]
    assert metric["numerator"] == 1 and metric["denominator"] == 41


@pytest.mark.parametrize(
    "field,value",
    [
        ("state_tokens", 701),
        ("head_tokens", 257),
        ("total_tokens", 1025),
        ("max_option_tokens", 49),
        ("option_total_tokens", 241),
        ("reserved_token", True),
    ],
)
def test_each_budget_limit_is_enforced(bundle, field, value):
    item = bundle["manifest"]["samples"][0]
    item[field] = value
    item["total_tokens"] = max(item["total_tokens"], item["state_tokens"] + item["head_tokens"] + 4)
    repin(bundle)
    with pytest.raises(ReplayError, match="success_without_preconditions"):
        report(bundle)


def test_missing_material_separate_from_eligible_budget(bundle):
    item = bundle["manifest"]["samples"][0]
    item["material_ready"] = False
    for row in bundle["predictions"]:
        if row["input_id"] == item["input_id"]:
            row.update(
                status="ineligible",
                error="material_unavailable",
                outcome=None,
                reason_ids=[],
                route_status="not_attempted",
                initial_routed_ids=[],
                routed_ids=[],
                available_ids=[],
                elapsed_ms=0,
            )
    repin(bundle)
    result = report(bundle)["by_scheme"]["C"]
    assert result["ratios"]["input_applicability"]["value"] == 59 / 60
    assert result["ratios"]["technical_failure"]["value"] == 0
    assert result["ratios"]["technical_failure"]["denominator"] == 60
    assert result["counts"]["input_material_missing"] == 1


def test_translated_chinese_does_not_meet_native_quota(bundle):
    bundle["manifest"]["samples"][0]["language"] = "translated_zh"
    with pytest.raises(ReplayError, match="native_zh_quota"):
        validate_batch(bundle["manifest"])


def test_second_pass_identity_cannot_be_swapped(bundle):
    repeat = bundle["labels"]["repeat"]
    excluded = next(r for r in bundle["labels"]["first"] if r["input_id"] not in {x["input_id"] for x in repeat})
    repeat[0]["input_id"] = excluded["input_id"]
    with pytest.raises(ReplayError, match="incomplete_label_pass"):
        validate_labels(bundle["labels"], bundle["manifest"]["samples"])


def test_package_must_be_frozen_before_selection_and_labels(bundle):
    bundle["manifest"]["package_frozen_at"] = bundle["manifest"]["selected_at"]
    with pytest.raises(ReplayError, match="selection_before_package"):
        report(bundle)
    bundle["manifest"]["package_frozen_at"] = "2026-09-01T01:00:00Z"
    bundle["manifest"]["selected_at"] = "2026-09-02T01:00:00Z"
    with pytest.raises(ReplayError, match="label_before_selection"):
        report(bundle)


def test_real_file_outside_control_is_rejected(bundle, tmp_path):
    make_real(bundle)
    controlled = tmp_path / "empty-control"
    controlled.mkdir()
    bundle["manifest"]["admissions"][0]["controlled_directory"] = str(controlled)
    repin(bundle)
    with pytest.raises(ReplayError):
        report(bundle)


def test_cannot_improve_failed_c_by_diagnostic_winner(bundle):
    for row in defect_rows(bundle):
        row.update(reason_ids=[], outcome="no_match")
    result = report(bundle)
    assert result["by_scheme"]["A"]["ratios"]["end_to_end_hit"]["value"] == 1
    assert result["gate_status"] == "FAIL"


def test_setup_cli_runs_without_predictions_or_model_authorization(bundle, tmp_path):
    setup = {k: bundle[k] for k in ("manifest", "labels", "run")}
    source, output = tmp_path / "setup.json", tmp_path / "checked.json"
    source.write_text(json.dumps(setup), encoding="utf-8")
    command = [sys.executable, "-m", "whynote.self_review", "--check-setup", str(source), str(output)]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    checked = json.loads(output.read_bytes())
    assert checked["status"] == "CONTRACT_VALIDATED" and checked["label_stable"]
    assert not checked["execution_authorized"] and not checked["environment_probed"]


def test_optimized_python_does_not_bypass_contract(bundle, tmp_path):
    bundle["manifest"]["admissions"][0]["status"] = "HOLD"
    source, output = tmp_path / "bad.json", tmp_path / "never.json"
    source.write_text(json.dumps(bundle), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-O", "-m", "whynote.self_review", str(source), str(output)], capture_output=True, text=True
    )
    assert result.returncode == 2 and "batch_not_admitted" in result.stdout
    assert not output.exists()
