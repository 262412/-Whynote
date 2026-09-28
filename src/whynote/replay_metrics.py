"""Adjudicated-label accounting, separate from replay inputs and model calls."""

import uuid

from .laya_local import QUESTIONS
from .replay import MODEL, POOL, PROTOCOL, VERSION, route_candidates
from .replay_laya import require
from .source_mapping import digest
from .task_reasons import TASK_TYPES


def quality_metrics(predictions, samples, labels, *, run_manifest=None):
    """No label file means no quality claim; synthetic CLI never supplies labels."""
    if labels is None:
        return None
    require(isinstance(run_manifest, dict), "missing_run_manifest")
    require(
        run_manifest.get("schema_version") == VERSION
        and run_manifest.get("protocol") == PROTOCOL
        and run_manifest.get("protocol_sha256") == digest(PROTOCOL),
        "run_protocol_mismatch",
    )
    expected_run = digest({k: v for k, v in run_manifest.items() if k != "run_id"})
    require(run_manifest.get("run_id") == expected_run, "run_manifest_mismatch")
    require(
        run_manifest.get("samples") == [{k: v for k, v in s.items() if k != "state"} for s in samples],
        "run_samples_mismatch",
    )
    backend = run_manifest.get("backend", {})
    require(backend.get("backend") == "laya_local" and backend.get("model") == MODEL, "not_pinned_model_run")
    require(isinstance(labels, dict) and set(labels) == {"schema_version", "items"}, "invalid_labels")
    require(labels["schema_version"] == "m5-replay-labels-v1" and isinstance(labels["items"], list), "invalid_labels")
    inputs = {s["input_id"]: s for s in samples}
    by_id = {}
    for label in labels["items"]:
        require(
            isinstance(label, dict)
            and set(label)
            == {
                "input_id",
                "review_origin",
                "reviewer_refs",
                "adjudication_ref",
                "partition",
                "verdict",
                "reason_ids",
                "legacy_reason_codes",
                "reference_task",
                "reference_domain",
            },
            "invalid_labels",
        )
        key = label["input_id"]
        require(isinstance(key, str) and key in inputs and key not in by_id, "invalid_label_identity")
        require(
            label["review_origin"] == "independent_adjudicated"
            and label["partition"] == "holdout"
            and inputs[key]["partition"] == "holdout",
            "labels_not_independent_holdout",
        )
        reviewers = label["reviewer_refs"]
        require(
            isinstance(reviewers, list)
            and len(reviewers) == 2
            and all(isinstance(v, str) for v in reviewers)
            and len(set(reviewers)) == 2
            and isinstance(label["adjudication_ref"], str),
            "invalid_review_provenance",
        )
        try:
            for value in reviewers + [label["adjudication_ref"]]:
                require(str(uuid.UUID(value)) == value, "invalid_review_provenance")
        except (ValueError, AttributeError):
            require(False, "invalid_review_provenance")
        require(label["verdict"] in ("defect", "no_defect", "unjudgeable"), "invalid_verdict")
        for field, allowed in (
            ("reason_ids", POOL),
            ("legacy_reason_codes", list(QUESTIONS["primary_reason"]["criteria"])[:-1]),
        ):
            values = label[field]
            require(
                isinstance(values, list)
                and all(isinstance(v, str) for v in values)
                and len(values) == len(set(values))
                and set(values) <= set(allowed),
                "invalid_label_reasons",
            )
            require(label["verdict"] == "defect" or not values, "invalid_label_reasons")
        require(
            label["reference_task"] in TASK_TYPES and label["reference_domain"] in ("code", "general"),
            "invalid_reference_route",
        )
        by_id[key] = label
    require(
        set(by_id) == {key for key, value in inputs.items() if value["partition"] == "holdout"},
        "incomplete_holdout_labels",
    )
    seen = set()
    for row in predictions:
        require(row.get("run_id") == expected_run, "mixed_replay_runs")
        key = (row["input_id"], row["scheme"])
        require(
            row["input_id"] in inputs and row["scheme"] in ("A", "B", "C") and key not in seen,
            "invalid_prediction_identity",
        )
        seen.add(key)
        require(row.get("state_sha256") == inputs[row["input_id"]]["state_sha256"], "prediction_input_mismatch")
    require(seen == {(key, scheme) for key in inputs for scheme in ("A", "B", "C")}, "missing_predictions")

    def ratio(numerator, denominator):
        return {
            "numerator": numerator,
            "denominator": denominator,
            "value": numerator / denominator if denominator else None,
        }

    results = {}
    for scheme in ("A", "B", "C"):
        defect = covered = omitted = material = hit = errors = judged = misleading = suggestions = 0
        unjudgeable = unlabeled = runtime_errors = abstained = reference_covered = routing_unavailable = 0
        initially_omitted = 0
        for row in (p for p in predictions if p["scheme"] == scheme):
            label = by_id.get(row["input_id"])
            if label is None:
                unlabeled += 1
                continue
            if label["verdict"] == "unjudgeable":
                unjudgeable += 1
                continue
            gold = set(label["legacy_reason_codes" if scheme == "A" else "reason_ids"])
            predicted = set(row["reason_ids"])
            if predicted:
                suggestions += 1
                misleading += bool(predicted - gold)
            runtime_errors += row["status"] != "ok"
            abstained += row["status"] == "ok" and row["outcome"] in ("unknown", "no_match")
            if label["verdict"] != "defect":
                continue
            defect += 1
            library_hit = bool(gold & set(row["library_ids"]))
            routed_hit = bool(gold & set(row["routed_ids"]))
            available_hit = bool(gold & set(row["available_ids"]))
            covered += library_hit
            # Failed route calls have no route to judge; do not turn runtime errors into routing omissions.
            route_observed = scheme == "A" or row["route"] is not None
            routing_unavailable += library_hit and not route_observed
            initially_omitted += (
                library_hit
                and route_observed
                and not bool(gold & set(row.get("initial_routed_ids", row["routed_ids"])))
            )
            omitted += library_hit and route_observed and not routed_hit
            material += routed_hit and not available_hit
            hit += bool(predicted & gold)
            if available_hit and row["status"] == "ok":
                judged += 1
                errors += not bool(predicted & gold)
            reference = None if scheme == "A" else label["reference_domain" if scheme == "B" else "reference_task"]
            reference_ids, _, _ = route_candidates(scheme, reference, inputs[row["input_id"]]["evidence_kinds"])
            reference_covered += bool(gold & set(reference_ids))
        results[scheme] = {
            "library_coverage": ratio(covered, defect),
            "routing_omission": ratio(omitted, covered),
            "material_unavailable": ratio(material, defect),
            "candidate_hit": ratio(hit, defect),
            "judgment_error": ratio(errors, judged),
            "misleading": ratio(misleading, suggestions),
            "reference_route_coverage": ratio(reference_covered, defect),
            "initial_routing_omission": ratio(initially_omitted, covered),
            "routing_unavailable": routing_unavailable,
            "runtime_errors": runtime_errors,
            "abstained": abstained,
            "unjudgeable": unjudgeable,
            "unlabeled": unlabeled,
        }
    return results
