"""Independent contract checks: a requested Noul answer cannot silently vanish."""

import asyncio
import json

import httpx
import pytest

from whynote.domain import REASON_CODES
from whynote.jev_provider import MODEL, evaluate_reason


@pytest.mark.parametrize("signal", ["factual_error_signal", "instruction_failure_signal"])
@pytest.mark.parametrize("answer", [None, {}, True])
def test_requested_noul_requires_a_typed_answer(tmp_path, signal, answer):
    keys = tmp_path / "synthetic.json"
    keys.write_text('{"typesafe":"synthetic-independent-key"}', encoding="utf-8")
    questions = {
        "primary_reason": {
            "type": "choice",
            "instructions": "Synthetic classification",
            "criteria": {code: code for code in REASON_CODES},
        },
        signal: {"type": "noul", "instructions": "Synthetic binary signal"},
    }
    response = {
        "model": MODEL,
        "answers": {
            "primary_reason": {
                "type": "choice",
                "choice": "factual_error",
                "confidence": 1,
                "probabilities": {code: int(code == "factual_error") for code in REASON_CODES},
            },
            signal: answer,
        },
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }
    calls = []

    def transport(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=response)

    with pytest.raises(ValueError, match="pinned contract"):
        result = asyncio.run(
            evaluate_reason(
                lambda: "Synthetic permitted state",
                questions,
                keys_file=keys,
                enabled=True,
                outbound_approval_ref="synthetic-only",
                budget_reservation_ref="synthetic-only",
                transport=httpx.MockTransport(transport),
            )
        )
        print({"signal": signal, "provider_answer": answer, "returned_signals": result["binary_signals"]})
    assert len(calls) == 1
