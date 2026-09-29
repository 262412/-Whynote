"""Fixed 60 x 3 supervisor. Durable metadata journal; no raw material in outputs."""

import argparse
import copy
import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from .controlled_inputs import load_projections
from .replay import POOL, write_json
from .replay_laya import ReplayError, require
from .replay_runtime import model_lock, trusted_command
from .self_review import check_setup, report
from .self_review_contract import LEGACY, budget_ok, timestamp
from .self_review_metrics import ERRORS
from .source_mapping import digest


def source_hash():
    # Include new/untracked modules as well as tracked modules; no git-index trust.
    directory = Path(__file__).parent
    return digest(
        {
            p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
            for p in sorted(directory.rglob("*.py"))
        }
    )


def payload_for(projection, input_id, scheme, operation="infer"):
    return json.dumps(
        {
            "operation": operation,
            "state": projection["state"],
            "input_id": input_id,
            "scheme": scheme,
            "evidence_kinds": projection["evidence_kinds"],
        },
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def prediction(sample, scheme, run, response, elapsed):
    allowed = list(LEGACY if scheme == "A" else POOL)
    row = {
        "run_id": run["run_id"],
        "input_id": sample["input_id"],
        "state_sha256": sample["state_sha256"],
        "scheme": scheme,
        "status": "error",
        "error": "invalid_response",
        "outcome": None,
        "route_status": "failed",
        "library_ids": allowed,
        "initial_routed_ids": [],
        "routed_ids": [],
        "available_ids": [],
        "reason_ids": [],
        "elapsed_ms": elapsed,
        "citation_present": False,
        "citation_supported": None,
        "safety_events": [],
    }
    if not budget_ok(sample) or not sample["material_ready"]:
        row.update(
            status="ineligible",
            error="input_budget_exceeded" if not budget_ok(sample) else "material_unavailable",
            route_status="not_attempted",
        )
        return row
    if not isinstance(response, dict):
        return row
    if set(response) == {"error"}:
        error = response["error"]
        row["error"] = error if isinstance(error, str) and error in ERRORS else "invalid_response"
        # With no completed worker response, a route is unavailable, not an
        # observed category omission. Only a pre-load failure proves no attempt.
        if row["error"] == "model_load_failed":
            row["route_status"] = "not_attempted"
        return row
    if set(response) != {"prediction"} or not isinstance(response["prediction"], dict):
        return row
    old = response["prediction"]
    require(all(old.get(k) == row[k] for k in ("input_id", "state_sha256", "scheme")), "worker_identity_changed")
    for key in ("routed_ids", "initial_routed_ids", "available_ids", "reason_ids"):
        values = old.get(key, old.get("routed_ids", []) if key == "initial_routed_ids" else [])
        require(isinstance(values, list) and all(isinstance(v, str) for v in values), "invalid_worker_candidates")
        # The old A policy's unknown sentinel is a legal abstention, never a reason label.
        require(
            set(values) <= set(allowed) | ({"other_or_unknown"} if scheme == "A" else set()),
            "invalid_worker_candidates",
        )
        row[key] = [v for v in values if v in allowed]
    row["route_status"] = "success" if scheme == "A" or old.get("route") is not None else "failed"
    if old.get("status") == "ok":
        require(elapsed <= 60000, "successful_attempt_over_timeout")
        require(old.get("outcome") in ("suggested", "unknown", "no_match"), "invalid_worker_outcome")
        require(
            len(row["reason_ids"]) <= 1 and bool(row["reason_ids"]) == (old["outcome"] == "suggested"),
            "invalid_worker_candidates",
        )
        row.update(status="ok", error=None, outcome=old.get("outcome"))
    else:
        error = old.get("error")
        row["error"] = error if isinstance(error, str) and error in ERRORS else "invalid_response"
        row["reason_ids"] = []
    require(
        set(row["initial_routed_ids"]) <= set(row["routed_ids"])
        and set(row["available_ids"]) <= set(row["routed_ids"])
        and set(row["reason_ids"]) <= set(row["available_ids"]),
        "invalid_worker_candidates",
    )
    return row


def journal_record(stream, value):
    stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")
    stream.flush()
    os.fsync(stream.fileno())


def execute_slots(setup, projections, invoke, output, *, guard=lambda: None):
    """Internal engine. Public real/synthetic entry points own their separate gates."""
    inputs, _, _ = check_setup(setup)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "setup.json", setup)
    rows = []
    run = setup["run"]
    with (output / "journal.jsonl").open("x", encoding="utf-8") as journal:
        try:
            for entry in run["schedule"]:
                sample = inputs[entry["input_id"]]
                for scheme in entry["schemes"]:
                    guard()
                    slot = len(rows)
                    common = {
                        "slot": slot,
                        "run_id": run["run_id"],
                        "input_id": sample["input_id"],
                        "state_sha256": sample["state_sha256"],
                        "scheme": scheme,
                    }
                    journal_record(journal, {**common, "event": "started", "at": datetime.now(UTC).isoformat()})
                    started = time.perf_counter()
                    response = {}
                    if budget_ok(sample) and sample["material_ready"]:
                        projection = projections[sample["input_id"]]
                        require(
                            hashlib.sha256(projection["state"].encode()).hexdigest() == sample["state_sha256"],
                            "prediction_material_changed",
                        )
                        try:
                            response = invoke(payload_for(projection, sample["input_id"], scheme))
                        except ReplayError as exc:
                            # Only fixed per-attempt errors continue. Isolation/cleanup/hash failures stop the run.
                            require(str(exc) in ERRORS, str(exc))
                            response = {"error": str(exc)}
                    elapsed = (time.perf_counter() - started) * 1000
                    guard()
                    row = prediction(sample, scheme, run, response, elapsed)
                    rows.append(row)
                    journal_record(journal, {**common, "event": "completed", "prediction": row})
        except BaseException:
            journal_record(
                journal,
                {
                    "event": "stopped",
                    "run_id": run["run_id"],
                    "completed_slots": len(rows),
                    "status": "INCONCLUSIVE",
                    "error": "execution_stopped",
                },
            )
            raise
    bundle = {**setup, "predictions": rows}
    result = report(bundle)
    write_json(output / "bundle.json", bundle)
    write_json(output / "report.json", result)
    return result


def execute_synthetic(setup, projections, invoke, output):
    require(setup["manifest"]["mode"] == "synthetic", "synthetic_cannot_execute_real")
    with model_lock():
        return execute_slots(setup, projections, invoke, output)


def execute_real(setup, exposures, environment, output):
    """No externally supplied callable or stub is accepted by the real entry point."""
    from .controlled_environment import probe_controls, runtime_hash
    from .isolation_probe import probe_network
    from .windows_isolation import isolated_profile

    setup = copy.deepcopy(setup)
    require(setup["manifest"]["mode"] == "real", "real_mode_required")
    _, _, differences = check_setup(setup)
    require(len(differences) <= 4, "unstable_labels")
    run = setup["run"]
    require(digest(environment) == run["environment_sha256"], "environment_changed")
    require(environment.get("status") == "VERIFIED_FOR_FREEZE", "environment_not_ready")
    require(environment.get("source_sha256") == run["source_sha256"], "environment_source_changed")
    require(
        runtime_hash(environment["python"], environment["base_python"]) == environment.get("runtime_sha256"),
        "runtime_changed",
    )
    for key in ("outbound", "concurrency", "cancel"):
        evidence = environment.get(key)
        require(isinstance(evidence, dict) and evidence.get("passed") is True, "environment_evidence_missing")
        require(digest(evidence) == run["freeze"][key + "_evidence_sha256"], "environment_evidence_changed")
    expected_source = run["source_sha256"]
    protocol = Path(environment["protocol_path"])

    def guard():
        require(source_hash() == expected_source, "source_changed")
        require(hashlib.sha256(protocol.read_bytes()).hexdigest() == run["protocol_sha256"], "protocol_changed")
        require(
            all(
                timestamp(b["approved_at"]) <= datetime.now(UTC) < timestamp(b["expires_at"])
                for b in setup["manifest"]["admissions"]
            ),
            "admission_expired",
        )

    guard()
    with model_lock():
        command = trusted_command(
            environment["python"],
            "whynote.controlled_worker",
            "--model-dir",
            environment["model_dir"],
            "--device",
            environment["device"],
        )
        roots = [
            Path(__file__).parent.parent,
            Path(environment["python"]).parents[1],
            Path(environment["base_python"]),
            Path(environment["model_dir"]),
        ]
        with isolated_profile(roots, environment["scratch"]) as sandbox:
            require(probe_network(sandbox, environment["python"])["passed"], "outbound_probe_failed")
            concurrency, cancel = probe_controls(sandbox, environment["python"])
            require(concurrency["passed"] and cancel["passed"], "process_controls_failed")

            def invoke(payload):
                raw, _ = sandbox.run(command, payload, timeout=60)
                try:
                    return json.loads(raw)
                except (ValueError, UnicodeError):
                    return {"error": "invalid_response"}

            # Probe the actual model with synthetic text before reading any prediction body.
            probe = {"state": "SYNTHETIC: return one. Answer: two.", "evidence_kinds": ["request", "answer"]}
            smoke = invoke(payload_for(probe, "0" * 64, "C"))
            require(smoke.get("prediction", {}).get("status") == "ok", "isolated_model_unavailable")
            projections = load_projections(setup["manifest"], exposures)
            for sample in setup["manifest"]["samples"]:
                actual = invoke(payload_for(projections[sample["input_id"]], sample["input_id"], "C", "measure"))
                require("measurement" in actual, "budget_measurement_failed")
                require(
                    all(
                        actual["measurement"][key] == sample[key] for key in actual["measurement"] if key != "evidence"
                    ),
                    "budget_measurement_changed",
                )
            run["started_at"] = datetime.now(UTC).isoformat()
            run["run_id"] = digest({k: v for k, v in run.items() if k != "run_id"})
            return execute_slots(setup, projections, invoke, output, guard=guard)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("setup", "exposures", "environment", "output"):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    try:
        result = execute_real(
            json.loads(args.setup.read_bytes()),
            json.loads(args.exposures.read_bytes()),
            json.loads(args.environment.read_bytes()),
            args.output,
        )
    except (ReplayError, OSError, ValueError, KeyError, TypeError):
        print(json.dumps({"status": "INCONCLUSIVE", "error": "controlled_execution_rejected_or_stopped"}))
        return 2
    print(json.dumps({"status": result["status"], "gate_status": result["gate_status"]}))
    return 0 if result["gate_status"] == "PASS_SELF_REVIEW_PILOT" else 1


if __name__ == "__main__":
    raise SystemExit(main())
