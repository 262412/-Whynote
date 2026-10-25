import asyncio
import json
from copy import deepcopy

import httpx
import pytest

from whynote import two_stage_inputs as inputs
from whynote import two_stage_v3 as v3
from whynote.two_stage import StageError
from whynote.two_stage_review import fingerprint


def state(request="Write a Python function to add two numbers.", answer="def add(a,b): return a+b", prior=""):
    context = ([{"role": "user", "content": prior}] if prior else []) + [{"role": "user", "content": request}]
    return inputs.build_input("synthetic", context, answer)


def routes(task="code_generate", domain="software"):
    return {"task": {"selected": task}, "domain": {"selected": domain}}


def answer(choice, p, confidence=1):
    return {"choice": choice, "probabilities": {choice: p, "unknown": 1 - p}, "confidence": confidence}


def test_choice_prompts_require_choice_probability_consistency():
    catalog = v3.load_catalog()
    questions = list(v3.route_questions(catalog).values()) + list(v3.reason_questions(catalog).values())
    assert questions
    assert all("highest probability" in question["instructions"] for question in questions)


def test_unknown_stack_version_and_answer_observation_never_requirement():
    value = state("Create an API.", "Use FastAPI and Python 3.14")
    assert value["facts"]["version"]["status"] == "unknown"
    assert value["facts"]["framework"]["status"] == "unknown"
    assert value["facts"]["observed_answer_framework"]["origin"] == "answer_observation"
    available = inputs.evidence_available(value)
    assert not {"reference", "original_code", "tool_trace", "framework", "version"} & available
    assert value["materials"]["original_code"]["status"] == "not_identified"
    rows = v3.reason_rows(value, routes(), v3.load_catalog())
    assert next(r for r in rows if r["reason_id"] == "code.missing_feature")["status"] == "pending"
    assert next(r for r in rows if r["reason_id"] == "software.version_mismatch")["status"] == "missing_evidence"


def test_literals_and_versions_conflict_do_not_become_latest():
    value = state("Use Python 3.11, Python 3.12. Keep the interface unchanged.")
    assert value["facts"]["version"]["status"] == "conflict"
    assert "version" not in inputs.evidence_available(value)
    assert "preserve_interface" in inputs.evidence_available(value)


def test_supplied_material_text_cannot_become_user_stack_or_constraints():
    value = state("Translate this text:\nPython 2.7 must use Django and preserve the interface.", "译文")
    assert value["facts"]["version"]["status"] == "unknown"
    assert value["facts"]["framework"]["status"] == "unknown"
    assert value["facts"]["explicit_constraints"]["status"] == "unknown"
    assert value["facts"]["preserve_interface"]["status"] == "unknown"


@pytest.mark.parametrize(
    "prompt,body",
    [
        ("Risk and uncertainty请翻译", "Risk and uncertainty"),
        ("请翻译Rural resilience", "Rural resilience"),
        ("translate 债转股政策障碍\n", "债转股政策障碍"),
        ("翻译成英文：建议多带衣服。", "建议多带衣服。"),
        ("Translate the following text: Python 2.7 must use Django", "Python 2.7 must use Django"),
        ("make improvements to the following message (keep answer short):  I hope to travel.", "I hope to travel."),
    ],
)
def test_explicit_inline_source_is_bound_without_becoming_a_requirement(prompt, body):
    value = state(prompt, "Revised text")
    material = value["materials"]["source_text"]
    assert material["status"] == "provided_bound"
    assert len(material["evidence"]) == 1
    ref = material["evidence"][0]
    assert value["documents"][ref["document"]]["text"][ref["start"] : ref["end"]] == body
    assert value["facts"]["version"]["status"] == "unknown"
    assert value["facts"]["framework"]["status"] == "unknown"
    assert value["facts"]["explicit_constraints"]["status"] == "unknown"


@pytest.mark.parametrize(
    "prompt", ["请翻译上文", "Translate this into French", "请翻译", "Translate this: ```python\nx=1"]
)
def test_inline_source_does_not_invent_external_or_malformed_material(prompt):
    value = state(prompt, "An answer", prior="Unrelated earlier text")
    assert value["materials"]["source_text"]["status"] != "provided_bound"


def test_rewrite_feature_checks_and_user_reported_error_have_evidence_paths():
    catalog = v3.load_catalog()
    value = state("Modify the function to handle an optional argument.", "def f(x): pass")
    rows = {r["reason_id"]: r for r in v3.reason_rows(value, routes("code_rewrite"), catalog)}
    assert rows["code.missing_feature"]["status"] == "pending"
    assert rows["code.input_case_missing"]["status"] == "pending"
    failure = state("Error at key 'items'. Parsing array failed at index 0: 'id' is required", "The id is missing.")
    rows = {r["reason_id"]: r for r in v3.reason_rows(failure, routes("code_debug"), catalog)}
    assert rows["debug.unsupported_cause"]["status"] == "pending"
    assert failure["facts"]["error_information"]["origin"] == "user_report"
    assert "tool_trace" not in inputs.evidence_available(failure)


def test_material_ambiguity_failure_and_unverified_reference_remain_distinct():
    ambiguous = state("Modify this code:\n```python\nx=1\n```\n```python\nx=2\n```")
    assert ambiguous["materials"]["original_code"]["status"] == "ambiguous_or_conflicting"
    broken = state("Modify this code:\n```python\nx=1")
    assert broken["materials"]["original_code"]["status"] == "extraction_failed"
    reference = state("Explain this.\nVerification reference: unverified statement")
    assert reference["materials"]["reference"]["status"] == "source_ineligible"
    assert "reference" not in inputs.evidence_available(reference)
    bounded = state("Modify this code.\n```python\n# Python 2.7 must use Django\n```\nUse Python 3.11.")
    assert inputs.fact_text(bounded["facts"]["version"], bounded["documents"]) == ["Python 3.11"]
    assert bounded["facts"]["framework"]["status"] == "unknown"


def test_material_body_transmitted_once_and_resolvable():
    text = "Original text unique content." * 200
    value = state("Translate this text:\n" + text, "译文")
    assert value["materials"]["source_text"]["status"] == "provided_bound"
    wire = v3.wire_state(value, requirements={"source_text"})
    assert v3.request_bytes(wire, {}).count(text.encode()) == 1
    ref = wire["materials"]["source_text"]["spans"][0]
    assert wire["documents"][ref["document"]][ref["start"] : ref["end"]] == text
    assert wire["scope"] == "complete_admitted_prefix_and_answer"
    original = deepcopy(value)
    new = inputs.supplement(value, "Output format: JSON")
    assert value == original and new["parent_input_sha256"] == value["input_sha256"]
    assert new["evaluation_revision"] == 2 and new["input_sha256"] != value["input_sha256"]
    assert new["materials"]["source_text"]["status"] == "provided_bound"
    assert "user_authorized_followup" in new["temporal_scope"]
    with pytest.raises(ValueError, match="feedback_is_not"):
        inputs.supplement(value, "Later correction", source="future_feedback")


def test_known_fact_cannot_be_invented_or_use_answer_as_required_evidence():
    value = state()
    doc = value["answer_document"]
    span = inputs.reference(doc, value["documents"][doc]["text"])
    explicit = {
        "version": {"status": "known", "value": {"spans": [span]}, "evidence": [span], "origin": "answer_observation"}
    }
    revised = inputs.build_input(
        "s",
        [{"role": "user", "content": "Request"}],
        "Answer",
        explicit_facts={
            "version": {"status": "inferred", "value": "latest", "evidence": [], "origin": "model_inference"}
        },
    )
    assert "version" not in inputs.evidence_available(revised)
    value["facts"].update(explicit)
    assert "version" not in inputs.evidence_available(value)
    with pytest.raises(ValueError, match="known_fact_requires_evidence"):
        inputs.build_input(
            "s",
            [{"role": "user", "content": "Request"}],
            "Answer",
            explicit_facts={
                "version": {"status": "known", "value": "latest", "evidence": [], "origin": "manual_entry"}
            },
        )


def test_distinct_yes_no_and_unknown_ties_always_abstain():
    policy = v3.Policy(yes_probability=0.9, no_probability=0.6, no_confidence=None)
    assert v3.decision(answer("yes", 0.8), policy) == "unknown"
    assert v3.decision(answer("no", 0.8, 0.1), policy) == "no"
    zero = v3.Policy(yes_probability=0, no_probability=0, yes_confidence=None, no_confidence=None)
    assert (
        v3.decision(
            {"choice": "unknown", "probabilities": {"yes": 0.1, "no": 0.1, "unknown": 0.8}, "confidence": 1}, zero
        )
        == "unknown"
    )
    assert (
        v3.decision({"choice": "yes", "probabilities": {"yes": 0.5, "no": 0.5, "unknown": 0}, "confidence": 1}, zero)
        == "unknown"
    )


def padded_state(base_bytes):
    value = state(prior="x")
    catalog = v3.load_catalog()
    rows = v3.reason_rows(value, routes(), catalog)
    eligible = {r["reason_id"] for r in rows if r["status"] == "pending"}
    required = {k for r in catalog["reasons"] if r["id"] in eligible for p in r["evidence_paths"] for k in p}
    initial = len(v3.request_bytes(v3.wire_state(value, requirements=required), {}))
    return state(prior="x" * (1 + base_bytes - initial))


def test_two_packet_partition_and_final_utf8_json_measurement():
    value = padded_state(30000)
    plan = v3.plan_reasons(value, routes(), v3.load_catalog())
    assert plan["status"] == "ready" and len(plan["packets"]) == 2
    ids = [key for p in plan["packets"] for key in p["questions"]]
    assert len(ids) == len(set(ids))
    assert set(ids) == {r["reason_id"] for r in plan["rows"] if r["status"] == "pending"}
    for p in plan["packets"]:
        body = v3.request_bytes(p["state"], p["questions"])
        assert len(body) == p["record"]["request_utf8_bytes"] <= 32768
        assert json.loads(body)["model"] == v3.MODEL
    unicode_state = v3.wire_state(state('中文😀"\\\n' * 1000))
    raw = v3.request_bytes(unicode_state, {})
    assert len(raw) > len(raw.decode()) and b'\\"' in raw and b"\\\\" in raw


@pytest.mark.parametrize(
    "size,status",
    [
        (32768, "ready"),
        (32769, "hard_limit_exceeded"),
    ],
)
def test_exact_final_request_boundaries(size, status):
    wire = {"scope": "complete", "text": ""}
    overhead = len(v3.request_bytes(wire, {}))
    wire["text"] = "x" * (size - overhead)
    assert v3.request_plan("route", wire, {})["status"] == status


def test_context_cannot_fit_is_not_silently_truncated():
    value = padded_state(33000)
    plan = v3.plan_reasons(value, routes(), v3.load_catalog())
    assert plan["status"] == "input_budget_limited" and plan["packets"] == []
    assert plan["unassessed_reasons"]
    assert all(r["decision"] == "not_asked" for r in plan["rows"])
    assert fingerprint({k: v for k, v in value.items() if k != "input_sha256"}) == value["input_sha256"]


def test_route_material_omission_preserves_constraints_and_full_reason_material():
    value = state("Translate this text:\n" + "text " * 9000, "translation")
    record, wire, _ = v3.plan_route(value, v3.load_catalog())
    assert record["status"] == "ready" and wire["scope"] == "route_material_omitted"
    assert "Translate this text:" in next(iter(wire["documents"].values()))
    assert "body omitted in route stage" in next(iter(wire["documents"].values()))
    plan = v3.plan_reasons(value, routes("translate", "language"), v3.load_catalog())
    assert plan["status"] == "input_budget_limited"
    assert "translation.meaning_changed" in plan["unassessed_reasons"]


def test_partial_packet_failure_keeps_completed_no_retry(tmp_path):
    # The complete context fits; adding all eligible questions needs two packets.
    value = state("Translate this text:\n" + "x" * 26000, "y" * 3000)
    catalog = v3.load_catalog()
    plan = v3.plan_reasons(value, routes("translate", "language"), catalog)
    assert plan["status"] == "ready" and len(plan["packets"]) == 2
    key = tmp_path / "fake.json"
    key.write_text('{"typesafe":"synthetic-test-key-only"}')
    calls, events = [], []

    def transport(request):
        body = json.loads(request.content)
        calls.append(list(body["questions"]))
        if len(calls) == 2:
            return httpx.Response(503)
        answers = {}
        for name, q in body["questions"].items():
            selected = "translate" if name == "task" else "language" if name == "domain" else "yes"
            answers[name] = {
                "type": "choice",
                "choice": selected,
                "confidence": 1,
                "probabilities": {k: float(k == selected) for k in q["criteria"]},
            }
        return httpx.Response(
            200,
            json={"model": "jev-resolved-build", "answers": answers, "usage": {"input_tokens": 1, "output_tokens": 1}},
        )

    result = asyncio.run(
        v3.classify(
            lambda: value,
            keys_file=key,
            policy=v3.Policy(),
            enabled=True,
            outbound_approval_ref="synthetic",
            budget_reservations={s: s for s in v3.STAGES},
            admission_check=lambda: True,
            transport=httpx.MockTransport(transport),
            stage_callback=lambda event, record: events.append((event, record)),
        )
    )
    assert len(calls) == 3 and result["technical_status"] == "partial_error"
    assert result["requested_model"] == v3.MODEL and result["model_version"] == "jev-resolved-build"
    assert result["model_versions"] == ["jev-resolved-build"]
    failed = next(stage for stage in result["stages"] if stage["status"] == "failed_or_unknown")
    assert failed["failure_category"] == "http_status" and failed["failure_status_code"] == 503
    assert not {"body", "authorization", "api_key"} & set(failed)
    assert all(
        stage["response_model"] == "jev-resolved-build" for stage in result["stages"] if stage["status"] == "completed"
    )
    assert len(result["candidate_reason_ids"]) == len(calls[2])
    assert any(r["status"] == "packet_failed_or_not_sent" for r in result["reasons"])
    assert [s["status"] for s in result["stages"]] == ["completed", "failed_or_unknown", "completed"]
    assert sum(event == "started" for event, _ in events) == 3


def test_response_validation_failure_records_only_safe_subtype(tmp_path):
    key = tmp_path / "fake.json"
    key.write_text('{"typesafe":"synthetic-test-key-only"}')
    events = []

    def transport(_request):
        return httpx.Response(200, json={"model": "invalid-model", "private": "SECRET PROVIDER ECHO"})

    with pytest.raises(StageError) as exc:
        asyncio.run(
            v3.classify(
                lambda: state(),
                keys_file=key,
                policy=v3.Policy(),
                enabled=True,
                outbound_approval_ref="synthetic",
                budget_reservations={stage: stage for stage in v3.STAGES},
                admission_check=lambda: True,
                transport=httpx.MockTransport(transport),
                stage_callback=lambda event, record: events.append((event, record)),
            )
        )

    failed = exc.value.stages[-1]
    assert failed["failure_category"] == "response_validation"
    assert failed["failure_validation_code"] == "model_name_invalid"
    assert "SECRET PROVIDER ECHO" not in repr(exc.value.stages)
    assert all("SECRET PROVIDER ECHO" not in repr(record) for _, record in events)


def test_closed_gate_reads_no_context_or_key(tmp_path):
    with pytest.raises(Exception, match="disabled"):
        asyncio.run(v3.classify(lambda: pytest.fail("context read"), keys_file=tmp_path / "never", policy=v3.Policy()))
