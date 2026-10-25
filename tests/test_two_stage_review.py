from copy import deepcopy

import pytest

from whynote import two_stage_review as review


@pytest.fixture
def catalog():
    return {
        "tasks": {"generate": {}, "explain": {}},
        "domains": {"general": {}},
        "reasons": [
            {
                "id": "missing",
                "label": "遗漏",
                "criteria": "missing requirement",
                "evidence_paths": [["request", "answer"]],
                "tasks": [],
                "domains": [],
            }
        ],
    }


@pytest.fixture
def case():
    return {
        "target_id": "a",
        "group_id": "g",
        "input_version": "v3",
        "input_sha256": "input-hash",
        "documents": {"d0": {"text": "A😀B\r\n需求", "role": "user"}},
        "model_candidates": ["missing"],
        "source": "positive_signal",
        "probability": 0.9,
    }


def filled(case, catalog, identity="r1", decision="present"):
    label = review.empty_label(case, catalog, identity)
    span = {"document": "d0", "start": 1, "end": 2, "sha256": review.text_hash(case["documents"]["d0"]["text"])}
    for axis, choice in (("task", "generate"), ("domain", "general")):
        label["routes"][axis] = {
            "status": "reviewed",
            "accepted": [choice],
            "evidence": [span],
            "basis": "visible request",
        }
    label["reasons"]["missing"].update(
        decision=decision, evidence=[span], basis="visible requirement", expertise="qualified"
    )
    label.update(exposure="none_reported", independent_attestation=True)
    return label


def test_blind_allowlist_and_unreviewed_are_not_absent(case, catalog):
    public = review.blind_case(case, catalog)
    assert not {"model_candidates", "source", "probability"} & public.keys()
    assert set(public) == {
        "target_id",
        "group_id",
        "input_version",
        "input_sha256",
        "documents",
        "schema_version",
        "catalog_sha256",
        "tasks",
        "domains",
        "reasons",
    }
    label = review.empty_label(case, catalog, "human1")
    assert label["reasons"]["missing"]["decision"] == "not_reviewed"
    review.validate_label(label, case, catalog)
    with pytest.raises(ValueError, match="route_not_reviewed"):
        review.validate_label(label, case, catalog, complete=True)


def test_unicode_provenance_and_all_reasons_required(case, catalog):
    label = filled(case, catalog)
    review.validate_label(label, case, catalog, complete=True)
    label["reasons"]["missing"]["evidence"][0]["end"] = 99
    with pytest.raises(ValueError, match="out_of_range"):
        review.validate_label(label, case, catalog)
    label = filled(case, catalog)
    label["reasons"] = {}
    with pytest.raises(ValueError, match="catalog_incomplete"):
        review.validate_label(label, case, catalog)


def test_material_insufficiency_and_reviewer_unfamiliar_are_distinct(case, catalog):
    label = filled(case, catalog)
    label["reasons"]["missing"]["expertise"] = "unfamiliar"
    with pytest.raises(ValueError, match="unfamiliar_is_not_material"):
        review.validate_label(label, case, catalog)
    label["reasons"]["missing"].update(decision="not_reviewed", evidence=[], basis="outside my expertise")
    review.validate_label(label, case, catalog)
    label["reasons"]["missing"].update(decision="insufficient_evidence", missing=[])
    with pytest.raises(ValueError, match="missing_material_required"):
        review.validate_label(label, case, catalog)


def test_ai_and_human_reviewers_have_equal_weight_but_same_human_delay_is_not_independent(case, catalog):
    initial = filled(case, catalog)
    ai = filled(case, catalog, "ai")
    ai["reviewer"]["kind"] = "ai"
    result = review.reconcile_labels([initial, ai], formal=True)
    assert result["status"] == "READY"
    assert result["evidence_level"] == "coequal_independent_reviewers"
    delayed = deepcopy(initial)
    delayed["round_id"] = "delayed"
    assert review.reconcile_labels([initial, delayed], formal=True)["evidence_level"] == "single_human"
    second = filled(case, catalog, "human2")
    second["routes"]["task"]["basis"] = "different words, same categories"
    assert review.reconcile_labels([initial, second], formal=True)["status"] == "READY"


def test_conflict_preserves_raw_labels_until_coequal_adjudication(case, catalog):
    a, b = filled(case, catalog), filled(case, catalog, "h2", "absent")
    result = review.reconcile_labels([a, b], formal=True)
    assert result["final_label"] is None and result["reason_conflicts"] == ["missing"]
    decision = filled(case, catalog, "adjudicator")
    decision["disagreement"] = "adjudicated"
    result = review.reconcile_labels([a, b], formal=True, adjudication=decision)
    assert result["raw_labels"] == [a, b] and result["final_label"] == decision


def test_ai_and_human_conflict_is_preserved_and_ai_can_adjudicate(case, catalog):
    human, ai = filled(case, catalog), filled(case, catalog, "ai", "absent")
    ai["reviewer"]["kind"] = "ai"
    result = review.reconcile_labels([human, ai], formal=True)
    assert result["final_label"] is None
    assert result["reason_conflicts"] == ["missing"]
    decision = filled(case, catalog, "ai-adjudicator")
    decision["reviewer"]["kind"] = "ai"
    decision["disagreement"] = "adjudicated"
    result = review.reconcile_labels([human, ai], formal=True, adjudication=decision)
    assert result["final_label"] == decision
    assert result["evidence_level"] == "coequal_independent_reviewers"


def test_review_all_positive_insufficient_and_frozen_random_others(case, catalog):
    label = filled(case, catalog)
    label["reasons"].update({"insufficient": {"decision": "insufficient_evidence"}, "absent": {"decision": "absent"}})
    assert review.review_requirement(label, seed="fixed", other_fraction=0) == ["missing", "insufficient"]
    assert review.review_requirement(label, seed="fixed", other_fraction=1) == ["missing", "insufficient", "absent"]


def test_transitive_case_conversation_near_duplicate_and_run_leakage():
    prompt = " ".join("token" + str(i) for i in range(100))
    cases = [
        {"target_id": "a", "group_prompt": prompt, "group_aliases": ["helpsteer:row1"]},
        {"target_id": "b", "group_prompt": "different question", "group_aliases": ["helpsteer:row1"]},
        {"target_id": "c", "group_prompt": prompt + " extra", "group_aliases": ["conversation:2"]},
        {"target_id": "d", "group_prompt": "independent question", "group_aliases": ["conversation:2"]},
        {"target_id": "e", "group_prompt": "new unseen case", "group_aliases": ["original:e"]},
    ]
    groups = review.group_cases(cases)
    assert len({groups[k] for k in "abcd"}) == 1 and groups["e"] != groups["a"]
    for case in cases:
        case.update(source="test", input_version="v3", input_sha256="h")
    cases[0]["diagnostic_flags"] = ["candidate_changed"]
    manifest = review.sample_manifest(cases, groups, {"primary_run": "03"}, calibration_count=1)
    assert all(row["partition"] == "diagnostic" for row in manifest["rows"] if row["target_id"] != "e")
    assert sum(row["selected"] for row in manifest["rows"]) == 2
    assert manifest == review.sample_manifest(list(reversed(cases)), groups, {"primary_run": "03"}, calibration_count=1)
    assert manifest["formal_independent_validation"] == "PENDING_NEW_UNEXPOSED_GROUPS"
