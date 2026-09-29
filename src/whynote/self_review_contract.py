"""D-20 metadata contracts; no raw context, model invocation or event writes."""

import hashlib
import random
import re
from datetime import datetime, timedelta
from itertools import permutations
from pathlib import Path

from .laya_local import MODEL, QUESTIONS
from .replay import POOL
from .replay_laya import require
from .source_mapping import digest
from .task_reasons import CATALOG_SHA256, TASK_TYPES

PROTOCOL_ID = "M54A-E-v0.1"
LEGACY = list(QUESTIONS["primary_reason"]["criteria"])[:-1]


def fields(value, names, code):
    require(isinstance(value, dict) and set(value) == set(names.split()), code)


def token(value):
    require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:@/-]{1,180}", value), "invalid_identifier")


def sha(value):
    require(isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value), "invalid_sha256")


def timestamp(value):
    require(isinstance(value, str), "invalid_timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        require(False, "invalid_timestamp")
    require(parsed.tzinfo is not None, "invalid_timestamp")
    return parsed


def ids(values, allowed=None):
    require(isinstance(values, list) and all(isinstance(v, str) for v in values), "invalid_ids")
    require(len(values) == len(set(values)), "duplicate_id")
    if allowed is not None:
        require(set(values) <= set(allowed), "invalid_reason_ids")


def budget_ok(item):
    return (
        item["input_utf8_bytes"] <= 8192
        and item["state_tokens"] <= 700
        and item["head_tokens"] <= 256
        and item["total_tokens"] <= 1024
        and item["max_option_tokens"] <= 48
        and item["option_total_tokens"] <= 240
        and not item["reserved_token"]
    )


def schedule(samples):
    """Stable order independent of JSON input order; six permutations, ten each."""
    keys = sorted(s["input_id"] for s in samples if s["partition"] == "validation")
    require(len(keys) == 60 and len(set(keys)) == 60, "invalid_validation_size")
    rng = random.Random(42)
    rng.shuffle(keys)
    orders = list(permutations("ABC")) * 10
    rng.shuffle(orders)
    return [{"input_id": key, "schemes": list(order)} for key, order in zip(keys, orders, strict=True)]


def repeat_ids(samples):
    rng = random.Random(42)
    selected = []
    for code, count in ((True, 13), (False, 7)):
        keys = sorted(
            s["input_id"] for s in samples if s["partition"] == "validation" and (s["task"] == "code_rewrite") == code
        )
        require(len(keys) >= count, "invalid_repeat_strata")
        selected.extend(rng.sample(keys, count))
    return sorted(selected)


def validate_batch(manifest):
    fields(
        manifest,
        "schema_version mode protocol_id package_sha256 package_frozen_at selected_at seed admissions samples",
        "invalid_batch",
    )
    require(manifest["schema_version"] == "m54a-self-review-batch-v1", "invalid_batch_version")
    require(manifest["mode"] in ("synthetic", "real"), "invalid_mode")
    require(manifest["protocol_id"] == PROTOCOL_ID, "protocol_mismatch")
    require(manifest["package_sha256"] == CATALOG_SHA256, "package_mismatch")
    require(timestamp(manifest["package_frozen_at"]) < timestamp(manifest["selected_at"]), "selection_before_package")
    require(type(manifest["seed"]) is int and manifest["seed"] == 42, "seed_mismatch")
    admissions = manifest["admissions"]
    require(isinstance(admissions, list) and admissions, "missing_admission")
    batches = {}
    for admission in admissions:
        fields(
            admission,
            "batch_id source revision file_path file_sha256 controlled_directory accessors purpose license_chain_ref "
            "retention_ref deletion_ref backup_ref exclusions_sha256 row_start row_end status approver_ref "
            "approved_at expires_at",
            "invalid_admission",
        )
        for key in (
            "batch_id",
            "source",
            "revision",
            "purpose",
            "license_chain_ref",
            "retention_ref",
            "deletion_ref",
            "backup_ref",
            "approver_ref",
        ):
            token(admission[key])
        require(admission["batch_id"] not in batches, "duplicate_batch")
        require(admission["status"] == "ADMITTED", "batch_not_admitted")
        for key in ("file_sha256", "exclusions_sha256"):
            sha(admission[key])
        ids(admission["accessors"])
        require(admission["accessors"] and admission["approver_ref"] in admission["accessors"], "invalid_accessors")
        for accessor in admission["accessors"]:
            token(accessor)
        for key in ("file_path", "controlled_directory"):
            require(isinstance(admission[key], str) and Path(admission[key]).is_absolute(), "invalid_controlled_path")
        require(
            type(admission["row_start"]) is int
            and type(admission["row_end"]) is int
            and 0 <= admission["row_start"] <= admission["row_end"],
            "invalid_row_range",
        )
        require(timestamp(admission["approved_at"]) < timestamp(admission["expires_at"]), "invalid_admission_time")
        batches[admission["batch_id"]] = admission
    samples = manifest["samples"]
    require(isinstance(samples, list) and len(samples) == 90, "invalid_sample_count")
    seen, groups, targets = set(), set(), set()
    for s in samples:
        fields(
            s,
            "input_id batch_id row_id target_key group_ids state_sha256 partition task language exposure_flag "
            "input_utf8_bytes state_tokens head_tokens total_tokens max_option_tokens option_total_tokens "
            "reserved_token material_ready budget_evidence_sha256",
            "invalid_sample",
        )
        sha(s["input_id"])
        sha(s["state_sha256"])
        sha(s["budget_evidence_sha256"])
        token(s["target_key"])
        require(isinstance(s["batch_id"], str) and s["batch_id"] in batches, "unknown_batch")
        batch = batches[s["batch_id"]]
        require(type(s["row_id"]) is int and batch["row_start"] <= s["row_id"] <= batch["row_end"], "row_not_admitted")
        target = (batch["source"], batch["revision"], s["row_id"])
        require(target not in targets, "duplicate_source_row")
        targets.add(target)
        require(s["input_id"] not in seen, "duplicate_sample")
        seen.add(s["input_id"])
        fields(s["group_ids"], "source conversation near_duplicate", "invalid_groups")
        # Values are shared group hashes across sources, not dataset names.
        for kind, value in s["group_ids"].items():
            sha(value)
            require((kind, value) not in groups, "group_leakage")
            groups.add((kind, value))
        require(s["partition"] in ("exploration", "validation"), "invalid_partition")
        require(s["task"] in TASK_TYPES, "invalid_task")
        require(s["language"] in ("zh_native", "zh_mixed_native", "other", "translated_zh"), "invalid_language")
        for key in ("exposure_flag", "material_ready", "reserved_token"):
            require(type(s[key]) is bool, "invalid_boolean")
        require(s["partition"] != "validation" or not s["exposure_flag"], "exposed_validation")
        for key in (
            "input_utf8_bytes",
            "state_tokens",
            "head_tokens",
            "total_tokens",
            "max_option_tokens",
            "option_total_tokens",
        ):
            require(type(s[key]) is int and s[key] > 0, "invalid_budget_measurement")
        require(s["total_tokens"] >= s["state_tokens"] + s["head_tokens"] + 4, "inconsistent_budget")
    for partition, count, code_count in (("exploration", 30, 20), ("validation", 60, 40)):
        subset = [s for s in samples if s["partition"] == partition]
        require(len(subset) == count, "invalid_partition_size")
        require(sum(s["task"] == "code_rewrite" for s in subset) == code_count, "invalid_task_quota")
    validation = [s for s in samples if s["partition"] == "validation"]
    require(sum(s["language"] in ("zh_native", "zh_mixed_native") for s in validation) >= 30, "native_zh_quota")
    require(sum(s["input_utf8_bytes"] > 1024 for s in validation) >= 10, "length_quota")
    return {s["input_id"]: s for s in validation}


def validate_labels(labels, samples):
    fields(labels, "schema_version review_origin reviewer_ref sealed_at first repeat final", "invalid_labels")
    require(labels["schema_version"] == "m54a-self-reviewed-blind-v1", "invalid_labels_version")
    require(labels["review_origin"] == "self_reviewed_blind", "invalid_review_origin")
    token(labels["reviewer_ref"])
    sealed = timestamp(labels["sealed_at"])
    validation = {s["input_id"] for s in samples if s["partition"] == "validation"}
    passes = {}
    for name in ("first", "repeat", "final"):
        rows = labels[name]
        require(isinstance(rows, list), "invalid_label_pass")
        by_id = {}
        for row in rows:
            fields(
                row,
                "input_id verdict reason_ids legacy_reason_codes reference_task reference_domain "
                "labeled_at evidence_sha256",
                "invalid_label",
            )
            key = row["input_id"]
            require(isinstance(key, str) and key in validation and key not in by_id, "invalid_label_identity")
            require(row["verdict"] in ("defect", "no_defect", "unjudgeable"), "invalid_verdict")
            for field, allowed in (("reason_ids", POOL), ("legacy_reason_codes", LEGACY)):
                ids(row[field], allowed)
                require(row["verdict"] == "defect" or not row[field], "invalid_label_reasons")
            require(
                row["reference_task"] in TASK_TYPES and row["reference_domain"] in ("code", "general"),
                "invalid_reference_route",
            )
            sha(row["evidence_sha256"])
            require(timestamp(row["labeled_at"]) <= sealed, "label_after_seal")
            by_id[key] = row
        expected = set(repeat_ids(samples)) if name == "repeat" else validation
        require(set(by_id) == expected, "incomplete_label_pass")
        passes[name] = by_id
    differences = []
    for key, repeat in passes["repeat"].items():
        first = passes["first"][key]
        require(
            timestamp(repeat["labeled_at"]) - timestamp(first["labeled_at"]) >= timedelta(hours=48), "repeat_too_early"
        )
        if repeat["verdict"] != first["verdict"] or any(
            set(repeat[k]) != set(first[k]) for k in ("reason_ids", "legacy_reason_codes")
        ):
            differences.append(key)
    for key, final in passes["final"].items():
        previous = passes["repeat"].get(key, passes["first"][key])
        require(timestamp(final["labeled_at"]) >= timestamp(previous["labeled_at"]), "final_before_review")
    return passes["final"], sorted(differences)


def validate_run(run, manifest, labels):
    fields(
        run,
        "schema_version mode manifest_sha256 labels_sha256 protocol_sha256 source_sha256 "
        "environment_sha256 model started_at schedule freeze run_id",
        "invalid_run",
    )
    require(run["schema_version"] == "m54a-self-review-run-v1", "invalid_run_version")
    require(run["mode"] == manifest["mode"] and run["model"] == MODEL, "run_mode_or_model_mismatch")
    for key in ("protocol_sha256", "source_sha256", "environment_sha256"):
        sha(run[key])
    require(run["manifest_sha256"] == digest(manifest) and run["labels_sha256"] == digest(labels), "seal_mismatch")
    require(run["run_id"] == digest({k: v for k, v in run.items() if k != "run_id"}), "run_hash_mismatch")
    require(run["schedule"] == schedule(manifest["samples"]), "schedule_mismatch")
    require(timestamp(labels["sealed_at"]) < timestamp(run["started_at"]), "prediction_before_seal")
    if manifest["mode"] == "synthetic":
        require(run["freeze"] is None, "synthetic_cannot_freeze")
        return
    freeze = run["freeze"]
    fields(
        freeze,
        "status operator_ref confirmed_at manifest_sha256 labels_sha256 protocol_sha256 source_sha256 "
        "environment_sha256 outbound_evidence_sha256 concurrency_evidence_sha256 cancel_evidence_sha256",
        "missing_execution_freeze",
    )
    require(freeze["status"] == "FROZEN_FOR_SELF_REVIEW", "execution_not_frozen")
    token(freeze["operator_ref"])
    require(
        timestamp(labels["sealed_at"]) <= timestamp(freeze["confirmed_at"]) < timestamp(run["started_at"]),
        "invalid_freeze_time",
    )
    for key in ("manifest_sha256", "labels_sha256", "protocol_sha256", "source_sha256", "environment_sha256"):
        require(freeze[key] == run[key], "freeze_hash_mismatch")
    for key in ("outbound_evidence_sha256", "concurrency_evidence_sha256", "cancel_evidence_sha256"):
        sha(freeze[key])


def verify_admitted_files(manifest, run, labels):
    """Only after structural checks; never download, parse or echo source content."""
    for batch in manifest["admissions"]:
        require(
            labels["reviewer_ref"] in batch["accessors"] and run["freeze"]["operator_ref"] in batch["accessors"],
            "accessor_not_admitted",
        )
        require(
            timestamp(batch["approved_at"]) <= min(timestamp(r["labeled_at"]) for r in labels["first"])
            and timestamp(run["started_at"]) < timestamp(batch["expires_at"]),
            "admission_not_current_at_run",
        )
        try:
            directory = Path(batch["controlled_directory"]).resolve(strict=True)
            path = Path(batch["file_path"]).resolve(strict=True)
            require(directory.is_dir() and path.is_file() and path.is_relative_to(directory), "file_outside_control")
            with path.open("rb") as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
        except (OSError, ValueError):
            require(False, "admitted_file_unavailable")
        require(actual == batch["file_sha256"], "admitted_file_changed")
