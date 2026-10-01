"""Offline diagnostics: preserve historical inputs and expose omitted failures."""

import hashlib
import importlib.util
import json
import socket
import sqlite3
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from whynote.window_audit import audit_exit_code, audit_history, diagnose, label_map, synthetic_expectations
from whynote.window_diagnostics import POLICY, QUESTION_VERSION, questions

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "qa/evidence/2026-09-30-m55-window"


@pytest.fixture
def supervisor():
    spec = importlib.util.spec_from_file_location("offline_supervisor", ROOT / "scripts/m55_window_validation.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def answers_for(expected, variant="explicit"):
    return {
        key: {"choice": next(k for k, v in label_map(variant, key).items() if v == value)}
        for key, value in expected.items()
    }


def test_twelve_original_bodies_designated_labels_and_pairs(supervisor):
    cases = supervisor.synthetic() + supervisor.synthetic_long()
    assert len(cases) == len({c["case_id"] for c in cases}) == 12
    schedule = json.loads((EVIDENCE / "schedule.json").read_text())
    hashes = json.loads((EVIDENCE / "hash-reconciliation.json").read_text())["records"]
    assert {c["case_id"] for c in cases} == {c["case_id"] for c in schedule if "input_id" not in c}
    for case in cases:
        expected = synthetic_expectations(case["case_id"])
        for index, old in enumerate(schedule):
            if old["case_id"] == case["case_id"]:
                assert case["expected"] == old["expected"]
                assert all(expected[k] == v for k, v in old["expected"].items())
                assert hashlib.sha256(case["state"].encode()).hexdigest() == hashes[index]["state_utf8_sha256"]
        pair = next(c for c in cases if c["case_id"] == case["case_id"].rsplit("_", 1)[0] + "_no")
        if "interface" not in case["case_id"]:
            assert json.loads(case["state"])["context"] == json.loads(pair["state"])["context"]
        if case["case_id"].startswith("long-"):
            text = json.loads(case["state"])["context"][0]["content"]
            assert text.startswith("List the numbers 1 and 2. Do not include 3.")
            assert text.endswith("The original instruction remains binding.")
            assert text.count("stone ") == {2048: 1650, 4096: 3650, 8192: 7650}[case["window"]]


@pytest.mark.parametrize("variant", ["original", "explicit"])
def test_label_maps_and_prediction_boundary(supervisor, variant):
    for key, question in questions(variant).items():
        assert set(label_map(variant, key)) == set(question["criteria"])
    assert label_map("original", "route")["mixed"] == "mixed"
    assert label_map("explicit", "route")["C"] == "new_code_or_other"
    case = supervisor.synthetic()[0] | {"variant": variant, "window": 2048}
    payload = supervisor.prediction_input(case | {"diagnostic_expected": synthetic_expectations(case["case_id"])})
    assert set(payload) == {"state", "variant", "window"}
    assert payload["state"] == case["state"]


def test_designated_correct_does_not_hide_extra_false_positive():
    expected = synthetic_expectations("instruction_no")
    answers = answers_for(expected)
    answers["general.unnecessary_refusal"]["choice"] = "A"
    fields = diagnose(expected, answers, "explicit")
    assert fields["general.instruction_not_followed"]["outcome"] == "matches"
    assert fields["general.unnecessary_refusal"]["outcome"] == "false_positive"


@pytest.mark.parametrize("case_id", ["refusal_no", "instruction_no", "interface_no"])
def test_all_defects_on_normal_case_fail(case_id):
    expected = synthetic_expectations(case_id)
    answers = answers_for(expected)
    for key in answers.keys() - {"route"}:
        answers[key]["choice"] = "A"
    assert sum(f["outcome"] == "false_positive" for f in diagnose(expected, answers, "explicit").values()) == 3


def test_unset_expectation_is_not_match_or_model_abstention():
    fields = diagnose({}, answers_for(synthetic_expectations("refusal_no")), "explicit")
    assert {f["outcome"] for f in fields.values()} == {"unset_expectation"}
    assert all(f["expected"] is None for f in fields.values())


@pytest.fixture
def bundle(tmp_path, supervisor):
    """Temporary synthetic records only, including repeated windows and both versions."""
    case = supervisor.synthetic()[3]
    raw_hash = hashlib.sha256(case["state"].encode()).hexdigest()
    legacy_hash = hashlib.sha256(json.dumps(case["state"], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    schedule, journal, hashes = [], [], []
    for window in (4096, 2048, 8192):
        for variant in ("original", "explicit"):
            index = len(schedule)
            item = {k: v for k, v in case.items() if k != "state"}
            item.update(window=window, variant=variant, state_sha256=legacy_hash)
            schedule.append(item)
            journal.extend(
                [
                    {"event": "started", "index": index, "case": deepcopy(item)},
                    {
                        "event": "result",
                        "index": index,
                        "case": deepcopy(item),
                        "result": {
                            "answers": answers_for(synthetic_expectations(case["case_id"]), variant),
                            "state_sha256": raw_hash,
                            "full_encoding_verified": True,
                            "cpu_fallback_count": 0,
                        },
                    },
                ]
            )
            hashes.append(
                {
                    "index": index,
                    "case_id": case["case_id"],
                    "legacy_schedule_json_string_sha256": legacy_hash,
                    "state_utf8_sha256": raw_hash,
                    "reconstructed_input_matches_worker": True,
                }
            )
    journal = [
        {
            "event": "start",
            "planned": 6,
            "source_sha256": "synthetic-source",
            "runtime_sha256": "synthetic-runtime",
            "policy": POLICY,
            "question_version": QUESTION_VERSION,
        },
        {"kind": "ready", "source_sha256": "synthetic-source"},
        *journal,
        {"event": "completed", "count": 6, "cleanup_verified": True},
    ]
    summary = {
        "planned_calls": 6,
        "completed_calls": 6,
        "full_encoding_verified": 6,
        "cpu_fallbacks": 0,
        "source_sha256": "synthetic-source",
        "runtime_sha256": "synthetic-runtime",
        "cleanup_verified": True,
        "groups": {f"{w}-short": {"calls": 2} for w in (2048, 4096, 8192)},
        "original_short_pair_primary": {"correct": 1, "total": 1, "formal_accuracy": None},
        "explicit_short_pair_primary": {"correct": 1, "total": 1, "formal_accuracy": None},
    }
    data = {
        "schedule.json": schedule,
        "journal.jsonl": journal,
        "hash-reconciliation.json": {"verified_count": 6, "records": hashes},
        "summary.json": summary,
    }

    def run():
        for name, rows in data.items():
            text = "\n".join(json.dumps(row) for row in rows) if name.endswith("jsonl") else json.dumps(rows)
            (tmp_path / name).write_text(text, encoding="utf-8")
        return audit_history(tmp_path, supervisor.synthetic() + supervisor.synthetic_long())

    return data, run


def test_consistent_synthetic_bundle_is_only_diagnostic_match(bundle):
    _, run = bundle
    report = run()
    assert report["integrity_errors"] == [] and audit_exit_code(report) == 0
    assert report["quality"] == "NA"
    assert report["cohorts"]["synthetic_short"]["unique_cases"] == 1
    assert report["designated_synthetic_short"]["explicit"] == {"matches": 1, "cases": 1}


@pytest.mark.parametrize("target", ["schedule", "started", "result", "reconciliation"])
@pytest.mark.parametrize("fault", ["missing", "duplicate", "reorder"])
def test_metadata_missing_duplicate_and_order_fail(bundle, target, fault):
    data, run = bundle
    if target == "schedule":
        rows, index = data["schedule.json"], 0
    elif target == "reconciliation":
        rows, index = data["hash-reconciliation.json"]["records"], 0
    else:
        rows = data["journal.jsonl"]
        index = next(i for i, r in enumerate(rows) if r.get("event") == target)
    if fault == "missing":
        rows.pop(index)
    elif fault == "duplicate":
        rows.insert(index, deepcopy(rows[index]))
    else:
        rows[index], rows[index + 1] = rows[index + 1], rows[index]
    report = run()
    assert report["integrity_errors"] and audit_exit_code(report) == 2
    if target == "result" and fault == "missing":
        assert report["requests"]["incomplete"] == 1
        assert report["cases"][0]["requests"][0]["fields"]["route"]["outcome"] == "missing_result"


@pytest.mark.parametrize("fault", ["legacy", "utf8", "case", "version", "count", "encoding", "label", "reconstruction"])
def test_metadata_and_unknown_labels_fail(bundle, fault):
    data, run = bundle
    result = data["journal.jsonl"][3]
    if fault in {"legacy", "utf8"}:
        field = "legacy_schedule_json_string_sha256" if fault == "legacy" else "state_utf8_sha256"
        data["hash-reconciliation.json"]["records"][0][field] = "0" * 64
    elif fault == "case":
        result["case"]["case_id"] = "wrong"
    elif fault == "version":
        data["journal.jsonl"][0]["question_version"] = "changed"
    elif fault == "count":
        data["summary.json"]["completed_calls"] = 7
    elif fault == "encoding":
        result["result"]["full_encoding_verified"] = False
    elif fault == "label":
        result["result"]["answers"]["route"]["choice"] = "not-a-label"
    else:
        data["hash-reconciliation.json"]["records"][0]["reconstructed_input_matches_worker"] = False
    report = run()
    assert report["integrity_errors"] and audit_exit_code(report) == 2


def test_case_identity_must_match_across_question_versions(bundle):
    data, run = bundle
    for index in (1, 3, 5):
        data["hash-reconciliation.json"]["records"][index]["state_utf8_sha256"] = "0" * 64
        data["journal.jsonl"][3 + index * 2]["result"]["state_sha256"] = "0" * 64
    report = run()
    assert "schedule:case_changed:instruction_no" in report["integrity_errors"]
    assert audit_exit_code(report) == 2


def test_failed_result_remains_incomplete(bundle):
    data, run = bundle
    data["journal.jsonl"][3]["result"] = {"error": "diagnostic_failed"}
    report = run()
    assert report["requests"]["incomplete"] == 1
    fields = report["cases"][0]["requests"][0]["fields"]
    assert {f["outcome"] for f in fields.values()} == {"missing_result"}
    assert audit_exit_code(report) != 0


@pytest.mark.parametrize("fault", ["extra_false_positive", "wrong_route", "all_insufficient", "missing_answer"])
def test_designated_success_never_cancels_other_failures(bundle, fault):
    data, run = bundle
    answers = data["journal.jsonl"][5]["result"]["answers"]  # explicit version, first window
    if fault == "extra_false_positive":
        answers["general.unnecessary_refusal"]["choice"] = "A"
    elif fault == "wrong_route":
        answers["route"]["choice"] = "A"
    elif fault == "missing_answer":
        del answers["route"]
    else:
        for key in answers:
            answers[key]["choice"] = "D" if key == "route" else "C"
        data["summary.json"]["explicit_short_pair_primary"]["correct"] = 0
    report = run()
    assert report["integrity_errors"] == [] and audit_exit_code(report) == 1
    group = next(g for g in report["cases"] if g["variant"] == "explicit")
    assert group["diagnostic_status"] != "expectations_met"
    assert not group["cross_window_consistent"]
    assert group["designated_matches_all_windows"] == (fault != "all_insufficient")


def test_temporary_unlabelled_records_stay_incomplete(bundle):
    data, run = bundle
    for row in data["schedule.json"]:
        row.update(case_id="temporary-real-shape", input_id="synthetic-id", expected={})
    for row in data["journal.jsonl"]:
        if "index" in row:
            row["case"] = deepcopy(data["schedule.json"][row["index"]])
    for row in data["hash-reconciliation.json"]["records"]:
        row["case_id"] = "temporary-real-shape"
    summary = data["summary.json"]
    summary["groups"] = {k.replace("short", "long"): v for k, v in summary["groups"].items()}
    for variant in ("original", "explicit"):
        summary[f"{variant}_short_pair_primary"].update(correct=0, total=0)
    report = run()
    assert report["integrity_errors"] == [] and audit_exit_code(report) == 1
    assert report["cohorts"]["real_long"]["unset_expectation_fields"] == 24
    assert {g["diagnostic_status"] for g in report["cases"]} == {"incomplete"}


def test_cli_reads_only_four_metadata_files_and_preserves_history(supervisor, monkeypatch, capsys):
    allowed = {EVIDENCE / n for n in ("schedule.json", "journal.jsonl", "hash-reconciliation.json", "summary.json")}
    before = {p: p.read_bytes() for p in EVIDENCE.iterdir() if p.is_file()}
    opened = []
    original_open = Path.open

    def read_only(path, mode="r", *args, **kwargs):
        assert path in allowed and mode == "r"
        opened.append(path)
        return original_open(path, mode, *args, **kwargs)

    def forbidden(*args, **kwargs):
        pytest.fail("offline verification attempted body/model/network/runtime access")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", read_only)
        for name in ("read_plan", "load_tokenizer", "model_lock", "trusted_command", "source_hash"):
            patch.setattr(supervisor, name, forbidden)
        patch.setattr("whynote.window_diagnostics.Engine", forbidden)
        patch.setattr(sqlite3, "connect", forbidden)
        patch.setattr(socket, "socket", forbidden)
        patch.setattr(sys, "argv", ["verify", "verify", "--evidence", str(EVIDENCE)])
        with pytest.raises(SystemExit) as caught:
            supervisor.main()
    assert caught.value.code == 1
    assert set(opened) == allowed and len(opened) == 4
    report = json.loads(capsys.readouterr().out)
    assert report["integrity_errors"] == []
    assert report["requests"] == {
        "planned": 65,
        "completed": 65,
        "incomplete": 0,
        "encoding_verified_in_history": 65,
        "hash_metadata_matched": 65,
    }
    assert report["designated_synthetic_short"] == {
        "original": {"matches": 1, "cases": 6},
        "explicit": {"matches": 5, "cases": 6},
    }
    assert report["cohorts"]["synthetic_short"]["unique_cases"] == 6
    assert report["cohorts"]["synthetic_long"]["unique_cases"] == 6
    assert report["cohorts"]["real_long"]["unique_cases"] == 9
    assert all(g["quality"] == "NA" for g in report["cases"])
    assert before == {p: p.read_bytes() for p in before}


@pytest.mark.parametrize("contents", [None, "not-json", "{}", "[]"])
def test_cli_missing_or_malformed_metadata_fails_safely(supervisor, tmp_path, monkeypatch, capsys, contents):
    if contents is not None:
        for name in ("schedule.json", "journal.jsonl", "hash-reconciliation.json", "summary.json"):
            (tmp_path / name).write_text(contents, encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["verify", "verify", "--evidence", str(tmp_path)])
    with pytest.raises(SystemExit) as caught:
        supervisor.main()
    assert caught.value.code == 2
    assert json.loads(capsys.readouterr().out)["integrity_errors"]
