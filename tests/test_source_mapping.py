import copy
import gzip
import hashlib
import json

import pytest

from whynote import source_mapping as mapping
from whynote import source_review as runner

REF = "00000000-0000-4000-8000-000000000001"
OTHER_REF = "00000000-0000-4000-8000-000000000002"


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def case_review(**changes):
    return {
        "review_ref": REF,
        "reviewer_ref": OTHER_REF,
        "review_version": "m5-case-review-v1",
        "diagnoses": ["missing_category", "multiple_issues"],
        "task": "code_rewrite",
        "context_language": "zh",
        "feedback_language": "en",
        "group_ref": REF,
        "partition": "exploration",
        "split_version": REF,
        "screening_ref": REF,
    } | changes


def helpsteer_row():
    return {
        "context": [{"role": "user", "content": "只改写函数，不改签名。"}],
        "response1": "def changed_name(): pass",
        "response2": "def unchanged_name(): pass",
        "feedback1": ["SYNTHETIC_FEEDBACK_ONE", "SYNTHETIC_FEEDBACK_TWO"],
        "feedback2": ["SYNTHETIC_FEEDBACK_THREE"],
        "language": "python",
        "edit": "FUTURE_EDIT_MUST_NOT_LEAK",
    }


def conversation():
    return [
        {"role": "user", "content": "不要改变函数签名。"},
        {"role": "assistant", "content": "def renamed(): pass"},
        {"role": "user", "content": "SYNTHETIC_FUTURE_FEEDBACK"},
        {"role": "assistant", "content": "SYNTHETIC_FUTURE_REWRITE"},
    ]


def linkage(**changes):
    return {"schema_review_ref": REF, "linkage_ref": OTHER_REF, "target_index": 1} | changes


def source(tmp_path, name="helpsteer3", rows=None, reviews=None, **changes):
    rows = [helpsteer_row()] if rows is None else rows
    data_path = tmp_path / f"{name}.json"
    review_path = tmp_path / f"{name}-reviews.json"
    result = {
        "source": name,
        "mode": "synthetic",
        "status": "admitted",
        "revision": mapping.REVISIONS[name],
        "config": next(iter(runner.CONFIGS[name])),
        "split": "train",
        "format": "json",
        "data_path": data_path.name,
        "reviews_path": review_path.name,
        "data_sha256": write_json(data_path, rows),
        "reviews_sha256": write_json(review_path, reviews or {}),
    }
    return result | changes


def batch(tmp_path, sources):
    path = tmp_path / "batch.json"
    write_json(path, {"schema_version": "m5-source-batch-v1", "sources": sources})
    return path


def test_helpsteer_feedback_pairing_and_no_text_output(tmp_path):
    s = source(tmp_path, reviews={"0": {"targets": {"response1": case_review(), "response2": case_review()}}})
    report, results = runner.run_batch(batch(tmp_path, [s]))
    first, second = results[0]["records"]
    assert first["feedback_count"] == 2
    assert second["feedback_count"] == 1
    assert first["feedback_refs"][0]["pointer"] == "/feedback1/0"
    assert second["feedback_refs"][0]["pointer"] == "/feedback2/0"
    assert first["feedback_origin"] == second["feedback_origin"] == "evaluator"
    assert first["context_sha256"] == second["context_sha256"]
    assert first["record_id"] != second["record_id"]
    assert first["prediction_refs"]["target"]["pointer"] == "/response1"
    assert first["ready_for_replay"] is True
    encoded = json.dumps([report, results], ensure_ascii=False)
    for token in ("SYNTHETIC_FEEDBACK", "FUTURE_EDIT", "不改签名", "def changed_name", "python"):
        assert token not in encoded
    assert report["quality_metrics"] is None
    assert report["user_cost_metrics"] is None
    assert report["sources"][0]["diagnoses"] == {"missing_category": 2, "multiple_issues": 2}


def test_helpsteer_bad_sibling_is_counted_not_dropped(tmp_path):
    row = helpsteer_row()
    row["feedback2"] = "must be an array"
    result = runner.run_source(tmp_path, source(tmp_path, rows=[row]))
    assert result["status"] == "partial"
    assert len(result["records"]) == 1
    assert result["rejected"] == [{"row_id": 0, "target_key": "response2", "code": "invalid_feedback_array"}]


@pytest.mark.parametrize("name", ["wildfb", "wildfeedback"])
def test_reviewed_conversation_stops_before_feedback(tmp_path, name):
    row = {
        "messages": conversation(),
        "user_feedback": conversation()[2]["content"],
        "label": 2,
        "history": "DO_NOT_GUESS_HISTORY",
        "chosen": "GENERATED_CHOSEN_NOT_ORIGINAL",
    }
    binding = linkage(conversation_pointer="/messages")
    s = source(tmp_path, name, [row], {"0": {"linkage": binding}})
    result = runner.run_source(tmp_path, s)
    assert result["status"] == "mapped"
    record = result["records"][0]
    assert record["target_turn_index"] == 1
    assert record["target_ref"]["pointer"] == "/messages/1/content"
    assert record["feedback_refs"][0]["pointer"] == "/messages/2/content"
    assert len(record["prediction_refs"]["context"]) == 1
    assert record["feedback_origin"] == "original_user"
    assert record["ready_for_replay"] is False
    if name == "wildfb":
        assert record["label_refs"][0]["origin"] == "automated"
    assert "GENERATED_CHOSEN" not in json.dumps(result)


@pytest.mark.parametrize(
    "change,code",
    [
        ({"user_feedback": "different"}, "feedback_target_mismatch"),
        ({"label": True}, "invalid_automated_label"),
        ({"messages": "flat text"}, "invalid_messages"),
        ({"messages": [{"role": "user", "content": "x"}]}, "invalid_target_index"),
    ],
)
def test_wildfb_schema_and_linkage_fail_closed(tmp_path, change, code):
    row = {"messages": conversation(), "user_feedback": conversation()[2]["content"], "label": 2} | change
    result = runner.run_source(tmp_path, source(tmp_path, "wildfb", [row], {"0": {"linkage": linkage()}}))
    assert not result["records"]
    assert result["rejected"][0]["code"] == code


def test_missing_linkage_cannot_create_original_user_record(tmp_path):
    result = runner.run_source(tmp_path, source(tmp_path, "wildfeedback", [{"messages": conversation()}]))
    assert result["rejected"][0]["code"] == "missing_linkage"


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"mode": "public"}, "public_data_not_enabled"),
        ({"status": "HOLD"}, "source_not_admitted"),
        ({"status": "excluded"}, "source_not_admitted"),
        ({"revision": "latest"}, "revision_mismatch"),
        ({"config": "preference"}, "config_not_supported"),
    ],
)
def test_denied_source_never_opens_data_or_review_files(tmp_path, monkeypatch, change, reason):
    s = source(tmp_path, **change)

    def forbidden(*args):
        pytest.fail("denied source was opened")

    monkeypatch.setattr(runner, "checked_file", forbidden)
    result = runner.run_source(tmp_path, s)
    assert result["status"] == "HOLD"
    assert result["reason"] == reason
    assert result["rows_read"] is None


def test_wildfeedback_preference_config_not_supported(tmp_path, monkeypatch):
    s = source(tmp_path, "wildfeedback", config="wildfeedback")
    monkeypatch.setattr(runner, "checked_file", lambda *args: pytest.fail("preference data was opened"))
    assert runner.run_source(tmp_path, s)["reason"] == "config_not_supported"


def test_unreviewed_language_and_diagnosis_remain_unknown(tmp_path):
    report, results = runner.run_batch(batch(tmp_path, [source(tmp_path)]))
    summary = report["sources"][0]
    assert summary["coverage"]["context_language"] == {"unknown": 2}
    assert summary["missing"]["review_ref"] == {"count": 2, "denominator": 2, "rate": 1.0}
    assert summary["diagnoses"] == {}
    assert all(r["review"]["diagnoses"] is None for r in results[0]["records"])
    assert summary["unreviewed_targets"] == 2


def test_pair_cannot_cross_partitions(tmp_path):
    reviews = {
        "0": {
            "targets": {"response1": case_review(), "response2": case_review(partition="holdout", group_ref=OTHER_REF)}
        }
    }
    report, results = runner.run_batch(batch(tmp_path, [source(tmp_path, reviews=reviews)]))
    assert report["sources"][0]["split_conflicts"] == 2
    assert all(not r["ready_for_replay"] for r in results[0]["records"])


def test_cross_source_overlap_is_rejected(tmp_path):
    hs = helpsteer_row()
    chat = hs["context"] + [
        {"role": "assistant", "content": hs["response1"]},
        {"role": "user", "content": "SYNTHETIC_FUTURE"},
    ]
    hs_source = source(tmp_path, reviews={"0": {"targets": {"response1": case_review()}}})
    wf_source = source(
        tmp_path,
        "wildfeedback",
        [{"messages": chat}],
        {
            "0": {
                "linkage": linkage(conversation_pointer="/messages"),
                "targets": {"conversation": case_review(partition="holdout", group_ref=OTHER_REF)},
            }
        },
    )
    report, _ = runner.run_batch(batch(tmp_path, [hs_source, wf_source]))
    assert report["sources"][0]["split_conflicts"] == 2
    assert report["sources"][1]["split_conflicts"] == 1


def test_group_split_version_mismatch(tmp_path):
    reviews = {"0": {"targets": {"response1": case_review(), "response2": case_review(split_version=OTHER_REF)}}}
    report, _ = runner.run_batch(batch(tmp_path, [source(tmp_path, reviews=reviews)]))
    assert report["sources"][0]["split_conflicts"] == 2


@pytest.mark.parametrize(
    "reviews,code",
    [
        ({"99": {}}, "unexpected_review_row"),
        ({"0": {"targets": {"response9": {}}}}, "unexpected_review_target"),
        ({"0": {"targets": {"response1": case_review(diagnoses=["guessed_gold"])}}}, "invalid_diagnoses"),
        ({"0": {"targets": {"response1": case_review(review_ref="raw text secret")}}}, "invalid_reference"),
    ],
)
def test_invalid_review_not_silently_accepted(tmp_path, reviews, code):
    result = runner.run_source(tmp_path, source(tmp_path, reviews=reviews))
    assert result["reason"] == code or any(r["code"] == code for r in result["rejected"])
    assert "raw text secret" not in json.dumps(result)


def test_rows_preserve_empty_and_malformed_denominators(tmp_path):
    s = source(tmp_path)
    raw = (json.dumps(helpsteer_row()) + "\n\n{broken\n").encode()
    (tmp_path / s["data_path"]).write_bytes(raw)
    s |= {"format": "jsonl", "data_sha256": hashlib.sha256(raw).hexdigest()}
    result = runner.run_source(tmp_path, s)
    assert result["rows_read"] == 3
    assert len(result["records"]) == 2
    assert [r["row_id"] for r in result["rejected"]] == [1, 2]


def test_compressed_jsonl_and_decompression_limit(tmp_path, monkeypatch):
    s = source(tmp_path)
    raw = gzip.compress(json.dumps(helpsteer_row()).encode())
    (tmp_path / s["data_path"]).write_bytes(raw)
    s |= {"format": "jsonl.gz", "data_sha256": hashlib.sha256(raw).hexdigest()}
    assert runner.run_source(tmp_path, s)["status"] == "mapped"
    monkeypatch.setattr(runner, "MAX_BYTES", len(raw) + 1)
    assert runner.run_source(tmp_path, s)["reason"] == "file_too_large"


def test_hash_mismatch_and_path_escape_do_not_stop_other_sources(tmp_path):
    bad = source(tmp_path, "wildfb", data_path="../outside.json")
    good = source(tmp_path)
    report, _ = runner.run_batch(batch(tmp_path, [bad, good]))
    assert report["sources"][0]["reason"] == "path_outside_root"
    assert report["sources"][1]["mapped_targets"] == 2
    (tmp_path / good["data_path"]).write_text("tampered", encoding="utf-8")
    assert runner.run_source(tmp_path, good)["reason"] == "file_hash_mismatch"


def test_all_denied_no_false_zero_denominators(tmp_path):
    report, _ = runner.run_batch(batch(tmp_path, [source(tmp_path, status="HOLD")]))
    assert report["sources"][0]["missing"]["task"]["rate"] is None
    assert report["sources"][0]["rows_read"] is None
    assert report["sources"][1]["reason"] == "source_not_supplied"


def test_record_identity_stable_but_target_changes(tmp_path):
    row = {
        "messages": conversation() + [{"role": "user", "content": "later feedback"}],
        "user_feedback": conversation()[2]["content"],
        "label": 2,
    }
    s = source(tmp_path, "wildfeedback", [row], {"0": {"linkage": linkage(conversation_pointer="/messages")}})
    first = runner.run_source(tmp_path, s)["records"][0]
    assert first == runner.run_source(tmp_path, s)["records"][0]
    later, errors = mapping.map_row(s, 0, row, {"linkage": linkage(conversation_pointer="/messages", target_index=3)})
    assert not errors
    assert later[0]["record_id"] != first["record_id"]


@pytest.mark.parametrize("raw", ['{"a": 1, "a": 2}', '{"a": NaN}'])
def test_ambiguous_json_rejected(raw):
    with pytest.raises(mapping.MappingError):
        runner.json_value(raw)


def test_cli_three_sources_and_no_overwrite(tmp_path, capsys):
    chat = conversation()
    wf = source(
        tmp_path,
        "wildfb",
        [{"messages": chat, "user_feedback": chat[2]["content"], "label": 2}],
        {"0": {"linkage": linkage()}},
    )
    hs = source(tmp_path)
    ms = source(
        tmp_path, "wildfeedback", [{"messages": chat}], {"0": {"linkage": linkage(conversation_pointer="/messages")}}
    )
    manifest = batch(tmp_path, [wf, hs, ms])
    output = tmp_path / "output"
    args = ["--manifest", str(manifest), "--output", str(output)]
    assert runner.main(args) == 0
    assert len(list(output.glob("*.json"))) == 7
    original = (output / "report.json").read_bytes()
    assert runner.main(args) == 1
    assert (output / "report.json").read_bytes() == original
    assert "SYNTHETIC_FUTURE" not in capsys.readouterr().out


def test_adapter_does_not_mutate_input(tmp_path):
    row = helpsteer_row()
    original = copy.deepcopy(row)
    mapping.map_row(source(tmp_path), 0, row, {})
    assert row == original


def test_corrupt_gzip_does_not_stop_other_sources(tmp_path):
    bad = source(tmp_path, "wildfb")
    raw = b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\xff\xffbroken"
    (tmp_path / bad["data_path"]).write_bytes(raw)
    bad |= {"format": "jsonl.gz", "data_sha256": hashlib.sha256(raw).hexdigest()}
    report, _ = runner.run_batch(batch(tmp_path, [bad, source(tmp_path)]))
    assert report["sources"][0]["reason"] == "source_io_error"
    assert report["sources"][1]["mapped_targets"] == 2


def test_missing_hash_is_rejected_before_file_read(tmp_path, monkeypatch):
    s = source(tmp_path, reviews_sha256=None)
    monkeypatch.setattr(runner, "read_bytes", lambda *args: pytest.fail("unversioned file was read"))
    assert runner.run_source(tmp_path, s)["reason"] == "invalid_file_hash"


def test_shipped_synthetic_example(tmp_path):
    from pathlib import Path

    manifest = Path(__file__).resolve().parents[1] / "fixtures/m5-synthetic/batch.json"
    report, results = runner.run_batch(manifest)
    assert [r["status"] for r in report["sources"]] == ["mapped", "mapped", "mapped"]
    assert sum(len(r["records"]) for r in results) == 4
    assert report["mode"] == "synthetic"


def test_all_bad_rows_remain_excluded_and_empty_input_is_not_success(tmp_path):
    s = source(tmp_path, rows=[None, "raw secret", 42])
    result = runner.run_source(tmp_path, s)
    assert result["status"] == "excluded"
    assert len(result["rejected"]) == result["rows_read"] == 3
    assert "raw secret" not in json.dumps(result)
    assert runner.run_source(tmp_path, source(tmp_path, rows=[]))["reason"] == "empty_source"


def test_unknown_message_fields_are_not_silently_removed(tmp_path):
    row = helpsteer_row()
    row["context"][0]["extra"] = "unknown data"
    result = runner.run_source(tmp_path, source(tmp_path, rows=[row]))
    assert result["rejected"][0]["code"] == "unsupported_message_fields"
