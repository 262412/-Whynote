import asyncio
import copy
import hashlib
import json
import math

import httpx
import pytest

from whynote.domain import NotFoundError
from whynote.jev_provider import ENDPOINT, MODEL
from whynote.two_stage import FIELDS, MAX_REQUEST_BYTES, VERSION, Policy, StageError, classify, load_catalog

POLICY = Policy("synthetic-test-thresholds", 0.8, 0.5, 0.8, 0.5)
STATE = {
    "request": "SYNTHETIC: 请等价重构函数并保留接口。",
    "answer": "SYNTHETIC ANSWER MARKER: def changed(x, y): return x + y",
    "original_code": "SYNTHETIC: def original(x): return x",
    "reference": "SYNTHETIC REFERENCE MARKER: fictional checked material",
}


def choice_answer(question, selected, *, probability=1.0, confidence=1.0):
    others = (1 - probability) / (len(question["criteria"]) - 1)
    return {
        "type": "choice",
        "choice": selected,
        "confidence": confidence,
        "probabilities": {key: probability if key == selected else others for key in question["criteria"]},
    }


def response(body, *, task="code_rewrite", domain="software", decisions=None):
    decisions = decisions or {}
    return {
        "model": MODEL,
        "answers": {
            name: choice_answer(
                question, task if name == "task" else domain if name == "domain" else decisions.get(name, "no")
            )
            for name, question in body["questions"].items()
        },
        "usage": {"input_tokens": 100, "output_tokens": 20},
    }


@pytest.fixture
def invoke(tmp_path):
    keys = tmp_path / "synthetic-keys.json"
    keys.write_text('{"typesafe":"synthetic-key-only"}', encoding="utf-8")
    options = {
        "keys_file": keys,
        "policy": POLICY,
        "enabled": True,
        "outbound_approval_ref": "synthetic-approval",
        "budget_reservations": {"route": "synthetic-route-reservation", "reasons": "synthetic-reasons-reservation"},
        "admission_check": lambda: True,
    }

    def call(handler, *, state=STATE, state_factory=None, **overrides):
        return asyncio.run(
            classify(
                state_factory or (lambda: copy.deepcopy(state)),
                transport=httpx.MockTransport(handler),
                **(options | overrides),
            )
        )

    return call


def test_native_requests_are_serial_and_second_questions_follow_route(invoke):
    requests = []

    def handler(request):
        assert request.method == "POST" and str(request.url) == ENDPOINT
        assert request.headers["authorization"] == "Bearer synthetic-key-only"
        body = json.loads(request.content)
        assert set(body) == {"model", "state", "questions"} and body["model"] == MODEL
        requests.append(request)
        if len(requests) == 1:
            assert list(body["questions"]) == ["task", "domain"]
            assert "answer" not in body["state"]
            assert list(body["questions"]["task"]["criteria"])[:3] == ["code_generate", "code_rewrite", "code_debug"]
        else:
            assert list(body["questions"]) == [
                "general.instruction_not_followed",
                "general.incomplete",
                "general.irrelevant",
                "general.style",
                "general.factual_error",
                "general.outdated",
                "general.unnecessary_refusal",
                "general.self_contradiction",
                "code.interface_changed",
                "code.behavior_changed",
                "code.incomplete_rewrite",
                "code.scope_exceeded",
                "software.version_mismatch",
                "software.environment_mismatch",
            ]
            assert body["state"] == STATE
            assert all(
                list(question["criteria"]) == ["yes", "no", "unknown"] for question in body["questions"].values()
            )
        return httpx.Response(200, json=response(body, decisions={"code.interface_changed": "yes"}))

    result = invoke(handler)
    assert len(requests) == 2
    assert result["schema_version"] == VERSION and result["route_scope"] == "selected_task_and_domain"
    assert result["outcome"] == "suggested" and result["primary_reason"] is None
    assert result["suggestions"] == [
        {
            "reason_id": "code.interface_changed",
            "label": "改变了需保留的接口",
            "confirmation_prompt": "你点踩是否因为“改变了需保留的接口”？",
            "attribution_source": "model_inferred_unconfirmed",
            "user_confirmed": False,
        }
    ]
    assert result["quality_status"] == "NOT_EVALUATED"
    for request, stage in zip(requests, result["stages"], strict=True):
        assert stage["request_sha256"] == hashlib.sha256(request.content).hexdigest()
        assert stage["question_ids"] == list(json.loads(request.content)["questions"])
        assert stage["usage"] == {"input_tokens": 100, "output_tokens": 20}
    assert all(value not in json.dumps(result) for value in ["synthetic-key-only", *STATE.values()])


def test_changed_route_changes_reason_questions(invoke):
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        return httpx.Response(200, json=response(body, task="summarize", domain="language"))

    result = invoke(handler, state=STATE | {"source_text": "SYNTHETIC: a short source."})
    assert len(bodies) == 2
    second = bodies[1]["questions"]
    assert "summary.distorted_meaning" in second and "language.register_mismatch" in second
    assert "code.interface_changed" not in second and "software.version_mismatch" not in second
    assert result["outcome"] == "no_match"


def test_missing_evidence_is_not_asked_and_never_no(invoke):
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        return httpx.Response(200, json=response(body))

    result = invoke(handler, state={key: STATE[key] for key in ("request", "answer")})
    assert "code.interface_changed" not in bodies[1]["questions"]
    assert "general.factual_error" not in bodies[1]["questions"]
    rows = {row["reason_id"]: row for row in result["reasons"]}
    assert rows["code.interface_changed"]["decision"] == "not_asked"
    assert rows["code.interface_changed"]["status"] == "missing_evidence"
    assert rows["code.interface_changed"]["missing_evidence"] == ["original_code"]
    assert rows["code.interface_changed"]["probabilities"] is None
    assert rows["summary.distorted_meaning"]["status"] == "route_not_selected"
    assert result["outcome"] == "unknown" and result["suggestions"] == []


@pytest.mark.parametrize("unresolved", ["mixed", "other", "unknown", "low_probability", "low_confidence", "tie"])
def test_unresolved_route_keeps_common_and_known_axis_only(invoke, unresolved):
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        reply = response(body, task=unresolved if unresolved in {"mixed", "other", "unknown"} else "code_rewrite")
        if "task" in reply["answers"]:
            answer = reply["answers"]["task"]
            if unresolved == "low_probability":
                answer.update(choice_answer(body["questions"]["task"], "code_rewrite", probability=0.7))
            elif unresolved == "low_confidence":
                answer["confidence"] = 0.1
            elif unresolved == "tie":
                answer["probabilities"] = {
                    key: 0.5 if key in {"code_rewrite", "code_debug"} else 0 for key in answer["probabilities"]
                }
        return httpx.Response(200, json=reply)

    permissive = Policy("synthetic-tie-check", 0, 0, 0.8, 0.5) if unresolved == "tie" else POLICY
    result = invoke(handler, policy=permissive)
    assert "general.instruction_not_followed" in bodies[1]["questions"]
    assert "software.version_mismatch" in bodies[1]["questions"]
    assert not any(key.startswith("code.") for key in bodies[1]["questions"])
    assert result["route_scope"] == "partial" and result["outcome"] == "unknown"


@pytest.mark.parametrize(
    "selected,probability,confidence", [("yes", 0.4, 0.9), ("yes", 0.9, 0.1), ("no", 0.9, 0.1), ("unknown", 1, 1)]
)
def test_unknown_and_below_threshold_do_not_become_suggestions(invoke, selected, probability, confidence):
    def handler(request):
        body = json.loads(request.content)
        reply = response(body)
        if "general.instruction_not_followed" in body["questions"]:
            reply["answers"]["general.instruction_not_followed"] = choice_answer(
                body["questions"]["general.instruction_not_followed"],
                selected,
                probability=probability,
                confidence=confidence,
            )
        return httpx.Response(200, json=reply)

    result = invoke(handler)
    row = result["reasons"][0]
    assert row["raw_choice"] == selected and row["decision"] == "unknown"
    assert result["outcome"] == "unknown" and not result["suggestions"]


def test_multiple_yes_keep_catalog_order_without_cross_question_ranking(invoke):
    def handler(request):
        body = json.loads(request.content)
        reply = response(body, decisions={"general.instruction_not_followed": "yes", "code.interface_changed": "yes"})
        if "general.instruction_not_followed" in body["questions"]:
            reply["answers"]["general.instruction_not_followed"] = choice_answer(
                body["questions"]["general.instruction_not_followed"], "yes", probability=0.8, confidence=0.5
            )
        return httpx.Response(200, json=reply)

    result = invoke(handler)
    assert [row["reason_id"] for row in result["suggestions"]] == [
        "general.instruction_not_followed",
        "code.interface_changed",
    ]
    assert result["primary_reason"] is None and result["user_confirmed"] is False


@pytest.mark.parametrize(
    "overrides",
    [
        {"enabled": False},
        {"enabled": 1},
        {"outbound_approval_ref": None},
        {"outbound_approval_ref": " "},
        {"admission_check": None},
        {"admission_check": lambda: False},
        {"admission_check": lambda: 1},
        {"budget_reservations": None},
        {"budget_reservations": {"route": "one"}},
        {"budget_reservations": {"route": "same", "reasons": "same"}},
        {"budget_reservations": {"route": "one", "reasons": " "}},
    ],
)
def test_denied_never_reads_keys_context_or_network(invoke, monkeypatch, overrides):
    def forbidden(*args):
        pytest.fail("Denied entry read keys, context or network")

    monkeypatch.setattr("whynote.two_stage.load_provider_key", forbidden)
    with pytest.raises(NotFoundError):
        invoke(forbidden, state_factory=forbidden, **overrides)


def test_default_disabled():
    with pytest.raises(NotFoundError):
        asyncio.run(classify(lambda: pytest.fail("context"), keys_file="unused", policy=POLICY))


@pytest.mark.parametrize("revoke_on,expected_calls", [(2, 0), (3, 0), (4, 1), (5, 1), (6, 2)])
def test_revocation_between_stages_stops_or_discards_results(invoke, revoke_on, expected_calls):
    checks, calls = [], []

    def admission():
        checks.append(True)
        return len(checks) < revoke_on

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(200, json=response(body, decisions={"code.interface_changed": "yes"}))

    with pytest.raises((NotFoundError, StageError)) as exc:
        invoke(handler, admission_check=admission)
    assert len(calls) == expected_calls
    if expected_calls:
        assert len(exc.value.stages) == expected_calls
        assert all(stage["status"] == "completed" for stage in exc.value.stages)


@pytest.mark.parametrize(
    "state",
    [
        STATE | {"feedback": "must never go to the model"},
        STATE | {"gold": "must not leak"},
        STATE | {"reference": " "},
        STATE | {"answer": None},
        {"request": "synthetic"},
        "synthetic scalar",
        STATE | {"answer": "x" * MAX_REQUEST_BYTES},
    ],
)
def test_invalid_projection_rejected_before_first_request(invoke, state):
    with pytest.raises(ValueError):
        invoke(lambda _: pytest.fail("network"), state=state)


@pytest.mark.parametrize("value", [True, -1, 1.1, math.nan, math.inf, "0.8"])
def test_invalid_threshold_rejected_before_context_or_key(invoke, monkeypatch, value):
    def forbidden(*args):
        pytest.fail("Invalid policy caused an external read")

    monkeypatch.setattr("whynote.two_stage.load_provider_key", forbidden)
    with pytest.raises(ValueError, match="threshold"):
        invoke(forbidden, state_factory=forbidden, policy=Policy("invalid", value, 0.5, 0.8, 0.5))


@pytest.mark.parametrize("bad_stage", [1, 2])
@pytest.mark.parametrize(
    "mutation",
    ["model", "missing", "extra", "null", "probability", "nan", "sum", "inconsistent", "confidence", "usage", "scalar"],
)
def test_bad_responses_stop_without_a_later_request_or_suggestion(invoke, bad_stage, mutation):
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        reply = response(body)
        if len(calls) == bad_stage:
            name = next(iter(reply["answers"]))
            answer = reply["answers"][name]
            if mutation == "model":
                reply["model"] = "jev-latest"
            elif mutation == "missing":
                del reply["answers"][name]
            elif mutation == "extra":
                reply["answers"]["extra"] = answer
            elif mutation == "null":
                reply["answers"][name] = None
            elif mutation in {"probability", "nan", "sum"}:
                answer["probabilities"][answer["choice"]] = {"probability": True, "nan": math.nan, "sum": 0.8}[mutation]
            elif mutation == "inconsistent":
                answer["choice"] = next(key for key in answer["probabilities"] if key != answer["choice"])
            elif mutation == "confidence":
                answer["confidence"] = -0.1
            elif mutation == "usage":
                reply["usage"]["input_tokens"] = True
            else:
                reply = []
        return httpx.Response(200, content=json.dumps(reply).encode())

    with pytest.raises(StageError, match="reconcile") as exc:
        invoke(handler)
    assert len(calls) == bad_stage
    assert exc.value.stages[-1]["status"] == "failed_or_unknown"
    if bad_stage == 2:
        assert exc.value.stages[0]["usage"] == {"input_tokens": 100, "output_tokens": 20}


@pytest.mark.parametrize("status", [307, 401, 429, 529])
def test_second_http_error_has_no_retry_and_retains_first_usage(invoke, status):
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 1:
            return httpx.Response(200, json=response(body))
        return httpx.Response(status, text="SECRET PROVIDER ECHO", headers={"Location": "https://other.invalid/"})

    with pytest.raises(StageError) as exc:
        invoke(handler)
    assert len(calls) == 2
    assert "SECRET" not in str(exc.value)
    assert exc.value.stages[0]["usage"]["input_tokens"] == 100
    assert exc.value.stages[1]["usage"] is None


def test_duplicate_response_key_is_rejected(invoke):
    with pytest.raises(StageError):
        invoke(lambda _: httpx.Response(200, content=b'{"answers":{},"answers":{}}'))


def test_second_request_too_large_retains_first_usage_and_marks_not_sent(invoke):
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        assert len(calls) == 1, "Oversized second request must not be sent"
        return httpx.Response(200, json=response(body))

    with pytest.raises(StageError, match="stage_request_too_large") as exc:
        invoke(handler, state=STATE | {"answer": "synthetic " + "x" * 26000})
    assert len(calls) == 1
    assert exc.value.stages[0]["usage"] == {"input_tokens": 100, "output_tokens": 20}
    assert exc.value.stages[1]["status"] == "not_sent" and exc.value.stages[1]["usage"] is None


def test_timeout_in_second_stage_retains_usage_without_retry(invoke):
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 2:
            raise httpx.ReadTimeout("SECRET ECHO")
        return httpx.Response(200, json=response(body))

    with pytest.raises(StageError, match="reconcile") as exc:
        invoke(handler)
    assert len(calls) == 2
    assert exc.value.stages[0]["status"] == "completed"
    assert exc.value.stages[1]["status"] == "failed_or_unknown"
    assert "SECRET" not in str(exc.value)


def test_general_domain_and_mixed_task_never_expand_all_specialists(invoke):
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        return httpx.Response(200, json=response(body, task="mixed", domain="general"))

    result = invoke(handler)
    assert len(bodies[1]["questions"]) == 8
    assert all(name.startswith("general.") for name in bodies[1]["questions"])
    assert result["route_scope"] == "partial" and result["outcome"] == "unknown"


def test_response_extras_are_stripped_and_target_answer_is_not_used_for_routing(invoke):
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        reply = response(body)
        reply["trace"] = "SECRET ECHO"
        for answer in reply["answers"].values():
            answer["explanation"] = "SECRET ECHO"
        return httpx.Response(200, json=reply)

    result = invoke(handler, state=STATE | {"tool_trace": "SYNTHETIC TOOL TRACE"})
    assert "answer" not in bodies[0]["state"] and "tool_trace" not in bodies[0]["state"]
    assert bodies[1]["state"]["tool_trace"] == "SYNTHETIC TOOL TRACE"
    assert "SECRET" not in json.dumps(result)


def test_catalog_has_specific_criteria_and_all_routes_have_reasons():
    catalog = load_catalog()
    ids = [reason["id"] for reason in catalog["reasons"]]
    assert len(ids) == len(set(ids)) == 86
    for axis, excluded in (
        ("tasks", {"mixed", "other", "unknown"}),
        ("domains", {"general", "mixed", "other", "unknown"}),
    ):
        for category in set(catalog[axis]) - excluded:
            assert sum(category in reason[axis] for reason in catalog["reasons"]) >= 2
    for reason in catalog["reasons"]:
        assert reason["label"].strip() and reason["criteria"].strip()
        assert set(reason["tasks"]) <= set(catalog["tasks"])
        assert set(reason["domains"]) <= set(catalog["domains"])
        assert set(reason["requires"]) <= set(FIELDS) - {"request", "answer"}


def test_catalog_and_legacy_original_contract_are_separate():
    from whynote.task_reasons import load_reasons
    from whynote.window_diagnostics import questions

    assert len(load_reasons()) == 11
    assert list(questions("original")) == [
        "route",
        "code.interface_changed",
        "general.instruction_not_followed",
        "general.unnecessary_refusal",
    ]
