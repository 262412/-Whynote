import json
import subprocess
import sys
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from whynote import task_reasons as reasons
from whynote.domain import MANUAL_REASONS, MANUAL_UI_VERSION, REASON_CODES, ConflictError, Principal, project
from whynote.laya_local import QUESTIONS
from whynote.store import EventStore

FULL = ("request", "answer", "original_code", "reference")
LEGACY = {
    "factual_error",
    "instruction_not_followed",
    "incomplete",
    "irrelevant",
    "style",
    "outdated",
    "unnecessary_refusal",
    "other_or_unknown",
}


def candidates(tasks=("code_rewrite",), evidence=FULL, fallback=()):
    return reasons.prepare_candidates(tasks, evidence, fallback)


def selection(current, outcome="suggested", ids=None):
    return {
        "candidate_set_id": current.candidate_set_id,
        "outcome": outcome,
        "reason_ids": ["code.interface_changed"] if ids is None else ids,
    }


def test_packaged_catalogue_identity_and_legacy_mapping():
    catalog = reasons.load_reasons()
    assert len(catalog) == len({r.reason_id for r in catalog}) == 11
    assert {r.legacy_reason_code for r in catalog if r.reason_id.startswith("general.")} == LEGACY - {
        "other_or_unknown"
    }
    assert all(r.legacy_reason_code is None for r in catalog if r.reason_id.startswith("code."))
    assert REASON_CODES == set(QUESTIONS["primary_reason"]["criteria"]) == LEGACY
    assert len(MANUAL_REASONS) == 8
    assert all(r.definition and r.example and r.counterexample and r.criteria and r.template for r in catalog)
    with pytest.raises(FrozenInstanceError):
        catalog[0].reason_id = "changed"


def test_catalogue_changed_under_same_version_is_rejected(monkeypatch, tmp_path):
    original = Path(reasons.__file__).with_name("task_reasons.json").read_text(encoding="utf-8")
    data = json.loads(original)
    data["reasons"][0]["criteria"] = "RAW_INPUT_MUST_NOT_BE_LOGGED"
    (tmp_path / "task_reasons.json").write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(reasons, "files", lambda _: tmp_path)
    with pytest.raises(reasons.ReasonContractError, match="^catalog_content_mismatch$"):
        reasons.load_reasons()


def test_catalogue_digest_is_newline_independent(monkeypatch, tmp_path):
    original = Path(reasons.__file__).with_name("task_reasons.json").read_text(encoding="utf-8")
    expected = reasons.load_reasons()
    (tmp_path / "task_reasons.json").write_bytes(original.replace("\r\n", "\n").replace("\n", "\r\n").encode())
    monkeypatch.setattr(reasons, "files", lambda _: tmp_path)
    assert reasons.load_reasons() == expected


@pytest.mark.parametrize(
    "tasks", [("code_rewrite",), ("mixed",), ("unknown",), ("other",), ("general", "code_rewrite")]
)
def test_uncertain_and_mixed_tasks_retain_code_and_common_reasons(tasks):
    current = candidates(tasks)
    assert len(current.reason_ids) == 11
    assert "code.interface_changed" in current.reason_ids
    assert "general.instruction_not_followed" in current.reason_ids


def test_general_route_then_explicit_cross_task_fallback():
    first = candidates(("general",))
    assert len(first.reason_ids) == 7
    fallback = candidates(("general",), fallback=("code_rewrite",))
    assert set(first.reason_ids) < set(fallback.reason_ids)
    assert len(fallback.reason_ids) == 11
    assert first.candidate_set_id != fallback.candidate_set_id
    with pytest.raises(reasons.ReasonContractError, match="candidate_set_mismatch"):
        reasons.validate_selection(selection(first, "no_match", []), fallback)


def test_task_order_and_duplicates_do_not_change_candidate_identity():
    a = candidates(("code_rewrite", "general"), fallback=("code_rewrite",))
    b = candidates(("general", "code_rewrite", "code_rewrite"), fallback=("code_rewrite",))
    assert a == b
    assert a.candidate_set_id == b.candidate_set_id


@pytest.mark.parametrize("field", ["task_types", "fallback_tasks", "evidence_kinds"])
def test_repetitions_beyond_enum_size_preserve_identity_but_do_not_hide_invalid_values(field):
    inputs = {"task_types": ["general"], "fallback_tasks": ["code_rewrite"], "evidence_kinds": list(FULL)}
    expected = reasons.prepare_candidates(**inputs)
    inputs[field] *= 10
    repeated = reasons.prepare_candidates(**inputs)
    assert repeated == expected
    assert repeated.candidate_set_id == expected.candidate_set_id
    inputs[field].append("invalid")
    with pytest.raises(reasons.ReasonContractError, match="^invalid_arguments$"):
        reasons.prepare_candidates(**inputs)


def test_missing_original_code_is_visible_and_cannot_be_selected():
    current = candidates(evidence=("request", "answer"))
    assert "code.interface_changed" in current.routed_reason_ids
    assert "code.interface_changed" not in current.reason_ids
    assert dict(current.unavailable)["code.interface_changed"] == ("original_code",)
    assert "general.instruction_not_followed" in current.reason_ids
    assert "general.factual_error" not in current.reason_ids
    with pytest.raises(reasons.ReasonContractError, match="reason_not_selectable"):
        reasons.validate_selection(selection(current), current)


@pytest.mark.parametrize("evidence", [(), ("request",), ("answer",), ("original_code", "reference")])
def test_incomplete_context_only_allows_unknown(evidence):
    current = candidates(evidence=evidence)
    assert current.reason_ids == ()
    assert reasons.validate_selection(selection(current, "unknown", []), current)["outcome"] == "unknown"
    with pytest.raises(reasons.ReasonContractError, match="insufficient_evidence"):
        reasons.validate_selection(selection(current, "no_match", []), current)


def test_unknown_and_no_match_do_not_become_user_rejection():
    current = candidates()
    for outcome in ("unknown", "no_match"):
        result = reasons.validate_selection(selection(current, outcome, []), current)
        assert result["outcome"] == outcome
        assert result["reason_ids"] == []
        assert result["scope"] == "offline_only"
        assert result["selection_origin"] == "caller_supplied"
        assert result["confirmation_status"] == "unconfirmed"
    for outcome in ("none_matched", "confirmed", "selected", None, []):
        with pytest.raises(reasons.ReasonContractError, match="invalid_outcome"):
            reasons.validate_selection(selection(current, outcome, []), current)


@pytest.mark.parametrize(
    "ids,code",
    [
        ([], "invalid_reason_count"),
        (["code.interface_changed", "code.interface_changed"], "invalid_reason_count"),
        (["a", "b", "c", "d"], "invalid_reason_count"),
        (["instruction_not_followed"], "reason_not_selectable"),
        (["unknown.reason"], "reason_not_selectable"),
        ([None], "invalid_reason_ids"),
        ("code.interface_changed", "invalid_reason_ids"),
    ],
)
def test_invalid_choices_fail_without_echoing_values(ids, code):
    current = candidates()
    with pytest.raises(reasons.ReasonContractError, match=f"^{code}$"):
        reasons.validate_selection(selection(current, ids=ids), current)


def test_multiple_issues_preserve_order_and_remain_unconfirmed():
    current = candidates()
    ids = ["code.interface_changed", "general.style"]
    result = reasons.validate_selection(selection(current, ids=ids), current)
    ids.append("general.irrelevant")
    assert result["reason_ids"] == ["code.interface_changed", "general.style"]
    assert result["selection_origin"] == "caller_supplied"
    assert result["confirmation_status"] == "unconfirmed"


@pytest.mark.parametrize(
    "change",
    [
        {"package_version": "old"},
        {"criteria_version": "old"},
        {"template_version": "old"},
        {"catalog_sha256": "0" * 64},
        {"reason_ids": ("made_up",)},
        {"unavailable": ()},
    ],
)
def test_modified_or_stale_candidate_set_is_rejected(change):
    current = candidates(evidence=("request", "answer"))
    modified = replace(current, **change)
    with pytest.raises(reasons.ReasonContractError):
        reasons.validate_selection(selection(modified, "unknown", []), modified)


@pytest.mark.parametrize(
    "task,evidence",
    [([], FULL), ("code_rewrite", FULL), (["translation"], FULL), ([None], FULL), (["general"], ["future_feedback"])],
)
def test_invalid_routing_arguments_are_not_guessed(task, evidence):
    with pytest.raises(reasons.ReasonContractError):
        reasons.prepare_candidates(task, evidence)


def test_old_manual_events_reject_new_ids_and_replay_unchanged(tmp_path):
    store = EventStore(tmp_path / "events.db")
    actor = Principal("synthetic", "alice")
    eid = store.create_action(
        actor,
        {"object_type": "answer", "object_id": "example", "object_version": "1"},
        {"interaction_contract": "manual-v1"},
        "create",
    )["event_id"]
    store.issue_display_ticket(actor, eid, "menu")
    store.record_display(actor, eid, "menu", "manual_menu", [code for code, _ in MANUAL_REASONS], MANUAL_UI_VERSION)
    before = store.get_events(actor, eid)
    for reason in reasons.load_reasons():
        with pytest.raises(ConflictError):
            store.record_user_action(actor, eid, "reason_selected", reason.reason_id, "menu", True, "new-id")
    assert store.get_events(actor, eid) == before
    store.record_user_action(actor, eid, "reason_selected", "other_or_unknown", "menu", True, "old-unknown")
    old_events = store.get_events(actor, eid)
    assert project(old_events)["reason_code"] == "other_or_unknown"
    current = candidates()
    reasons.validate_selection(selection(current, "no_match", []), current)
    assert EventStore(store.path).get_events(actor, eid) == old_events
    assert project(old_events)["attribution_status"] == "selected"


def test_installed_cli_exports_then_validates_selection_from_other_directory(tmp_path):
    command = [sys.executable, "-X", "utf8", "-m", "whynote.task_reasons", "--task", "code_rewrite"]
    for kind in FULL:
        command += ["--evidence", kind]
    run = subprocess.run(command, cwd=tmp_path, capture_output=True, encoding="utf-8", check=True)
    exported = json.loads(run.stdout)
    assert len(exported["reasons"]) == 11
    payload = {
        "candidate_set_id": exported["candidate_set_id"],
        "outcome": "suggested",
        "reason_ids": ["code.interface_changed"],
    }
    path = tmp_path / "selection.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = subprocess.run(
        command + ["--selection-file", str(path)], cwd=tmp_path, capture_output=True, encoding="utf-8", check=True
    )
    assert json.loads(result.stdout)["reason_ids"] == payload["reason_ids"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["selection.json"]


def test_cli_wrong_version_and_malformed_file_are_safe(tmp_path, capsys):
    assert reasons.main(["--task", "general", "--package-version", "old"]) == 2
    assert "package_version_mismatch" in capsys.readouterr().out
    path = tmp_path / "invalid.json"
    path.write_text("SYNTHETIC_RAW_SECRET", encoding="utf-8")
    assert reasons.main(["--task", "general", "--selection-file", str(path)]) == 2
    output = capsys.readouterr().out
    assert "invalid_selection_file" in output
    assert "SYNTHETIC_RAW_SECRET" not in output


def test_catalogue_output_mutation_does_not_change_next_call():
    current = candidates()
    output = reasons.describe_candidates(current)
    output["reasons"][0]["criteria"] = "changed"
    assert reasons.describe_candidates(current)["reasons"][0]["criteria"] != "changed"


def test_developer_exploration_cases_validate_without_claiming_prediction():
    path = Path(__file__).resolve().parents[1] / "fixtures/m5-task-reasons/exploration.json"
    cases = json.loads(path.read_text(encoding="utf-8"))
    assert cases["review_origin"] == "developer_synthetic"
    assert cases["holdout_status"] == "not_established"
    assert len(cases["cases"]) == 7
    for case in cases["cases"]:
        assert case["developer_review"]
        assert (case["original_code"] is not None) == ("original_code" in case["evidence_kinds"])
        current = candidates(case["task_types"], case["evidence_kinds"], case["fallback_tasks"])
        payload = selection(current, case["outcome"], case["reason_ids"])
        result = reasons.validate_selection(payload, current)
        assert result["outcome"] == case["outcome"]
        assert result["reason_ids"] == case["reason_ids"]


def test_m5a_records_do_not_imply_original_code_or_reference_evidence():
    from whynote.source_review import run_batch

    manifest = Path(__file__).resolve().parents[1] / "fixtures/m5-synthetic/batch.json"
    _, results = run_batch(manifest)
    for result in results:
        for record in result["records"]:
            assert set(record["prediction_refs"]) == {"context", "target"}
            current = candidates((record["review"]["task"],), ("request", "answer"))
            assert not any(r.startswith("code.") for r in current.reason_ids)
            assert "general.factual_error" not in current.reason_ids


@pytest.mark.parametrize(
    "content,code",
    [
        ('{"outcome":"unknown","outcome":"suggested"}', "invalid_selection_file"),
        (" " * 8193, "selection_too_large"),
    ],
)
def test_cli_duplicate_keys_and_large_files_rejected(tmp_path, capsys, content, code):
    path = tmp_path / "selection.json"
    path.write_text(content, encoding="utf-8")
    assert reasons.main(["--task", "general", "--selection-file", str(path)]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == code
