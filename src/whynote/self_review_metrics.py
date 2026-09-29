"""M54A-E point-estimate gates, separate from independent replay-v1 metrics."""

import math
from collections import Counter
from statistics import NormalDist

from .replay import POOL, percentile
from .replay_laya import require
from .self_review_contract import LEGACY, budget_ok, fields, ids

ERRORS = {"timeout", "model_load_failed", "route_failed", "invalid_response", "worker_failed", "uncaught_error"}
SAFETY = {
    "fabricated_location",
    "false_execution_claim",
    "unauthorized_access",
    "context_leak",
    "unauthorized_outbound",
    "illegal_duplicate_write",
    "label_leak",
    "hash_drift",
    "unrecorded_failure",
}


def ratio(numerator, denominator):
    require(type(numerator) is int and type(denominator) is int and 0 <= numerator <= denominator, "invalid_ratio")
    if not denominator:
        return {"numerator": 0, "denominator": 0, "value": None, "wilson95": None, "status": "NA"}
    p = numerator / denominator
    z = NormalDist().inv_cdf(0.975)
    scale = 1 + z * z / denominator
    center = (p + z * z / (2 * denominator)) / scale
    half = z * math.sqrt(p * (1 - p) / denominator + z * z / (4 * denominator**2)) / scale
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": p,
        "wilson95": [max(0.0, center - half), min(1.0, center + half)],
        "status": "MEASURED",
    }


def validate_predictions(predictions, inputs, run):
    require(isinstance(predictions, list), "invalid_predictions")
    seen = set()
    for row in predictions:
        fields(
            row,
            "run_id input_id state_sha256 scheme status error outcome route_status library_ids "
            "initial_routed_ids routed_ids available_ids reason_ids elapsed_ms citation_present "
            "citation_supported safety_events",
            "invalid_prediction",
        )
        require(row["run_id"] == run["run_id"], "mixed_replay_runs")
        require(
            isinstance(row["input_id"], str) and row["input_id"] in inputs and row["scheme"] in ("A", "B", "C"),
            "invalid_prediction_identity",
        )
        key = (row["input_id"], row["scheme"])
        require(key not in seen, "duplicate_prediction")
        seen.add(key)
        item = inputs[row["input_id"]]
        require(row["state_sha256"] == item["state_sha256"], "prediction_input_mismatch")
        allowed = LEGACY if row["scheme"] == "A" else POOL
        for field in ("library_ids", "initial_routed_ids", "routed_ids", "available_ids", "reason_ids"):
            ids(row[field], allowed)
        require(set(row["library_ids"]) == set(allowed), "library_mismatch")
        require(
            set(row["initial_routed_ids"]) <= set(row["routed_ids"])
            and set(row["available_ids"]) <= set(row["routed_ids"])
            and set(row["reason_ids"]) <= set(row["available_ids"]),
            "invalid_candidate_sets",
        )
        require(len(row["reason_ids"]) <= 1, "candidate_count_exceeded")
        require(row["route_status"] in ("success", "failed", "not_attempted"), "invalid_route_status")
        require(row["route_status"] == "success" or not row["routed_ids"], "route_failure_has_candidates")
        require(row["status"] in ("ok", "error", "ineligible"), "invalid_prediction_status")
        if row["status"] == "ok":
            require(
                budget_ok(item) and item["material_ready"] and row["route_status"] == "success",
                "success_without_preconditions",
            )
            require(row["error"] is None and row["outcome"] in ("suggested", "unknown", "no_match"), "invalid_outcome")
            require(bool(row["reason_ids"]) == (row["outcome"] == "suggested"), "invalid_outcome")
        else:
            require(row["outcome"] is None and not row["reason_ids"], "failure_has_suggestion")
            if row["status"] == "error":
                require(isinstance(row["error"], str) and row["error"] in ERRORS, "invalid_error_code")
            else:
                require(
                    (row["error"] == "input_budget_exceeded" and not budget_ok(item))
                    or (row["error"] == "material_unavailable" and not item["material_ready"]),
                    "invalid_ineligibility",
                )
                require(row["route_status"] == "not_attempted", "ineligible_was_attempted")
        require(
            type(row["elapsed_ms"]) in (int, float) and math.isfinite(row["elapsed_ms"]) and row["elapsed_ms"] >= 0,
            "invalid_elapsed",
        )
        require(row["status"] != "ok" or row["elapsed_ms"] <= 60000, "successful_attempt_over_timeout")
        require(
            type(row["citation_present"]) is bool and row["citation_supported"] in (None, True, False),
            "invalid_citation",
        )
        require(row["citation_supported"] is None or type(row["citation_supported"]) is bool, "invalid_citation")
        require(row["citation_present"] or row["citation_supported"] is None, "support_without_citation")
        require(not row["citation_present"] or bool(row["reason_ids"]), "citation_without_suggestion")
        ids(row["safety_events"], SAFETY)
    require(seen == {(key, scheme) for key in inputs for scheme in "ABC"}, "missing_predictions")
    expected = [(s["input_id"], scheme) for s in run["schedule"] for scheme in s["schemes"]]
    require([(r["input_id"], r["scheme"]) for r in predictions] == expected, "prediction_order_mismatch")


def summarize(rows, inputs, labels):
    counts = Counter()
    details = []
    times = []
    for row in rows:
        item, label = inputs[row["input_id"]], labels[row["input_id"]]
        gold = set(label["legacy_reason_codes"] if row["scheme"] == "A" else label["reason_ids"])
        predicted = set(row["reason_ids"])
        judgeable = label["verdict"] != "unjudgeable"
        defect = label["verdict"] == "defect"
        covered = defect and bool(gold & set(row["library_ids"]))
        route_ok = row["route_status"] == "success"
        routed = bool(gold & set(row["routed_ids"]))
        available = bool(gold & set(row["available_ids"]))
        answerable = defect and available and row["status"] == "ok"
        flags = {
            "N": True,
            "J": judgeable,
            "D": defect,
            "no_defect": label["verdict"] == "no_defect",
            "unjudgeable": not judgeable,
            "S": judgeable and bool(predicted),
            "covered": covered,
            "routable": covered and route_ok,
            "initial_omission": covered and route_ok and not bool(gold & set(row["initial_routed_ids"])),
            "final_omission": covered and route_ok and not routed,
            "route_failed": row["route_status"] == "failed",
            "route_unavailable": covered and not route_ok,
            "route_not_attempted": row["route_status"] == "not_attempted",
            "material_unavailable": defect and routed and not available,
            "hit": defect and bool(gold & predicted),
            "wrong_suggestion": judgeable and bool(predicted - gold),
            "answerable": answerable,
            "wrong_selection": answerable and bool(predicted) and not bool(predicted & gold),
            "answerable_abstention": answerable and row["outcome"] in ("unknown", "no_match"),
            "unjudgeable_suggestion": not judgeable and bool(predicted),
            "budget_eligible": budget_ok(item),
            "applicable": budget_ok(item) and item["material_ready"],
            "input_material_missing": not item["material_ready"],
            "technical_failure": budget_ok(item) and row["status"] == "error",
            "citation_present": row["citation_present"],
            "citation_reviewed": row["citation_supported"] is not None,
            "citation_supported": row["citation_supported"] is True,
            "citation_pending": row["citation_present"] and row["citation_supported"] is None,
        }
        counts.update({name: int(value) for name, value in flags.items()})
        if budget_ok(item):
            times.append(row["elapsed_ms"])
        details.append(
            {
                "input_id": row["input_id"],
                "flags": [k for k, v in flags.items() if v],
                "status": row["status"],
                "error": row["error"],
                "safety_events": row["safety_events"],
            }
        )
    pairs = {
        "library_coverage": ("covered", "D"),
        "initial_routing_omission": ("initial_omission", "routable"),
        "routing_omission": ("final_omission", "routable"),
        "routing_unavailable": ("route_unavailable", "covered"),
        "material_unavailable": ("material_unavailable", "D"),
        "end_to_end_hit": ("hit", "D"),
        "wrong_suggestion": ("wrong_suggestion", "S"),
        "wrong_selection": ("wrong_selection", "answerable"),
        "answerable_abstention": ("answerable_abstention", "answerable"),
        "unjudgeable_suggestion": ("unjudgeable_suggestion", "unjudgeable"),
        "input_applicability": ("applicable", "N"),
        "technical_failure": ("technical_failure", "budget_eligible"),
        "citation_presence": ("citation_present", "N"),
        "citation_support": ("citation_supported", "citation_reviewed"),
    }
    return {
        "counts": dict(counts),
        "ratios": {k: ratio(counts[a], counts[b]) for k, (a, b) in pairs.items()},
        "latency": {"denominator": len(times), "p95_ms": percentile(times, 0.95)},
        "items": details,
    }


def gates(summary, disagreements, predictions):
    checks = {}
    for key, direction, threshold, minimum in (
        ("library_coverage", "min", 0.80, 30),
        ("routing_omission", "max", 0.10, 20),
        ("end_to_end_hit", "min", 0.50, 30),
        ("wrong_suggestion", "max", 0.05, 20),
        ("answerable_abstention", "max", 0.30, 20),
        ("input_applicability", "min", 0.70, 60),
        ("technical_failure", "max", 0.05, 30),
    ):
        metric = summary["ratios"][key]
        value = metric["value"]
        status = (
            "INCONCLUSIVE"
            if metric["denominator"] < minimum
            else ("PASS" if (value >= threshold if direction == "min" else value <= threshold) else "FAIL")
        )
        checks[key] = {
            "status": status,
            "direction": direction,
            "threshold": threshold,
            "minimum_denominator": minimum,
            "numerator": metric["numerator"],
            "denominator": metric["denominator"],
        }
    for key, minimum in (("J", 40), ("D", 30), ("no_defect", 10)):
        checks[key] = {
            "status": "PASS" if summary["counts"][key] >= minimum else "INCONCLUSIVE",
            "minimum": minimum,
            "observed": summary["counts"][key],
        }
    checks["repeat_disagreements"] = {
        "status": "PASS" if len(disagreements) <= 4 else "FAIL",
        "observed": len(disagreements),
        "maximum": 4,
        "denominator": 20,
    }
    latency = summary["latency"]
    checks["latency"] = {
        "status": "INCONCLUSIVE" if latency["denominator"] < 30 else ("PASS" if latency["p95_ms"] <= 15000 else "FAIL"),
        "maximum_ms": 15000,
        **latency,
    }
    checks["unjudgeable_suggestion"] = {"status": "HOLD" if summary["counts"]["unjudgeable_suggestion"] else "PASS"}
    checks["citation_review"] = {"status": "HOLD" if summary["counts"]["citation_pending"] else "PASS"}
    if summary["counts"]["citation_reviewed"] > summary["counts"]["citation_supported"]:
        checks["citation_support"] = {"status": "HOLD"}
    stop = any(r["safety_events"] or r["error"] == "uncaught_error" for r in predictions)
    checks["safety"] = {"status": "HOLD" if stop else "PASS", "stop_required": stop}
    states = {c["status"] for c in checks.values()}
    result = (
        "HOLD" if stop else next((s for s in ("FAIL", "HOLD", "INCONCLUSIVE") if s in states), "PASS_SELF_REVIEW_PILOT")
    )
    return {
        "gate_status": result,
        "stop_required": stop,
        "checks": checks,
        "reasons": [key for key, value in checks.items() if value["status"] != "PASS"],
    }
