from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from whynote import two_stage_batch as batch
from whynote import two_stage_calibration as cal
from whynote import two_stage_confirmation as confirm
from whynote import two_stage_inputs as inputs
from whynote import two_stage_review as review
from whynote import two_stage_v3 as v3
from whynote import two_stage_v3_batch as collection


def test_confirmation_cannot_repeat_rename_or_return_to_search(tmp_path):
    registry = confirm.ConfirmationRegistry(tmp_path / "registry.db")
    manifest = {"units": ["group:g", "target:t", "alias"], "policy": {"probability": 0.8}}
    key = registry.freeze(manifest)
    with pytest.raises(ValueError, match="already_frozen"):
        registry.freeze(manifest)
    with pytest.raises(ValueError, match="already_used"):
        registry.freeze(manifest | {"policy": {"probability": 0.7}})
    with pytest.raises(ValueError, match="cannot_return_to_search"):
        registry.forbid_search([{"group_id": "g", "target_id": "new", "unit_aliases": []}])
    with pytest.raises(ValueError, match="cannot_return_to_search"):
        registry.forbid_search([{"group_id": "new", "target_id": "new", "unit_aliases": ["alias"]}])
    registry.consume(key, manifest)
    with pytest.raises(ValueError, match="consumed"):
        registry.consume(key, manifest)
    assert registry.db.execute("SELECT state FROM studies").fetchone()[0] == "consumed"
    registry.close()


def test_versioned_review_amendment_binds_runtime_sample_and_primary_run(tmp_path):
    workspace = SimpleNamespace(
        lock={
            "binding": {
                "runtime": {"target_bytes": 32768},
                "required_prediction_run": "v3-primary-new",
                "review_policy_amendment_sha256": "amendment-hash",
            }
        },
        sample_sha256="sample-hash",
    )
    path = tmp_path / "amendment.json"
    path.write_text("{}", encoding="utf-8")
    amendment_sha = batch.sha(batch.read(path))
    workspace.lock["binding"]["review_policy_amendment_sha256"] = amendment_sha
    amendment = {
        "workflow_binding": {
            "runtime_sha256": review.fingerprint(workspace.lock["binding"]["runtime"]),
            "sample_sha256": "sample-hash",
            "required_prediction_run": "v3-primary-new",
        },
        "cohort_binding": {"current_workflow_sample_sha256": "sample-hash"},
    }
    assert confirm.versioned_workflow_binding_matches(workspace, amendment, path)
    wrong_runtime = deepcopy(amendment)
    wrong_runtime["workflow_binding"]["runtime_sha256"] = "old-runtime"
    assert not confirm.versioned_workflow_binding_matches(workspace, wrong_runtime, path)
    wrong_sample = deepcopy(amendment)
    wrong_sample["workflow_binding"]["sample_sha256"] = "confirmation-sample"
    assert not confirm.versioned_workflow_binding_matches(workspace, wrong_sample, path)
    wrong_run = deepcopy(amendment)
    wrong_run["workflow_binding"]["required_prediction_run"] = "other-run"
    assert not confirm.versioned_workflow_binding_matches(workspace, wrong_run, path)


def label(name="h1", day=0):
    catalog = v3.load_catalog()
    case = {"target_id": "t", "group_id": "g", "input_version": "v3", "input_sha256": "i"}
    value = review.empty_label(case, catalog, name)
    value.update(
        created_at=(datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=day)).isoformat(),
        exposure="none_reported",
        independent_attestation=True,
    )
    value["reasons"]["general.incomplete"].update(decision="present", expertise="qualified")
    return value


def test_second_review_all_positive_single_delayed_and_ai_boundary():
    config = cal.risk_config() | {"calibration_other_review_fraction": 0}
    first = label()
    ai = label("ai", 1)
    ai["reviewer"]["kind"] = "ai"
    result, info = confirm.calibration_label([first, ai], config)
    assert (
        info["status"] == "REVIEW_INCOMPLETE" and result["reasons"]["general.incomplete"]["decision"] == "not_reviewed"
    )
    result, info = confirm.calibration_label([first, label(day=1)], config)
    assert info["status"] == "REVIEW_INCOMPLETE"
    result, info = confirm.calibration_label([first, label(day=2)], config)
    assert info["status"] == "READY" and info["evidence_level"] == "single_human_delayed"
    assert first["reasons"]["general.incomplete"]["decision"] == "present"


def test_disputed_second_review_is_missing_until_explicit_adjudication():
    config = cal.risk_config() | {"calibration_other_review_fraction": 0}
    first, second = label(), label("h2", 1)
    second["reasons"]["general.incomplete"]["decision"] = "absent"
    result, evidence = confirm.calibration_label([first, second], config)
    assert evidence["unresolved_conflicts"] == ["general.incomplete"]
    assert result["reasons"]["general.incomplete"]["decision"] == "not_reviewed"
    adjudication = label("judge", 2)
    adjudication["disagreement"] = "adjudicated"
    result, evidence = confirm.calibration_label([first, second, adjudication], config)
    assert result["reasons"]["general.incomplete"]["decision"] == "present"


def test_three_packet_ledger_unknown_cost_and_resume_never_retries(tmp_path):
    target = {"target_id": "t", "source": "synthetic", "input_sha256": "h", "disposition": "runnable"}
    plan = {"batch_id": "mock-v3", "targets": [target]}
    ledger = batch.Ledger(tmp_path / "mock.db", plan, "mock")
    refs = ledger.claim(target, stages=v3.STAGES)
    assert len(refs) == 3 and ledger.held == 3 * ledger.reserve
    for stage in v3.STAGES:
        record = {"stage": stage, "budget_reservation_ref": refs[stage], "status": "started", "usage": None}
        ledger.stage("t", "started", record)
        if stage == "reasons.1":
            ledger.stage("t", "failed", record | {"status": "failed_or_unknown"})
        else:
            ledger.stage(
                "t", "completed", record | {"status": "completed", "usage": {"input_tokens": 100, "output_tokens": 1}}
            )
    assert ledger.held == ledger.reserve
    ledger.finish("t", "partial_error", result={"partial": True})
    saved = [tuple(row) for row in ledger.db.execute("SELECT * FROM stages")]
    ledger.recover()
    assert ledger.pending() == set() and ledger.held == ledger.reserve
    assert [tuple(row) for row in ledger.db.execute("SELECT * FROM stages")] == saved
    ledger.close()


def test_adjudication_cannot_replace_missing_primary_or_independent_opinions():
    adjudication = label("judge")
    adjudication["disagreement"] = "adjudicated"
    result, info = confirm.calibration_label([adjudication], cal.risk_config())
    assert result is None and info["status"] == "PRIMARY_REVIEW_REQUIRED"
    first, second = label(), label("h2")
    first["routes"]["task"].update(status="reviewed", accepted=["code_generate"])
    second["routes"]["task"].update(status="reviewed", accepted=["code_rewrite"])
    result, info = confirm.calibration_label([first, second], cal.risk_config())
    assert info["unresolved_route_conflicts"] == ["task"]
    assert result["routes"]["task"]["status"] == "not_reviewed"


def test_confirmation_collector_has_separate_run_policy_and_targets(tmp_path):
    workspace = SimpleNamespace(
        path=tmp_path,
        sample_sha256="sample",
        lock={"binding": {"required_prediction_run": "primary", "runtime": {"v": 3}}},
    )
    policy = v3.Policy(route_probability=0.9)
    manifest = {
        "primary_run": "confirm",
        "runtime": {"v": 3},
        "sample_sha256": "sample",
        "policy": asdict(policy),
        "target_ids": ["selected"],
    }
    batch.save(tmp_path / "confirmation.freeze.json", {"id": review.fingerprint(manifest), "manifest": manifest})
    assert collection.collection_scope(workspace, "confirm", policy, live=True) == {"selected"}
    with pytest.raises(ValueError, match="confirmation_policy_changed"):
        collection.collection_scope(workspace, "confirm", v3.Policy(), live=True)
    with pytest.raises(ValueError, match="primary_collection_policy_changed"):
        collection.collection_scope(workspace, "primary", policy, live=True)
    with pytest.raises(ValueError, match="predeclared"):
        collection.collection_scope(workspace, "renamed-confirm", policy, live=True)


def test_primary_collection_only_contains_selected_calibration_groups(tmp_path):
    workspace = SimpleNamespace(
        path=tmp_path,
        lock={"binding": {"required_prediction_run": "primary"}},
        sample={
            "rows": [
                {"target_id": "cal", "partition": "calibration", "selected": True},
                {"target_id": "same-group-variant", "partition": "calibration", "selected": False},
                {"target_id": "diagnostic", "partition": "diagnostic", "selected": True},
                {"target_id": "reserved", "partition": "confirmation", "selected": True},
            ]
        },
    )
    for live in (False, True):
        assert collection.collection_scope(workspace, "primary", v3.Policy(), live=live) == {"cal"}


def test_prediction_request_reconstruction_detects_score_and_hash_tampering(tmp_path):
    import asyncio

    import httpx

    value = inputs.build_input(
        "t", [{"role": "user", "content": "Write a Python function to add numbers."}], "def add(a,b): return a+b"
    )
    fake = tmp_path / "fake.json"
    fake.write_text('{"typesafe":"synthetic-key-only"}')
    result = asyncio.run(
        v3.classify(
            lambda: value,
            keys_file=fake,
            policy=v3.Policy(),
            enabled=True,
            outbound_approval_ref="synthetic",
            budget_reservations={s: s for s in v3.STAGES},
            admission_check=lambda: True,
            stage_callback=lambda *_: None,
            transport=httpx.MockTransport(batch.synthetic_response),
        )
    )
    record = {"result": result, "stages": deepcopy(result["stages"]), "technical_status": result["technical_status"]}
    confirm.validate_requests(record, value, v3.load_catalog(), v3.Policy())
    tampered = deepcopy(record)
    tampered["stages"][0]["request_sha256"] = "wrong"
    with pytest.raises(ValueError, match="request_binding"):
        confirm.validate_requests(tampered, value, v3.load_catalog(), v3.Policy())
    tampered = deepcopy(record)
    tampered["result"]["assessment"] = "invented"
    with pytest.raises(ValueError, match="outcome_changed"):
        confirm.validate_requests(tampered, value, v3.load_catalog(), v3.Policy())


def test_policy_freeze_binds_analysis_and_creates_a_distinct_confirmation_run(tmp_path):
    workspace = SimpleNamespace(
        path=tmp_path,
        sample_sha256="sample",
        check=lambda: None,
        lock={
            "binding": {
                "required_prediction_run": "primary",
                "runtime": {"v": 3},
                "analysis": {"v": 1},
                "source_expires_at": "2099-01-01T00:00:00+00:00",
            }
        },
        sample={
            "confirmation_scope": "internal_exposed_population",
            "rows": [
                {
                    "target_id": "confirm-case",
                    "group_id": "group",
                    "partition": "confirmation",
                    "selected": True,
                    "unit_aliases": ["alias"],
                }
            ],
        },
    )
    search_result = {
        "status": "CANDIDATE_SELECTED_NOT_CONFIRMED",
        "selected_policy": asdict(v3.Policy()),
        "runtime": {"v": 3},
        "primary_run": "primary",
        "analysis": {"v": 1},
        "data_pins": {},
        "risk_config": cal.risk_config(),
    }
    registry = confirm.ConfirmationRegistry(tmp_path / "registry.db")
    with pytest.raises(ValueError, match="analysis_changed"):
        confirm.freeze(workspace, search_result | {"analysis": {"v": 2}}, registry)
    manifest = confirm.freeze(workspace, search_result, registry)
    assert manifest["primary_run"] != "primary"
    assert manifest["target_ids"] == ["confirm-case"] and not manifest["optional_stopping_allowed"]
    template = collection.config_template(workspace, confirmation=manifest)
    assert template["live"]["batch_id"] == manifest["primary_run"] and not template["live"]["enabled"]
    registry.close()
