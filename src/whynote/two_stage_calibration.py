"""Shared-threshold research selection and exact target-level risk diagnostics.

Scores are not calibrated probabilities of correctness. Search never consumes a
confirmation row. Binomial confirmation requires equal-weight independent units.
"""

import json
import math
from collections import defaultdict
from dataclasses import asdict, replace
from importlib.resources import files

from . import two_stage_v3 as v3
from .two_stage_review import fingerprint, require

VERSION = "jev-threshold-search-v1"
RISKS = ("route", "yes", "no")


def risk_config():
    return json.loads(files("whynote").joinpath("two_stage_risk.json").read_text(encoding="utf-8"))


def binomial_cdf(k, n, p):
    """Finite binomial sum in log space (no normal/Wald approximation)."""
    require(type(n) is int and n >= 0 and type(k) is int and 0 <= p <= 1, "invalid_binomial_arguments")
    if k < 0:
        return 0.0
    if k >= n or p == 0:
        return 1.0
    if p == 1:
        return 0.0
    # The complementary sum avoids cancellation near a small upper tail.
    if k > n * p and n - k - 1 < k:
        return max(0.0, 1.0 - binomial_cdf(n - k - 1, n, 1 - p))
    log_term = n * math.log1p(-p)
    terms = [log_term]
    log_odds = math.log(p) - math.log1p(-p)
    for i in range(k):
        log_term += math.log(n - i) - math.log(i + 1) + log_odds
        terms.append(log_term)
    peak = max(terms)
    return min(1.0, math.exp(peak) * math.fsum(math.exp(t - peak) for t in terms))


def clopper_pearson_upper(errors, n, alpha):
    require(type(n) is int and n >= 0 and type(errors) is int and 0 <= errors <= n, "invalid_error_count")
    require(0 < alpha < 1, "invalid_alpha")
    if n == 0:
        return None
    if errors == n:
        return 1.0
    if errors == 0:
        return -math.expm1(math.log(alpha) / n)
    lo, hi = errors / n, 1.0
    for _ in range(70):
        mid = (lo + hi) / 2
        if binomial_cdf(errors, n, mid) > alpha:
            lo = mid
        else:
            hi = mid
    return hi


def maximum_passing_errors(n, risk_limit, alpha):
    if clopper_pearson_upper(0, n, alpha) is None or clopper_pearson_upper(0, n, alpha) > risk_limit:
        return -1
    lo, hi = 0, n
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        # Inverting the lower-tail exact test is equivalent to the CP upper bound.
        if binomial_cdf(mid, n, risk_limit) <= alpha:
            lo = mid
        else:
            hi = mid
    return lo


def sample_size(
    risk_limit,
    *,
    family_alpha=0.05,
    simultaneous=3,
    true_error=0.05,
    power=0.8,
    available_groups=0,
    available_effective=0,
    maximum_n=10000,
):
    require(0 < risk_limit < 1 and 0 < family_alpha < 1, "invalid_risk_plan")
    require(type(simultaneous) is int and simultaneous > 0, "invalid_simultaneous_risks")
    require(0 <= true_error < risk_limit and 0 < power < 1, "invalid_planning_alternative")
    require(all(type(x) is int and x >= 0 for x in (available_groups, available_effective)), "invalid_available_count")
    require(available_effective <= available_groups, "effective_exceeds_independent_groups")
    alpha = family_alpha / simultaneous
    zero = math.ceil(math.log(alpha) / math.log1p(-risk_limit))
    selected, errors, achieved = None, None, None
    for n in range(zero, maximum_n + 1):
        count = maximum_passing_errors(n, risk_limit, alpha)
        actual_power = binomial_cdf(count, n, true_error)
        if actual_power >= power:
            selected, errors, achieved = n, count, actual_power
            break
    return {
        "risk_limit": risk_limit,
        "family_alpha": family_alpha,
        "per_risk_alpha": alpha,
        "simultaneous_risks": simultaneous,
        "planning_error_rate": true_error,
        "requested_power": power,
        "zero_error_minimum": zero,
        "planned_effective_targets": selected,
        "maximum_passing_errors": errors,
        "achieved_planning_power": achieved,
        "available_independent_groups": available_groups,
        "available_effective_targets": available_effective,
        "effective_shortfall": None if selected is None else max(0, selected - available_effective),
        "independent_group_shortfall_lower_bound": None if selected is None else max(0, selected - available_groups),
        "status": "INSUFFICIENT_EVIDENCE"
        if selected is None or available_effective < selected
        else "PLANNING_CAPACITY_ONLY",
        "stopping_rule": "fix_confirmation_sample_before_labels_no_optional_extension",
        "unit": "one_preselected_target_per_independent_conversation_group_with_corresponding_prediction_event",
    }


def sample_plan(config, *, available_groups=0, effective=None):
    effective = effective or {}
    return {
        risk: sample_size(
            value["limit"],
            family_alpha=config["family_alpha"],
            simultaneous=config["simultaneous_risks"],
            true_error=value["planning_error_rate"],
            power=config["power"],
            available_groups=available_groups,
            available_effective=effective.get(risk, 0),
        )
        for risk, value in config["risks"].items()
    }


def validate_units(records, *, partition, binding, primary_run):
    require(
        all(row["partition"] == partition for row in records), "confirmation_or_diagnostic_data_cannot_enter_search"
    )
    require(len({row["group_id"] for row in records}) == len(records), "one_target_per_independent_group_required")
    require(len({row["target_id"] for row in records}) == len(records), "duplicate_target_runs_do_not_increase_n")
    for row in records:
        require(
            row["runtime"] == binding and row["run_id"] == primary_run, "prediction_condition_or_primary_run_mismatch"
        )
        require(row["input_sha256"] == row["expected_input_sha256"], "prediction_input_mismatch")
        require(row.get("synthetic") is False, "synthetic_predictions_cannot_calibrate")
        if row.get("label"):
            require(row["label"]["input_sha256"] == row["input_sha256"], "label_prediction_input_mismatch")


def evaluate_target(row, policy, catalog):
    prediction = row.get("prediction")
    labels = row.get("label")
    supported_present = (
        sum(r["decision"] == "present" and r.get("expertise") == "qualified" for r in labels["reasons"].values())
        if labels
        else 0
    )
    if not prediction or not prediction.get("routes"):
        budget_abstention = row.get("prediction_status") == "input_budget_limited"
        return {
            "events": {},
            "counterfactual": [],
            "accepted_decisions": 0,
            "present": supported_present,
            "detected": 0,
            "route_missed_present": 0,
            "missing_prediction": not budget_abstention,
            "budget_abstention": budget_abstention,
        }
    routes = v3.select_routes(prediction["routes"], policy)
    same_route = all(r["selected"] == prediction["routes"][axis].get("selected") for axis, r in routes.items())
    events = {}
    concrete = [(axis, result["selected"]) for axis, result in routes.items() if result["status"] == "selected"]
    if concrete:
        outcomes = []
        for axis, selected in concrete:
            label = labels["routes"][axis] if labels else None
            outcomes.append(
                None if not label or label["status"] == "not_reviewed" else selected not in label["accepted"]
            )
        events["route"] = True if True in outcomes else None if None in outcomes else False
    available = set(row["evidence_eligible_reason_ids"])
    raw_answers = {r["reason_id"]: r for r in prediction["reasons"] if r.get("raw_choice") is not None}
    budget_limited = {r["reason_id"] for r in prediction["reasons"] if r.get("status") == "input_budget_limited"}
    collected, counterfactual = defaultdict(list), []
    detected, present, route_missed = 0, 0, 0
    for reason in catalog["reasons"]:
        key = reason["id"]
        reason_label = labels["reasons"].get(key) if labels else None
        truth = reason_label["decision"] if reason_label else "not_reviewed"
        # Human present already asserts adequate evidence, including missed routes.
        is_present = truth == "present" and reason_label.get("expertise") == "qualified"
        present += is_present
        selected = v3.routed(reason, routes)
        if is_present and not selected:
            route_missed += 1
        if not selected or key not in available:
            continue
        if key not in raw_answers:
            if not (same_route and key in budget_limited):
                counterfactual.append(key)
            continue
        raw = raw_answers[key]
        decision = v3.decision(
            {"choice": raw["raw_choice"], "probabilities": raw["probabilities"], "confidence": raw["confidence"]},
            policy,
        )
        if decision == "unknown":
            continue
        if decision == "yes" and is_present:
            detected += 1
        supported = {"present"} if decision == "yes" else {"absent", "out_of_scope"}
        missing = truth == "not_reviewed" or reason_label.get("expertise") != "qualified"
        collected[decision].append(None if missing else truth not in supported)
    for risk, outcomes in collected.items():
        events[risk] = True if True in outcomes else None if None in outcomes else False
    return {
        "events": events,
        "counterfactual": counterfactual,
        "accepted_decisions": int(bool(collected)),
        "present": present,
        "detected": detected,
        "route_missed_present": route_missed,
        "missing_prediction": False,
        "budget_abstention": bool(budget_limited),
    }


def risk_counts(outcomes, limit, alpha, *, binomial_applicable=True):
    n, errors, missing = len(outcomes), sum(x is True for x in outcomes), sum(x is None for x in outcomes)
    worst = clopper_pearson_upper(errors + missing, n, alpha) if binomial_applicable else None
    best = clopper_pearson_upper(errors, n, alpha) if binomial_applicable else None
    zero_minimum = math.ceil(math.log(alpha) / math.log1p(-limit))
    status = (
        "INSUFFICIENT_EVIDENCE"
        if n < zero_minimum or not binomial_applicable
        else "PASS"
        if worst <= limit
        else "FAIL"
        if best > limit
        else "INSUFFICIENT_EVIDENCE"
    )
    return {
        "effective_independent_targets": n,
        "observed_errors": errors,
        "missing_labels": missing,
        "missing_rate": missing / n if n else None,
        "empirical_known_risk": errors / (n - missing) if n > missing else None,
        "worst_case_errors": errors + missing,
        "best_case_upper": best,
        "worst_case_upper": worst,
        "risk_limit": limit,
        "alpha": alpha,
        "status": status,
        "binomial_applicable": binomial_applicable,
        "weighted_denominator_used": False,
    }


def evaluate(records, policy, catalog, config, *, sampling="equal_group_weight"):
    outcomes = [evaluate_target(row, policy, catalog) for row in records]
    alpha = config["family_alpha"] / config["simultaneous_risks"]
    sources = sorted({row["source"] for row in records})
    by_source = {}
    for source in sources:
        selected = [result for row, result in zip(records, outcomes, strict=True) if row["source"] == source]
        by_source[source] = {
            risk: risk_counts(
                [x["events"][risk] for x in selected if risk in x["events"]],
                config["risks"][risk]["limit"],
                alpha / max(1, len(sources)),
            )
            for risk in RISKS
        }
    weighted = sampling != "equal_group_weight" or any(row.get("weight", 1) != 1 for row in records)
    counts = {
        risk: risk_counts(
            [x["events"][risk] for x in outcomes if risk in x["events"]],
            config["risks"][risk]["limit"],
            alpha,
            binomial_applicable=not weighted,
        )
        for risk in RISKS
    }
    counterfactual = [
        {"target_id": row["target_id"], "reason_ids": value["counterfactual"]}
        for row, value in zip(records, outcomes, strict=True)
        if value["counterfactual"]
    ]
    present = sum(x["present"] for x in outcomes)
    per_category = {}
    for reason in catalog["reasons"]:
        key = reason["id"]
        reviewed = sum(
            row.get("label") is not None
            and row["label"]["reasons"].get(key, {}).get("decision", "not_reviewed") != "not_reviewed"
            for row in records
        )
        per_category[key] = {"reviewed_targets": reviewed, "status": "NOT_VALIDATED"}
    return {
        "risks": counts,
        "by_source": by_source,
        "sampling": sampling,
        "total_independent_targets": len(records),
        "reliable_judgment_targets": sum(x["accepted_decisions"] for x in outcomes),
        "judgment_coverage": sum(x["accepted_decisions"] for x in outcomes) / len(records) if records else None,
        "route_coverage": sum("route" in x["events"] for x in outcomes) / len(records) if records else None,
        "counterfactual_not_evaluated": counterfactual,
        "missing_prediction_targets": sum(x["missing_prediction"] for x in outcomes),
        "input_budget_abstained_targets": sum(x["budget_abstention"] for x in outcomes),
        "end_to_end": {
            "human_present_reasons": present,
            "detected_reasons": sum(x["detected"] for x in outcomes),
            "route_missed_present_reasons": sum(x["route_missed_present"] for x in outcomes),
            "recall_on_supported_human_present_only": sum(x["detected"] for x in outcomes) / present
            if present
            else None,
            "independent_denominator_is_not_reason_count": True,
        },
        "per_category": per_category,
    }


def quantile_grid(values, quantiles, baseline):
    values = sorted(set(values))
    result = {baseline}
    if values:
        result.update(values[round(q * (len(values) - 1))] for q in quantiles)
    return sorted(result)


def candidates(records, axis, config):
    probabilities, confidences = [], []
    for row in records:
        prediction = row.get("prediction") or {}
        answers = prediction.get("routes", {}).values() if axis == "route" else prediction.get("reasons", [])
        for answer in answers:
            choice = answer.get("choice", answer.get("raw_choice"))
            if choice in v3.legacy.UNRESOLVED or choice is None or (axis != "route" and choice != axis):
                continue
            probabilities.append(answer["probabilities"][choice])
            confidences.append(answer["confidence"])
    return [
        (p, c)
        for p in quantile_grid(probabilities, config["grid_quantiles"], 0.8)
        for c in [None, *quantile_grid(confidences, config["grid_quantiles"], 0.5)]
    ]


def _search(records, catalog, config, *, sampling):
    policy, curve, chosen = v3.Policy(), [], []
    for axis in config["search_order"]:
        feasible = []
        for probability, confidence in candidates(records, axis, config):
            candidate = replace(policy, **{axis + "_probability": probability, axis + "_confidence": confidence})
            result = evaluate(records, candidate, catalog, config, sampling=sampling)
            risk = result["risks"][axis]
            if sampling == "equal_group_weight":
                passed = risk["status"] == "PASS"
                upper = risk["worst_case_upper"]
            else:
                # A conservative search guard for stratified calibration. No
                # weighted total is passed into an ordinary binomial interval.
                parts = [part[axis] for part in result["by_source"].values()]
                passed = bool(parts) and all(part["status"] == "PASS" for part in parts)
                upper = max((part["worst_case_upper"] or 1 for part in parts), default=1)
            coverage = result["route_coverage"] if axis == "route" else result["judgment_coverage"]
            curve.append(
                {
                    "stage": axis,
                    "probability": probability,
                    "confidence": confidence,
                    "confidence_strategy": "probability_only" if confidence is None else "probability_and_confidence",
                    "risk": risk,
                    "source_risks": {s: r[axis] for s, r in result["by_source"].items()},
                    "coverage": coverage,
                    "counterfactual_targets": len(result["counterfactual_not_evaluated"]),
                    "counterfactual_not_evaluated": result["counterfactual_not_evaluated"],
                    "search_guard_satisfied": passed,
                }
            )
            if passed and not result["counterfactual_not_evaluated"] and not result["missing_prediction_targets"]:
                feasible.append((coverage or 0, -(upper or 0), probability, confidence is None, candidate))
        if not feasible:
            return None, curve, chosen, axis
        policy = max(feasible, key=lambda x: x[:4])[-1]
        chosen.append({"stage": axis, "policy": asdict(policy)})
    return policy, curve, chosen, None


def reliability(records, catalog, policy):
    """Score bins are observed diagnostics, not a fitted probability calibrator."""
    bins = defaultdict(lambda: {"n": 0, "supported": 0, "missing_labels": 0})
    for row in records:
        label = row.get("label")
        prediction = row.get("prediction") or {}
        for reason in prediction.get("reasons", []):
            choice = reason.get("raw_choice")
            if choice not in {"yes", "no"}:
                continue
            p = reason["probabilities"][choice]
            key = f"{choice}:{min(9, int(p * 10)) / 10:.1f}"
            bucket = bins[key]
            bucket["n"] += 1
            reviewed = label["reasons"].get(reason["reason_id"], {}) if label else {}
            truth = reviewed.get("decision", "not_reviewed")
            if truth == "not_reviewed" or reviewed.get("expertise") != "qualified":
                bucket["missing_labels"] += 1
            elif truth in ({"present"} if choice == "yes" else {"absent", "out_of_scope"}):
                bucket["supported"] += 1
    return {
        "bins": dict(bins),
        "unit": "correlated_question_observations_descriptive_only",
        "score_is_correctness_probability": False,
        "probability_calibrator_fitted": False,
    }


def search(records, catalog, binding, primary_run, *, config=None, sampling="source_stratified"):
    config = config or risk_config()
    validate_units(records, partition="calibration", binding=binding, primary_run=primary_run)
    policy, curve, sequence, blocked_axis = _search(records, catalog, config, sampling=sampling)
    folds = []
    for fold in range(config["cv_folds"]):
        held = [
            row
            for row in records
            if int(fingerprint([config["seed"], row["group_id"]])[:8], 16) % config["cv_folds"] == fold
        ]
        held_groups = {row["group_id"] for row in held}
        training = [row for row in records if row["group_id"] not in held_groups]
        selected, _, _, _ = _search(training, catalog, config, sampling=sampling)
        folds.append(
            {
                "fold": fold,
                "training_groups": len(training),
                "held_out_groups": len(held),
                "selected_policy": asdict(selected) if selected else None,
                "held_out": evaluate(held, selected, catalog, config, sampling=sampling) if selected else None,
            }
        )
    return {
        "schema_version": VERSION,
        "status": "CANDIDATE_SELECTED_NOT_CONFIRMED" if policy else "INSUFFICIENT_EVIDENCE",
        "selected_policy": asdict(policy) if policy else None,
        "blocked_axis": blocked_axis,
        "risk_config": config,
        "runtime": binding,
        "primary_run": primary_run,
        "data_sha256": fingerprint(records),
        "search_sequence": sequence,
        "risk_coverage_curve": curve,
        "group_cross_validation": folds,
        "reliability": reliability(records, catalog, policy),
        "quality_status": "NOT_CONFIRMED",
        "production_approved": False,
        "calibration_bounds_are_not_confirmation": True,
        "confirmation_requires_frozen_policy_predictions": True,
        "confirmation_rows_used": 0,
        "sample_plan": sample_plan(
            config,
            available_groups=len(records),
            effective={
                risk: report["effective_independent_targets"]
                for risk, report in evaluate(records, policy or v3.Policy(), catalog, config, sampling=sampling)[
                    "risks"
                ].items()
            },
        ),
    }


def curve_svg(result):
    """A local research figure; absent observations never become invented points."""
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="960" height="350" viewBox="0 0 960 350">',
        '<rect width="960" height="350" fill="white"/>',
        '<g font-family="sans-serif" font-size="12" fill="#243e42">',
        '<text x="24" y="22">Threshold search: risk bounds vs judgment coverage (not confirmation)</text>',
    ]
    for index, axis in enumerate(RISKS):
        left, top, width, height = 50 + index * 315, 65, 255, 220
        parts.append(f'<text x="{left}" y="48">{axis}</text>')
        for tick in range(6):
            y = top + height * (1 - tick / 5)
            parts.append(f'<path d="M{left},{y}h{width}" stroke="#e0e6e5"/>')
            parts.append(f'<text x="{left - 30}" y="{y + 4}">{tick / 5:.1f}</text>')
        parts.append(f'<path d="M{left},{top}v{height}h{width}" fill="none" stroke="#48666a"/>')
        parts.append(f'<text x="{left}" y="305">0</text><text x="{left + width - 10}" y="305">1</text>')
        points = []
        for row in result["risk_coverage_curve"]:
            if row["stage"] != axis or row["coverage"] is None:
                continue
            upper = row["risk"]["worst_case_upper"]
            if upper is None:
                bounds = [r["worst_case_upper"] for r in row["source_risks"].values()]
                upper = max(bounds) if bounds and all(x is not None for x in bounds) else None
            if upper is not None:
                points.append((row["coverage"], upper, row["confidence"] is None))
        for coverage, upper, probability_only in sorted(set(points)):
            color = "#157a86" if probability_only else "#b86b22"
            parts.append(
                f'<circle cx="{left + width * coverage:.2f}" cy="{top + height * (1 - upper):.2f}" r="3.5" fill="{color}" fill-opacity=".65"/>'
            )
        limit = result["risk_config"]["risks"][axis]["limit"]
        y = top + height * (1 - limit)
        parts.append(f'<path d="M{left},{y}h{width}" stroke="#b53737" stroke-dasharray="4 3"/>')
        if not points:
            parts.append(f'<text x="{left + 15}" y="160">NO MATCHING OBSERVATIONS</text>')
    parts.append(
        '<text x="24" y="335">Teal: probability only; orange: + confidence; red: risk limit. Stratified panels use the largest source bound.</text></g></svg>'
    )
    return "".join(parts).encode("utf-8")
