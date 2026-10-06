"""Source-only evaluation groups joined to immutable historical predictions."""

from collections import Counter
from pathlib import Path

from . import evaluation_signals as signals
from . import explore_inputs
from . import two_stage as core
from . import two_stage_batch as batch
from . import two_stage_materials as materials
from . import two_stage_offline as offline
from .source_mapping import MappingError, digest, messages

EXTRA_CODE = (
    "src/whynote/evaluation_signals.py",
    "src/whynote/two_stage_groups.py",
    "src/whynote/explore_inputs.py",
    "src/whynote/source_mapping.py",
)
QUALITY = {"status": "NOT_EVALUATED", "accuracy": None, "precision": None, "recall": None, "f1": None}


def selectors(source, originals):
    selected = {}
    for original in originals:
        index, pointer = original["row_id"], original["target_id"]
        if source == "helpsteer3":
            fields = {"context", pointer, "feedback" + pointer[-1]}
        elif source == "wildfb":
            fields = {"history", "messages", "user_feedback", "label"}
        else:
            fields = {"Content"}
        selected.setdefault(index, set()).update(fields)
    if source == "wildfeedback" and selected:
        targets = list(selected)
        # Only identity/annotation metadata from other turns; never their bodies.
        for index in range(max(selected) + 2):
            selected.setdefault(index, set()).update({"UtterranceId", "TurnId", "Role"})
        for index in targets:
            for related in (index - 1, index, index + 1):
                if related >= 0:
                    selected.setdefault(related, set()).update(signals.WF_FIELDS)
    return selected


def segments(rows):
    """A broken sequence stays unbound until an explicit valid reset."""
    starts, start, expected = {}, None, None
    for index, row in sorted(rows.items()):
        number, turn, role = (row.get(k) for k in ("UtterranceId", "TurnId", "Role"))
        if type(number) is int and number == 0:
            start, expected = index, 0
        valid = (
            start is not None
            and type(number) is int
            and type(turn) is int
            and number == expected
            and turn == number // 2 + 1
            and role == ("User" if number % 2 == 0 else "Agent")
            and index == start + number
        )
        if not valid:
            start, expected = None, None
        starts[index] = start
        if valid:
            expected += 1
    return starts


def group_original(original, pin, rows, starts):
    """No prediction, policy or model-result argument is accepted here."""
    source, index = original["source"], original["row_id"]
    row, reference = rows.get(index, {}), original["reference"]
    evidence = []

    def ref(row_id, field):
        value = rows.get(row_id, {}).get(field)
        evidence.append({"row_id": row_id, "pointer": "/" + field, "value_sha256": digest(value)})

    audit = {}
    if source == "helpsteer3":
        pointer = original["target_id"]
        field = "feedback" + pointer[-1]
        linked = (
            pointer in {"response1", "response2"}
            and row.get(pointer) == original["answer"]
            and row.get("context") == original["context"]
            and row.get(field) == reference.get("feedback")
            and reference.get("origin") == "evaluator"
        )
        result = signals.helpsteer(row.get(field), complete=linked)
        ref(index, field)
        origin = label_origin = "evaluator"
        binding = "matching_response_feedback_suffix" if linked else "source_snapshot_binding_unverified"
    elif source == "wildfb":
        linked = False
        try:
            pair = messages(row.get("messages"))
            history = messages(row["history"]) if row.get("history") else []
            feedback = messages([row.get("user_feedback")])[0]
            linked = (
                isinstance(row.get("history"), list)
                and len(pair) == 2
                and [m["role"] for m in pair] == ["user", "assistant"]
                and feedback["role"] == "user"
                and (not history or history[-1]["role"] == "assistant")
                and original["target_id"] == "messages/1"
                and original["context"] == history + pair[:1]
                and original["answer"] == pair[1]["content"]
                and reference.get("feedback") == [feedback["content"]]
                and reference.get("automated_label") == row.get("label")
                and reference.get("origin") == "original_user"
            )
        except (MappingError, KeyError, TypeError):
            pass
        result = signals.wildfb(row.get("label"), linked=linked)
        for field in ("messages", "user_feedback", "label"):
            ref(index, field)
        origin, label_origin = "original_user", "automated"
        binding = (
            "published_pair_and_followup_fields_match_snapshot" if linked else "source_snapshot_binding_unverified"
        )
    else:
        following = rows.get(index + 1)
        start = starts.get(index)
        bound = (
            start is not None
            and original["target_id"] == f"utterance/{row.get('UtterranceId')}"
            and row.get("Content") == original["answer"]
            and row.get("UtterranceId") == len(original["context"])
            and reference.get("conversation_start_row") == start
            and reference.get("feedback_row_id") == index + 1
            and original.get("source_conversation_id") == digest({"file": pin["sha256"], "start": start})
        )
        result = signals.wildfeedback(row, following, same_segment=bound and starts.get(index + 1) == start)
        linked = bound and result["reason"] in {
            "following_user_signal",
            "no_explicit_sat_dsat",
            "missing_or_invalid_sat_dsat",
        }
        if not bound:
            result.update(group="unclear_signal", reason="source_snapshot_binding_unverified")
        old = reference.get("automated_annotation", {})
        previous = rows.get(index - 1, {})
        audit = {
            "historical_agent_annotation_matches_preceding_user": bound
            and all(
                type(old.get(k)) is bool and old[k] == row.get(k) == previous.get(k)
                for k in ("Satisfaction", "Disatisfaction")
            ),
            "historical_agent_annotation_not_target_feedback": True if bound else None,
            "verified_following_labels_differ_from_historical": (
                any(old.get(k) != following.get(k) for k in ("Satisfaction", "Disatisfaction")) if linked else None
            ),
            "historical_sat": old.get("Satisfaction") if type(old.get("Satisfaction")) is bool else None,
            "historical_dsat": old.get("Disatisfaction") if type(old.get("Disatisfaction")) is bool else None,
            "conversation_start_row": start,
            "following_user_row": index + 1,
        }
        for field in sorted(signals.WF_FIELDS):
            ref(index, field)
            ref(index + 1, field)
        origin, label_origin = "original_user", "automated"
        binding = "following_user_in_verified_segment_and_related_topic" if linked else result["reason"]
    if "_error" in row:
        result.update(group="unclear_signal", reason=row["_error"])
        linked, binding = False, row["_error"]
    return result | {
        "rule_version": signals.VERSION,
        "feedback_origin": origin,
        "label_origin": label_origin,
        "user_dislike_action": "not_observed",
        "defect_truth": "NOT_EVALUATED",
        "linkage": {"verified": linked, "basis": binding, "human_semantic_confirmation": False},
        "source_row_id": index,
        "source_target": original["target_id"],
        "source_row_group_id": digest({"source": source, "revision": pin["revision"], "row": index}),
        "source_conversation_id": original.get("source_conversation_id"),
        "source_file": {k: pin[k] for k in ("path", "revision", "sha256")},
        "evidence": evidence,
        "temporal_audit": audit,
    }


def checked_projection(record, projection):
    batch.require(
        batch.sha(core._encode(projection)) == record["input_sha256"], "historical_input_fingerprint_mismatch"
    )
    catalog = core.load_catalog()
    route_state = {k: v for k, v in projection.items() if k not in {"answer", "tool_trace"}}
    route_body = core.request_bytes(route_state, core.route_questions(catalog))
    batch.require(
        batch.sha(route_body) == record["route_request_sha256"]
        and len(route_body) == record["route_request_utf8_bytes"],
        "historical_route_fingerprint_mismatch",
    )
    for stage in record["stages"]:
        if stage.get("question_ids") is None:
            batch.require(
                stage["stage"] == "reasons"
                and stage["status"] == "not_sent"
                and stage.get("request_sha256") is None
                and stage.get("request_utf8_bytes") is None,
                "historical_unbuilt_stage_invalid",
            )
            continue
        if stage["stage"] == "route":
            state, questions = route_state, core.route_questions(catalog)
        else:
            batch.require(record["routes"] is not None, "historical_reason_without_route")
            state = projection
            _, questions = core.prepare_reasons(catalog, record["routes"], set(projection))
        body = core.request_bytes(state, questions)
        batch.require(
            stage["question_ids"] == list(questions)
            and stage["request_sha256"] == batch.sha(body)
            and stage["request_utf8_bytes"] == len(body),
            "historical_stage_fingerprint_mismatch",
        )


def memberships(root, source_plan, histories, inputs):
    permitted = {
        target["target_id"]: target
        for target in source_plan["targets"]
        if target["eligible"] and any(h["by_id"][target["target_id"]]["disposition"] == "runnable" for h in histories)
    }
    originals = {}
    for key, target in permitted.items():
        inputs.gate.check()
        raw = inputs.original.execute("SELECT payload FROM inputs WHERE input_id=?", (key,)).fetchone()
        batch.require(raw is not None, "missing_original_target")
        original = batch.decode(raw[0])
        projected = inputs.projection(target)
        base = {k: v for k, v in projected.items() if k not in materials.FIELDS}
        for history in histories:
            record = history["by_id"][key]
            if record["disposition"] == "runnable":
                checked_projection(record, base if record["contract_version"] == "jev-two-stage-v1" else projected)
                if record["contract_version"] == core.VERSION:
                    batch.require(
                        record["materials"] == inputs.material_metadata, "historical_material_metadata_mismatch"
                    )
        originals[key] = original
    grouped = {}
    for pin in source_plan["sources"]:
        source = pin["source"]
        spec = explore_inputs.SOURCES[source]
        batch.require(
            (pin["revision"], pin["bytes"], pin["sha256"]) == (spec[1], spec[3], spec[4]), "unsupported_source_revision"
        )
        selected = {key: value for key, value in originals.items() if value["source"] == source}
        rows = signals.selected_source_rows(
            batch.research_path(root, pin["path"]), source, selectors(source, selected.values()), inputs.gate.check
        )
        starts = segments(rows) if source == "wildfeedback" else {}
        for key, original in selected.items():
            batch.require(
                original["revision"] == pin["revision"] and original["file_sha256"] == pin["sha256"],
                "original_source_identity_mismatch",
            )
            grouped[key] = group_original(original, pin, rows, starts)
    batch.require(set(grouped) == set(permitted), "evaluation_membership_set_mismatch")
    return grouped


def rate(numerator, denominator):
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator if denominator else None,
    }


def bucket(records, policy):
    disposition, status = Counter(r["disposition"] for r in records), Counter(r["technical_status"] for r in records)
    denominator = {
        "original_targets": len(records),
        "original_excluded": disposition["original_exclusion"],
        "new_excluded": sum(n for name, n in disposition.items() if name not in {"original_exclusion", "runnable"}),
        "runnable": disposition["runnable"],
        "technical_success": status["success"],
        "technical_error": status["error"],
        "reconcile": status["reconcile"],
        "not_executed": status["pending"],
        "result_targets": status["success"],
    }
    batch.require(
        denominator["original_targets"]
        == denominator["original_excluded"] + denominator["new_excluded"] + denominator["runnable"]
        and denominator["runnable"] == sum(status[s] for s in ("success", "error", "reconcile", "pending")),
        "evaluation_denominator_mismatch",
    )
    value = offline.aggregate(records, policy)
    success, coverage = status["success"], value["coverage"]
    asked, missing = coverage.get("asked_reason_count", 0), coverage.get("missing_evidence_reason_count", 0)
    material_counts = {field: Counter() for field in materials.FIELDS}
    known = 0
    for record in records:
        if record["technical_status"] == "success" and "materials" in record:
            known += 1
            for field, item in record["materials"].items():
                material_counts[field][item["status"]] += 1
    value.update(
        denominators=denominator,
        rates={
            "candidate_yield": rate(value["assessments"]["candidate_found"], success),
            "abstention": rate(value["assessments"]["abstained"], success),
            "no_issue_in_evaluated_scope": rate(value["assessments"]["no_issue_detected_in_evaluated_scope"], success),
            "route_complete": rate(coverage.get("route_complete", 0), success),
            "material_question_coverage": rate(asked, asked + missing),
        },
        material_extraction={
            "successful_targets_with_metadata": known,
            "successful_targets_without_metadata": success - known,
            "by_field": {field: dict(counts) for field, counts in material_counts.items()},
            "scope": "successful_targets_only_historical_metadata_not_inferred",
        },
        quality=QUALITY,
    )
    batch.require(sum(value["assessments"].values()) == success, "assessment_denominator_mismatch")
    return value


def breakdown(records, policy, grouped):
    def group_records(items, name):
        return [r for r in items if r["disposition"] == "runnable" and grouped[r["target_id"]]["group"] == name]

    overall = bucket(records, policy)
    by_group = {name: bucket(group_records(records, name), policy) for name in signals.GROUPS}
    batch.require(
        sum(v["denominators"]["runnable"] for v in by_group.values()) == overall["denominators"]["runnable"],
        "group_denominator_mismatch",
    )
    by_source = {}
    for source in sorted(batch.SOURCES):
        subset = [r for r in records if r["source"] == source]
        by_source[source] = {
            "overall": bucket(subset, policy),
            "by_group": {name: bucket(group_records(subset, name), policy) for name in signals.GROUPS},
        }
    return {"overall": overall, "by_group": by_group, "by_source": by_source}


def model_view(record, policy):
    result = record["result"]
    view = core.summarize_assessment(
        result["routes"] if result else None,
        result["reasons"] if result else None,
        policy,
        technical_status=record["technical_status"],
    )
    if result and "assessment" in result:
        batch.require(
            result["assessment"] == view["assessment"] and result["coverage"] == view["coverage"],
            "historical_assessment_mismatch",
        )
    return {
        "technical_status": record["technical_status"],
        "disposition": record["disposition"],
        "exclusion_code": record["exclusion_code"],
        "input_sha256": record["input_sha256"],
        "source_record_sha256": batch.sha(batch.encode(record)),
        "legacy_outcome": result["outcome"] if result else None,
        **view,
    }


def comparison_flags(left, right):
    comparable = left["disposition"] == right["disposition"] == "runnable"
    stages = []
    for name in ("route", "reasons"):
        a = next((s for s in left["stages"] if s["stage"] == name and s.get("question_ids") is not None), None)
        b = next((s for s in right["stages"] if s["stage"] == name and s.get("question_ids") is not None), None)
        stages.append(
            {
                "stage": name,
                "first_request_recorded": a is not None,
                "second_request_recorded": b is not None,
                "both_recorded": a is not None and b is not None,
                "question_ids_changed": a["question_ids"] != b["question_ids"] if a and b else None,
                "request_fingerprint_changed": a["request_sha256"] != b["request_sha256"] if a and b else None,
            }
        )

    def extracted(record):
        return {k: v["locator"] for k, v in record.get("materials", {}).items() if v["status"] == "extracted"}

    return {
        "both_runnable": comparable,
        "input_fingerprint_changed": left["input_sha256"] != right["input_sha256"] if comparable else None,
        "extracted_material_changed": extracted(left) != extracted(right) if comparable else None,
        "material_metadata_availability_changed": ("materials" in left) != ("materials" in right),
        "stages": stages,
    }


def compare(histories, grouped, rows):
    left, right = histories
    cohorts = {
        name: []
        for name in (
            "both_success",
            "only_first_success",
            "only_second_success",
            "neither_success_runnable",
            "excluded_both",
        )
    }
    changes = Counter()
    transitions = Counter()
    for row in rows:
        key = row["target_id"]
        a, b = left["by_id"][key], right["by_id"][key]
        ok_a, ok_b = a["technical_status"] == "success", b["technical_status"] == "success"
        name = (
            "both_success"
            if ok_a and ok_b
            else "only_first_success"
            if ok_a
            else "only_second_success"
            if ok_b
            else "excluded_both"
            if a["disposition"] != "runnable" and b["disposition"] != "runnable"
            else "neither_success_runnable"
        )
        cohorts[name].append(key)
        flags = row["comparison"]
        for flag in (
            "input_fingerprint_changed",
            "extracted_material_changed",
            "material_metadata_availability_changed",
        ):
            changes[flag] += flags[flag] is True
            if name == "both_success":
                changes["both_success:" + flag] += flags[flag] is True
        for stage in flags["stages"]:
            for flag in ("both_recorded", "question_ids_changed", "request_fingerprint_changed"):
                changes[stage["stage"] + ":" + flag] += stage[flag] is True
                if name == "both_success":
                    changes["both_success:" + stage["stage"] + ":" + flag] += stage[flag] is True
        if name == "both_success":
            transitions[
                row["batches"][left["id"]]["assessment"] + " -> " + row["batches"][right["id"]]["assessment"]
            ] += 1
    return {
        "batch_order": [h["id"] for h in histories],
        "cohort_counts": {k: len(v) for k, v in cohorts.items()},
        "change_counts": dict(changes),
        "paired_assessment_transitions": dict(transitions),
        "cohorts": {
            name: {h["id"]: breakdown([h["by_id"][key] for key in ids], h["policy"], grouped) for h in histories}
            for name, ids in cohorts.items()
            if name != "excluded_both"
        },
        "quality": QUALITY,
        "causal_quality_attribution": "NOT_EVALUATED",
    }


def run_groups(args, project_root):
    batch.require(args.keys_file is None and args.config_file is None, "offline_keys_and_live_config_forbidden")
    batch.require(
        args.source_batch and args.compare_batch and args.output_dir, "two_source_batches_and_new_output_dir_required"
    )
    root = Path(args.data_root or project_root).resolve()
    sources = [batch.research_path(root, p) for p in (args.source_batch, args.compare_batch)]
    output = batch.research_path(root, args.output_dir)
    batch.require(len(set(sources)) == 2, "distinct_source_batches_required")
    batch.require(
        all(p.parent == root / batch.RESEARCH and p.name.startswith("m55-two-stage-") for p in sources),
        "invalid_source_batch_path",
    )
    batch.require(
        not output.exists() and all(not output.is_relative_to(p) for p in sources),
        "new_independent_output_directory_required",
    )
    paths = [
        [p / name for name in ("plan.json", "live.results.jsonl", "live.summary.json", "live.sqlite3")] for p in sources
    ]
    plans = [batch.decode(batch.read(p[0])) for p in paths]
    batch.require(plans[0]["batch_id"] != plans[1]["batch_id"], "distinct_source_batch_ids_required")
    source_plan = batch.metadata(root)
    batch.source_admission(root, source_plan)
    frozen = []
    for plan in plans:
        batch.require(
            set(plan["runtime"]["code_pins"]) in (offline.OLD_CODE_FILES, set(batch.CODE_FILES)),
            "source_code_pin_set_changed",
        )
        frozen.extend(Path(plan["runtime"]["project_root"]) / name for name in plan["runtime"]["code_pins"])
    identity = batch.runtime_identity(project_root)
    code_files = [project_root / name for name in EXTRA_CODE]
    allowed = batch.offline_files(root, project_root, source_plan, output, None, output / "unused-synthetic-key.json")
    allowed += [p for group in paths for p in group] + frozen + code_files
    with batch.OfflineGuard(allowed, project_root, write_root=output) as guard:
        before = [offline.fingerprint(p) for group in paths for p in group]
        code_pins = [offline.fingerprint(p) for p in code_files]
        histories = []
        for plan, files in zip(plans, paths, strict=True):
            for name, expected in plan["runtime"]["code_pins"].items():
                batch.require(
                    offline.fingerprint(Path(plan["runtime"]["project_root"]) / name)["sha256"] == expected,
                    "historical_frozen_code_changed",
                )
            records = [batch.decode(line) for line in batch.read(files[1], 256 * 1024 * 1024).splitlines()]
            policy = offline.validate_history(
                plan, records, source_plan, batch.decode(batch.read(files[2])), batch.read(files[3], 256 * 1024 * 1024)
            )
            histories.append(
                {
                    "id": plan["batch_id"],
                    "records": records,
                    "by_id": {r["target_id"]: r for r in records},
                    "policy": policy,
                }
            )
        gate = batch.Gate(root, source_plan, output, {}, None, "groups")
        with batch.Inputs(root, source_plan, gate) as inputs:
            grouped = memberships(root, source_plan, histories, inputs)
        rows = []
        for target in source_plan["targets"]:
            key = target["target_id"]
            rows.append(
                {
                    "rule_version": signals.VERSION,
                    **{k: target[k] for k in ("target_id", "source", "group_id", "original_state_sha256")},
                    "input_snapshot": source_plan["input_pins"]["inputs"],
                    "evaluation": grouped.get(key),
                    "batches": {h["id"]: model_view(h["by_id"][key], h["policy"]) for h in histories},
                    "comparison": comparison_flags(*(h["by_id"][key] for h in histories)),
                }
            )
        value = {
            "schema_version": signals.VERSION,
            "assessment_version": core.ASSESSMENT_VERSION,
            "runtime": identity,
            "evaluation_code_pins": code_pins,
            "source_files": before,
            "input_pins": source_plan["input_pins"],
            "source_pins": source_plan["sources"],
            "historical_runtimes": {p["batch_id"]: p["runtime"] for p in plans},
            "group_counts": {name: sum(v["group"] == name for v in grouped.values()) for name in signals.GROUPS},
            "grouping_sha256": batch.sha(batch.encode({k: grouped[k] for k in sorted(grouped)})),
            "source_signal_audit": {
                source: {
                    "reasons": dict(
                        Counter(v["reason"] for k, v in grouped.items() if histories[0]["by_id"][k]["source"] == source)
                    ),
                    "linkage_verified": sum(
                        v["linkage"]["verified"]
                        for k, v in grouped.items()
                        if histories[0]["by_id"][k]["source"] == source
                    ),
                }
                for source in sorted(batch.SOURCES)
            },
            "helpsteer3_all_mostly_or_perfectly": sum(
                v["linkage"]["verified"] and v["labels"].get("all_mostly_or_perfectly", False) for v in grouped.values()
            ),
            "wildfeedback_temporal_audit": {
                name: sum(v["temporal_audit"].get(name) is True for v in grouped.values())
                for name in (
                    "historical_agent_annotation_matches_preceding_user",
                    "historical_agent_annotation_not_target_feedback",
                    "verified_following_labels_differ_from_historical",
                )
            },
            "batches": {h["id"]: breakdown(h["records"], h["policy"], grouped) for h in histories},
            "comparison": compare(histories, grouped, rows),
            "quality": QUALITY,
            "real_api_requests": 0,
            "real_key_reads": 0,
            "live_authorized": False,
            "network_attempts_denied": guard.network_denied,
            "group_scope": "source_feedback_only_not_defect_truth_or_observed_dislike",
            "group_denominators": "Assigned runnable targets only; exclusions stay unassigned in original source totals.",
            "material_rate_denominator": "Asked plus missing-material questions within historical selected routes; not the entire catalog.",
            "source_semantics": {
                "helpsteer3": "Pinned release keeps three evaluator statements per response; strict structured prefix.",
                "wildfb": "Published target pair plus subsequent user_feedback; label from automated extraction, no human semantic confirmation.",
                "wildfeedback": "User-turn SAT/DSAT concerns the previous assistant turn; only following related User in a verified segment is linked.",
                "wildfeedback_paper": "https://aclanthology.org/2026.acl-long.1701.pdf#page=16",
            },
        }
        batch.require(
            before == [offline.fingerprint(p) for group in paths for p in group], "source_changed_during_grouping"
        )
        gate.check()
        output.mkdir(parents=True, exist_ok=False)
        offline.write_lines(output / "groups.results.jsonl", rows)
        batch.save(output / "groups.summary.json", value, new=True)
        return {
            "schema_version": signals.VERSION,
            "output_dir": str(output),
            "grouping_sha256": value["grouping_sha256"],
            "group_counts": value["group_counts"],
            "batches": {name: data["overall"]["denominators"] for name, data in value["batches"].items()},
            "real_api_requests": 0,
            "real_key_reads": 0,
            "quality": QUALITY,
        }
