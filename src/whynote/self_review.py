"""Read-only post-run analysis: python -m whynote.self_review bundle.json report.json."""

import argparse
import json
from pathlib import Path

from .replay import length_bucket, write_json
from .replay_laya import ReplayError, require
from .self_review_contract import (
    fields,
    timestamp,
    validate_batch,
    validate_labels,
    validate_run,
    verify_admitted_files,
)
from .self_review_metrics import gates, ratio, summarize, validate_predictions


def check_setup(setup):
    """Check sealed metadata before running; evidence receipts are not environment probes."""
    fields(setup, "manifest labels run", "invalid_setup")
    manifest, labels, run = (setup[k] for k in ("manifest", "labels", "run"))
    inputs = validate_batch(manifest)
    final, differences = validate_labels(labels, manifest["samples"])
    require(
        all(timestamp(r["labeled_at"]) >= timestamp(manifest["selected_at"]) for r in labels["first"]),
        "label_before_selection",
    )
    validate_run(run, manifest, labels)
    if manifest["mode"] == "real":
        verify_admitted_files(manifest, run, labels)
    return inputs, final, differences


def report(bundle):
    fields(bundle, "manifest labels run predictions", "invalid_bundle")
    inputs, final, differences = check_setup({k: bundle[k] for k in ("manifest", "labels", "run")})
    manifest, run, predictions = (bundle[k] for k in ("manifest", "run", "predictions"))
    validate_predictions(predictions, inputs, run)
    by_scheme = {
        scheme: summarize([r for r in predictions if r["scheme"] == scheme], inputs, final) for scheme in "ABC"
    }
    decision = gates(by_scheme["C"], differences, predictions)
    strata = {}
    for field in ("task", "language", "batch_id", "length_bucket"):
        values = {
            key: length_bucket(s["input_utf8_bytes"]) if field == "length_bucket" else s[field]
            for key, s in inputs.items()
        }
        strata[field] = {
            value: {
                scheme: summarize(
                    [r for r in predictions if r["scheme"] == scheme and values[r["input_id"]] == value], inputs, final
                )
                for scheme in "ABC"
            }
            for value in sorted(set(values.values()))
        }
    synthetic = manifest["mode"] == "synthetic"
    return {
        "schema_version": "m54a-self-review-report-v1",
        "run_id": run["run_id"],
        "scope": "synthetic_accounting_only" if synthetic else "self_reviewed_blind_pilot",
        "status": "SYNTHETIC_VERIFIED" if synthetic else decision["gate_status"],
        "independent_acceptance": False,
        "production_approved": False,
        "repeat_disagreement": {**ratio(len(differences), 20), "items": differences},
        "admission_validation": "metadata_only" if synthetic else "metadata_and_file_hashes",
        "freeze_validation": "not_applicable" if synthetic else "receipt_binding_only",
        "by_scheme": by_scheme,
        "strata": strata,
        **decision,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--check-setup", action="store_true", help="validate manifest/labels/run without predictions")
    args = parser.parse_args()
    try:
        artifact = json.loads(args.bundle.read_bytes())
        if args.check_setup:
            _, _, differences = check_setup(artifact)
            result = {
                "status": "CONTRACT_VALIDATED",
                "execution_authorized": False,
                "environment_probed": False,
                "repeat_disagreements": len(differences),
                "label_stable": len(differences) <= 4,
                "run_id": artifact["run"]["run_id"],
            }
        else:
            result = report(artifact)
        write_json(args.output, result)
    except ReplayError as exc:
        print(json.dumps({"status": "INCONCLUSIVE", "error": str(exc)}))
        return 2
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        print(json.dumps({"status": "INCONCLUSIVE", "error": "invalid_or_unavailable_artifact"}))
        return 2
    if args.check_setup:
        print(json.dumps(result))
        return 0 if result["label_stable"] else 1
    print(json.dumps({"status": result["status"], "gate_status": result["gate_status"]}))
    return 0 if result["gate_status"] == "PASS_SELF_REVIEW_PILOT" else 1


if __name__ == "__main__":
    raise SystemExit(main())
