"""Versioned feedback rules with no dependency on an external model."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Iterable, Mapping

TAXONOMY_VERSION = "candidate-v1"
REASON_CODES = frozenset(
    {
        "factual_error",
        "instruction_not_followed",
        "incomplete",
        "irrelevant",
        "style",
        "outdated",
        "unnecessary_refusal",
        "other_or_unknown",
    }
)
DISPLAY_MODES = frozenset({"manual_menu", "model_suggestion", "edit_menu"})
USER_ACTION_MODES = {
    "reason_selected": "manual_menu",
    "reason_confirmed": "model_suggestion",
    "reason_edited": "edit_menu",
    "attribution_invalidated": "model_suggestion",
}


class ConflictError(ValueError):
    """A write conflicts with an existing action or idempotency key."""


class NotFoundError(LookupError):
    """An action is absent or not visible to the current principal."""


@dataclass(frozen=True)
class Principal:
    tenant_ref: str
    actor_ref: str


@dataclass(frozen=True)
class GateSignals:
    """Policy outputs supplied by trusted services, never by the feedback client."""

    authorized: bool
    scenario_eligible: bool
    sampled: bool
    budget_available: bool
    policy_version: str
    scenario_id: str
    sampling_policy_id: str | None = None
    sample_id: str | None = None
    inclusion_probability: float | None = None


def decide_gate(signals: GateSignals) -> dict[str, Any]:
    checks = {
        "authorization": signals.authorized,
        "scenario": signals.scenario_eligible,
        "sampling": signals.sampled,
        "budget": signals.budget_available,
    }
    probability = signals.inclusion_probability
    if probability is not None and (not isfinite(probability) or not 0 <= probability <= 1):
        raise ValueError("inclusion_probability must be finite and between 0 and 1")
    if probability is not None and not signals.sampling_policy_id:
        raise ValueError("a probability requires a sampling policy ID")
    if signals.sampled and probability == 0:
        raise ValueError("a selected sample cannot have zero inclusion probability")
    denied = [name for name, allowed in checks.items() if not allowed]
    # Authorization failure does not permit even sampling metadata to be retained.
    retained_probability = probability if signals.authorized else None
    retained_sample_id = signals.sample_id if signals.authorized else None
    retained_policy_id = signals.sampling_policy_id if signals.authorized else None
    weight = 1 / retained_probability if retained_probability and not denied else None
    if weight is not None:
        weight_status = "valid"
    elif retained_probability == 0:
        weight_status = "positivity_violation"
    elif denied:
        weight_status = "not_applicable_gate_denied"
    elif retained_probability is not None:
        weight_status = "not_selected"
    else:
        weight_status = "unidentifiable"
    return {
        "gate_outcome": "deny" if denied else "allow",
        "eligible": signals.authorized and signals.scenario_eligible,
        "deny_reasons": denied,
        "checks": checks,
        "gate_policy_version": signals.policy_version,
        "scenario_id": signals.scenario_id,
        "sampling_policy_id": retained_policy_id,
        "sample_id": retained_sample_id,
        "inclusion_probability": retained_probability,
        "inverse_probability_weight": weight,
        "weight_status": weight_status,
    }


def validate_display(state: Mapping[str, Any], mode: str, shown_reason_codes: list[str]) -> None:
    if state["action_status"] != "active":
        raise ConflictError("cannot display reasons for a retracted action")
    if mode not in DISPLAY_MODES:
        raise ConflictError("unsupported display mode")
    if not shown_reason_codes or len(shown_reason_codes) > len(REASON_CODES):
        raise ConflictError("display must contain reason codes")
    if len(set(shown_reason_codes)) != len(shown_reason_codes) or not set(shown_reason_codes) <= REASON_CODES:
        raise ConflictError("display contains invalid or duplicate reason codes")
    status = state["attribution_status"]
    if mode == "manual_menu" and status not in {"none", "model_inferred_unconfirmed", "invalidated"}:
        raise ConflictError("manual menu cannot replace a submitted response")
    if mode == "model_suggestion" and (
        status != "model_inferred_unconfirmed" or shown_reason_codes != [state["reason_code"]]
    ):
        raise ConflictError("display does not match the active model suggestion")
    if mode == "edit_menu" and status not in {"model_inferred_unconfirmed", "selected", "confirmed", "edited"}:
        raise ConflictError("there is no attribution to edit")


def validate_user_action(
    state: Mapping[str, Any], user_action: str, reason_code: str | None, display: Mapping[str, Any]
) -> None:
    if state["action_status"] != "active":
        raise ConflictError("cannot attribute a retracted action")
    mode = display["mode"]
    required_mode = USER_ACTION_MODES.get(user_action)
    if required_mode and mode != required_mode:
        raise ConflictError("user action does not match the displayed mode")
    if user_action in {"reason_selected", "reason_confirmed", "reason_edited"}:
        if reason_code not in REASON_CODES or reason_code not in display["shown_reason_codes"]:
            raise ConflictError("reason was not displayed")
    elif reason_code is not None:
        raise ConflictError("this action must not include a reason code")
    status = state["attribution_status"]
    if user_action == "reason_selected" and status not in {"none", "model_inferred_unconfirmed", "invalidated"}:
        raise ConflictError("a submitted reason requires an edit action")
    if user_action == "reason_confirmed" and (
        status != "model_inferred_unconfirmed" or state["reason_code"] != reason_code
    ):
        raise ConflictError("confirmation requires the active model suggestion")
    if user_action == "reason_edited" and status not in {
        "model_inferred_unconfirmed",
        "selected",
        "confirmed",
        "edited",
    }:
        raise ConflictError("there is no attribution to edit")
    if user_action in {"reason_declined", "reason_skipped"} and status not in {
        "none",
        "model_inferred_unconfirmed",
        "invalidated",
    }:
        raise ConflictError("a submitted user reason cannot be cleared by declining or skipping")
    if user_action == "attribution_invalidated" and status != "model_inferred_unconfirmed":
        raise ConflictError("there is no active model suggestion to invalidate")


def project(events: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Rebuild the user-visible state from immutable, ordered events."""

    state: dict[str, Any] = {
        "projection_version": "2",
        "action_status": "active",
        "inference_status": "not_requested",
        "attribution_status": "none",
        "attribution_source": "none",
        "reason_code": None,
        "gate": None,
    }
    for event in events:
        kind = event["event_type"]
        payload = event["payload"]
        if state["action_status"] == "retracted":
            continue
        if kind == "negative_feedback_action_recorded":
            state["inference_status"] = "gate_pending"
            state["target_ref"] = payload["target_ref"]
        elif kind == "inference_gate_decided":
            state["gate"] = payload
            state["inference_status"] = "denied" if payload["gate_outcome"] == "deny" else "queued"
        elif kind == "inference_abstained":
            state["inference_status"] = "abstained"
            state["abstain_reason"] = payload["reason"]
        elif kind == "model_suggestion_recorded":
            if state["action_status"] == "retracted":
                continue
            state["inference_status"] = "succeeded"
            if state["attribution_status"] == "none":
                state["attribution_status"] = "model_inferred_unconfirmed"
                state["attribution_source"] = "model_inferred_unconfirmed"
                state["reason_code"] = payload["reason_code"]
        elif kind == "attribution_invalidated":
            state["attribution_status"] = "invalidated"
            state["attribution_source"] = "none"
            state["reason_code"] = None
        elif kind in {"reason_selected", "reason_confirmed", "reason_edited"}:
            state["attribution_status"] = {
                "reason_selected": "selected",
                "reason_confirmed": "confirmed",
                "reason_edited": "edited",
            }[kind]
            state["attribution_source"] = {
                "reason_selected": payload.get("attribution_source", "user_selected"),
                "reason_confirmed": "user_confirmed",
                "reason_edited": "user_edited",
            }[kind]
            state["reason_code"] = payload["reason_code"]
        elif kind in {"reason_declined", "reason_skipped", "reason_unresponded"}:
            state["attribution_status"] = kind.removeprefix("reason_")
            state["attribution_source"] = "none"
            state["reason_code"] = None
        elif kind == "action_retracted":
            state["action_status"] = "retracted"
            if state["inference_status"] in {"gate_pending", "queued", "running"}:
                state["inference_status"] = "cancelled"
            state["attribution_status"] = "none"
            state["attribution_source"] = "none"
            state["reason_code"] = None
    return state


def validate_jev_response(response: Mapping[str, Any], pinned_model: str) -> dict[str, Any]:
    """Validate TypeSafe Choice/Noul output before any calibration or routing."""

    if response.get("model") != pinned_model:
        raise ValueError("unexpected model version")
    answers = response.get("answers")
    if not isinstance(answers, dict):
        raise ValueError("missing answers")
    answer = answers.get("primary_reason")
    if not isinstance(answer, dict) or answer.get("type") != "choice":
        raise ValueError("missing Choice answer")
    probabilities = answer.get("probabilities")
    if not isinstance(probabilities, dict) or set(probabilities) != REASON_CODES:
        raise ValueError("probability keys must match taxonomy")
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or not 0 <= value <= 1
        for value in probabilities.values()
    ):
        raise ValueError("invalid probability")
    if abs(sum(probabilities.values()) - 1) > 0.02:
        raise ValueError("probabilities do not sum to approximately one")
    reason = answer.get("choice")
    if (
        not isinstance(reason, str)
        or reason not in REASON_CODES
        or probabilities[reason] < max(probabilities.values()) - 0.02
    ):
        raise ValueError("choice is inconsistent with probabilities")
    confidence = answer.get("confidence")
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not isfinite(confidence)
        or not 0 <= confidence <= 1
    ):
        raise ValueError("invalid confidence")
    signals: dict[str, float] = {}
    for name in ("factual_error_signal", "instruction_failure_signal"):
        signal = answers.get(name)
        if signal is None:
            continue
        score = signal.get("noul") if isinstance(signal, dict) and signal.get("type") == "noul" else None
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not isfinite(score) or not 0 <= score <= 1:
            raise ValueError("invalid Noul answer")
        signals[name] = float(score)
    usage = response.get("usage")
    if not isinstance(usage, dict) or any(
        isinstance(usage.get(key), bool) or not isinstance(usage.get(key), int) or usage[key] < 0
        for key in ("input_tokens", "output_tokens")
    ):
        raise ValueError("invalid usage")
    return {
        "provider": "typesafe",
        "model_version": pinned_model,
        "taxonomy_version": TAXONOMY_VERSION,
        "primary_reason": reason,
        "reason_probabilities": probabilities,
        "binary_signals": signals,
        "raw_confidence": float(confidence),
        "usage": usage,
    }
