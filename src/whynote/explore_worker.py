"""Resident offline Laya worker; no feedback, labels, source paths or prior state in IPC."""

import argparse
import contextlib
import ctypes
import hashlib
import json
import sys
import time

from .controlled_inputs import load_tokenizer, measure_budget
from .controlled_replay import source_hash
from .controlled_worker import checked_prediction
from .laya_local import MODEL
from .laya_manifest import SHA256
from .replay import infer_scheme, length_bucket
from .replay_laya import ReplayError, require
from .self_review_contract import budget_ok, sha


def memory(torch, device):
    result = {
        "rss_bytes": None,
        "rss_peak_bytes": None,
        "gpu_allocated_bytes": 0,
        "gpu_reserved_bytes": 0,
        "gpu_peak_bytes": 0,
    }
    if sys.platform == "win32":

        class Counters(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_ulong), ("faults", ctypes.c_ulong)] + [
                (key, ctypes.c_size_t)
                for key in (
                    "peak",
                    "working",
                    "paged_peak",
                    "paged",
                    "nonpaged_peak",
                    "nonpaged",
                    "pagefile",
                    "pagefile_peak",
                )
            ]

        kernel, psapi = ctypes.WinDLL("kernel32"), ctypes.WinDLL("psapi")
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        require(
            psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb),
            "memory_probe_failed",
        )
        result.update(rss_bytes=counters.working, rss_peak_bytes=counters.peak)
    if device == "cuda":
        result.update(
            gpu_allocated_bytes=torch.cuda.memory_allocated(),
            gpu_reserved_bytes=torch.cuda.memory_reserved(),
            gpu_peak_bytes=torch.cuda.max_memory_allocated(),
        )
    return result


class Engine:
    metadata = {"backend": "laya_local", "model": MODEL}

    def __init__(self, model_dir, device):
        self.device = device
        self.tokenizer = load_tokenizer(model_dir)
        import laya
        import torch

        self.torch = torch
        self.agent = laya.load(model_dir, device=device, fast=False, compile=False, expected_sha256=SHA256)
        require(
            str(self.agent.device) == device
            and self.agent.cfg.get("max_len") == 1024
            and self.agent.cfg.get("head_max_len") == 256,
            "model_load_failed",
        )

    def predict(self, state, questions):
        return checked_prediction(self.agent, state, questions)

    def infer(self, payload):
        require(
            isinstance(payload, dict) and set(payload) == {"input_id", "state", "scheme", "evidence_kinds"},
            "invalid_worker_payload",
        )
        sha(payload["input_id"])
        state, scheme = payload["state"], payload["scheme"]
        require(isinstance(state, str) and state.strip() and len(state.encode()) <= 1048576, "invalid_worker_payload")
        require(isinstance(scheme, str) and scheme in ("A", "B", "C"), "invalid_worker_payload")
        require(
            isinstance(payload["evidence_kinds"], list)
            and all(
                isinstance(k, str) and k in ("request", "answer", "original_code") for k in payload["evidence_kinds"]
            ),
            "invalid_worker_payload",
        )
        measured = measure_budget(self.tokenizer, state)
        measured.pop("evidence")
        reply = {
            "input_id": payload["input_id"],
            "state_sha256": hashlib.sha256(state.encode()).hexdigest(),
            "scheme": scheme,
            "measurement": measured,
            "prediction": None,
            "error": None,
        }
        if not budget_ok(measured):
            return reply | {"bucket": "ineligible", "error": "input_budget_exceeded"}
        # Per-request seeds and local item/result ensure no previous example is carried forward.
        self.torch.manual_seed(42)
        if self.device == "cuda":
            self.torch.cuda.manual_seed_all(42)
        item = {
            "input_id": payload["input_id"],
            "source": "explore_projection",
            "task": "unknown",
            "language": "unknown",
            "partition": "exploration",
            "state": state,
            "state_sha256": reply["state_sha256"],
            "input_utf8_bytes": len(state.encode()),
            "length_bucket": length_bucket(len(state.encode())),
            "input_error": None,
            "evidence_kinds": payload["evidence_kinds"],
        }
        predicted = infer_scheme(self, item, scheme)
        bucket = "suggested" if predicted["reason_ids"] else "abstained"
        if predicted["status"] != "ok":
            bucket = "technical_failure"
        return reply | {"prediction": predicted, "error": predicted["error"], "bucket": bucket}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    args = parser.parse_args()
    started = time.perf_counter()
    failure = "model_load_failed"
    try:
        with contextlib.redirect_stdout(sys.stderr):
            engine = Engine(args.model_dir, args.device)
        failure = "uncaught_error"
        ready = {
            "kind": "ready",
            "source_sha256": source_hash(),
            "load_ms": (time.perf_counter() - started) * 1000,
            "metrics": memory(engine.torch, args.device),
        }
    except Exception:
        print(json.dumps({"error": failure}), flush=True)
        return
    print(json.dumps(ready), flush=True)
    while raw := sys.stdin.buffer.readline(2 * 1048576 + 1):
        started = time.perf_counter()
        try:
            require(len(raw) <= 2 * 1048576 and raw.endswith(b"\n"), "invalid_worker_payload")
            with contextlib.redirect_stdout(sys.stderr):
                result = engine.infer(json.loads(raw))
            result["metrics"] = memory(engine.torch, args.device)
            result["elapsed_ms"] = (time.perf_counter() - started) * 1000
        except ReplayError as exc:
            result = {
                "error": str(exc) if str(exc) in ("invalid_worker_payload", "invalid_response") else "uncaught_error"
            }
        except Exception:
            result = {"error": "uncaught_error"}
        print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)
        if result.get("error") == "uncaught_error":
            return
