"""Independent D-20 accounting boundaries; all approvals and materials are synthetic."""

import copy
import json
import subprocess
import sys

import pytest
from test_self_review import bundle as bundle
from test_self_review import repin

from whynote.replay import POOL
from whynote.self_review import report


def defect_predictions(artifact):
    return [r for r in artifact["predictions"] if r["scheme"] == "C" and r["reason_ids"]]


@pytest.mark.parametrize("omitted,expected", [(4, "PASS"), (5, "FAIL")])
def test_actual_report_routing_ten_percent_boundary(bundle, omitted, expected):
    for row in defect_predictions(bundle)[:omitted]:
        row.update(
            initial_routed_ids=[POOL[1]],
            routed_ids=[POOL[1]],
            available_ids=[POOL[1]],
            reason_ids=[],
            outcome="no_match",
        )
    result = report(bundle)
    check = result["checks"]["routing_omission"]
    assert (check["numerator"], check["denominator"], check["status"]) == (omitted, 40, expected)
    assert result["by_scheme"]["C"]["ratios"]["end_to_end_hit"]["numerator"] == 40 - omitted


@pytest.mark.parametrize("covered,expected", [(32, "PASS"), (31, "FAIL")])
def test_actual_report_library_eighty_percent_boundary(bundle, covered, expected):
    for label in bundle["labels"]["final"][covered:40]:
        label["reason_ids"] = []  # Library-external defect remains in D.
    repin(bundle)
    check = report(bundle)["checks"]["library_coverage"]
    assert (check["numerator"], check["denominator"], check["status"]) == (covered, 40, expected)


@pytest.mark.parametrize("hits,expected", [(20, "PASS"), (19, "FAIL")])
def test_actual_report_half_of_defects_boundary(bundle, hits, expected):
    for row in defect_predictions(bundle)[hits:]:
        row.update(reason_ids=[], outcome="unknown")
    check = report(bundle)["checks"]["end_to_end_hit"]
    assert (check["numerator"], check["denominator"], check["status"]) == (hits, 40, expected)


@pytest.mark.parametrize("applicable,expected", [(42, "PASS"), (41, "FAIL")])
def test_actual_report_seventy_percent_material_boundary(bundle, applicable, expected):
    missing = {s["input_id"] for s in bundle["manifest"]["samples"][applicable:60]}
    for item in bundle["manifest"]["samples"]:
        if item["input_id"] in missing:
            item["material_ready"] = False
    for row in bundle["predictions"]:
        if row["input_id"] in missing:
            row.update(
                status="ineligible",
                error="material_unavailable",
                outcome=None,
                reason_ids=[],
                initial_routed_ids=[],
                routed_ids=[],
                available_ids=[],
                route_status="not_attempted",
                elapsed_ms=0,
            )
    repin(bundle)
    result = report(bundle)
    check = result["checks"]["input_applicability"]
    assert (check["numerator"], check["denominator"], check["status"]) == (applicable, 60, expected)
    assert result["checks"]["technical_failure"]["denominator"] == 60
    assert result["by_scheme"]["C"]["counts"]["input_material_missing"] == 60 - applicable


@pytest.mark.parametrize("slow,expected_p95,expected_status", [(3, 1000, "PASS"), (4, 15001, "FAIL")])
def test_nearest_rank_boundary_uses_all_sixty_attempts(bundle, slow, expected_p95, expected_status):
    for row in defect_predictions(bundle)[:slow]:
        row["elapsed_ms"] = 15001
    check = report(bundle)["checks"]["latency"]
    assert (check["denominator"], check["p95_ms"], check["status"]) == (60, expected_p95, expected_status)


@pytest.mark.parametrize("failures,expected", [(3, "PASS"), (4, "FAIL")])
def test_technical_error_five_percent_boundary(bundle, failures, expected):
    for row in defect_predictions(bundle)[:failures]:
        row.update(status="error", error="worker_failed", outcome=None, reason_ids=[])
    result = report(bundle)
    check = result["checks"]["technical_failure"]
    assert (check["numerator"], check["denominator"], check["status"]) == (failures, 60, expected)
    assert result["by_scheme"]["C"]["ratios"]["end_to_end_hit"]["denominator"] == 40


def test_diagnostic_technical_failures_do_not_choose_or_fail_c(bundle):
    for row in bundle["predictions"]:
        if row["scheme"] in "AB":
            row.update(status="error", error="worker_failed", outcome=None, reason_ids=[])
    result = report(bundle)
    assert result["gate_status"] == "PASS_SELF_REVIEW_PILOT"
    for scheme in "AB":
        assert result["by_scheme"][scheme]["ratios"]["technical_failure"]["value"] == 1
    assert result["by_scheme"]["C"]["ratios"]["end_to_end_hit"]["value"] == 1
    assert result["scope"] == "synthetic_accounting_only" and not result["independent_acceptance"]


def test_global_safety_stop_takes_priority_over_c_hard_failure(bundle):
    for row in defect_predictions(bundle):
        row.update(reason_ids=[], outcome="unknown")
    next(r for r in bundle["predictions"] if r["scheme"] == "B")["safety_events"] = ["hash_drift"]
    result = report(bundle)
    assert result["checks"]["end_to_end_hit"]["status"] == "FAIL"
    assert result["gate_status"] == "HOLD" and result["stop_required"]
    assert "safety" in result["reasons"] and "end_to_end_hit" in result["reasons"]


def test_strata_reconcile_to_overall_counts_and_fraction_parts(bundle):
    for index, row in enumerate(defect_predictions(bundle)):
        if index % 3 == 0:
            row.update(reason_ids=[], outcome="unknown")
        elif index % 3 == 1:
            row["reason_ids"] = [POOL[1]]
    before = copy.deepcopy(bundle)
    result = report(bundle)
    assert bundle == before, "Analysis must not mutate sealed input or predictions"
    for groups in result["strata"].values():
        for scheme in "ABC":
            overall = result["by_scheme"][scheme]
            for key, value in overall["counts"].items():
                assert sum(group[scheme]["counts"][key] for group in groups.values()) == value
            for name, metric in overall["ratios"].items():
                for part in ("numerator", "denominator"):
                    assert sum(group[scheme]["ratios"][name][part] for group in groups.values()) == metric[part]


def test_two_actual_cli_writers_cannot_overwrite_the_same_report(bundle, tmp_path):
    source, output = tmp_path / "synthetic-bundle.json", tmp_path / "report.json"
    source.write_text(json.dumps(bundle), encoding="utf-8")
    before = source.read_bytes()
    command = [sys.executable, "-m", "whynote.self_review", str(source), str(output)]
    children = [subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
    try:
        outputs = [child.communicate(timeout=30) for child in children]
        assert sorted(child.returncode for child in children) == [0, 2], outputs
        result = json.loads(output.read_bytes())
        assert result["run_id"] == bundle["run"]["run_id"]
        assert result["status"] == "SYNTHETIC_VERIFIED" and not result["production_approved"]
        assert source.read_bytes() == before
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait()
