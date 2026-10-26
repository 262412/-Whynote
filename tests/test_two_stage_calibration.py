from copy import deepcopy

import pytest

from whynote import two_stage_calibration as cal
from whynote import two_stage_v3 as v3


@pytest.mark.parametrize("limit,minimum", [(0.1, 39), (0.05, 80)])
def test_zero_error_exact_minimum(limit, minimum):
    alpha = 0.05 / 3
    assert cal.clopper_pearson_upper(0, minimum, alpha) <= limit
    assert cal.clopper_pearson_upper(0, minimum - 1, alpha) > limit


@pytest.mark.parametrize("limit,true_error,n,errors", [(0.1, 0.05, 252, 15), (0.05, 0.025, 533, 16)])
def test_predeclared_power_examples(limit, true_error, n, errors):
    plan = cal.sample_size(limit, true_error=true_error)
    assert plan["planned_effective_targets"] == n
    assert plan["maximum_passing_errors"] == errors
    assert plan["achieved_planning_power"] >= 0.8
    assert plan["effective_shortfall"] == n
    assert plan["status"] == "INSUFFICIENT_EVIDENCE"


def test_cp_known_exact_values_and_missing_worst_case():
    assert cal.clopper_pearson_upper(0, 10, 0.05) == pytest.approx(0.2588655508930523)
    assert cal.clopper_pearson_upper(10, 10, 0.05) == 1
    assert cal.clopper_pearson_upper(0, 0, 0.05) is None
    assert cal.clopper_pearson_upper(1, 10, 0.05) == pytest.approx(0.3941633024365047)
    risk = cal.risk_counts([False] * 80 + [None] * 20, 0.1, 0.05 / 3)
    assert risk["missing_labels"] == 20 and risk["worst_case_errors"] == 20
    assert risk["status"] != "PASS"
    assert cal.risk_counts([], 0.1, 0.05 / 3)["status"] == "INSUFFICIENT_EVIDENCE"
    assert cal.risk_counts([False] * 8, 0.1, 0.05 / 3)["status"] == "INSUFFICIENT_EVIDENCE"
    weighted = cal.risk_counts([False] * 100, 0.1, 0.05 / 3, binomial_applicable=False)
    assert weighted["worst_case_upper"] is None and weighted["status"] == "INSUFFICIENT_EVIDENCE"


def catalog():
    return {
        "reasons": [
            {"id": "r", "tasks": [], "domains": []},
            {"id": "s", "tasks": ["code_rewrite"], "domains": []},
        ]
    }


def prediction(choice="yes", task="code_generate", probability=0.95):
    return {
        "routes": {
            "task": {
                "choice": task,
                "probabilities": {task: probability, "unknown": 1 - probability},
                "confidence": 0.9,
            },
            "domain": {"choice": "software", "probabilities": {"software": 0.95, "unknown": 0.05}, "confidence": 0.9},
        },
        "reasons": [
            {
                "reason_id": "r",
                "raw_choice": choice,
                "probabilities": {choice: 0.95, "unknown": 0.05},
                "confidence": 0.9,
            }
        ],
    }


def row(key="a", truth="present"):
    return {
        "target_id": key,
        "group_id": key,
        "partition": "calibration",
        "runtime": {"input_version": "v3"},
        "run_id": "primary",
        "input_sha256": "h",
        "expected_input_sha256": "h",
        "synthetic": False,
        "source": "s1",
        "evidence_eligible_reason_ids": ["r", "s"],
        "prediction": prediction(),
        "label": {
            "input_sha256": "h",
            "routes": {
                "task": {"status": "reviewed", "accepted": ["code_generate"]},
                "domain": {"status": "reviewed", "accepted": ["software"]},
            },
            "reasons": {
                "r": {"decision": truth, "expertise": "qualified"},
                "s": {"decision": "present", "expertise": "qualified"},
            },
        },
    }


def test_unsupported_yes_no_and_missing_label_are_different():
    config = cal.risk_config()
    rows = [row("a", "insufficient_evidence"), row("b", "not_reviewed"), row("c", "present")]
    result = cal.evaluate(rows, v3.Policy(), catalog(), config)
    risk = result["risks"]["yes"]
    assert risk["effective_independent_targets"] == 3 and risk["observed_errors"] == 1 and risk["missing_labels"] == 1
    assert result["end_to_end"]["route_missed_present_reasons"] == 3
    assert result["end_to_end"]["human_present_reasons"] == 4
    rows[0]["prediction"] = prediction("no")
    assert cal.evaluate(rows, v3.Policy(), catalog(), config)["risks"]["no"]["observed_errors"] == 1
    rows[0]["label"]["reasons"]["r"]["decision"] = "absent"
    assert cal.evaluate(rows, v3.Policy(), catalog(), config)["risks"]["no"]["observed_errors"] == 0


def test_missing_counterfactual_never_gets_fabricated_answer():
    value = row()
    value["prediction"] = prediction(task="code_rewrite", probability=0.7)
    before = cal.evaluate_target(value, v3.Policy(), catalog())
    after = cal.evaluate_target(value, v3.Policy(route_probability=0.6), catalog())
    assert before["counterfactual"] == [] and after["counterfactual"] == ["s"]
    assert "no" not in after["events"]
    search_result = cal.search([value], catalog(), {"input_version": "v3"}, "primary")
    assert any(
        point["counterfactual_not_evaluated"] == [{"target_id": "a", "reason_ids": ["s"]}]
        for point in search_result["risk_coverage_curve"]
    )
    assert search_result["selected_policy"] is None


def test_no_run_group_or_confirmation_leakage():
    values = [row("a"), row("b")]
    cal.validate_units(values, partition="calibration", binding={"input_version": "v3"}, primary_run="primary")
    values[1]["group_id"] = "a"
    with pytest.raises(ValueError, match="one_target"):
        cal.validate_units(values, partition="calibration", binding={"input_version": "v3"}, primary_run="primary")
    values = [row()]
    values[0]["partition"] = "confirmation"
    with pytest.raises(ValueError, match="cannot_enter_search"):
        cal.search(values, catalog(), {"input_version": "v3"}, "primary")
    values[0]["partition"] = "calibration"
    values[0]["runtime"] = {"input_version": "v2"}
    with pytest.raises(ValueError, match="condition_or_primary"):
        cal.search(values, catalog(), {"input_version": "v3"}, "primary")


def test_quantile_grid_contains_baseline_and_confidence_comparison():
    candidates = cal.candidates([row()], "yes", cal.risk_config())
    assert (0.8, 0.5) in candidates and (0.8, None) in candidates
    assert len(candidates) <= 9 * 10


def test_empty_search_cannot_select_threshold_or_validate_rare_category():
    result = cal.search([], catalog(), {"input_version": "v3"}, "primary")
    assert result["status"] == "INSUFFICIENT_EVIDENCE" and result["selected_policy"] is None
    assert result["confirmation_rows_used"] == 0
    assert all(fold["selected_policy"] is None for fold in result["group_cross_validation"])


def test_target_not_question_denominator_and_weighted_warning():
    r = row()
    r["prediction"]["reasons"].append(deepcopy(r["prediction"]["reasons"][0]) | {"reason_id": "s"})
    r["prediction"]["routes"]["task"] = prediction(task="code_rewrite")["routes"]["task"]
    r["weight"] = 10
    result = cal.evaluate([r], v3.Policy(), catalog(), cal.risk_config(), sampling="source_stratified")
    assert result["risks"]["yes"]["effective_independent_targets"] == 1
    assert result["risks"]["yes"]["worst_case_upper"] is None
    assert result["per_category"]["r"]["status"] == "NOT_VALIDATED"


def test_unfamiliar_reviewer_cannot_assert_material_insufficiency_as_gold():
    value = row(truth="insufficient_evidence")
    value["label"]["reasons"]["r"]["expertise"] = "unfamiliar"
    risk = cal.evaluate([value], v3.Policy(), catalog(), cal.risk_config())["risks"]["yes"]
    assert risk["observed_errors"] == 0 and risk["missing_labels"] == 1


def test_sequential_search_and_group_cv_select_only_an_unconfirmed_candidate():
    values = [row(str(i)) for i in range(120)]
    reasons = catalog()
    reasons["reasons"][1]["tasks"] = []
    for value in values:
        value["label"]["reasons"]["s"]["decision"] = "absent"
        value["prediction"]["reasons"].append(prediction("no")["reasons"][0] | {"reason_id": "s"})
    result = cal.search(values, reasons, {"input_version": "v3"}, "primary")
    assert result["status"] == "CANDIDATE_SELECTED_NOT_CONFIRMED"
    assert [step["stage"] for step in result["search_sequence"]] == ["route", "yes", "no"]
    assert all(fold["selected_policy"] for fold in result["group_cross_validation"])
    assert result["sample_plan"]["yes"]["available_effective_targets"] == 120
    assert not result["production_approved"] and result["confirmation_rows_used"] == 0


def test_explicit_budget_abstention_is_not_a_missing_model_call_or_counterfactual():
    value = row()
    value["prediction"].update(routes=v3.select_routes(value["prediction"]["routes"], v3.Policy()))
    value["prediction"]["reasons"][0].update(raw_choice=None, status="input_budget_limited")
    result = cal.evaluate_target(value, v3.Policy(), catalog())
    assert result["counterfactual"] == [] and result["budget_abstention"]
    assert "yes" not in result["events"] and result["present"] == 2
    value.update(prediction=None, prediction_status="input_budget_limited")
    result = cal.evaluate_target(value, v3.Policy(), catalog())
    assert not result["missing_prediction"] and result["present"] == 2 and result["detected"] == 0
    value["prediction_status"] = "not_collected"
    assert cal.evaluate_target(value, v3.Policy(), catalog())["missing_prediction"]
