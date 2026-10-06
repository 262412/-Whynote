import io
import json

import pytest

from whynote import evaluation_signals as signals
from whynote import two_stage_batch as batch


def feedback(*levels):
    return [f"The response is {level} helpful. Synthetic explanation." for level in levels]


@pytest.mark.parametrize(
    "levels,expected",
    [
        (("perfectly",) * 3, "positive_signal"),
        (("not", "slightly", "not"), "negative_signal"),
        (("not", "perfectly", "mostly"), "mixed_signal"),
        (("mostly",) * 3, "unclear_signal"),
        (("mostly", "perfectly", "perfectly"), "unclear_signal"),
        (("partially", "slightly", "slightly"), "unclear_signal"),
        (("perfectly",) * 2, "unclear_signal"),
    ],
)
def test_helpsteer_levels_do_not_become_defect_truth(levels, expected):
    result = signals.helpsteer(feedback(*levels), complete=True)
    assert result["group"] == expected
    assert result["labels"]["evaluation_count"] == len(levels)
    assert "gold" not in result


@pytest.mark.parametrize("value", [None, [], [None] * 3, ["Elsewhere perfectly helpful"] * 3])
def test_missing_or_non_structured_helpfulness_is_unclear(value):
    assert signals.helpsteer(value, complete=True)["group"] == "unclear_signal"


def test_partial_or_unverified_helpsteer_feedback_cannot_be_accepted():
    assert signals.helpsteer(feedback(*(["perfectly"] * 3)), complete=False)["group"] == "unclear_signal"


@pytest.mark.parametrize(
    "label,group",
    [
        (1, "negative_signal"),
        (2, "negative_signal"),
        (3, "positive_signal"),
        (4, "positive_signal"),
        (None, "unclear_signal"),
        (True, "unclear_signal"),
        ("4", "unclear_signal"),
        (5, "unclear_signal"),
    ],
)
def test_wildfb_label_mapping_and_linkage(label, group):
    assert signals.wildfb(label, linked=True)["group"] == group
    assert signals.wildfb(label, linked=False)["group"] == "unclear_signal"


@pytest.mark.parametrize(
    "sat,dsat,group",
    [
        (True, False, "positive_signal"),
        (False, True, "negative_signal"),
        (True, True, "mixed_signal"),
        (False, False, "unclear_signal"),
        (None, False, "unclear_signal"),
        (False, 0, "unclear_signal"),
    ],
)
def test_wildfeedback_boolean_semantics(sat, dsat, group):
    target = {"UtterranceId": 3, "TurnId": 2, "Role": "Agent"}
    following = {
        "UtterranceId": 4,
        "TurnId": 3,
        "Role": "User",
        "Preceeding": "YES",
        "Satisfaction": sat,
        "Disatisfaction": dsat,
    }
    assert signals.wildfeedback(target, following, same_segment=True)["group"] == group


@pytest.mark.parametrize("change", ["reset", "turn", "role", "new_topic", "missing", "segment"])
def test_wildfeedback_rejects_cross_target_feedback(change):
    target = {"UtterranceId": 3, "TurnId": 2, "Role": "Agent"}
    following = {
        "UtterranceId": 4,
        "TurnId": 3,
        "Role": "User",
        "Preceeding": "YES",
        "Satisfaction": True,
        "Disatisfaction": False,
    }
    if change == "reset":
        following["UtterranceId"] = 0
    if change == "turn":
        following["TurnId"] = 4
    if change == "role":
        following["Role"] = "Agent"
    if change == "new_topic":
        following["Preceeding"] = "NO"
    if change == "missing":
        following = None
    assert signals.wildfeedback(target, following, same_segment=change != "segment")["group"] == "unclear_signal"


def test_selective_source_read_never_decodes_unselected_values():
    raw = b'{"Role":"User","Content":"PRIVATE_BODY_\xff","nested":{"x":[1,{"y":"\\""}]},"Satisfaction":true}'
    assert signals.select_fields(raw, {"Role", "Satisfaction"}) == {"Role": "User", "Satisfaction": True}
    with pytest.raises((ValueError, UnicodeError)):
        signals.select_fields(raw, {"Content"})
    with pytest.raises(batch.Closed, match="duplicate_source_field"):
        signals.select_fields(b'{"Role":"User","Role":"Agent"}', {"Role"})


def test_array_framing_preserves_existing_mapping_and_skips_unselected_bodies():
    from whynote.explore_inputs import array_chunks, array_records

    raw = json.dumps([{"Content": 'a}\\"', "label": 1}, {"Content": "b", "label": 4}]).encode()
    assert [row for _, row, error in array_records(io.BytesIO(raw))] == json.loads(raw)
    assert [signals.select_fields(row, {"label"}) for _, row in array_chunks(io.BytesIO(raw))] == [
        {"label": 1},
        {"label": 4},
    ]


def test_bounded_lines_skip_oversized_record_without_losing_next_index(monkeypatch):
    monkeypatch.setattr(signals, "MAX_RECORD", 8)
    assert list(signals.bounded_lines(io.BytesIO(b"x" * 30 + b"\n{}\n"))) == [(0, None), (1, b"{}\n")]


def test_admission_is_rechecked_before_each_selected_row(tmp_path):
    path = tmp_path / "synthetic.jsonl"
    path.write_bytes(b'{"label":1}\n{"label":4}\n')
    calls = 0

    def check():
        nonlocal calls
        calls += 1
        if calls == 3:
            raise batch.Closed("synthetic_admission_revoked")

    with pytest.raises(batch.Closed, match="synthetic_admission_revoked"):
        signals.selected_source_rows(path, "wildfb", {0: {"label"}, 1: {"label"}}, check)
