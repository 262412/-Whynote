import copy

import pytest

from whynote import two_stage as core
from whynote.two_stage_offline import aggregate, reason_rows, sensitivity

POLICY = core.Policy("test", 0.8, 0.5, 0.8, 0.5)


def answer(choice="no", probability=1.0, confidence=1.0):
    return {
        "choice": choice,
        "confidence": confidence,
        "probabilities": {
            key: probability if key == choice else (1 - probability) / 2 for key in ("yes", "no", "unknown")
        },
    }


def result(*, choices=None, partial=False, evidence=("request", "answer")):
    catalog = core.load_catalog()
    routes = core.select_routes(
        {
            axis: {"choice": value, "probabilities": {k: float(k == value) for k in catalog[plural]}, "confidence": 1.0}
            for axis, value, plural in (
                ("task", "unknown" if partial else "conversation", "tasks"),
                ("domain", "general", "domains"),
            )
        },
        POLICY,
    )
    rows, questions = core.prepare_reasons(catalog, routes, set(evidence))
    answers = {name: (choices or {}).get(name, answer()) for name in questions}
    return core._result(catalog, POLICY, routes, rows, answers, [])


def record(value):
    return {"target_id": "synthetic", "technical_status": "success", "result": value, "routes": value["routes"]}


def test_missing_material_reproduces_legacy_unknown_and_separates_scope():
    value = result()
    assert value["outcome"] == "unknown"  # Preserved old expression of the defect.
    assert value["assessment"] == "no_issue_detected_in_evaluated_scope"
    assert value["assessment_label"] == "已检查项未检出问题"
    assert "缺材料未检查" in value["scope_notice"]
    assert value["coverage"]["missing_evidence_types"] == {"reference": 2}
    assert value["coverage"]["route_not_selected_count"] > 0
    assert (
        sum(
            value["coverage"][k]
            for k in ("asked_reason_count", "missing_evidence_reason_count", "route_not_selected_count")
        )
        == 86
    )
    assert all(r["decision"] == "not_asked" for r in value["reasons"] if r["status"] == "missing_evidence")
    assert value["schema_version"] != value["catalog_version"] == core.CATALOG_VERSION


def test_multiple_yes_and_partial_coverage_are_independent():
    value = result(choices={"general.incomplete": answer("yes"), "general.style": answer("yes")}, partial=True)
    assert value["assessment"] == "candidate_found"
    assert len(value["suggestions"]) == 2 and value["primary_reason"] is None
    assert value["user_confirmed"] is False
    assert all(row["user_confirmed"] is False for row in value["suggestions"])
    assert value["coverage"]["route_complete"] is False
    assert value["coverage"]["unresolved_axes"] == ["task"]
    assert value["coverage"]["missing_evidence_reason_count"] == 2


@pytest.mark.parametrize(
    ("raw", "probability", "confidence", "causes"),
    [
        ("no", 0.7, 0.4, ["probability", "confidence"]),
        ("yes", 0.7, 1, ["probability"]),
        ("unknown", 1, 1, ["raw_unknown"]),
        ("unknown", 0.7, 0.4, ["raw_unknown", "probability", "confidence"]),
    ],
)
def test_abstention_sources_keep_raw_values(raw, probability, confidence, causes):
    original = answer(raw, probability, confidence)
    value = result(choices={"general.style": original})
    assert value["assessment"] == "abstained"
    assert value["coverage"]["abstentions"] == [{"reason_id": "general.style", "raw_choice": raw, "causes": causes}]
    row = next(r for r in value["reasons"] if r["reason_id"] == "general.style")
    assert row["probabilities"] == original["probabilities"] and row["confidence"] == original["confidence"]


def test_partial_all_no_and_empty_evaluation():
    value = result(partial=True)
    assert value["assessment"] == "no_issue_detected_in_evaluated_scope"
    assert value["coverage"]["unresolved_axes"] == ["task"]
    view = core.summarize_assessment(value["routes"], [], POLICY)
    assert view["assessment"] == "not_evaluated" and view["coverage"]["asked_reason_count"] == 0


@pytest.mark.parametrize("status", ["error", "reconcile", "excluded", "pending", "running"])
def test_technical_failures_do_not_become_model_abstention(status):
    view = core.summarize_assessment(None, None, POLICY, technical_status=status)
    assert view["assessment"] is view["coverage"] is None


def test_reason_sensitivity_only_redecides_saved_answers_and_preserves_input():
    value = result(choices={"general.style": answer("yes", 0.7)})
    frozen = copy.deepcopy(value)
    assert reason_rows(value["reasons"], POLICY) == value["reasons"]
    lowered = core.Policy("comparison", 0.8, 0.5, 0.7, 0.5)
    rows = reason_rows(value["reasons"], lowered)
    assert sum(r["decision"] == "yes" for r in rows) == 1
    assert [r for r in rows if r["decision"] == "not_asked"] == [
        r for r in frozen["reasons"] if r["decision"] == "not_asked"
    ]
    assert value == frozen
    counts = aggregate([record(value)], lowered)
    assert counts["candidate_reasons"] == counts["candidate_targets"] == 1


def test_tie_cannot_be_accepted_by_lowering_thresholds():
    raw = {"choice": "yes", "probabilities": {"yes": 0.5, "no": 0.5, "unknown": 0}, "confidence": 1}
    value = result(choices={"general.style": raw})
    relaxed = core.Policy("comparison", 0.1, 0.1, 0.1, 0.1)
    rows = reason_rows(value["reasons"], relaxed)
    view = core.summarize_assessment(value["routes"], rows, relaxed)
    assert view["assessment"] == "abstained"
    assert view["coverage"]["abstentions"][0]["causes"] == ["tie"]


def test_route_counterfactuals_never_invent_answers_or_gold():
    value = result(partial=True)
    catalog = core.load_catalog()
    raw = {
        "choice": "code_generate",
        "probabilities": {
            k: 0.75 if k == "code_generate" else 0.25 / (len(catalog["tasks"]) - 1) for k in catalog["tasks"]
        },
        "confidence": 1,
    }
    value["routes"].update(core.select_routes({"task": raw}, POLICY))
    saved = record(value)
    frozen = copy.deepcopy(saved)
    analysis, _, changes = sensitivity([saved], POLICY)
    extra = [r for r in changes if r["axis"] == "task" and r["scenario"] == "route_probability=0.7"]
    assert extra and all(r["prediction_status"] == "no_historical_answer" for r in extra)
    assert saved == frozen and all("decision" not in r for r in changes)
    assert analysis["accuracy"] is analysis["f1"] is analysis["best_threshold"] is None
    assert analysis["quality_status"] == "NOT_EVALUATED"
    counts = analysis["route_scenarios"]["task:baseline"]["counts"]
    assert counts["saved_route_answers"] == 1 and counts["unresolved"] == 1
