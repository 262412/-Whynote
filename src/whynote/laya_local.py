"""Explicit local research adapter; never consumes feedback events or cloud keys."""

import copy
import os
from importlib.metadata import version
from pathlib import Path

from .domain import NotFoundError, validate_jev_response

REVISION = "55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851"
MODEL = f"convaiinnovations/laya/multilingual@{REVISION}"
PROMPT_VERSION = "laya-reason-local-v1"
QUESTIONS = {
    "primary_reason": {
        "type": "choice",
        "instructions": "Why is this answer unsatisfactory? Choose the main reason supported by the text.",
        "criteria": {
            "factual_error": "Incorrect facts or calculation",
            "instruction_not_followed": "Ignores the user's explicit instructions",
            "incomplete": "Missing necessary details or steps",
            "irrelevant": "Does not address the question",
            "style": "Unsuitable tone or presentation",
            "outdated": "Information is out of date",
            "unnecessary_refusal": "Refuses a reasonable request",
            "other_or_unknown": "Other reason or insufficient evidence",
        },
    },
    "factual_error_signal": {"type": "noul", "instructions": "Does the answer contain a factual or calculation error?"},
    "instruction_failure_signal": {
        "type": "noul",
        "instructions": "Does the answer violate explicit user instructions?",
    },
}


class LocalLaya:
    def __init__(self, agent):
        self.agent = agent

    @classmethod
    def load(cls, model_dir, *, device="cuda", enabled=False):
        if enabled is not True:
            raise NotFoundError("Local model is disabled")
        from .laya_manifest import SHA256

        if version("laya") != "0.3.21" or version("transformers") != "4.57.6":
            raise ValueError("Local SDK versions do not match the tested runtime")
        model_dir = Path(model_dir).resolve(strict=True)
        for name, value in {
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "USE_TF": "0",
            "TOKENIZERS_PARALLELISM": "false",
        }.items():
            os.environ[name] = value
        import laya

        agent = laya.load(str(model_dir), device=device, fast=False, compile=False, expected_sha256=SHA256)
        if str(agent.device) != device:
            raise RuntimeError("Requested local device was unavailable; choose cpu explicitly if needed")
        if agent.cfg.get("max_len") != 1024 or agent.cfg.get("head_max_len") != 256:
            raise ValueError("Unexpected checkpoint token configuration")
        return cls(agent)

    def evaluate_reason(self, state_factory, *, enabled=False):
        if enabled is not True:
            raise NotFoundError("Local model is disabled")
        state = state_factory()
        if not isinstance(state, str) or not state.strip() or len(state.encode("utf-8")) > 8192:
            raise ValueError("Input must be nonempty text within 8192 UTF-8 bytes")
        if any(token in state for token in self.agent.tok.all_special_tokens):
            raise ValueError("Input contains a reserved model token")
        if len(self.agent.tok.encode(state, add_special_tokens=False)) > 700:
            raise ValueError("Input exceeds 700 model tokens; shorten it before retrying")
        try:
            response = self.agent.predict(state, copy.deepcopy(QUESTIONS))
        except Exception:
            raise RuntimeError("Local inference failed") from None
        try:
            if not isinstance(response, dict) or response.get("model") != "laya-rl-agent":
                raise ValueError
            answers = response.get("answers")
            if not isinstance(answers, dict) or set(answers) != set(QUESTIONS):
                raise ValueError
            if any(
                not isinstance(answers[k], dict) or answers[k].get("type") != q["type"] for k, q in QUESTIONS.items()
            ):
                raise ValueError
            result = validate_jev_response(response, "laya-rl-agent")
        except (ValueError, TypeError, AttributeError, KeyError):
            raise RuntimeError("Local response does not match the pinned Choice/Noul contract") from None
        return {
            **result,
            "provider": "laya_local",
            "model_version": MODEL,
            "prompt_version": PROMPT_VERSION,
            "attribution_source": "model_inferred_unconfirmed",
            "calibrated": False,
        }
