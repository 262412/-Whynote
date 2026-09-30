"""Bounded M5 research probes. Does not change application defaults or select a reason."""

import argparse
import contextlib
import hashlib
import json
import sys
import time

from .controlled_replay import source_hash
from .explore_worker import Engine, memory
from .replay import DECISIONS, REASONS, ROUTE_C
from .replay_laya import require, validate_response

POLICY = "m55-full-input-windows-v1"
QUESTION_VERSION = "m55-explicit-discrimination-v1"
WINDOWS = (2048, 4096, 8192)
BYTE_LIMIT = 1048576  # Transport ceiling, not a product default.
PROMPTS = {
    "general.unnecessary_refusal": (
        "Does the assistant answer refuse the user's reasonable request without justification? "
        "An answer that actually provides the requested content is not a refusal."
    ),
    "general.instruction_not_followed": (
        "Does the assistant answer violate an explicit instruction already present in the context? "
        "Do not invent requirements or use later feedback."
    ),
    "code.interface_changed": (
        "Did the user explicitly require preserving an existing function interface, and did the answer change it? "
        "Both conditions must hold. A requested change is not a violation. Missing original code is insufficient."
    ),
}


def questions(variant):
    require(variant in ("original", "explicit"), "invalid_worker_payload")
    if variant == "original":
        return ROUTE_C | {
            r.reason_id: {"type": "choice", "instructions": r.criteria, "criteria": DECISIONS}
            for r in REASONS
            if r.reason_id in PROMPTS
        }
    return {
        "route": {
            "type": "choice",
            "instructions": "What task does the latest user request ask the assistant to perform?",
            "criteria": {
                "A": "Rewrite existing code",
                "B": "A non-code task",
                "C": "Write new code or another task",
                "D": "Insufficient information",
            },
        }
    } | {
        key: {
            "type": "choice",
            "instructions": value,
            "criteria": {
                "A": "Yes, the stated defect is present",
                "B": "No, the stated defect is absent",
                "C": "Insufficient evidence",
            },
        }
        for key, value in PROMPTS.items()
    }


def full_sequence(tokenizer, state, question):
    """Independent uncut construction; compare every token with the SDK and forward batch."""
    from laya.agent import Agent
    from laya.common import render_options

    def encode(text):
        return tokenizer(text, add_special_tokens=False)["input_ids"]

    internal = Agent._to_internal(question)
    require(not any(t in state for t in tokenizer.all_special_tokens), "reserved_token")
    head = encode(f"{internal['t']} question: {internal['ins']}")
    options = [encode(" " + option) for option in render_options(internal)]
    require(all(len(o) <= 48 for o in options), "option_truncated")
    require(len(head) + sum(len(o) + 1 for o in options) <= 256, "head_truncated")
    ids = [tokenizer.cls_token_id, *head, tokenizer.sep_token_id]
    for option in options:
        ids.extend([tokenizer.mask_token_id, *option])
    return ids + [tokenizer.sep_token_id, *encode(state), tokenizer.sep_token_id]


def fits(state_tokens, total_tokens, byte_count, window):
    require(window in WINDOWS, "invalid_window")
    return state_tokens <= window - 260 and total_tokens <= window and byte_count <= BYTE_LIMIT


def predict_checked(engine, state, variant, window):
    require(window in WINDOWS and isinstance(state, str) and state.strip(), "invalid_worker_payload")
    require(len(state.encode()) <= BYTE_LIMIT, "input_budget_exceeded")
    qs = questions(variant)
    expected = [full_sequence(engine.tokenizer, state, q) for q in qs.values()]
    n = len(engine.tokenizer(state, add_special_tokens=False)["input_ids"])
    require(fits(n, max(map(len, expected)), len(state.encode()), window), "input_budget_exceeded")
    agent = engine.agent
    from laya.agent import Agent

    internal = {key: Agent._to_internal(q) for key, q in qs.items()}
    encoded = agent._encode_state(state, list(qs), internal, max_len=window, head_max_len=256)
    require([v["ids"] for v in encoded] == expected, "sdk_truncation")
    seen = []
    original_forward = agent._forward

    def forward(batch):
        rows = batch["input_ids"].tolist()
        masks = batch["attention_mask"].tolist()
        actual = [
            [token for token, keep in zip(row, mask, strict=True) if keep]
            for row, mask in zip(rows, masks, strict=True)
        ]
        require(actual == expected[len(seen) : len(seen) + len(actual)], "forward_truncation")
        seen.extend(actual)
        return original_forward(batch)

    agent._forward = forward
    before = agent.cpu_fallback_count
    engine.torch.manual_seed(42)
    if engine.device == "cuda":
        engine.torch.cuda.manual_seed_all(42)
        engine.torch.cuda.reset_peak_memory_stats()
    try:
        response = validate_response(agent.predict(state, qs, max_len=window, head_max_len=256), qs)
    finally:
        agent._forward = original_forward
    require(seen == expected, "forward_not_verified")
    return {
        "answers": response,
        "primary_reason": None,
        "selection_status": "cross_question_ranking_not_validated",
        "state_tokens": n,
        "total_tokens": list(map(len, expected)),
        "input_utf8_bytes": len(state.encode()),
        "state_sha256": hashlib.sha256(state.encode()).hexdigest(),
        "full_encoding_verified": True,
        "cpu_fallback_count": agent.cpu_fallback_count - before,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cuda")
    args = parser.parse_args()
    start = time.perf_counter()
    try:
        with contextlib.redirect_stdout(sys.stderr):
            engine = Engine(args.model_dir, args.device)
        print(
            json.dumps(
                {"kind": "ready", "source_sha256": source_hash(), "load_ms": (time.perf_counter() - start) * 1000}
            ),
            flush=True,
        )
    except Exception:
        print(json.dumps({"error": "model_load_failed"}), flush=True)
        return
    while raw := sys.stdin.buffer.readline(2 * BYTE_LIMIT + 1):
        start = time.perf_counter()
        try:
            require(len(raw) <= 2 * BYTE_LIMIT and raw.endswith(b"\n"), "invalid_worker_payload")
            payload = json.loads(raw)
            require(
                isinstance(payload, dict) and set(payload) == {"state", "variant", "window"}, "invalid_worker_payload"
            )
            with contextlib.redirect_stdout(sys.stderr):
                result = predict_checked(engine, **payload)
            result.update(metrics=memory(engine.torch, args.device), elapsed_ms=(time.perf_counter() - start) * 1000)
        except Exception:
            # Never expose SDK exception text or input in ordinary diagnostics.
            result = {"error": "diagnostic_failed"}
        print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)
        if result.get("error"):
            return
