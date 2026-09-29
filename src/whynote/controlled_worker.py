"""One isolated prediction/measurement. Stdin never contains gold or reviewer fields."""

import argparse
import contextlib
import json
import sys

from .controlled_inputs import load_tokenizer, measure_budget
from .laya_local import MODEL
from .laya_manifest import SHA256
from .replay import infer_scheme, length_bucket
from .replay_laya import ReplayError, check_budget, require, validate_response
from .self_review_contract import sha


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cuda")
    args = parser.parse_args()
    try:
        raw = sys.stdin.buffer.read(1048577)
        require(len(raw) <= 1048576, "input_bytes_exceeded")
        payload = json.loads(raw)
        require(
            isinstance(payload, dict)
            and set(payload) == {"operation", "state", "input_id", "scheme", "evidence_kinds"},
            "invalid_worker_payload",
        )
        require(
            payload["operation"] in ("measure", "infer") and payload["scheme"] in ("A", "B", "C"),
            "invalid_worker_payload",
        )
        state = payload["state"]
        require(isinstance(state, str) and state.strip(), "invalid_worker_payload")
        sha(payload["input_id"])
        require(
            isinstance(payload["evidence_kinds"], list)
            and all(
                isinstance(k, str) and k in ("request", "answer", "original_code") for k in payload["evidence_kinds"]
            ),
            "invalid_worker_payload",
        )
        # Suppress SDK stdout; stderr is discarded by the supervisor, never persisted.
        with contextlib.redirect_stdout(sys.stderr):
            if payload["operation"] == "measure":
                result = {"measurement": measure_budget(load_tokenizer(args.model_dir), state)}
            else:
                require(len(state.encode()) <= 8192, "input_bytes_exceeded")
                # Validate tokenizer/runtime before loading; laya verifies all pinned
                # artifact hashes. Path canonicalization belongs to the supervisor.
                load_tokenizer(args.model_dir)
                import laya
                import torch

                agent = laya.load(args.model_dir, device=args.device, fast=False, compile=False, expected_sha256=SHA256)
                require(
                    str(agent.device) == args.device
                    and agent.cfg.get("max_len") == 1024
                    and agent.cfg.get("head_max_len") == 256,
                    "model_load_failed",
                )
                torch.manual_seed(42)
                if args.device == "cuda":
                    torch.cuda.manual_seed_all(42)

                class Backend:
                    metadata = {"backend": "laya_local", "model": MODEL}

                    def predict(self, state, questions):
                        budget = check_budget(agent, state, questions)
                        answers = validate_response(agent.predict(state, questions), questions)
                        return {"answers": answers, "budget": budget}

                import hashlib

                item = {
                    "input_id": payload["input_id"],
                    "source": "controlled_projection",
                    "task": "unknown",
                    "language": "unknown",
                    "partition": "validation",
                    "state": state,
                    "state_sha256": hashlib.sha256(state.encode()).hexdigest(),
                    "input_utf8_bytes": len(state.encode()),
                    "length_bucket": length_bucket(len(state.encode())),
                    "input_error": None,
                    "evidence_kinds": payload["evidence_kinds"],
                }
                result = {"prediction": infer_scheme(Backend(), item, payload["scheme"])}
    except ReplayError as exc:
        result = {"error": str(exc)}
    except Exception:
        result = {"error": "model_load_failed"}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
