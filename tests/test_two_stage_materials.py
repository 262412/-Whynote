import hashlib
import json

import httpx
import pytest
from test_two_stage import invoke as invoke
from test_two_stage import response

from whynote import two_stage as core
from whynote import two_stage_materials as material


def extract(text, *, prior=()):
    context = [*prior, {"role": "user", "content": text}]
    values, metadata = material.extract_materials("synthetic-target", context)
    for key, value in values.items():
        locator = metadata[key]["locator"]
        source = context[locator["message_index"]]["content"]
        assert source[locator["start"] : locator["end"]] == value
        assert hashlib.sha256(source.encode()).hexdigest() == locator["source_sha256"]
        assert locator["offset_unit"] == "unicode_codepoint" and locator["interval"] == "[start,end)"
        assert metadata[key]["target_id"] == "synthetic-target"
        assert metadata[key]["rule_version"] == material.VERSION
    return values, metadata


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Translate this into English:\n原始文本。", "原始文本。"),
        ("请摘要。\n原文：\r\n甲🙂e\u0301\r\n乙", "甲🙂e\u0301\r\n乙"),
        ('Summarize the following text:\n```text\ntext with ``` inline\n"``"\n```', 'text with ``` inline\n"``"\n'),
        ("Translate this source text:\n````text\n```\n原文\n````", "```\n原文\n"),
    ],
)
def test_explicit_source_boundaries_and_unicode_are_exact(text, expected):
    values, meta = extract(text)
    assert values["source_text"] == expected
    assert meta["source_text"]["status"] == "extracted"
    assert expected not in json.dumps(meta, ensure_ascii=False)


def test_explicit_original_code_and_multiple_blocks():
    values, meta = extract("Please refactor this code:\n```python\ndef old(x): return x\n```")
    assert values["original_code"] == "def old(x): return x\n"
    assert meta["source_text"]["status"] == "not_found"
    values, meta = extract("Fix these code blocks:\n```py\nx=1\n```\n```py\ny=2\n```")
    assert "original_code" not in values and meta["original_code"]["status"] == "ambiguous"
    values, _ = extract("Refactor code.\nOriginal code:\n```py\nx=1\n```\nExpected output:\n```text\n1\n```")
    assert values["original_code"] == "x=1\n"


def test_fix_target_is_explicit_from_declared_code_fence():
    values, _ = extract("Please fix this:\n```python\ndef f(: return 1\n```")
    assert values["original_code"] == "def f(: return 1\n"


@pytest.mark.parametrize("label", ["Example code", "Expected output", "Feedback", "Gold"])
def test_examples_feedback_and_gold_do_not_become_original_code(label):
    values, meta = extract(f"Fix my function. {label}:\n```python\nx=1\n```")
    assert "original_code" not in values
    assert meta["original_code"]["status"] == "ambiguous"


def test_table_input_and_explicit_data_block():
    table = "| x | y |\r\n| --- | --- |\r\n| 1 | 2 |\r\n"
    values, _ = extract("Analyze this input table:\r\n" + table)
    assert values["table"] == table
    values, _ = extract("Calculate totals.\nInput data:\n```csv\nx,y\n1,2\n```")
    assert values["table"] == "x,y\n1,2\n"
    values, meta = extract("Create a table about cities.")
    assert "table" not in values and meta["table"]["status"] == "not_found"


def test_independent_reference_requires_explicit_verification_label():
    values, _ = extract("Check the claim.\nVerification reference:\n```text\nfixed documented basis\n```")
    assert values["reference"] == "fixed documented basis\n"
    values, meta = extract("I remember that the claim is true. Tests passed. 我执行过。")
    assert "reference" not in values and "tool_trace" not in values
    assert meta["tool_trace"]["reason_code"] == "no_authenticated_tool_record"


def test_multi_turn_only_uniquely_linked_user_material():
    previous = [
        {"role": "user", "content": "Source text:\n```\nfirst source\n```"},
        {"role": "assistant", "content": "Source text:\n```\nmodel invention\n```"},
    ]
    values, meta = extract("Translate the above source text.", prior=previous)
    assert values["source_text"] == "first source\n" and meta["source_text"]["locator"]["message_index"] == 0
    values, _ = extract("Write something new.", prior=previous)
    assert not values
    values, meta = extract(
        "Translate the above source text.",
        prior=[*previous, {"role": "user", "content": "Source text:\n```\nsecond\n```"}],
    )
    assert "source_text" not in values and meta["source_text"]["status"] == "ambiguous"
    values, _ = extract(
        "Check the above claim.",
        prior=[{"role": "assistant", "content": "Verification reference:\n```\nmodel invention\n```"}],
    )
    assert "reference" not in values


def test_failure_does_not_truncate_or_rewrite_and_absence_is_distinct():
    text = "Summarize this:\n```\nunclosed source"
    frozen = text
    values, meta = extract(text)
    assert text == frozen and not values and meta["source_text"]["status"] == "parse_failed"
    values, meta = extract("Summarize the life of Newton.")
    assert meta["source_text"]["status"] == "not_found"
    assert meta["reference"]["status"] == "not_found"


def test_duplicated_material_can_exceed_full_wire_limit():
    text = "Translate this source text:\n```\n" + "x" * 16000 + "\n```"
    values, _ = extract(text)
    state = {"request": text, "answer": "synthetic answer"}
    questions = core.route_questions(core.load_catalog())
    assert len(core.request_bytes({"request": text}, questions)) <= core.MAX_REQUEST_BYTES
    materialized = state | values
    assert (
        len(core.request_bytes({k: v for k, v in materialized.items() if k != "answer"}, questions))
        > core.MAX_REQUEST_BYTES
    )
    assert materialized["request"] == text and materialized["source_text"] == "x" * 16000 + "\n"


def test_unclosed_code_keeps_independent_closed_input_data():
    values, meta = extract(
        "Fix this code using input data.\nInput data:\n```csv\nx,y\n1,2\n```\nOriginal code:\n```python\nx="
    )
    assert values["table"] == "x,y\n1,2\n"
    assert meta["original_code"]["status"] == "parse_failed" and "original_code" not in values


def test_reference_to_previous_answer_does_not_link_unrelated_user_source():
    values, _ = extract(
        "Summarize the previous answer.", prior=[{"role": "user", "content": "Source text:\n```\nunrelated\n```"}]
    )
    assert "source_text" not in values


def test_explanation_code_is_not_automatically_a_modification_object():
    values, _ = extract("Explain this function.\nOriginal code:\n```py\ndef f(): return 1\n```")
    assert "original_code" not in values


def test_new_material_can_block_first_wire_request_without_sending(invoke):
    text = "Translate this source text:\n```\n" + "x" * 16000 + "\n```"
    values, _ = extract(text)
    state = {"request": text, "answer": "synthetic", **values}
    assert len(core._encode(state)) <= core.MAX_REQUEST_BYTES
    with pytest.raises(core.StageError, match="stage_request_too_large") as error:
        invoke(lambda _: pytest.fail("oversized wire must not be sent"), state=state)
    assert [stage["status"] for stage in error.value.stages] == ["not_sent"]


def test_second_wire_request_is_rechecked_after_material_and_actual_route(invoke):
    text = "Summarize this source text:\n```\n" + "x" * 7000 + "\n```"
    values, _ = extract(text)
    state = {"request": text, "answer": "y" * 15000, **values}
    sent = []

    def handler(request):
        sent.append(request)
        assert len(sent) == 1
        return httpx.Response(200, json=response(json.loads(request.content), task="summarize", domain="language"))

    with pytest.raises(core.StageError, match="stage_request_too_large") as error:
        invoke(handler, state=state)
    assert [stage["status"] for stage in error.value.stages] == ["completed", "not_sent"]
    assert len(sent) == 1 and error.value.stages[1]["request_utf8_bytes"] > core.MAX_REQUEST_BYTES
