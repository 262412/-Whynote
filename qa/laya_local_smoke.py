"""Explicit real-checkpoint smoke test; never run by ordinary pytest or CI."""

import argparse
import importlib.metadata
import json
import socket
import time
from pathlib import Path

from whynote.laya_local import MODEL, QUESTIONS, LocalLaya
from whynote.laya_manifest import SHA256


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    def deny_network(*args, **kwargs):
        raise AssertionError("Network access forbidden in the local smoke test")

    socket.socket.connect = deny_network
    socket.socket.connect_ex = deny_network
    started = time.perf_counter()
    adapter = LocalLaya.load(args.model_dir, enabled=True)
    load_ms = round((time.perf_counter() - started) * 1000, 1)
    cases = [
        (
            "zh_arithmetic",
            "User question: 17 加 25 等于多少？\nAssistant answer: 17 加 25 等于 41。\nUser feedback: 计算结果不对",
        ),
        (
            "zh_instruction",
            "User question: 请用中文回答法国的首都。\nAssistant answer: The capital of France is Paris.",
        ),
        ("zh_irrelevance", "User question: 怎样煮米饭？\nAssistant answer: 木星是太阳系中最大的行星。"),
    ]
    results = []
    # Verify the fixed prompts/options themselves fit without instruction or option truncation.
    from laya.common import encode_text, render_options

    tok = adapter.agent.tok
    prompt_tokens = {}
    for name, definition in QUESTIONS.items():
        internal = adapter.agent._to_internal(definition)
        options = render_options(internal)
        sizes = [len(encode_text(tok, " " + option, add_special_tokens=False)["input_ids"]) for option in options]
        instructions = len(
            encode_text(tok, f"{internal['t']} question: {internal['ins']}", add_special_tokens=False)["input_ids"]
        )
        assert max(sizes) <= 48 and instructions + sum(size + 1 for size in sizes) <= 256
        prompt_tokens[name] = {"instructions": instructions, "options": sizes}
    for case_id, state in cases:
        started = time.perf_counter()
        result = adapter.evaluate_reason(lambda state=state: state, enabled=True)
        results.append({"case_id": case_id, "elapsed_ms": round((time.perf_counter() - started) * 1000, 1), **result})
    try:
        adapter.evaluate_reason(lambda: "中文测试" * 500, enabled=True)
    except ValueError:
        rejected_long_input = True
    else:
        raise AssertionError("Long input was silently accepted")
    import torch

    report = {
        "model": MODEL,
        "files_sha256": SHA256,
        "runtime": {name: importlib.metadata.version(name) for name in ("laya", "torch", "transformers", "fastapi")},
        "device": torch.cuda.get_device_name(),
        "cuda": torch.version.cuda,
        "peak_allocated_mib": round(torch.cuda.max_memory_allocated() / 1024**2, 1),
        "load_ms": load_ms,
        "network_connect_blocked": True,
        "long_input_rejected": rejected_long_input,
        "fixed_prompt_token_counts": prompt_tokens,
        "results": results,
        "scope": "developer synthetic transport smoke; not accuracy evaluation or independent acceptance",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"cases": len(results), "load_ms": load_ms, "output": str(args.output)}))


if __name__ == "__main__":
    main()
