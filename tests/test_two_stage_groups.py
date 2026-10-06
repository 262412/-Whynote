import copy
import gzip
import shutil
import socket
import sqlite3

import pytest
from test_evaluation_signals import feedback
from test_two_stage_batch import ROOT, args, pin, sample, write_json
from test_two_stage_batch import bundle as bundle
from test_two_stage_offline import saved as saved

from whynote import explore_inputs
from whynote import two_stage as core
from whynote import two_stage_batch as batch
from whynote import two_stage_groups as groups
from whynote import two_stage_offline as offline
from whynote.source_mapping import digest


def annotation(number, sat=False, dsat=False, **extra):
    return {
        "UtterranceId": number,
        "TurnId": number // 2 + 1,
        "Role": "User" if number % 2 == 0 else "Agent",
        "Preceeding": "YES",
        "Satisfaction": sat,
        "Disatisfaction": dsat,
        **extra,
    }


@pytest.fixture
def source_data(bundle, monkeypatch):
    """Only synthetic admitted bodies; excluded identities have no body rows."""
    raw_sources = {
        "helpsteer3": {
            "context": [{"role": "user", "content": "PRIVATE_REQUEST"}],
            "response2": "PRIVATE_ANSWER",
            "feedback2": feedback("perfectly", "perfectly", "perfectly"),
            "response1": "EXCLUDED_SIBLING",
            "feedback1": feedback("not", "not", "not"),
        },
        "wildfb": {
            "history": [],
            "messages": [
                {"role": "user", "content": "PRIVATE_REQUEST"},
                {"role": "assistant", "content": "PRIVATE_ANSWER"},
            ],
            "user_feedback": {"role": "user", "content": "PRIVATE_FEEDBACK"},
            "label": 3,
        },
        "wildfeedback": [
            annotation(0, True, Content="PRIVATE_REQUEST"),
            annotation(1, True, Content="PRIVATE_ANSWER"),
            annotation(2, dsat=True, Content="PRIVATE_FEEDBACK"),
        ],
    }
    originals_path = bundle.root / "var/research/inputs.sqlite3"
    outbound_path = bundle.root / batch.PREP / "outbound.sqlite3"
    specs = {}
    with sqlite3.connect(originals_path) as db, sqlite3.connect(outbound_path) as outbound:
        for entry in bundle.plan["sources"]:
            source = entry["source"]
            path = bundle.root / entry["path"]
            raw = batch.encode(raw_sources[source])
            path.write_bytes(gzip.compress(raw + b"\n") if source == "helpsteer3" else raw + b"\n")
            entry.update(pin(path))
            specs[source] = ("synthetic", "pinned", path.name, entry["bytes"], entry["sha256"])
            target = next(t for t in bundle.plan["targets"] if t["source"] == source and t["eligible"])
            old_key = target["target_id"]
            original = batch.decode(db.execute("SELECT payload FROM inputs WHERE input_id=?", (old_key,)).fetchone()[0])
            original["file_sha256"] = entry["sha256"]
            if source == "helpsteer3":
                original["reference"] = {"origin": "evaluator", "feedback": raw_sources[source]["feedback2"]}
            elif source == "wildfb":
                original["reference"] = {
                    "origin": "original_user",
                    "feedback": ["PRIVATE_FEEDBACK"],
                    "automated_label": 3,
                }
            else:
                original["row_id"] = 1
                original["reference"] = {
                    "origin": "automated_annotation",
                    "conversation_start_row": 0,
                    "feedback_row_id": 2,
                    "automated_annotation": {"Satisfaction": True, "Disatisfaction": False},
                }
                original["source_conversation_id"] = digest({"file": entry["sha256"], "start": 0})
            key = digest({k: original[k] for k in ("source", "revision", "file_sha256", "row_id", "target_id")})
            original["input_id"] = key
            target.update(target_id=key, input_key=key)
            db.execute(
                "UPDATE inputs SET input_id=?,payload=? WHERE input_id=?",
                (key, batch.encode(original).decode(), old_key),
            )
            outbound.execute("UPDATE inputs SET target_id=? WHERE target_id=?", (key, old_key))
    manifest_path = bundle.root / "var/research/source-manifest.json"
    manifest = batch.decode(manifest_path.read_bytes())
    for entry, updated in zip(manifest["sources"], bundle.plan["sources"], strict=True):
        entry.update(updated)
    bundle.plan["input_pins"]["source_manifest"] = write_json(manifest_path, manifest)
    bundle.plan["input_pins"]["inputs"] = pin(originals_path)
    outbound_pin = pin(outbound_path)
    bundle.plan.update(db_sha256=outbound_pin["sha256"], db_bytes=outbound_pin["bytes"])
    bundle.plan_sha = write_json(bundle.root / batch.PREP / "plan.json", bundle.plan)["sha256"]
    monkeypatch.setattr(batch, "PLAN_SHA256", bundle.plan_sha)
    monkeypatch.setattr(explore_inputs, "SOURCES", specs)
    return bundle


@pytest.fixture
def pair(source_data, request):
    bundle, plan, records, summary = request.getfixturevalue("saved")
    second = bundle.directory.with_name("m55-two-stage-second")
    second.mkdir()
    plan2, records2, summary2 = copy.deepcopy((plan, records, summary))
    plan2["batch_id"] = summary2["batch_id"] = "second"
    for record in records2:
        record["batch_id"] = "second"
    batch.save(second / "plan.json", plan2, new=True)
    offline.write_lines(second / "live.results.jsonl", records2)
    batch.save(second / "live.summary.json", summary2, new=True)
    shutil.copyfile(bundle.directory / "live.sqlite3", second / "live.sqlite3")
    with sqlite3.connect(second / "live.sqlite3") as db:
        db.execute(
            "UPDATE meta SET value=? WHERE key='identity'",
            (batch.encode({"plan_sha256": batch.sha(batch.encode(plan2)), "mode": "live"}).decode(),),
        )
    return bundle, second, records


def test_groups_full_offline_join_is_deterministic_and_preserves_inputs_results_and_denominators(pair):
    bundle, second, records = pair
    paths = [p for directory in (bundle.directory, second) for p in directory.iterdir()]
    paths += [bundle.root / pin["path"] for pin in bundle.plan["input_pins"].values()]
    before = {str(p): p.read_bytes() for p in paths}
    outputs = []
    for name in ("groups-a", "groups-b"):
        output = second.with_name(name)
        args_ = args(
            bundle, "groups", source_batch=str(bundle.directory), compare_batch=str(second), output_dir=str(output)
        )
        result = batch.run(args_, ROOT)
        summary = batch.decode((output / "groups.summary.json").read_bytes())
        rows = [batch.decode(line) for line in (output / "groups.results.jsonl").read_bytes().splitlines()]
        outputs.append(rows)
        assert result["real_api_requests"] == result["real_key_reads"] == summary["network_attempts_denied"] == 0
        assert result["group_counts"]["positive_signal"] == 2 and result["group_counts"]["negative_signal"] == 1
        overall = summary["batches"]["test"]["overall"]
        assert overall["denominators"] == {
            "original_targets": 3000,
            "original_excluded": 2997,
            "new_excluded": 0,
            "runnable": 3,
            "technical_success": 3,
            "technical_error": 0,
            "reconcile": 0,
            "not_executed": 0,
            "result_targets": 3,
        }
        assert summary["comparison"]["cohort_counts"]["both_success"] == 3
        assert summary["batches"]["test"]["by_group"]["mixed_signal"]["rates"]["candidate_yield"]["value"] is None
        assert summary["quality"] == groups.QUALITY
        for row, original in zip(rows, records, strict=True):
            view = row["batches"]["test"]
            assert view["source_record_sha256"] == batch.sha(batch.encode(original))
            if original["result"]:
                assert view["assessment"] == original["result"]["assessment"]
                assert view["coverage"] == original["result"]["coverage"]
            else:
                assert row["evaluation"] is None and view["assessment"] is None
        with pytest.raises(batch.Closed, match="new_independent_output"):
            batch.run(args_, ROOT)
        for p in output.iterdir():
            assert not any(
                marker in p.read_bytes()
                for marker in (
                    b"PRIVATE_REQUEST",
                    b"PRIVATE_ANSWER",
                    b"PRIVATE_FEEDBACK",
                    b"PRIVATE_GOLD",
                    b"EXCLUDED_SIBLING",
                )
            )
    assert outputs[0] == outputs[1]
    assert {str(p): p.read_bytes() for p in paths} == before


@pytest.mark.parametrize("source", sorted(batch.SOURCES))
def test_changing_evaluation_labels_cannot_change_projection_wire_or_fingerprint(source):
    target, state, original = sample(source)
    before = batch.adapt_materials(target, state, original)[0]
    question = core.route_questions(core.load_catalog())
    original["reference"] = {
        "feedback": feedback("not", "perfectly", "mostly"),
        "label": 4,
        "group": "mixed_signal",
        "gold": "PRIVATE_GOLD",
    }
    original["future"] = [{"role": "user", "content": "Verification reference:\nPRIVATE_FEEDBACK"}]
    after = batch.adapt_materials(target, state, original)[0]
    assert before == after
    assert core.request_bytes(before, question) == core.request_bytes(after, question)
    assert batch.sha(core.request_bytes(before, question)) == batch.sha(core.request_bytes(after, question))


def test_helpsteer_siblings_keep_separate_feedback_and_common_source_row():
    row = {
        "context": [{"role": "user", "content": "question"}],
        "response1": "a",
        "response2": "b",
        "feedback1": feedback("not", "not", "not"),
        "feedback2": feedback("perfectly", "perfectly", "perfectly"),
    }
    pin_ = {"path": "synthetic", "revision": "pinned", "sha256": "1" * 64}
    originals = [
        {
            "source": "helpsteer3",
            "row_id": 0,
            "target_id": f"response{i}",
            "context": row["context"],
            "answer": row[f"response{i}"],
            "reference": {"origin": "evaluator", "feedback": row[f"feedback{i}"]},
        }
        for i in (1, 2)
    ]
    first, second = [groups.group_original(o, pin_, {0: row}, {}) for o in originals]
    assert first["group"] == "negative_signal" and second["group"] == "positive_signal"
    assert first["source_row_group_id"] == second["source_row_group_id"]
    assert groups.selectors("helpsteer3", originals[1:]) == {0: {"context", "response2", "feedback2"}}
    originals[1]["reference"]["feedback"] = row["feedback1"]
    assert groups.group_original(originals[1], pin_, {0: row}, {})["group"] == "unclear_signal"


def test_wildfb_wrong_target_pair_is_explicitly_unclear():
    original = {
        "source": "wildfb",
        "row_id": 0,
        "target_id": "messages/1",
        "context": [{"role": "user", "content": "q"}],
        "answer": "a",
        "reference": {"origin": "original_user", "feedback": ["thanks"], "automated_label": 4},
    }
    pin_ = {"path": "synthetic", "revision": "pinned", "sha256": "1" * 64}
    row = {
        "history": [],
        "messages": original["context"] + [{"role": "assistant", "content": "different"}],
        "user_feedback": {"role": "user", "content": "thanks"},
        "label": 4,
    }
    result = groups.group_original(original, pin_, {0: row}, {})
    assert result["group"] == "unclear_signal" and result["linkage"]["verified"] is False
    assert result["feedback_origin"] == "original_user" and result["label_origin"] == "automated"


def test_segment_requires_unbroken_prefix_not_only_adjacent_pair():
    rows = {
        0: annotation(0),
        1: annotation(1),
        2: annotation(8),
        3: annotation(9),
        4: annotation(10),
        5: annotation(0),
        6: annotation(1),
    }
    assert groups.segments(rows) == {0: 0, 1: 0, 2: None, 3: None, 4: None, 5: 5, 6: 5}


@pytest.mark.parametrize("source", sorted(batch.SOURCES))
def test_missing_source_association_stays_in_unclear_group(source):
    _, _, original = sample(source)
    result = groups.group_original(original, {"path": "synthetic", "revision": "pinned", "sha256": "1" * 64}, {}, {})
    assert result["group"] == "unclear_signal" and result["linkage"]["verified"] is False
    assert result["reason"]


def test_group_statistics_preserve_no_unknown_not_asked_and_raw_rejected_yes():
    from test_two_stage_assessment import POLICY, answer, record, result

    value = result(choices={"general.style": answer("yes", 0.7)})
    frozen = copy.deepcopy(value)
    row = record(value) | {"disposition": "runnable"}
    stats = groups.bucket([row], POLICY)
    assert stats["assessments"]["abstained"] == 1 and stats["candidate_targets"] == 0
    assert stats["decisions"]["unknown"] == 1
    assert stats["decisions"]["no"] == value["coverage"]["asked_reason_count"] - 1
    assert stats["decisions"]["not_asked"] == 86 - value["coverage"]["asked_reason_count"]
    assert stats["rejected_raw_choices"]["yes"] == 1
    assert value == frozen


@pytest.mark.parametrize("change", ["duplicate", "missing", "input"])
def test_identity_damage_is_rejected_without_silently_dropping_targets(pair, change):
    bundle, _, records = pair
    if change == "input":
        record = next(r for r in records if r["result"])
        with pytest.raises(batch.Closed, match="historical_input_fingerprint_mismatch"):
            groups.checked_projection(record, {"request": "different", "answer": "different"})
        return
    changed = records[:-1] if change == "missing" else records[:-1] + [records[0]]
    with pytest.raises(batch.Closed, match="source_target_set_changed"):
        offline.validate_history(
            batch.decode((bundle.directory / "plan.json").read_bytes()),
            changed,
            bundle.plan,
            batch.decode((bundle.directory / "live.summary.json").read_bytes()),
            (bundle.directory / "live.sqlite3").read_bytes(),
        )


def test_statuses_never_count_failures_as_no_issue_and_decisions_remain_distinct(pair):
    bundle, _, records = pair
    allowed = copy.deepcopy([r for r in records if r["disposition"] == "runnable"])
    before = batch.encode(allowed)
    baseline = groups.bucket(allowed, batch.OFFLINE_POLICY)
    assert baseline["decisions"] == offline.aggregate(allowed, batch.OFFLINE_POLICY)["decisions"]
    assert batch.encode(allowed) == before
    for record, status in zip(allowed, ("error", "reconcile", "pending"), strict=True):
        record["technical_status"] = status
    value = groups.bucket(allowed, batch.OFFLINE_POLICY)
    assert value["denominators"]["runnable"] == 3 and value["denominators"]["result_targets"] == 0
    assert all(v == 0 for v in value["assessments"].values())
    assert all(v["value"] is None for v in value["rates"].values())


def test_groups_reject_credentials_before_any_read(bundle, monkeypatch):
    monkeypatch.setattr(batch, "read", lambda *_: pytest.fail("must reject credentials before reading"))
    with pytest.raises(batch.Closed, match="offline_keys_and_live_config_forbidden"):
        batch.run(args(bundle, "groups", keys_file="fake-key-never-read"), ROOT)


def test_unbuilt_reason_stage_keeps_missing_request_distinct_from_changed_questions(pair):
    _, _, records = pair
    original = next(r for r in records if r["result"])
    record = copy.deepcopy(original)
    record["routes"] = None
    stage = next(s for s in record["stages"] if s["stage"] == "reasons")
    stage["status"] = "not_sent"
    for key in ("question_ids", "request_sha256", "request_utf8_bytes"):
        stage.pop(key)
    groups.checked_projection(record, {"request": "PRIVATE_REQUEST", "answer": "PRIVATE_ANSWER"})
    flags = groups.comparison_flags(record, original)["stages"][1]
    assert flags["both_recorded"] is False and flags["question_ids_changed"] is None
    assert flags["first_request_recorded"] is False and flags["second_request_recorded"] is True
    stage["status"] = "started"
    with pytest.raises(batch.Closed, match="historical_unbuilt_stage_invalid"):
        groups.checked_projection(record, {"request": "PRIVATE_REQUEST", "answer": "PRIVATE_ANSWER"})


def test_groups_guard_blocks_external_calls_and_unapproved_reads(pair, monkeypatch, tmp_path):
    bundle, second, _ = pair
    credential = tmp_path / "fake-key"
    credential.write_text("synthetic secret", encoding="utf-8")
    original = groups.memberships

    def checked(*args_, **kwargs):
        with pytest.raises(batch.Closed, match="offline_network_forbidden"):
            socket.getaddrinfo("example.invalid", 443)
        with pytest.raises(batch.Closed, match="offline_file_read_forbidden"):
            credential.read_bytes()
        return original(*args_, **kwargs)

    monkeypatch.setattr(groups, "memberships", checked)
    batch.run(
        args(
            bundle,
            "groups",
            source_batch=str(bundle.directory),
            compare_batch=str(second),
            output_dir=str(second.with_name("guard-test")),
        ),
        ROOT,
    )
