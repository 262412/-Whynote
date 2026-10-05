"""Read-only diagnostic expectations and evidence checks; never formal quality/gold."""

import json
import re
from collections import Counter

from .window_diagnostics import POLICY, PROMPTS, QUESTION_VERSION, WINDOWS, questions


def label_map(variant, question):
    """Keep original mixed/other separate from explicit C (new code or another task)."""
    labels = questions(variant)[question]["criteria"]
    if variant == "original":
        return {key: key for key in labels}
    if question == "route":
        return {"A": "code_rewrite", "B": "general", "C": "new_code_or_other", "D": "unknown"}
    return {"A": "yes", "B": "no", "C": "unknown"}


def synthetic_expectations(case_id):
    """Content-derived expectations, kept outside prediction material and historical labels.

    Refusing the tea steps also violates the requested instruction. Changing a
    required signature violates both interface preservation and the instruction.
    The number answers and both code answers contain no refusal. Non-code tasks
    have no interface-preservation requirement, so that conjunctive defect is absent.
    """
    short_id = case_id.split("-", 2)[-1] if case_id.startswith("long-") else case_id
    refusal = "yes" if short_id == "refusal_yes" else "no"
    instruction = "yes" if short_id in ("refusal_yes", "instruction_yes", "interface_yes") else "no"
    interface = "yes" if short_id == "interface_yes" else "no"
    if short_id not in {
        f"{name}_{value}" for name in ("refusal", "instruction", "interface") for value in ("yes", "no")
    }:
        raise ValueError("unknown_synthetic_case")
    return {
        "route": "code_rewrite" if short_id.startswith("interface_") else "general",
        "general.unnecessary_refusal": refusal,
        "general.instruction_not_followed": instruction,
        "code.interface_changed": interface,
    }


def diagnose(expected, answers, variant):
    """Missing expectation and model abstention are independent, non-passing fields."""
    fields = {}
    for key in ("route", *PROMPTS):
        mapping = label_map(variant, key)
        wanted = expected.get(key)
        if wanted is not None and wanted not in set(mapping.values()) - {"unknown"}:
            raise ValueError("invalid_expectation")
        answer = answers.get(key, {})
        choice = answer.get("choice")
        predicted = mapping.get(choice)
        if choice is None:
            outcome = "missing_result"
        elif predicted is None:
            outcome = "unknown_label"
        elif predicted == "unknown":
            outcome = "insufficient_evidence"
        elif wanted is None:
            outcome = "unset_expectation"
        elif predicted == wanted:
            outcome = "matches"
        elif key == "route":
            outcome = "route_mismatch"
        else:
            outcome = "false_positive" if wanted == "no" else "false_negative"
        fields[key] = {"expected": wanted, "predicted": predicted, "outcome": outcome}
    if set(expected) - fields.keys() or set(answers) - fields.keys():
        raise ValueError("unknown_question")
    return fields


def audit_history(directory, synthetic_cases):
    """Read exactly four metadata files. No source paths, databases or body recovery."""
    schedule = json.loads((directory / "schedule.json").read_text(encoding="utf-8"))
    journal = [json.loads(line) for line in (directory / "journal.jsonl").read_text(encoding="utf-8").splitlines()]
    reconciliation = json.loads((directory / "hash-reconciliation.json").read_text(encoding="utf-8"))
    summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    errors = []

    def check(condition, error):
        if not condition:
            errors.append(error)

    def indexed(rows, name):
        result = {}
        for row in rows:
            index = row["index"]
            check(type(index) is int and 0 <= index < len(schedule), f"{name}:invalid_index")
            check(index not in result, f"{name}:duplicate_index:{index}")
            result[index] = row
        check(set(result) == set(range(len(schedule))), f"{name}:missing_or_extra_index")
        check([r["index"] for r in rows] == list(range(len(schedule))), f"{name}:order_or_count")
        return result

    check(bool(schedule), "schedule:empty")
    identities = [(c["case_id"], c["variant"], c["window"]) for c in schedule]
    check(len(set(identities)) == len(identities), "schedule:duplicate_request")
    kinds = [row.get("event", row.get("kind")) for row in journal]
    check(kinds == ["start", "ready"] + ["started", "result"] * len(schedule) + ["completed"], "journal:event_order")
    starts = [r for r in journal if r.get("event") == "start"]
    ready = [r for r in journal if r.get("kind") == "ready"]
    ends = [r for r in journal if r.get("event") == "completed"]
    check(len(starts) == len(ready) == len(ends) == 1, "journal:lifecycle_count")
    if len(starts) == len(ready) == len(ends) == 1:
        start, loaded, end = starts[0], ready[0], ends[0]
        check(start["planned"] == end["count"] == len(schedule), "journal:planned_completed_count")
        check(start["policy"] == POLICY and start["question_version"] == QUESTION_VERSION, "journal:version")
        check(start["source_sha256"] == loaded["source_sha256"] == summary["source_sha256"], "journal:source")
        check(start["runtime_sha256"] == summary["runtime_sha256"], "journal:runtime")
        check(end["cleanup_verified"] is True and summary["cleanup_verified"] is True, "journal:cleanup")
    started = indexed([r for r in journal if r.get("event") == "started"], "started")
    results = indexed([r for r in journal if r.get("event") == "result"], "result")
    hashes = indexed(reconciliation["records"], "reconciliation")
    check(reconciliation["verified_count"] == len(schedule), "reconciliation:count")
    known = {case["case_id"]: case for case in synthetic_cases}
    groups = {}
    case_identities = {}
    completed = encoded = hash_matched = fallbacks = 0
    historical_groups = Counter()
    for index, case in enumerate(schedule):
        case_id, variant, window = case["case_id"], case["variant"], case["window"]
        check(window in WINDOWS, f"schedule:invalid_window:{index}")
        for name, rows in (("started", started), ("result", results)):
            if index in rows:
                check(rows[index]["case"] == case, f"{name}:identity:{index}")
        result = results.get(index, {}).get("result", {})
        complete = bool(result) and not result.get("error")
        completed += complete
        encoded += result.get("full_encoding_verified") is True
        fallbacks += result.get("cpu_fallback_count", 0)
        check(not complete or result.get("full_encoding_verified") is True, f"encoding:unverified:{index}")
        digest = hashes.get(index)
        if digest:
            check(digest["case_id"] == case_id, f"reconciliation:identity:{index}")
            legacy, utf8 = digest["legacy_schedule_json_string_sha256"], digest["state_utf8_sha256"]
            check(
                all(isinstance(v, str) and re.fullmatch(r"[0-9a-f]{64}", v) for v in (legacy, utf8)),
                f"hash:format:{index}",
            )
            check(case["state_sha256"] == legacy, f"hash:legacy_schedule:{index}")
            # This is a consistency check of saved metadata, not a fresh reconstruction.
            check(digest["reconstructed_input_matches_worker"] is True, f"hash:historical_reconstruction:{index}")
            matched = complete and result.get("state_sha256") == utf8
            hash_matched += matched
            check(not complete or matched, f"hash:worker_utf8:{index}")
        identity = (
            case.get("input_id"),
            case["state_sha256"],
            digest.get("state_utf8_sha256") if digest else None,
            json.dumps(case["expected"], sort_keys=True),
            case.get("label_source"),
        )
        check(case_identities.setdefault(case_id, identity) == identity, f"schedule:case_changed:{case_id}")
        if case_id in known:
            cohort = "synthetic_long" if case_id.startswith("long-") else "synthetic_short"
            check(case["expected"] == known[case_id]["expected"], f"schedule:designated_expectation:{index}")
            check("input_id" not in case, f"schedule:synthetic_input_id:{index}")
            if cohort == "synthetic_long":
                check(window == known[case_id]["window"] and variant == "explicit", f"schedule:long_probe:{index}")
            expected = synthetic_expectations(case_id)
            source = "synthetic_diagnostic_not_gold"
        else:
            check(bool(case.get("input_id")), f"schedule:unknown_case:{index}")
            if case.get("label_source") == "prior_exploratory_review_not_gold":
                cohort, source = "real_short", case["label_source"]
                expected = case["expected"]
            else:
                cohort, source, expected = "real_long", "unlabelled", {}
                check(case["expected"] == {} and "label_source" not in case, f"schedule:unapproved_labels:{index}")
        fields = diagnose(expected, result.get("answers", {}) if complete else {}, variant)
        for key, field in fields.items():
            check(field["outcome"] != "unknown_label", f"answer:unknown_label:{index}:{key}")
        group_key = (case_id, variant)
        group = groups.setdefault(
            group_key,
            {
                "case_id": case_id,
                "variant": variant,
                "cohort": cohort,
                "label_source": source,
                "input_id": case.get("input_id"),
                "quality": "NA",
                "requests": [],
            },
        )
        group["requests"].append({"index": index, "window": window, "fields": fields})
        primary = case["expected"]
        designated = bool(primary) and all(fields[k]["outcome"] == "matches" for k in primary)
        group.setdefault("designated_matches", []).append(designated)
        check(group["input_id"] == case.get("input_id"), f"schedule:case_input_changed:{index}")
        if cohort != "real_short":
            historical_groups[f"{window}-{'short' if cohort == 'synthetic_short' else 'long'}"] += 1
    # Case-level counts require every repeated window to match; one correct answer cannot offset another.
    for group in groups.values():
        signatures = {json.dumps(r["fields"], sort_keys=True) for r in group["requests"]}
        group["cross_window_consistent"] = len(signatures) == 1
        group["designated_matches_all_windows"] = all(group.pop("designated_matches"))
        outcomes = [f["outcome"] for r in group["requests"] for f in r["fields"].values()]
        group["diagnostic_status"] = (
            "diagnostic_failure"
            if any(o in {"false_positive", "false_negative", "route_mismatch", "unknown_label"} for o in outcomes)
            else "incomplete"
            if any(o != "matches" for o in outcomes)
            else "expectations_met"
        )
    designated_short = {}
    for variant in ("original", "explicit"):
        short = [g for g in groups.values() if g["cohort"] == "synthetic_short" and g["variant"] == variant]
        designated_short[variant] = {
            "matches": sum(g["designated_matches_all_windows"] for g in short),
            "cases": len(short),
        }
        old = summary[f"{variant}_short_pair_primary"]
        check(
            old["correct"] == designated_short[variant]["matches"] and old["total"] == len(short),
            f"summary:designated:{variant}",
        )
        check(old["formal_accuracy"] is None, f"summary:formal_accuracy:{variant}")
    for key, value in (
        ("planned_calls", len(schedule)),
        ("completed_calls", completed),
        ("full_encoding_verified", encoded),
        ("cpu_fallbacks", fallbacks),
    ):
        check(summary[key] == value, f"summary:{key}")
    check({k: v["calls"] for k, v in summary["groups"].items()} == dict(historical_groups), "summary:group_calls")
    cohort_summary = {}
    for cohort in ("synthetic_short", "synthetic_long", "real_short", "real_long"):
        selected = [g for g in groups.values() if g["cohort"] == cohort]
        fields = [f for g in selected for r in g["requests"] for f in r["fields"].values()]
        cohort_summary[cohort] = {
            "unique_cases": len({g["case_id"] for g in selected}),
            "case_variants": len(selected),
            "requests": sum(len(g["requests"]) for g in selected),
            "field_outcomes_per_request": dict(Counter(f["outcome"] for f in fields)),
            "unset_expectation_fields": sum(f["expected"] is None for f in fields),
            "case_variant_status": dict(Counter(g["diagnostic_status"] for g in selected)),
        }
    return {
        "scope": "offline_diagnostic_not_formal_quality_or_adoption",
        "quality": "NA",
        "integrity_errors": errors,
        "requests": {
            "planned": len(schedule),
            "completed": completed,
            "incomplete": len(schedule) - completed,
            "encoding_verified_in_history": encoded,
            "hash_metadata_matched": hash_matched,
        },
        "designated_synthetic_short": designated_short,
        "cohorts": cohort_summary,
        "cases": list(groups.values()),
    }


def audit_exit_code(report):
    if report["integrity_errors"]:
        return 2
    return int(any(g["diagnostic_status"] != "expectations_met" for g in report["cases"]))
