"""Synthetic Windows environment qualification; never grants dataset admission."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

from .controlled_replay import payload_for, source_hash
from .isolation_probe import probe_network
from .replay import write_json
from .replay_laya import ReplayError, require
from .replay_runtime import model_lock, plain_synthetic_process, trusted_command
from .source_mapping import digest
from .windows_isolation import isolated_profile


def runtime_hash(python, base_python):
    """Bind installed runtime code and native libraries, including newly added files."""
    python = Path(python).resolve(strict=True)
    roots = {"runtime": python.parents[1], "base": Path(base_python).resolve(strict=True)}
    hashes = {}
    for name, root in roots.items():
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix.lower() in (".py", ".pyd", ".dll", ".exe", ".json"):
                with path.open("rb") as stream:
                    hashes[name + "/" + path.relative_to(root).as_posix()] = hashlib.file_digest(
                        stream, "sha256"
                    ).hexdigest()
    return digest(hashes)


def probe_controls(sandbox, python):
    # Called while the supervisor owns the machine mutex. The second process must fail.
    package = str(Path(__file__).parent.parent)
    code = (
        "import sys;sys.path.insert(0,sys.argv[1]);from whynote.replay_runtime import model_lock;"
        "from whynote.replay_laya import ReplayError\n"
        "try:\n with model_lock(): print('unexpected_acquisition')\n"
        "except ReplayError as e: print(str(e))"
    )
    raw, _ = plain_synthetic_process([str(python), "-I", "-B", "-c", code, package], b"")
    concurrency = {"passed": raw.strip() == b"model_busy", "scope": "second_process_rejected_while_mutex_owned"}
    code = (
        "import subprocess,sys,time;subprocess.Popen([sys.executable,'-I','-c','import time;time.sleep(60)']);"
        "time.sleep(60)"
    )
    command = [str(python), "-I", "-B", "-c", code]
    timed_out = False
    try:
        sandbox.run(command, b"", timeout=1)
    except ReplayError as exc:
        timed_out = str(exc) == "timeout" and sandbox.cleanup_verified
    original_wait = sandbox.kernel.WaitForSingleObject
    interrupted = False

    def interrupt_once(handle, milliseconds):
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            raise KeyboardInterrupt()
        return original_wait(handle, milliseconds)

    cancelled = False
    sandbox.kernel.WaitForSingleObject = interrupt_once
    try:
        sandbox.run(command, b"", timeout=1)
    except KeyboardInterrupt:
        cancelled = sandbox.cleanup_verified
    finally:
        sandbox.kernel.WaitForSingleObject = original_wait
    raw, _ = sandbox.run([str(python), "-I", "-B", "-c", "print(42)"], b"")
    cancel = {
        "passed": timed_out and cancelled and raw.strip() == b"42",
        "timeout_job_empty": timed_out,
        "injected_interrupt_job_empty": cancelled,
        "next_process_succeeded": raw.strip() == b"42",
    }
    return concurrency, cancel


def qualify(python, base_python, model_dir, scratch, protocol_path, device="cuda"):
    python, base_python, model_dir, scratch, protocol_path = [
        Path(p).resolve(strict=True) for p in (python, base_python, model_dir, scratch, protocol_path)
    ]
    result = {
        "schema_version": "m54a-environment-v1",
        "status": "NOT_READY",
        "python": str(python),
        "base_python": str(base_python),
        "model_dir": str(model_dir),
        "scratch": str(scratch),
        "protocol_path": str(protocol_path),
        "device": device,
        "source_sha256": source_hash(),
        "runtime_sha256": runtime_hash(python, base_python),
        "production_approved": False,
    }
    with (
        model_lock(),
        isolated_profile([Path(__file__).parent.parent, python.parents[1], base_python, model_dir], scratch) as box,
    ):
        result["outbound"] = probe_network(box, python)
        result["concurrency"], result["cancel"] = probe_controls(box, python)
        command = trusted_command(python, "whynote.controlled_worker", "--model-dir", model_dir, "--device", device)
        projection = {"state": "SYNTHETIC: return one. Answer: two.", "evidence_kinds": ["request", "answer"]}
        smoke = []
        for scheme in "ABC":
            raw, elapsed = box.run(command, payload_for(projection, "0" * 64, scheme))
            value = json.loads(raw)
            smoke.append(
                {
                    "scheme": scheme,
                    "status": value.get("prediction", {}).get("status", "error"),
                    "error": value.get("error"),
                    "elapsed_ms": elapsed,
                    "output_sha256": hashlib.sha256(raw).hexdigest(),
                }
            )
        result["synthetic_model_checks"] = smoke
    if all(result[key]["passed"] for key in ("outbound", "concurrency", "cancel")) and all(
        s["status"] == "ok" for s in smoke
    ):
        result["status"] = "VERIFIED_FOR_FREEZE"
    require(
        runtime_hash(python, base_python) == result["runtime_sha256"] and source_hash() == result["source_sha256"],
        "environment_changed_during_probe",
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("python", "base-python", "model-dir", "scratch", "protocol-path", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cuda")
    args = parser.parse_args()
    try:
        result = qualify(args.python, args.base_python, args.model_dir, args.scratch, args.protocol_path, args.device)
        write_json(args.output, result)
        print(json.dumps({"status": result["status"], "production_approved": False}))
        return 0 if result["status"] == "VERIFIED_FOR_FREEZE" else 1
    except (OSError, ValueError, ReplayError):
        print(json.dumps({"status": "NOT_READY", "error": "environment_probe_failed"}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
