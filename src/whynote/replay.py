"""Synthetic M5-1 replay: one pinned Laya, three product schemes, no event writes."""

import argparse
import hashlib
import json
import math
import re
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from .laya_local import MODEL, QUESTIONS
from .replay_laya import LayaReplay, ReplayError, require
from .source_mapping import MappingError, digest, pointer_value
from .source_review import checked_file, json_value, local_path, read_bytes, rows_from, run_batch
from .task_reasons import (
    CATALOG_SHA256,
    CRITERIA_VERSION,
    PACKAGE_VERSION,
    TASK_TYPES,
    load_reasons,
    prepare_candidates,
)

VERSION = "m5-replay-v1"
REASONS = load_reasons()
POOL = [r.reason_id for r in REASONS]
DOMAIN = {
    "code": [r.reason_id for r in REASONS if "code_rewrite" in r.task_types],
    "general": [r.reason_id for r in REASONS if "general" in r.task_types],
}
ROUTE_B = {
    "route": {
        "type": "choice",
        "instructions": "Choose the single main domain of the user's request.",
        "criteria": {"code": "Code rewriting or programming", "general": "Other tasks"},
    }
}
ROUTE_C = {
    "route": {
        "type": "choice",
        "instructions": "Choose the task in the user's request, preserving negations.",
        "criteria": {
            "code_rewrite": "Rewrite existing code",
            "general": "General non-code task",
            "mixed": "Code rewrite and another task",
            "other": "Other task",
            "unknown": "Insufficient information to identify the task",
        },
    }
}
DECISIONS = {
    "yes": "The evidence supports this defect",
    "no": "This defect is not supported",
    "unknown": "Insufficient evidence to judge",
}
PROTOCOL = {
    "version": VERSION,
    "model": MODEL,
    "package_version": PACKAGE_VERSION,
    "catalog_sha256": CATALOG_SHA256,
    "criteria_version": CRITERIA_VERSION,
    "seed": 42,
    "candidate_count": 1,
    "scheme_order": ["A", "B", "C"],
    "context": "identical_prediction_only_state",
    "max_bytes": 8192,
    "max_state_tokens": 700,
    "max_head_tokens": 256,
    "max_total_tokens": 1024,
    "timeout_seconds": 60,
    "max_samples": 100,
    "A": QUESTIONS,
    "B": ROUTE_B,
    "C": ROUTE_C,
    "decision_choices": DECISIONS,
    "reason_instructions": {r.reason_id: r.criteria for r in REASONS},
    "fallback": "C general without yes and with original_code: add code once",
    "quality_thresholds": None,
    "purpose": "synthetic_engineering_replay",
}


def state_text(fields):
    return json.dumps(fields, ensure_ascii=False, separators=(",", ":"))


def sample(fields, *, identity, source, task, language, evidence, partition, error=None):
    state = state_text(fields) if fields is not None else None
    return {
        "input_id": digest(identity),
        "identity": identity,
        "source": source,
        "task": task,
        "language": language,
        "partition": partition,
        "evidence_kinds": evidence,
        "state": state,
        "state_sha256": hashlib.sha256(state.encode()).hexdigest() if state else None,
        "input_error": error,
    }


def load_samples(manifest_path):
    manifest_path = Path(manifest_path)
    raw = read_bytes(manifest_path)
    manifest = json_value(raw)
    require(isinstance(manifest, dict), "invalid_replay_manifest")
    require(
        set(manifest)
        == {
            "schema_version",
            "mode",
            "source_batch_path",
            "source_batch_sha256",
            "exploration_path",
            "exploration_sha256",
        },
        "invalid_replay_manifest",
    )
    require(manifest["schema_version"] == VERSION and manifest["mode"] == "synthetic", "mode_not_enabled")
    root = manifest_path.parent
    batch_raw = checked_file(root, manifest, "source_batch")
    batch_path = local_path(root, manifest["source_batch_path"])
    report, sources = run_batch(batch_path)
    # Detect replacement of the manifest between the pinned read and mapper execution.
    require(report["manifest_sha256"] == hashlib.sha256(batch_raw).hexdigest(), "batch_changed")
    configs = {s["source"]: s for s in json_value(batch_raw)["sources"]}
    samples = []
    for source in sources:
        if not source["records"]:
            continue
        config = configs[source["source"]]
        # Recheck the fixed bytes before resolving the mapper's prediction-only references.
        rows = {
            i: row
            for i, row, error in rows_from(checked_file(batch_path.parent, config, "data"), config["format"])
            if error is None
        }
        for record in source["records"]:
            error = None if record["ready_for_replay"] else "source_review_incomplete"
            fields = None
            if not error:
                row = rows[record["row_id"]]
                refs = record["prediction_refs"]

                def resolve(ref, row=row):
                    value = pointer_value(row, ref["pointer"])
                    require(digest(value) == ref["sha256"], "reference_hash_mismatch")
                    return value

                fields = {"context": [resolve(ref) for ref in refs["context"]], "answer": resolve(refs["target"])}
            review = record["review"]
            samples.append(
                sample(
                    fields,
                    identity={
                        k: record[k]
                        for k in (
                            "record_id",
                            "source",
                            "revision",
                            "config",
                            "split",
                            "row_id",
                            "target_key",
                            "target_version",
                            "context_sha256",
                            "prediction_refs",
                            "review",
                        )
                    },
                    source=source["source"],
                    task=review["task"],
                    language=review["context_language"],
                    evidence=["request", "answer"],
                    partition=review["partition"],
                    error=error,
                )
            )
    if manifest["exploration_path"] is not None:
        examples = json_value(checked_file(root, manifest, "exploration"))
        require(
            isinstance(examples, dict) and examples.get("schema_version") == "m5-task-reason-exploration-v1",
            "invalid_exploration",
        )
        require(
            examples.get("review_origin") == "developer_synthetic"
            and examples.get("holdout_status") == "not_established"
            and examples.get("package_version") == PACKAGE_VERSION,
            "invalid_exploration",
        )
        require(isinstance(examples.get("cases"), list), "invalid_exploration")
        names = set()
        for row in examples["cases"]:
            require(isinstance(row, dict), "invalid_exploration")
            name = row.get("case_id")
            require(
                isinstance(name, str) and re.fullmatch(r"[a-z0-9_]{1,64}", name) and name not in names,
                "invalid_case_id",
            )
            names.add(name)
            fields = {
                key: row[key] for key in ("request", "answer", "original_code", "reference") if row.get(key) is not None
            }
            require(
                {"request", "answer"} <= set(fields) and all(isinstance(v, str) and v.strip() for v in fields.values()),
                "invalid_exploration",
            )
            tasks = row.get("task_types")
            require(isinstance(tasks, list) and tasks and all(t in TASK_TYPES for t in tasks), "invalid_exploration")
            task = tasks[0] if len(set(tasks)) == 1 else "mixed"
            samples.append(
                sample(
                    fields,
                    identity={"file_sha256": manifest["exploration_sha256"], "case_id": name},
                    source="task_examples",
                    task=task,
                    language="unknown",
                    evidence=list(fields),
                    partition="exploration",
                )
            )
    else:
        require(manifest["exploration_sha256"] is None, "invalid_replay_manifest")
    require(len(samples) <= PROTOCOL["max_samples"], "invalid_sample_count")
    require(len({s["input_id"] for s in samples}) == len(samples), "duplicate_sample")
    return samples, {
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "input_manifest": manifest,
        "source_report": report,
        "mapping_failures": [
            {
                "source": result["source"],
                "status": result["status"],
                "reason": result["reason"],
                "rejected": result["rejected"],
            }
            for result in sources
        ],
    }


def route_candidates(scheme, route, evidence):
    if scheme == "A":
        ids = list(QUESTIONS["primary_reason"]["criteria"])
        return ids, ids, []
    if scheme == "B":
        require(route in DOMAIN, "invalid_route")
        routed = DOMAIN[route]
        available = [
            r.reason_id for r in REASONS if r.reason_id in routed and set(r.required_evidence) <= set(evidence)
        ]
    else:
        require(scheme == "C" and route in TASK_TYPES, "invalid_route")
        candidates = prepare_candidates([route], evidence)
        routed, available = list(candidates.routed_reason_ids), list(candidates.reason_ids)
    return list(routed), available, [r for r in routed if r not in available]


def infer_scheme(backend, item, scheme):
    result = {k: item[k] for k in ("input_id", "source", "task", "language", "partition", "state_sha256")}
    result |= {
        "scheme": scheme,
        "status": "error",
        "error": None,
        "route": None,
        "fallback_used": False,
        "library_ids": list(QUESTIONS["primary_reason"]["criteria"])[:-1] if scheme == "A" else POOL,
        "routed_ids": [],
        "available_ids": [],
        "unavailable_ids": [],
        "reason_ids": [],
        "outcome": None,
        "stages": [],
        "attribution_source": "model_inferred_unconfirmed"
        if backend.metadata["backend"] == "laya_local"
        else "test_stub",
        "calibrated": False,
    }
    started = time.perf_counter()

    def call(name, questions):
        stage = {
            "stage": name,
            "question_ids": list(questions),
            "questions_sha256": digest(questions),
            "option_order": {k: list(q.get("criteria", {})) for k, q in questions.items()},
        }
        result["stages"].append(stage)
        before = time.perf_counter()
        try:
            reply = backend.predict(item["state"], questions)
            stage.update(reply)
            return reply["answers"]
        except ReplayError as exc:
            stage["error"] = str(exc)
            raise
        finally:
            stage["elapsed_ms"] = round((time.perf_counter() - before) * 1000, 3)

    def decide(stage_name):
        questions = {
            r.reason_id: {"type": "choice", "instructions": r.criteria, "criteria": DECISIONS}
            for r in REASONS
            if r.reason_id in result["available_ids"]
        }
        if not questions:
            return "unknown", []
        answers = call(stage_name, questions)
        yes = [key for key in questions if answers[key]["choice"] == "yes"]
        if yes:
            winner = max(yes, key=lambda k: answers[k]["probabilities"]["yes"])
            return "suggested", [winner]
        if result["unavailable_ids"] or any(a["choice"] == "unknown" for a in answers.values()):
            return "unknown", []
        return "no_match", []

    try:
        require(not item["input_error"], item["input_error"])
        require(len(item["state"].encode("utf-8")) <= 8192, "input_bytes_exceeded")
        if scheme == "A":
            result["routed_ids"], result["available_ids"], result["unavailable_ids"] = route_candidates("A", None, [])
            choice = call("reason", QUESTIONS)["primary_reason"]["choice"]
            outcome, ids = ("unknown", []) if choice == "other_or_unknown" else ("suggested", [choice])
        else:
            route = call("route", ROUTE_B if scheme == "B" else ROUTE_C)["route"]["choice"]
            result["route"] = route
            result["routed_ids"], result["available_ids"], result["unavailable_ids"] = route_candidates(
                scheme, route, item["evidence_kinds"]
            )
            result["initial_routed_ids"] = list(result["routed_ids"])
            outcome, ids = decide("reason")
            if scheme == "C" and route == "general" and not ids and "original_code" in item["evidence_kinds"]:
                result["fallback_used"] = True
                result["routed_ids"], result["available_ids"], result["unavailable_ids"] = route_candidates(
                    "C", "mixed", item["evidence_kinds"]
                )
                outcome, ids = decide("fallback_reason")
        result.update(status="ok", outcome=outcome, reason_ids=ids)
    except ReplayError as exc:
        result["error"] = str(exc)
    result["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
    return result


def percentile(values, fraction):
    if not values:
        return None
    values = sorted(values)
    return values[max(0, math.ceil(fraction * len(values)) - 1)]


def summarize(predictions):
    def count(rows):
        latencies = [r["elapsed_ms"] for r in rows]
        stages = sorted({s["stage"] for r in rows for s in r["stages"]})
        return {
            "attempts": len(rows),
            "valid_outputs": sum(r["status"] == "ok" for r in rows),
            "statuses": dict(Counter(r["error"] if r["status"] == "error" else r["outcome"] for r in rows)),
            "fallbacks": sum(r["fallback_used"] for r in rows),
            "candidate_counts": {
                "routed": sum(len(r["routed_ids"]) for r in rows),
                "available": sum(len(r["available_ids"]) for r in rows),
            },
            "elapsed_ms": {"p50": percentile(latencies, 0.5), "p95": percentile(latencies, 0.95)},
            "stages": {
                stage: {"calls": len(times), "p50_ms": percentile(times, 0.5), "p95_ms": percentile(times, 0.95)}
                for stage in stages
                for times in [[s["elapsed_ms"] for r in rows for s in r["stages"] if s["stage"] == stage]]
            },
            "quality_metrics": None,
        }

    return {
        "by_scheme": {scheme: count([r for r in predictions if r["scheme"] == scheme]) for scheme in ("A", "B", "C")},
        "strata": {
            key: {
                value: {
                    scheme: count([r for r in predictions if r[key] == value and r["scheme"] == scheme])
                    for scheme in ("A", "B", "C")
                }
                for value in sorted({r[key] for r in predictions})
            }
            for key in ("source", "task", "language")
        },
        "quality_metrics": None,
        "user_cost_metrics": None,
    }


def write_json(path, data):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


class UnavailableBackend:
    def __init__(self, code):
        self.metadata = {"backend": "laya_local", "model": MODEL, "load_error": code, "model_loaded": False}
        self.code = code

    def predict(self, state, questions):
        raise ReplayError(self.code)

    def close(self):
        pass


def run_replay(samples, inputs, output, backend):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    run_manifest = {
        "schema_version": VERSION,
        "protocol": PROTOCOL,
        "protocol_sha256": digest(PROTOCOL),
        "backend": backend.metadata,
        "inputs": inputs,
        "samples": [{k: v for k, v in item.items() if k != "state"} for item in samples],
        "quality_labels": None,
        "scope": "synthetic_engineering_only",
        "started_at": datetime.now(UTC).isoformat(),
        "implementation_sha256": {
            name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in (
                "replay.py",
                "replay_laya.py",
                "replay_metrics.py",
                "laya_local.py",
                "laya_manifest.py",
                "source_mapping.py",
                "source_review.py",
                "task_reasons.py",
                "task_reasons.json",
            )
        },
    }
    run_manifest["run_id"] = digest(run_manifest)
    write_json(output / "run-manifest.json", run_manifest)
    predictions = []
    with (output / "predictions.jsonl").open("x", encoding="utf-8") as stream:
        for item in samples:
            for scheme in ("A", "B", "C"):
                result = infer_scheme(backend, item, scheme)
                result["run_id"] = run_manifest["run_id"]
                predictions.append(result)
                stream.write(json.dumps(result, ensure_ascii=False, allow_nan=False) + "\n")
                stream.flush()
    report = summarize(predictions) | {
        "run_id": run_manifest["run_id"],
        "sample_count": len(samples),
        "source_mapping": inputs["source_report"],
        "mapping_failures": inputs.get("mapping_failures", []),
        "backend": backend.metadata,
    }
    write_json(output / "report.json", report)
    for source in ("wildfb", "helpsteer3", "wildfeedback", "task_examples"):
        write_json(output / f"{source}-report.json", summarize([r for r in predictions if r["source"] == source]))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    parser.add_argument("--enable-local-model", action="store_true")
    args = parser.parse_args(argv)
    backend = None
    try:
        require(args.enable_local_model, "model_disabled")
        require(not args.output.exists(), "output_exists")
        samples, inputs = load_samples(args.manifest)
        if not samples:
            backend = UnavailableBackend("no_eligible_samples")
        else:
            try:
                backend = LayaReplay(args.model_dir, device=args.device, enabled=True)
            except ReplayError as exc:
                backend = UnavailableBackend(str(exc))
        report = run_replay(samples, inputs, args.output, backend)
        print(json.dumps({"status": "completed", "run_id": report["run_id"], "samples": len(samples)}))
        return (
            0
            if all(v["valid_outputs"] == len(samples) for v in report["by_scheme"].values())
            and all(s["status"] == "mapped" and not s["split_conflicts"] for s in inputs["source_report"]["sources"])
            else 2
        )
    except (ReplayError, MappingError) as exc:
        print(json.dumps({"status": "rejected", "code": str(exc)}))
        return 1
    except (OSError, TypeError, ValueError, KeyError, RecursionError):
        print(json.dumps({"status": "rejected", "code": "invalid_input_or_io"}))
        return 1
    finally:
        if backend is not None:
            backend.close()


if __name__ == "__main__":
    raise SystemExit(main())
