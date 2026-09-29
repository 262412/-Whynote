"""Pinned local Laya for explicitly registered synthetic template sessions."""

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

from .laya_local import MODEL, REVISION
from .replay import infer_scheme, sample
from .replay_laya import ReplayError, require
from .task_reasons import TASK_TYPES, ReasonContractError
from .template_suggestions import prepare

VERSION = "m5-live-laya-v1"
ERRORS = {
    "backend_unavailable",
    "timeout",
    "model_load_failed",
    "inference_failed",
    "invalid_response",
    "invalid_state",
    "input_bytes_exceeded",
    "input_tokens_exceeded",
    "reserved_token",
    "question_tokens_exceeded",
    "total_tokens_exceeded",
}


def error_code(value):
    return value if isinstance(value, str) and value in ERRORS else "invalid_response"


def live(config):
    return config.get("suggestion_backend", "fixture") == "laya_local"


def validate_config(config):
    backend = config.get("suggestion_backend", "fixture")
    if backend not in ("fixture", "laya_local"):
        raise ValueError("Invalid suggestion backend")
    if backend == "fixture":
        return
    if config.get("suggestion_template_enabled") is not True or config.get("suggestion_model_revision") != REVISION:
        raise ValueError("Live suggestions require pinned synthetic templates")
    for key in ("suggestion_python", "suggestion_model_dir"):
        if not isinstance(config.get(key), str) or not Path(config[key]).is_absolute():
            raise ValueError("Absolute local runtime and model paths are required")
    targets = config.get("suggestion_synthetic_targets")
    if not isinstance(targets, dict) or not targets or len(targets) > 100:
        raise ValueError("Explicit synthetic targets are required")
    if any(not isinstance(k, str) or not isinstance(v, str) or not k or not v for k, v in targets.items()):
        raise ValueError("Invalid synthetic target binding")


def evidence(question, answer):
    kinds = [k for k, text in (("request", question), ("answer", answer)) if text.strip()]
    if re.search(r"(?m)^```[^\n]*\n[\s\S]+?\n```[ \t]*$", question):
        kinds.append("original_code")
    return kinds


def infer(backend, question, answer):
    require(backend.metadata.get("model") == MODEL, "invalid_response")
    item = sample(
        {"request": question, "answer": answer},
        identity={"scope": VERSION},
        source="scripted",
        task="unknown",
        language="unknown",
        evidence=evidence(question, answer),
        partition="synthetic",
    )
    result = infer_scheme(backend, item, "C")
    if result["status"] != "ok":
        raise ReplayError(error_code(result["error"]))
    return {
        "version": VERSION,
        "model": MODEL,
        "route": result["route"],
        "fallback_used": result["fallback_used"],
        "outcome": result["outcome"],
        "reason_ids": result["reason_ids"],
    }


def presentation(question, answer, object_version, result):
    require(
        isinstance(result, dict)
        and set(result) == {"version", "model", "route", "fallback_used", "outcome", "reason_ids"}
        and result["version"] == VERSION
        and result["model"] == MODEL
        and result["route"] in TASK_TYPES
        and type(result["fallback_used"]) is bool,
        "invalid_response",
    )
    require(isinstance(result["reason_ids"], list) and len(result["reason_ids"]) <= 1, "invalid_response")
    if result["fallback_used"]:
        require(result["route"] == "general" and "original_code" in evidence(question, answer), "invalid_response")
    try:
        return prepare(
            question,
            answer,
            object_version,
            {
                "tasks": ["mixed" if result["fallback_used"] else result["route"]],
                "outcome": result["outcome"],
                "reason_ids": result["reason_ids"],
                "citations": {},
            },
        )
    except ReasonContractError:
        raise ReplayError("invalid_response") from None


async def evaluate(config, question, answer):
    validate_config(config)
    require(live(config), "backend_unavailable")
    require(all(isinstance(t, str) and t.strip() for t in (question, answer)), "invalid_state")
    payload = json.dumps({"request": question, "answer": answer}, ensure_ascii=False, separators=(",", ":"))
    require(len(payload.encode()) <= 8192, "input_bytes_exceeded")
    package_root = str(Path(__file__).resolve().parents[1])
    bootstrap = "import sys; sys.path.insert(0, sys.argv.pop(1)); from whynote.live_suggestions import main; main()"
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            config["suggestion_python"],
            "-I",
            "-B",
            "-X",
            "utf8",
            "-c",
            bootstrap,
            package_root,
            "--model-dir",
            config["suggestion_model_dir"],
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=os.environ.copy(),
            **({"creationflags": 0x08000000} if sys.platform == "win32" else {}),
        )
        stdout, _ = await asyncio.wait_for(process.communicate(payload.encode()), 60)
        require(process.returncode == 0 and len(stdout) <= 16384, "backend_unavailable")
        result = json.loads(stdout)
        if isinstance(result, dict) and set(result) == {"error"}:
            raise ReplayError(error_code(result["error"]))
        return result
    except TimeoutError:
        raise ReplayError("timeout") from None
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ReplayError("backend_unavailable") from None
    finally:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()


def main():
    import contextlib
    import socket

    from .laya_local import LocalLaya
    from .replay_laya import check_budget, validate_response

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", required=True)
    args = parser.parse_args()

    def deny_network(*args, **kwargs):
        raise OSError("network_disabled")

    socket.socket.connect = socket.socket.connect_ex = deny_network
    try:
        raw = sys.stdin.buffer.read(8193)
        require(len(raw) <= 8192, "input_bytes_exceeded")
        fields = json.loads(raw)
        require(set(fields) == {"request", "answer"}, "invalid_state")
        require(all(isinstance(v, str) and v.strip() for v in fields.values()), "invalid_state")
        with contextlib.redirect_stdout(sys.stderr):
            adapter = LocalLaya.load(args.model_dir, enabled=True)
            import torch

            torch.manual_seed(42)
            torch.cuda.manual_seed_all(42)

            class Backend:
                metadata = {"model": MODEL, "backend": "laya_local"}

                def predict(self, state, questions):
                    budget = check_budget(adapter.agent, state, questions)
                    try:
                        answers = validate_response(adapter.agent.predict(state, questions), questions)
                    except ReplayError:
                        raise
                    except Exception:
                        raise ReplayError("inference_failed") from None
                    return {"answers": answers, "budget": budget}

            result = infer(Backend(), fields["request"], fields["answer"])
    except ReplayError as exc:
        result = {"error": error_code(str(exc))}
    except Exception:
        result = {"error": "model_load_failed"}
    print(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    main()
