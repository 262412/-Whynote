"""Read-only historical derivation, sensitivity and admitted input material audit."""

import hashlib
import sqlite3
from collections import Counter
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path

from . import two_stage as core
from . import two_stage_batch as batch
from . import two_stage_materials as materials

VERSION = "jev-offline-derivation-v1"
ASSESSMENTS = ("candidate_found", "no_issue_detected_in_evaluated_scope", "abstained", "not_evaluated")
OLD_CODE_FILES = set(batch.CODE_FILES) - {"src/whynote/two_stage_materials.py", "src/whynote/two_stage_offline.py"}


def fingerprint(path):
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return {"path": str(path), "sha256": digest.hexdigest(), "bytes": size}


def reason_rows(rows, policy):
    """Re-decide only saved answers; never touch not_asked or the raw fields."""
    result = deepcopy(rows)
    for row in result:
        if row["decision"] == "not_asked":
            continue
        answer = {"choice": row["raw_choice"], **row}
        accepted = core._accepted(answer, policy.reason_probability, policy.reason_confidence)
        row["decision"] = row["raw_choice"] if accepted else "unknown"
        row["status"] = (
            "below_threshold_or_tied"
            if not accepted
            else "model_unknown"
            if row["raw_choice"] == "unknown"
            else "evaluated"
        )
    return result


def historical_evidence(record):
    return {
        "request",
        "answer",
        *(key for key, value in record.get("materials", {}).items() if value["status"] == "extracted"),
    }


def validate_history(plan, records, source_plan, summary, ledger_raw):
    """Verify identity, saved output/ledger agreement and baseline arithmetic."""
    batch.require(plan["schema_version"] in {"jev-two-stage-batch-v1", batch.VERSION}, "unsupported_source_version")
    runtime = plan["runtime"]
    batch.require(
        summary["mode"] == "live" and summary["synthetic"] is False and summary["runtime"] == runtime,
        "source_summary_identity_changed",
    )
    batch.require(runtime["contract_version"] in {"jev-two-stage-v1", core.VERSION}, "unsupported_source_contract")
    if runtime["contract_version"] == core.VERSION:
        batch.require(
            plan.get("input_version") == runtime.get("input_version") == batch.INPUT_VERSION
            and runtime.get("material_rule_version") == materials.VERSION,
            "unsupported_source_input_version",
        )
    else:
        batch.require(
            plan["schema_version"] == "jev-two-stage-batch-v1" and "input_version" not in plan,
            "unsupported_source_input_version",
        )
    batch.require(
        runtime["catalog_version"] == core.CATALOG_VERSION
        and runtime["catalog_sha256"] == core.CATALOG_SHA256
        and runtime["model"] == batch.MODEL,
        "source_catalog_or_model_changed",
    )
    batch.require(
        plan["source_plan_sha256"] == batch.PLAN_SHA256
        and plan["input_pins"] == source_plan["input_pins"]
        and plan["sources"] == source_plan["sources"]
        and plan["outbound_sha256"] == source_plan["db_sha256"]
        and plan["seed"] == 42
        and plan["source_batch"] == "batch-1000-final",
        "source_input_identity_changed",
    )
    batch.require(runtime["code_sha256"] == batch.sha(batch.encode(runtime["code_pins"])), "source_code_pins_invalid")
    expected_files = OLD_CODE_FILES if runtime["contract_version"] == "jev-two-stage-v1" else set(batch.CODE_FILES)
    batch.require(set(runtime["code_pins"]) == expected_files, "source_code_pin_set_changed")
    originals = {row["target_id"]: row for row in source_plan["targets"]}
    targets = {row["target_id"]: row for row in plan["targets"]}
    batch.require(
        len(targets) == len(plan["targets"]) == len(records) == len(originals)
        and {row["target_id"] for row in records} == set(targets) == set(originals),
        "source_target_set_changed",
    )
    policy = core.Policy(**plan["config"]["policy"])
    policy.validate()
    catalog = core.load_catalog()
    db = sqlite3.connect(":memory:")
    try:
        db.deserialize(ledger_raw)
        db.execute("PRAGMA query_only=ON")
        batch.require(db.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "source_ledger_invalid")
        identity = db.execute("SELECT value FROM meta WHERE key='identity'").fetchone()
        batch.require(
            identity is not None
            and batch.decode(identity[0]) == {"plan_sha256": batch.sha(batch.encode(plan)), "mode": "live"},
            "source_ledger_identity_changed",
        )
        ledger = {row[0]: row[1:] for row in db.execute("SELECT id,status,result FROM targets")}
        ledger_stages = {}
        for target_id, name, ref, status, held, estimate, metadata in db.execute(
            "SELECT target,name,ref,status,held,estimate,metadata FROM stages ORDER BY CASE name WHEN 'route' THEN 0 ELSE 1 END"
        ):
            ledger_stages.setdefault(target_id, []).append(
                batch.decode(metadata)
                | {
                    "stage": name,
                    "status": status,
                    "budget_reservation_ref": ref,
                    "held_reservation": held,
                    "known_usage_estimate": estimate,
                }
            )
        for record in records:
            key = record["target_id"]
            target, original = targets[key], originals[key]
            batch.require(all(record[k] == value for k, value in target.items()), "source_result_target_changed")
            batch.require(
                all(target[k] == original[k] for k in ("source", "group_id", "expires_at", "original_state_sha256")),
                "source_target_identity_changed",
            )
            batch.require(
                record["batch_id"] == plan["batch_id"] == summary["batch_id"]
                and record["mode"] == "live"
                and record["synthetic"] is False
                and record["schema_version"] == plan["schema_version"]
                and record["policy"] == asdict(policy)
                and record["contract_version"] == runtime["contract_version"]
                and record["catalog_sha256"] == core.CATALOG_SHA256,
                "source_result_version_changed",
            )
            if not original["eligible"]:
                batch.require(
                    record["technical_status"] == "excluded"
                    and target["disposition"] == "original_exclusion"
                    and target["exclusion_code"] == original["exclusion_code"],
                    "source_exclusion_changed",
                )
            if target["disposition"] == "runnable":
                status, result = ledger[key]
                batch.require(
                    record["technical_status"] == ("reconcile" if status == "running" else status)
                    and record["result"] == (batch.decode(result) if result else None),
                    "source_ledger_result_changed",
                )
            else:
                batch.require(key not in ledger and record["result"] is None, "excluded_target_has_result")
            result = record["result"]
            batch.require(record["stages"] == ledger_stages.get(key, []), "source_ledger_stages_changed")
            route_stage = next(
                (stage for stage in record["stages"] if stage["stage"] == "route" and stage["status"] == "completed"),
                None,
            )
            routes = None
            if route_stage is not None:
                route_answers, _ = core.validate_response(
                    core._encode(
                        {
                            "model": batch.MODEL,
                            "answers": {
                                name: {"type": "choice", **value} for name, value in route_stage["answers"].items()
                            },
                            "usage": route_stage["usage"],
                        }
                    ),
                    core.route_questions(catalog),
                )
                routes = core.select_routes(route_answers, policy)
            batch.require(routes == record["routes"], "source_route_baseline_mismatch")
            if record["technical_status"] != "success":
                batch.require(result is None, "non_success_has_prediction")
                continue
            batch.require(
                result["schema_version"] == runtime["contract_version"]
                and result["policy"] == asdict(policy)
                and result["catalog_sha256"] == core.CATALOG_SHA256,
                "source_prediction_identity_changed",
            )
            batch.require(routes == result["routes"], "source_route_baseline_mismatch")
            expected_rows, questions = core.prepare_reasons(catalog, routes, historical_evidence(record))
            batch.require(
                [row["reason_id"] for row in result["reasons"]] == [row["id"] for row in catalog["reasons"]],
                "source_reason_catalog_changed",
            )
            stage = next(s for s in result["stages"] if s["stage"] == "reasons")
            # Reuse the unchanged native response validator for saved typed values.
            answers, _ = core.validate_response(
                core._encode(
                    {
                        "model": batch.MODEL,
                        "answers": {k: {"type": "choice", **v} for k, v in stage["answers"].items()},
                        "usage": stage["usage"],
                    }
                ),
                questions,
            )
            reproduced = core._result(catalog, policy, routes, expected_rows, answers, [])
            batch.require(
                reproduced["reasons"] == result["reasons"]
                and reproduced["suggestions"] == result["suggestions"]
                and reproduced["outcome"] == result["outcome"],
                "source_reason_baseline_mismatch",
            )
        counts = Counter(row["technical_status"] for row in records)
        batch.require(
            counts["success"] == summary["technical_success"]
            and counts["error"] == summary["technical_error"]
            and counts["reconcile"] == summary["reconcile"]
            and counts["pending"] == summary["not_executed"]
            and len(records) == summary["original_targets"],
            "source_summary_mismatch",
        )
    finally:
        db.close()
    return policy


def aggregate(records, policy):
    assessments, outcomes, decisions, raw, rejected, coverage, missing, cause_counts = (Counter() for _ in range(8))
    asked_ids, histogram, unknown_masks = set(), Counter(), Counter()
    candidate_reasons = 0
    for record in records:
        if record["technical_status"] != "success":
            continue
        result = record["result"]
        rows = reason_rows(result["reasons"], policy)
        view = core.summarize_assessment(result["routes"], rows, policy)
        assessments[view["assessment"]] += 1
        outcomes[result["outcome"]] += 1
        c = view["coverage"]
        route_key = "route_complete" if c["route_complete"] else "route_partial"
        coverage[route_key] += 1
        coverage[view["assessment"] + ":" + route_key] += 1
        for name in (
            "asked_reason_count",
            "missing_evidence_reason_count",
            "route_not_selected_count",
            "abstention_count",
        ):
            coverage[name] += c[name]
        cause_counts.update(c["abstention_causes"])
        missing.update(c["missing_evidence_types"].keys())
        histogram[c["asked_reason_count"]] += 1
        if result["outcome"] == "unknown":
            unknown_masks[f"{route_key}:" + ("has_abstention" if c["abstention_count"] else "all_asked_no")] += 1
        for row in rows:
            decisions[row["decision"]] += 1
            if row["decision"] != "not_asked":
                asked_ids.add(row["reason_id"])
                raw[row["raw_choice"]] += 1
                if row["decision"] == "unknown":
                    rejected[row["raw_choice"]] += 1
            candidate_reasons += row["decision"] == "yes"
    return {
        "policy": asdict(policy),
        "assessments": {name: assessments[name] for name in ASSESSMENTS},
        "legacy_outcomes": dict(outcomes),
        "decisions": dict(decisions),
        "raw_choices": dict(raw),
        "rejected_raw_choices": dict(rejected),
        "candidate_targets": assessments["candidate_found"],
        "candidate_reasons": candidate_reasons,
        "coverage": dict(coverage),
        "missing_material_targets": dict(missing),
        "abstention_causes_overlapping": dict(cause_counts),
        "asked_reason_ids": sorted(asked_ids),
        "asked_count_histogram": dict(histogram),
        "legacy_unknown_breakdown": dict(unknown_masks),
    }


def sensitivity(records, policy):
    reason_scenarios = [("baseline", policy)] + [
        (f"{name}={value}", replace(policy, **{name: value}))
        for name, values in (("reason_probability", (0.7, 0.9)), ("reason_confidence", (0.3, 0.7)))
        for value in values
    ]
    reason_summary, rejections = {}, []
    for name, candidate in reason_scenarios:
        reason_summary[name] = aggregate(records, candidate)
        for record in records:
            if record["technical_status"] == "success":
                rows = reason_rows(record["result"]["reasons"], candidate)
                rejected = {
                    choice: [r["reason_id"] for r in rows if r["raw_choice"] == choice and r["decision"] == "unknown"]
                    for choice in ("yes", "no")
                }
                if any(rejected.values()):
                    rejections.append({"scenario": name, "target_id": record["target_id"], "rejected": rejected})
    catalog = core.load_catalog()
    route_summary, route_changes = {}, []
    for axis in ("task", "domain"):
        for name, candidate in [("baseline", policy)] + [
            (f"{field}={value}", replace(policy, **{field: value}))
            for field, values in (("route_probability", (0.7, 0.9)), ("route_confidence", (0.3, 0.7)))
            for value in values
        ]:
            counts, additional, missing_material = Counter(), Counter(), Counter()
            for record in records:
                if record["technical_status"] == "excluded":
                    counts["excluded"] += 1
                    continue
                if not record["routes"]:
                    counts["no_saved_route_answer"] += 1
                    continue
                route = record["routes"][axis]
                counts["saved_route_answers"] += 1
                selected = core.select_routes(
                    {axis: {k: route[k] for k in ("choice", "probabilities", "confidence")}}, candidate
                )[axis]
                counts[selected["status"]] += 1
                causes = core.rejection_causes(route, candidate.route_probability, candidate.route_confidence)
                if not causes and route["choice"] in core.UNRESOLVED:
                    causes.append("reserved_category")
                counts.update(causes)
                routes = record["routes"] | {axis: selected}
                new_rows, new = core.prepare_reasons(catalog, routes, historical_evidence(record))
                old_rows, old = core.prepare_reasons(catalog, record["routes"], historical_evidence(record))
                extra = sorted(set(new) - set(old))
                newly_missing = [
                    row["reason_id"]
                    for row, previous in zip(new_rows, old_rows, strict=True)
                    if row["status"] == "missing_evidence" and previous["status"] == "route_not_selected"
                ]
                missing_material.update(newly_missing)
                if extra:
                    counts["targets_with_new_questions"] += 1
                    additional.update(extra)
                if extra or newly_missing:
                    route_changes.append(
                        {
                            "axis": axis,
                            "scenario": name,
                            "target_id": record["target_id"],
                            "new_question_ids": extra,
                            "new_scope_missing_material_ids": newly_missing,
                            "prediction_status": "no_historical_answer",
                        }
                    )
            route_summary[f"{axis}:{name}"] = {
                "counts": dict(counts),
                "additional_question_counts": dict(additional),
                "new_scope_missing_material_counts": dict(missing_material),
                "rejection_cause_counts_overlap": True,
            }
    return (
        {
            "reason_scenarios": reason_summary,
            "route_scenarios": route_summary,
            "quality_status": "NOT_EVALUATED",
            "accuracy": None,
            "f1": None,
            "best_threshold": None,
            "counterfactual_notice": "Route and material changes may add unasked questions; no historical answers exist for those questions.",
        },
        rejections,
        route_changes,
    )


def write_lines(path, values):
    with path.open("xb") as stream:
        for value in values:
            stream.write(batch.encode(value) + b"\n")


def derive(records, policy, output):
    def values():
        for record in records:
            result = record["result"]
            view = core.summarize_assessment(
                result["routes"] if result else None,
                result["reasons"] if result else None,
                policy,
                technical_status=record["technical_status"],
            )
            yield {"source_record": record, "derived": view, "rule_version": VERSION}

    write_lines(output / "derived.results.jsonl", values())
    analysis, rejections, routes = sensitivity(records, policy)
    batch.save(output / "sensitivity.json", analysis, new=True)
    write_lines(output / "reason-rejections.jsonl", rejections)
    write_lines(output / "route-counterfactuals.jsonl", routes)
    return aggregate(records, policy) | {
        "technical_statuses": dict(Counter(r["technical_status"] for r in records)),
        "real_api_requests": 0,
        "real_key_reads": 0,
    }


def audit_materials(root, source_plan, records, output):
    historical = {row["target_id"]: row for row in records}
    gate = batch.Gate(root, source_plan, output, {}, None, "materials")
    counts = {source: {field: Counter() for field in materials.FIELDS} for source in sorted(batch.SOURCES)}
    sizes, added, extraction_targets = Counter(), Counter(), 0
    catalog = core.load_catalog()
    with batch.Inputs(root, source_plan, gate) as inputs, (output / "materials.results.jsonl").open("xb") as stream:
        for target in source_plan["targets"]:
            if not target["eligible"]:
                continue
            projection = inputs.projection(target)
            meta = inputs.material_metadata
            baseline = {key: value for key, value in projection.items() if key not in materials.FIELDS}
            extraction_targets += any(value["status"] == "extracted" for value in meta.values())
            for field, value in meta.items():
                counts[target["source"]][field][value["status"]] += 1
            row = {
                "target_id": target["target_id"],
                "source": target["source"],
                "materials": meta,
                "input_version": batch.INPUT_VERSION,
                "projection_sha256": batch.sha(core._encode(projection)),
                "projection_utf8_bytes": len(core._encode(projection)),
                "size_basis": "historical_routes_conditional_no_new_prediction",
            }
            row["state_duplication_utf8_bytes"] = len(core._encode(projection)) - len(core._encode(baseline))
            sizes["state_duplication_utf8_bytes"] += row["state_duplication_utf8_bytes"]
            sizes["projection_over_limit"] += row["projection_utf8_bytes"] > core.MAX_REQUEST_BYTES
            routes = historical[target["target_id"]]["routes"]
            for stage in ("route", "reasons"):
                if stage == "reasons" and routes is None:
                    sizes["reason_size_unavailable_no_historical_route"] += 1
                    row["reasons"] = None
                    continue
                if stage == "route":
                    old_questions = new_questions = core.route_questions(catalog)
                    old_state, new_state = (
                        {k: v for k, v in state.items() if k not in {"answer", "tool_trace"}}
                        for state in (baseline, projection)
                    )
                else:
                    _, old_questions = core.prepare_reasons(catalog, routes, set(baseline))
                    _, new_questions = core.prepare_reasons(catalog, routes, set(projection))
                    old_state, new_state = baseline, projection
                old_bytes, new_bytes = (
                    len(core.request_bytes(state, questions))
                    for state, questions in ((old_state, old_questions), (new_state, new_questions))
                )
                extra = sorted(set(new_questions) - set(old_questions))
                row[stage] = {
                    "baseline_utf8_bytes": old_bytes,
                    "materialized_utf8_bytes": new_bytes,
                    "delta_utf8_bytes": new_bytes - old_bytes,
                    "within_limit": new_bytes <= core.MAX_REQUEST_BYTES,
                    "new_question_ids": extra,
                    "new_question_prediction_status": "no_historical_answer",
                }
                sizes[stage + "_targets"] += 1
                sizes[stage + "_bytes_added"] += new_bytes - old_bytes
                sizes[stage + "_max_utf8_bytes"] = max(sizes[stage + "_max_utf8_bytes"], new_bytes)
                sizes[stage + "_over_limit"] += new_bytes > core.MAX_REQUEST_BYTES
                sizes[stage + "_new_over_limit"] += old_bytes <= core.MAX_REQUEST_BYTES < new_bytes
                if stage == "reasons":
                    added.update(extra)
                    sizes["targets_with_new_questions"] += bool(extra)
            stream.write(batch.encode(row) + b"\n")
    return {
        "by_source": counts,
        "extracted_targets": extraction_targets,
        "eligible_targets": sum(t["eligible"] for t in source_plan["targets"]),
        "original_excluded": sum(not t["eligible"] for t in source_plan["targets"]),
        "request_sizes": dict(sizes),
        "new_question_counts": dict(added),
        "conditional_on_historical_routes": True,
        "quality_status": "NOT_EVALUATED",
        "real_api_requests": 0,
        "real_key_reads": 0,
    }


def run_offline(args, project_root):
    if args.mode == "groups":
        from .two_stage_groups import run_groups

        return run_groups(args, project_root)
    batch.require(args.keys_file is None and args.config_file is None, "offline_keys_and_live_config_forbidden")
    batch.require(args.source_batch and args.output_dir, "source_batch_and_new_output_dir_required")
    root = Path(args.data_root or project_root).resolve()
    source = batch.research_path(root, args.source_batch)
    output = batch.research_path(root, args.output_dir)
    batch.require(
        source.parent == root / batch.RESEARCH and source.name.startswith("m55-two-stage-"), "invalid_source_batch_path"
    )
    batch.require(
        not output.exists() and not output.is_relative_to(source), "new_independent_output_directory_required"
    )
    source_paths = [source / name for name in ("plan.json", "live.results.jsonl", "live.summary.json", "live.sqlite3")]
    plan = batch.decode(batch.read(source_paths[0]))
    source_plan = batch.metadata(root)
    batch.source_admission(root, source_plan)
    frozen_root = Path(plan["runtime"]["project_root"]).resolve()
    batch.require(
        set(plan["runtime"]["code_pins"]) in (OLD_CODE_FILES, set(batch.CODE_FILES)), "source_code_pin_set_changed"
    )
    frozen_files = [frozen_root / name for name in plan["runtime"]["code_pins"]]
    identity = batch.runtime_identity(project_root)
    allowed = (
        batch.offline_files(root, project_root, source_plan, output, None, output / "unused-synthetic-key.json")
        + source_paths
        + frozen_files
    )
    with batch.OfflineGuard(allowed, project_root, write_root=output) as guard:
        pins = [fingerprint(path) for path in source_paths]
        for name, expected in plan["runtime"]["code_pins"].items():
            batch.require(fingerprint(frozen_root / name)["sha256"] == expected, "historical_frozen_code_changed")
        records = [batch.decode(line) for line in batch.read(source_paths[1], 256 * 1024 * 1024).splitlines()]
        source_summary = batch.decode(batch.read(source_paths[2]))
        policy = validate_history(
            plan, records, source_plan, source_summary, batch.read(source_paths[3], 256 * 1024 * 1024)
        )
        output.mkdir(parents=True, exist_ok=False)
        value = (
            derive(records, policy, output)
            if args.mode == "derive"
            else audit_materials(root, source_plan, records, output)
        )
        batch.require(pins == [fingerprint(path) for path in source_paths], "source_changed_during_derivation")
        value.update(
            schema_version=VERSION,
            assessment_version=core.ASSESSMENT_VERSION,
            material_rule_version=materials.VERSION,
            source_files=pins,
            source_batch_id=plan["batch_id"],
            source_runtime=plan["runtime"],
            source_input_pins=plan["input_pins"],
            runtime=identity,
            baseline_policy=asdict(policy),
            source_cost_snapshot={
                key: source_summary[key]
                for key in (
                    "cost_unit",
                    "known_usage_estimate",
                    "held_reservations",
                    "unknown_cost_reservations",
                    "actual_charge_usd",
                )
            },
            source_pricing={
                key: plan["config"].get("live", {}).get("budget", {}).get(key)
                for key in ("input_usd_per_million", "output_usd_per_million", "pricing_ref")
            },
            network_attempts_denied=guard.network_denied,
            live_authorized=False,
        )
        batch.save(output / f"{args.mode}.summary.json", value, new=True)
        return value
