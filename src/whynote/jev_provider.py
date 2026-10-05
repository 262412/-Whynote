"""Reserved TypeSafe transport. No caller in the feedback or Outbox paths."""

import asyncio
import json

from .domain import REASON_CODES, NotFoundError, validate_jev_response
from .provider_keys import load_provider_key

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-1.13.0"


async def evaluate_reason(
    state_factory,
    questions,
    *,
    keys_file,
    enabled=False,
    outbound_approval_ref=None,
    budget_reservation_ref=None,
    transport=None,
):
    """Caller must perform admission and reserve budget before enabling this seam.

    state_factory delays context access until admission and credentials pass.
    transport is only an HTTP testing seam, not a configurable provider endpoint.
    """
    if enabled is not True or any(
        not isinstance(ref, str) or not ref.strip() for ref in (outbound_approval_ref, budget_reservation_ref)
    ):
        raise NotFoundError("Jev entry is disabled or admission is incomplete")
    if not isinstance(questions, dict) or set(questions) - {
        "primary_reason",
        "factual_error_signal",
        "instruction_failure_signal",
    }:
        raise ValueError("Unsupported Jev questions")
    primary = questions.get("primary_reason")
    if (
        not isinstance(primary, dict)
        or primary.get("type") != "choice"
        or not isinstance(primary.get("criteria"), dict)
        or set(primary["criteria"]) != REASON_CODES
    ):
        raise ValueError("Jev Choice criteria must match the reason taxonomy")
    for name, question in questions.items():
        if (
            not isinstance(question, dict)
            or not isinstance(question.get("instructions"), str)
            or not question["instructions"].strip()
        ):
            raise ValueError("Jev instructions are required")
        if name != "primary_reason" and question.get("type") != "noul":
            raise ValueError("Jev optional signals must be Noul questions")
    key = load_provider_key(keys_file, "typesafe")
    state = state_factory()
    if not isinstance(state, (str, dict, list)):
        raise ValueError("Jev state must be text or structured text")
    try:
        body = json.dumps(
            {"model": MODEL, "state": state, "questions": questions}, ensure_ascii=False, allow_nan=False
        ).encode()
    except (TypeError, ValueError):
        raise ValueError("Invalid Jev request") from None
    if len(body) > 8192:
        raise ValueError("Jev request exceeds the reserved interface limit")

    raw = await _request_bytes(body, key, transport)
    try:
        response = json.loads(raw)
        if not isinstance(response, dict) or set(response.get("answers", {})) != set(questions):
            raise ValueError
        # The shared validator allows omitted optional signals in older inputs.
        # Here every requested question must have a typed answer, including Noul.
        for name, question in questions.items():
            answer = response["answers"][name]
            if not isinstance(answer, dict) or answer.get("type") != question["type"]:
                raise ValueError
        return validate_jev_response(response, MODEL)
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Jev response does not match the pinned contract") from None


async def _request_bytes(body, key, transport=None):
    """Shared bounded transport; callers enforce their versioned admission contract."""
    import httpx  # Optional extra; disabled entries do not initialize a client.

    try:
        async with (
            asyncio.timeout(60),
            httpx.AsyncClient(transport=transport, trust_env=False, timeout=httpx.Timeout(60, connect=10)) as client,
        ):
            async with client.stream(
                "POST",
                ENDPOINT,
                content=body,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                follow_redirects=False,
            ) as response:
                if response.status_code != 200:
                    raise ValueError(f"Jev request failed (HTTP {response.status_code})")
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 1_048_576:
                        raise ValueError("Jev response exceeds the reserved interface limit")
    except (TimeoutError, httpx.HTTPError):
        raise ValueError("Jev transport failed; reconcile the reserved budget before retrying") from None
    return raw
