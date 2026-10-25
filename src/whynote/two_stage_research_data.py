"""Controlled historical adapters and body-free v3 planning/sampling manifests."""

from collections import Counter
from pathlib import Path

from . import two_stage as legacy
from . import two_stage_batch as batch
from . import two_stage_inputs as inputs
from . import two_stage_offline as offline
from . import two_stage_review as review
from . import two_stage_v3 as v3

VERSION = "jev-research-workflow-v1"
OVERSIZE_TARGET = "01ef78b2f80c81f3215e648c6467f155c1a73c3e6bb4cb52b0e5acdeb0c0bb36"
MODEL_FILES = (
    "src/whynote/two_stage.py",
    "src/whynote/two_stage_materials.py",
    "src/whynote/two_stage_inputs.py",
    "src/whynote/two_stage_v3.py",
    "src/whynote/two_stage_catalog_v3.json",
    "src/whynote/jev_provider.py",
    "src/whynote/provider_keys.py",
    "src/whynote/domain.py",
    "src/whynote/two_stage_review.py",
    "src/whynote/two_stage_batch.py",
    "src/whynote/two_stage_v3_batch.py",
    "src/whynote/two_stage_research_data.py",
    "scripts/start_jev_research.ps1",
)
ANALYSIS_FILES = (
    "src/whynote/two_stage_calibration.py",
    "src/whynote/two_stage_confirmation.py",
    "src/whynote/two_stage_review.py",
    "src/whynote/two_stage_review_ui.py",
    "src/whynote/two_stage_review.html",
    "src/whynote/two_stage_research.py",
    "src/whynote/two_stage_risk.json",
)


def file_sha(path):
    return offline.fingerprint(Path(path))["sha256"]


def analysis_binding(project_root):
    pins = {name: file_sha(Path(project_root) / name) for name in ANALYSIS_FILES}
    return {"code_pins": pins, "sha256": review.fingerprint(pins)}


def runtime(project_root):
    project_root = Path(project_root).resolve()
    for module in (legacy, inputs, review, v3):
        review.require(
            Path(module.__file__).resolve().is_relative_to(project_root / "src"), "wrong_research_module_origin"
        )
    catalog = v3.load_catalog()
    pins = {name: file_sha(project_root / name) for name in MODEL_FILES}
    return {
        "input_version": inputs.VERSION,
        "catalog_version": catalog["version"],
        "catalog_sha256": review.fingerprint(catalog),
        "model": v3.MODEL,
        "prompt_version": v3.PROMPT_VERSION,
        "prompt_sha256": review.fingerprint(
            {"route": v3.route_questions(catalog), "reasons": v3.reason_questions(catalog)}
        ),
        "request_version": v3.REQUEST_VERSION,
        "inference_code_sha256": review.fingerprint(pins),
        "code_pins": pins,
        "protocol_version": v3.VERSION,
        "target_bytes": v3.TARGET_BYTES,
        "hard_bytes": v3.HARD_BYTES,
        "max_reason_packets": v3.MAX_REASON_PACKETS,
    }


def read_history(path, source_plan):
    path = Path(path).resolve()
    raw = {
        name: batch.read(path / name, 256 * 1024 * 1024)
        for name in ("plan.json", "live.results.jsonl", "live.summary.json", "live.sqlite3")
    }
    plan = batch.decode(raw["plan.json"])
    records = [batch.decode(line) for line in raw["live.results.jsonl"].splitlines()]
    offline.validate_history(plan, records, source_plan, batch.decode(raw["live.summary.json"]), raw["live.sqlite3"])
    for name, pin in plan["runtime"]["code_pins"].items():
        review.require(file_sha(Path(plan["runtime"]["project_root"]) / name) == pin, "historical_code_changed")
    return (
        plan,
        {r["target_id"]: r for r in records},
        {str(path / name): batch.sha(value) for name, value in raw.items()},
    )


def evaluation_groups(path, source_plan):
    if path is None:
        return {}, {}
    path = Path(path).resolve()
    raw = batch.read(path, 64 * 1024 * 1024)
    targets = {t["target_id"]: t for t in source_plan["targets"]}
    groups = {}
    for line in raw.splitlines():
        row = batch.decode(line)
        review.require(
            row["target_id"] in targets
            and row["original_state_sha256"] == targets[row["target_id"]]["original_state_sha256"],
            "group_input_changed",
        )
        groups[row["target_id"]] = row["evaluation"]
    review.require(set(groups) == set(targets), "group_population_changed")
    return groups, {str(path): batch.sha(raw)}


def _model(record):
    return record.get("result") or {}


def diagnostic_flags(records, previous, source_groups, *, seed):
    """All positive-group candidates and changed candidates; bounded other strata."""
    flags, pools = (
        {key: [] for key in records},
        {
            name: []
            for name in ("negative_no_issue", "negative_abstained", "material_ambiguous", "association_unconfirmed")
        },
    )
    for key, record in records.items():
        result = _model(record)
        group = source_groups.get(key) or {}
        signal = group.get("group", "unclear_signal")
        candidates = {s["reason_id"] for s in result.get("suggestions", [])}
        old = _model(previous.get(key, {}))
        if signal == "positive_signal" and candidates:
            flags[key].append("positive_group_candidate")
        if result and old and candidates != {s["reason_id"] for s in old.get("suggestions", [])}:
            flags[key].append("candidate_set_changed")
        assessment = result.get("assessment")
        if signal == "negative_signal" and assessment in {"no_issue_detected_in_evaluated_scope", "abstained"}:
            pools[
                "negative_no_issue" if assessment == "no_issue_detected_in_evaluated_scope" else "negative_abstained"
            ].append(key)
        if any(m.get("status") in {"ambiguous", "parse_failed"} for m in record.get("materials", {}).values()):
            pools["material_ambiguous"].append(key)
        if group.get("linkage", {}).get("verified") is False:
            pools["association_unconfirmed"].append(key)
        if key == OVERSIZE_TARGET:
            flags[key].append("historical_33588_bytes")
    for name, pool in pools.items():
        for key in sorted(pool, key=lambda key: review.fingerprint([seed, name, key]))[:16]:
            flags[key].append(name)
    return flags


def original_input(reader, target):
    old = reader.projection(target)
    original = batch.decode(
        reader.original.execute("SELECT payload FROM inputs WHERE input_id=?", (target["target_id"],)).fetchone()[0]
    )
    value = inputs.build_input(target["target_id"], original["context"], original["answer"])
    return value, old, original


def safe_plan(value, old, historical, catalog):
    route, _, _ = v3.plan_route(value, catalog)
    possible = v3.plan_possible_routes(value, catalog)
    model = _model(historical)
    routes = model.get("routes")
    if not routes:
        stage = next(
            (s for s in historical.get("stages", []) if s["stage"] == "route" and s["status"] == "completed"), None
        )
        if stage:
            routes = legacy.select_routes(stage["answers"], batch.OFFLINE_POLICY)
    actual = v3.plan_reasons(value, routes, catalog) if routes else None
    return {
        "target_id": value["target_id"],
        "input_sha256": value["input_sha256"],
        "historical_input_sha256": batch.sha(legacy._encode(old)),
        "input_version": inputs.VERSION,
        "route": route,
        "possible_routes": possible,
        "historical_route_illustration": None
        if actual is None
        else {
            "is_new_prediction": False,
            "status": actual["status"],
            "packets": [p["record"] for p in actual["packets"]],
            "missing_evidence": {
                r["reason_id"]: r["missing_evidence"] for r in actual["rows"] if r["status"] == "missing_evidence"
            },
            "unassessed_for_budget": actual["unassessed_reasons"],
            "material_delivery": actual.get("material_delivery", {}),
        },
        "materials": {
            name: {k: v for k, v in material.items() if k != "evidence"}
            for name, material in value["materials"].items()
        },
        "foundation_states": {name: fact["status"] for name, fact in value["facts"].items()},
        "maximum_requests": possible["maximum_request_count"] if route["status"] == "ready" else 0,
        "prediction_compatibility": "NEW_PREDICTIONS_REQUIRED",
        "evidence_eligible_reason_ids": [r["id"] for r in catalog["reasons"] if not inputs.missing_evidence(r, value)],
    }


def distribution(values):
    values = sorted(values)
    if not values:
        return {"n": 0, "min": None, "median": None, "p95": None, "max": None}
    return {
        "n": len(values),
        "min": values[0],
        "median": values[len(values) // 2],
        "p95": values[min(len(values) - 1, int(len(values) * 0.95))],
        "max": values[-1],
    }


def prepare(
    root,
    project_root,
    output,
    primary,
    compare,
    groups_file,
    *,
    seed="jev-review-20261006-v1",
    calibration_count=400,
    progress=None,
):
    root, output = Path(root).resolve(), Path(output).resolve()
    review.require(
        output.is_relative_to(root / "var/research") and not output.exists(), "new_controlled_output_required"
    )
    source = batch.metadata(root)
    batch.source_admission(root, source)
    primary_plan, records, protected = read_history(primary, source)
    _, previous, compare_pins = read_history(compare, source)
    protected.update(compare_pins)
    source_groups, group_pins = evaluation_groups(groups_file, source)
    protected.update(group_pins)
    flags = diagnostic_flags(records, previous, source_groups, seed=seed)
    identity = runtime(project_root)
    binding = {
        "runtime": identity,
        "analysis": analysis_binding(project_root),
        "primary_run": primary_plan["batch_id"],
        "source_plan_sha256": batch.PLAN_SHA256,
        "source_expires_at": source["expires_at"],
        "historical_pins": protected,
    }
    binding["required_prediction_run"] = (
        "v3-primary-"
        + review.fingerprint([identity, binding["analysis"], batch.PLAN_SHA256, seed, calibration_count])[:16]
    )
    rows, cases = [], []
    catalog = v3.load_catalog()
    old_targets = {t["target_id"]: t for t in primary_plan["targets"]}
    gate = batch.Gate(root, source, output, {}, None, "preview")
    regression = None
    with batch.Inputs(root, source, gate) as reader:
        for target in source["targets"]:
            if not target["eligible"]:
                continue
            value, old, original = original_input(reader, target)
            key = target["target_id"]
            review.require(
                batch.sha(legacy._encode(old)) == old_targets[key]["input_sha256"], "historical_input_changed"
            )
            row = safe_plan(value, old, records[key], catalog)
            rows.append(row)
            user_messages = [m["content"] for m in original["context"] if m["role"] == "user"]
            aliases = ["prepared:" + target["group_id"], "target:" + key]
            for field in ("conversation_group_id", "source_conversation_id"):
                if original.get(field) is not None:
                    aliases.append(target["source"] + ":" + field + ":" + str(original[field]))
            if target["source"] in {"helpsteer3", "wildfb"}:
                aliases.append(target["source"] + ":row:" + str(original["row_id"]))
            cases.append(
                {
                    "target_id": key,
                    "source": target["source"],
                    "input_version": inputs.VERSION,
                    "input_sha256": value["input_sha256"],
                    "group_aliases": aliases,
                    "group_prompt": user_messages[0],
                    "diagnostic_flags": flags[key],
                    "exposure": [
                        "historical_runs_01_02_03_explored",
                        "aggregate_results_shared",
                        "not_formally_unseen",
                    ],
                }
            )
            if key == OVERSIZE_TARGET:
                stages = records[key].get("stages", [])
                old_route = next(s for s in stages if s["stage"] == "route")
                old_reasons = next(s for s in stages if s["stage"] == "reasons")
                previous_routes = legacy.select_routes(
                    old_route["answers"], legacy.Policy(**primary_plan["config"]["policy"])
                )
                _, questions = legacy.prepare_reasons(legacy.load_catalog(), previous_routes, set(old))
                body = legacy.request_bytes(old, questions, model=primary_plan["runtime"]["model"])
                review.require(
                    len(body) == old_reasons["request_utf8_bytes"] == 33588
                    and batch.sha(body) == old_reasons["request_sha256"],
                    "historical_33588_regression_changed",
                )
                regression = {
                    "target_id": key,
                    "before": [
                        {
                            k: stage[k]
                            for k in ("stage", "request_utf8_bytes", "request_sha256", "question_ids", "status")
                        }
                        for stage in (old_route, old_reasons)
                    ],
                    "after": row,
                    "historical_files_unchanged": True,
                }
            if progress and len(rows) % 100 == 0:
                progress({"planned": len(rows), "total": 2531})
    review.require(len(rows) == 2531 and regression is not None, "complete_current_population_required")
    groups = review.group_cases(cases)
    sample = review.sample_manifest(cases, groups, binding, seed=seed, calibration_count=calibration_count)
    for row in rows:
        row["group_id"] = groups[row["target_id"]]
    # Freeze before publication; all artifacts contain metadata only, no source bodies.
    for path, pin in protected.items():
        review.require(file_sha(Path(path)) == pin, "historical_file_changed_during_planning")
    summary = {
        "schema_version": VERSION,
        "input_version": inputs.VERSION,
        "original_targets": 3000,
        "original_excluded": 469,
        "planned_targets": len(rows),
        "independent_groups": len(set(groups.values())),
        "route_budget_limited": sum(row["route"]["status"] != "ready" for row in rows),
        "possible_reason_budget_limited_targets": sum(
            row["possible_routes"]["budget_limited_question_sets"] > 0 for row in rows
        ),
        "maximum_three_request_targets": sum(row["maximum_requests"] == 3 for row in rows),
        "maximum_planned_requests": sum(row["maximum_requests"] for row in rows),
        "route_bytes": distribution([row["route"]["request_utf8_bytes"] for row in rows]),
        "largest_sendable_reason_packet_bytes": distribution(
            [row["possible_routes"]["largest_sendable_packet_bytes"] for row in rows]
        ),
        "material_states": {
            name: dict(Counter(row["materials"][name]["status"] for row in rows))
            for name in inputs.legacy_materials.FIELDS
        },
        "diagnostic_flags": dict(Counter(flag for case in cases for flag in case["diagnostic_flags"])),
        "review_selected": dict(Counter(row["partition"] for row in sample["rows"] if row["selected"])),
        "human_labels": "PENDING",
        "thresholds": "NOT_SELECTED",
        "confirmation": "NOT_EXECUTED",
        "production": "NOT_APPROVED",
        "compatible_historical_predictions": 0,
        "real_api_calls": 0,
        "key_reads": 0,
        "history_sha256_verified": protected,
    }
    output.mkdir(parents=True)
    batch.save(output / "sample.manifest.json", sample, new=True)
    offline.write_lines(output / "size-plan.jsonl", rows)
    batch.save(output / "size-summary.json", summary, new=True)
    batch.save(output / "historical-33588.regression.json", regression, new=True)
    batch.save(
        output / "workflow.lock.json",
        {
            "schema_version": VERSION,
            "data_root": str(root),
            "project_root": str(Path(project_root).resolve()),
            "binding": binding,
            "files": {
                name: file_sha(output / name)
                for name in (
                    "sample.manifest.json",
                    "size-plan.jsonl",
                    "size-summary.json",
                    "historical-33588.regression.json",
                )
            },
        },
        new=True,
    )
    return summary
