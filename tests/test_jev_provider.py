import asyncio
import copy
import json

import httpx
import pytest

from whynote.domain import REASON_CODES, NotFoundError
from whynote.jev_provider import ENDPOINT, MODEL, evaluate_reason


@pytest.fixture
def call(tmp_path):
    keys = tmp_path / "keys.json"
    keys.write_text(json.dumps({"typesafe": "synthetic-jev-key", "deepseek": "unused"}), encoding="utf-8")
    questions = {
        "primary_reason": {
            "type": "choice",
            "instructions": "Synthetic classification only",
            "criteria": {code: code for code in REASON_CODES},
        }
    }
    options = dict(
        keys_file=keys,
        enabled=True,
        outbound_approval_ref="synthetic-review",
        budget_reservation_ref="synthetic-reservation",
    )

    def invoke(handler, factory=lambda: "Synthetic state", question=questions, **kwargs):
        return asyncio.run(
            evaluate_reason(factory, question, transport=httpx.MockTransport(handler), **{**options, **kwargs})
        )

    return invoke


def response():
    return {
        "model": MODEL,
        "answers": {
            "primary_reason": {
                "type": "choice",
                "choice": "factual_error",
                "confidence": 0.8,
                "probabilities": {code: int(code == "factual_error") for code in REASON_CODES},
            }
        },
        "usage": {"input_tokens": 50, "output_tokens": 20},
    }


def test_native_endpoint_and_normalized_model_result(call):
    calls = []

    def handler(request):
        calls.append(request)
        assert str(request.url) == ENDPOINT and request.method == "POST"
        assert request.headers["authorization"] == "Bearer synthetic-jev-key"
        body = json.loads(request.content)
        assert set(body) == {"state", "model", "questions"} and body["model"] == MODEL
        assert "messages" not in body
        return httpx.Response(200, json=response())

    result = call(handler)
    assert len(calls) == 1
    assert result["provider"] == "typesafe" and result["model_version"] == MODEL
    assert result["primary_reason"] == "factual_error" and "attribution_status" not in result


@pytest.mark.parametrize(
    "options", [{"enabled": False}, {"outbound_approval_ref": None}, {"budget_reservation_ref": ""}]
)
def test_denied_never_reads_context_key_or_network(call, options):
    def forbidden(*_):
        pytest.fail("Denied call must not read context or use network")

    with pytest.raises(NotFoundError):
        call(forbidden, factory=forbidden, keys_file="not-even-absolute", **options)


def test_default_is_disabled():
    with pytest.raises(NotFoundError):
        asyncio.run(evaluate_reason(lambda: pytest.fail("context read"), {}, keys_file="unused"))


@pytest.mark.parametrize("status", [301, 307, 401, 429, 503, 529])
def test_errors_are_single_attempt_and_never_follow_redirects(call, status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status, text="SECRET state and credential", headers={"Location": "https://other.invalid/"}
        )

    with pytest.raises(ValueError) as exc:
        call(handler)
    assert len(calls) == 1 and "SECRET" not in str(exc.value)
    assert str(status) in str(exc.value)


@pytest.mark.parametrize("error", [httpx.ReadTimeout, httpx.ConnectError])
def test_transport_errors_do_not_expose_provider_content(call, error):
    def handler(request):
        raise error("SECRET")

    with pytest.raises(ValueError, match="reconcile") as exc:
        call(handler)
    assert "SECRET" not in str(exc.value)


@pytest.mark.parametrize("mutation", ["version", "probability", "usage", "missing", "extra", "scalar"])
def test_invalid_response_or_drift_is_refused(call, mutation):
    result = copy.deepcopy(response())
    if mutation == "version":
        result["model"] = "jev-latest"
    elif mutation == "probability":
        result["answers"]["primary_reason"]["probabilities"]["factual_error"] = 2
    elif mutation == "usage":
        result["usage"]["input_tokens"] = True
    elif mutation == "missing":
        result["answers"] = {}
    elif mutation == "extra":
        result["answers"]["extra"] = {"type": "noul", "noul": 0.8}
    else:
        result = []
    with pytest.raises(ValueError, match="pinned contract"):
        call(lambda _: httpx.Response(200, json=result))


def test_malformed_json_and_oversized_response(call):
    for body in (b"SECRET", b"x" * 1_048_577):
        with pytest.raises(ValueError) as exc:
            call(lambda _, body=body: httpx.Response(200, content=body))
        assert "SECRET" not in str(exc.value)


def test_oversized_input_is_rejected_without_network(call):
    with pytest.raises(ValueError, match="limit"):
        call(lambda _: pytest.fail("network called"), factory=lambda: "x" * 8193)


def test_empty_key_rejected_before_context(call, tmp_path):
    keys = tmp_path / "empty.json"
    keys.write_text('{"typesafe":"","deepseek":"unused"}', encoding="utf-8")
    with pytest.raises(ValueError, match="key"):
        call(lambda _: pytest.fail("network"), factory=lambda: pytest.fail("context"), keys_file=keys)


def test_invalid_questions_rejected_before_context(call):
    with pytest.raises(ValueError, match="criteria"):
        call(lambda _: pytest.fail("network"), factory=lambda: pytest.fail("context"), question={})


def test_total_deadline_is_enforced(call, monkeypatch):
    timeout = asyncio.timeout
    monkeypatch.setattr("whynote.jev_provider.asyncio.timeout", lambda _: timeout(0.01))

    async def slow(request):
        await asyncio.Event().wait()

    with pytest.raises(ValueError, match="reconcile"):
        call(slow)


def test_optional_noul_uses_existing_validator(call):
    questions = {
        "primary_reason": {
            "type": "choice",
            "instructions": "Synthetic only",
            "criteria": {code: code for code in REASON_CODES},
        },
        "factual_error_signal": {"type": "noul", "instructions": "Synthetic factual signal"},
    }
    result = response()
    result["answers"]["factual_error_signal"] = {"type": "noul", "noul": 0.5}
    assert call(lambda _: httpx.Response(200, json=result), question=questions)["binary_signals"] == {
        "factual_error_signal": 0.5
    }
