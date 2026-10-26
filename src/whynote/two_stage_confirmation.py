"""Freeze a fixed policy/sample before confirmation labels; consume it once."""

import json
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from . import two_stage_batch as batch
from . import two_stage_calibration as cal
from . import two_stage_review as review
from . import two_stage_v3 as v3
from . import two_stage_v3_batch as collection

REVIEW_POLICY_FILES = (
    "calibration-review-policy-amendment-20261014-v7.json",
    "calibration-review-policy-amendment-20261014-v6.json",
    "calibration-review-policy-amendment-20261014-v5.json",
    "calibration-review-policy-amendment-20261014-v4.json",
    "calibration-review-policy-amendment-20261014-v3.json",
    "calibration-review-policy-amendment-20261014-v2.json",
    "calibration-review-policy-amendment-20261013.json",
)
REVIEW_ADDENDUM_FILE = "calibration-ai-review.addendum.json"
REVIEW_QUEUE_FILE = "calibration-second-review.plan.json"
REVIEW_PROVENANCE_FILE = "calibration-provenance-correction-reviewer01-initial.json"


def versioned_workflow_binding_matches(workspace, amendment, amendment_path):
    binding = workspace.lock["binding"]
    workflow = amendment["workflow_binding"]
    amendment_sha = batch.sha(batch.read(amendment_path))
    return (
        binding.get("review_policy_amendment_sha256") == amendment_sha
        and workflow.get("runtime_sha256") == review.fingerprint(binding["runtime"])
        and workflow.get("sample_sha256") == workspace.sample_sha256
        and workflow.get("required_prediction_run") == binding.get("required_prediction_run")
        and amendment.get("cohort_binding", {}).get("current_workflow_sample_sha256") == workspace.sample_sha256
    )


def calibration_review_policy(workspace):
    """Load the owner-authorized review amendment against its original frozen cohort."""
    amendment_path = next(
        (workspace.path / name for name in REVIEW_POLICY_FILES if (workspace.path / name).is_file()), None
    )
    if amendment_path is None:
        return None
    addendum_path = workspace.path / REVIEW_ADDENDUM_FILE
    queue_path = workspace.path / REVIEW_QUEUE_FILE
    provenance_path = workspace.path / REVIEW_PROVENANCE_FILE
    source = workspace.path / "review-policy-source"
    source_sample_path = source / "sample.manifest.json"
    source_lock_path = source / "workflow.lock.json"
    amendment = batch.decode(batch.read(amendment_path))
    addendum = batch.decode(batch.read(addendum_path))
    queue = batch.decode(batch.read(queue_path))
    provenance = batch.decode(batch.read(provenance_path))
    source_sample = batch.decode(batch.read(source_sample_path))
    source_lock = batch.decode(batch.read(source_lock_path))
    source_lock_sha = batch.sha(batch.read(source_lock_path))

    review.require(amendment["status"] == "OWNER_AUTHORIZED_EFFECTIVE", "review_policy_not_owner_authorized")
    review.require(
        batch.sha(batch.read(queue_path)) == amendment["base_plan"]["sha256"] == addendum["base_plan"]["sha256"],
        "review_policy_queue_binding_mismatch",
    )
    review.require(
        batch.sha(batch.read(source_sample_path))
        == amendment["base_plan"]["sample_sha256"]
        == addendum["base_plan"]["sample_sha256"],
        "review_policy_sample_binding_mismatch",
    )
    review.require(
        source_lock_sha == addendum["base_plan"]["workflow_lock_sha256"]
        and amendment["supersedes"]["prior_addendum_sha256"] == batch.sha(batch.read(addendum_path))
        and amendment["supersedes"]["prior_addendum_id"] == addendum["addendum_id"],
        "review_policy_history_binding_mismatch",
    )
    binding = workspace.lock["binding"]
    source_binding_matches = binding.get("review_policy_source_workflow_sha256") == source_lock_sha and binding.get(
        "review_policy_source_runtime_sha256"
    ) == review.fingerprint(source_lock["binding"]["runtime"])
    if "workflow_binding" in amendment:
        review.require(
            source_binding_matches and versioned_workflow_binding_matches(workspace, amendment, amendment_path),
            "review_policy_runtime_binding_mismatch",
        )
    else:
        review.require(
            source_binding_matches
            and review.fingerprint(binding["runtime"]) == binding["review_policy_source_runtime_sha256"],
            "review_policy_runtime_binding_mismatch",
        )

    resolution = amendment["second_review_resolution"]
    assignment = addendum["parallel_ai_review"]
    primary = queue["primary_review"]
    role_policy = amendment["project_review_policy"]
    review.require(
        resolution["effective_status"] == "SATISFIED_BY_OWNER_APPROVED_AI_REVIEW"
        and role_policy["same_role_level"]
        and role_policy["same_review_and_audit_scope"]
        and role_policy["same_material_and_evidence_requirements"]
        and role_policy["equal_decision_weight"]
        and role_policy["actual_reviewer_kind_must_be_preserved"]
        and role_policy["ai_may_satisfy_an_authorized_second_review_gate"]
        and amendment["quality_boundary"]["ai_labels_count_as_human_gold"] is False
        and resolution["reviewer_kind"] == assignment["reviewer_kind"] == "ai"
        and resolution["reviewer_id"] == assignment["reviewer_id"]
        and resolution["primary_round_id"] == assignment["round_id"]
        and resolution["locked_label_counts"].get(resolution["primary_round_id"]) == 400
        and assignment["partition"] == "calibration"
        and assignment["target_count"] == primary["locked_count"] == 400
        and assignment["reason_count"] == queue["reason_count"] == 7748,
        "review_policy_scope_mismatch",
    )
    original_rows = {
        row["target_id"]: (row["group_id"], row["input_sha256"])
        for row in source_sample["rows"]
        if row["selected"] and row["partition"] == "calibration"
    }
    current_rows = {
        row["target_id"]: (row["group_id"], row["input_sha256"])
        for row in workspace.sample["rows"]
        if row["selected"] and row["partition"] == "calibration"
    }
    queue_rows = {row["target_id"]: row for row in queue["queue"]}
    review.require(
        set(original_rows) == set(current_rows) == set(queue_rows)
        and all(
            current_rows[target] == original_rows[target] == (row["group_id"], row["input_sha256"])
            for target, row in queue_rows.items()
        ),
        "review_policy_current_cohort_mismatch",
    )
    review.require(
        provenance["schema_version"] == "jev-review-provenance-correction-v1"
        and provenance["target_count"] == len(queue_rows) == 400
        and provenance["human_statement"]["answer"] == "是，人工独立判断，AI 仅录入"
        and {item["target_id"] for item in provenance["items"]} == set(queue_rows),
        "review_policy_human_provenance_mismatch",
    )
    risk = cal.risk_config()
    reason_catalog = {reason["id"] for reason in workspace.catalog["reasons"]}
    for _, row in queue_rows.items():
        review.require(
            len(row["reason_ids"]) == len(set(row["reason_ids"])) and set(row["reason_ids"]) <= reason_catalog,
            "review_policy_invalid_reason_queue",
        )
    return {
        "amendment": amendment,
        "queue": queue_rows,
        "primary_review": primary,
        "reviewer_id": resolution["reviewer_id"],
        "reviewer_kind": resolution["reviewer_kind"],
        "round_id": resolution["primary_round_id"],
        "seed": queue["seed"],
        "other_fraction": risk["calibration_other_review_fraction"],
        "pins": {
            str(path): batch.sha(batch.read(path))
            for path in (
                amendment_path,
                addendum_path,
                queue_path,
                provenance_path,
                source_sample_path,
                source_lock_path,
            )
        },
    }


def read_labels(workspace, *, review_policy=None):
    result, pins = defaultdict(list), {}
    if review_policy is None:
        paths = sorted((workspace.path / "labels").glob("*/*.locked.json"))
    else:
        identities = (
            (review_policy["primary_review"]["reviewer_id"], review_policy["primary_review"]["round_id"]),
            (review_policy["reviewer_id"], review_policy["round_id"]),
        )
        paths = sorted(
            path
            for reviewer, round_id in identities
            for path in (workspace.path / "labels" / review.fingerprint([reviewer, round_id])).glob("*.locked.json")
        )
        allowed = set(review_policy["queue"])
        review.require(len(paths) == 2 * len(allowed), "review_policy_locked_label_count_mismatch")
    allowed = set(review_policy["queue"]) if review_policy is not None else None
    identities = (
        {
            (review_policy["primary_review"]["reviewer_id"], review_policy["primary_review"]["round_id"]),
            (review_policy["reviewer_id"], review_policy["round_id"]),
        }
        if review_policy is not None
        else None
    )
    for path in paths:
        receipt = batch.decode(batch.read(path.with_suffix(".receipt.json")))
        raw = batch.read(path, expected=receipt["sha256"])
        value = batch.decode(raw)
        if identities is not None:
            identity = (value["reviewer"]["id"], value["round_id"])
            review.require(
                identity in identities and value["target_id"] in allowed, "review_policy_label_outside_scope"
            )
            review.require(value["exposure"] == "none_reported", "review_policy_label_not_blind")
        case, _ = workspace.case(value["target_id"])
        review.validate_label(value, case, workspace.catalog)
        result[value["target_id"]].append(value)
        pins[str(path)] = receipt["sha256"]
    if review_policy is not None:
        review.require(
            all(len(result.get(target, [])) == 2 for target in allowed),
            "review_policy_reviewer_pair_incomplete",
        )
        pins.update(review_policy["pins"])
    return result, pins


def calibration_label(labels, config, *, review_policy=None, required_reason_ids=None):
    if review_policy is not None:
        human = [
            item
            for item in labels
            if item["reviewer"]["kind"] == "human"
            and item["reviewer"]["id"] == review_policy["primary_review"]["reviewer_id"]
            and item["round_id"] == review_policy["primary_review"]["round_id"]
        ]
        ai = [
            item
            for item in labels
            if item["reviewer"]["kind"] == review_policy["reviewer_kind"]
            and item["reviewer"]["id"] == review_policy["reviewer_id"]
            and item["round_id"] == review_policy["round_id"]
        ]
        if len(human) != 1 or len(ai) != 1:
            return None, {"status": "PENDING_PRIMARY_REVIEW_PAIR", "evidence_level": "none"}
        primary, second = human[0], ai[0]
        binding = ("target_id", "group_id", "input_sha256", "catalog_sha256")
        review.require(
            all(primary[key] == second[key] for key in binding)
            and primary["phase"] == second["phase"] == "independent_input_review",
            "review_policy_label_pair_binding_mismatch",
        )
        if (
            primary["exposure"] != "none_reported"
            or not primary["independent_attestation"]
            or second["exposure"] != "none_reported"
            or not second["independent_attestation"]
        ):
            return None, {"status": "EXPOSED_REVIEW_DIAGNOSTIC_ONLY", "evidence_level": "exposed_review_pair"}
        required = review.review_requirement(
            primary,
            seed=review_policy["seed"],
            other_fraction=review_policy["other_fraction"],
        )
        review.require(required == required_reason_ids, "review_policy_reason_queue_mismatch")
        pending, conflicts = [], []
        final = json.loads(json.dumps(primary))
        for reason_id in required:
            first_decision = primary["reasons"][reason_id]["decision"]
            second_decision = second["reasons"][reason_id]["decision"]
            if first_decision == "not_reviewed" or second_decision == "not_reviewed":
                pending.append(reason_id)
            elif first_decision != second_decision:
                conflicts.append(reason_id)
        for reason_id in pending + conflicts:
            final["reasons"][reason_id]["decision"] = "not_reviewed"
        route_conflicts = []
        for axis in ("task", "domain"):
            a, b = primary["routes"][axis], second["routes"][axis]
            if (a["status"], sorted(a["accepted"])) != (b["status"], sorted(b["accepted"])):
                final["routes"][axis].update(status="not_reviewed", accepted=[], evidence=[], basis="")
                route_conflicts.append(axis)
        if conflicts or route_conflicts:
            final["disagreement"] = "disputed"
        elif not pending:
            final["disagreement"] = "agreed"
        return final, {
            "status": "READY" if not pending and not conflicts and not route_conflicts else "REVIEW_INCOMPLETE",
            "required_second_review": required,
            "pending_second_review": pending,
            "unresolved_conflicts": conflicts,
            "unresolved_route_conflicts": route_conflicts,
            "evidence_level": "owner_authorized_human_ai_opinions",
            "ai_reviewer_id": review_policy["reviewer_id"],
            "ai_round_id": review_policy["round_id"],
            "ai_label_hash": review.fingerprint(second),
            "ai_labels_count_as_human_gold": False,
            "correction_rounds_included": False,
        }
    human = sorted(
        (x for x in labels if x["reviewer"]["kind"] == "human" and x["phase"] == "independent_input_review"),
        key=lambda x: (x["created_at"], x["reviewer"]["id"], x["round_id"]),
    )
    if not human:
        return None, {"status": "PENDING_HUMAN_LABEL", "evidence_level": "none"}
    originals = [item for item in human if item["disagreement"] != "adjudicated"]
    if not originals:
        return None, {"status": "PRIMARY_REVIEW_REQUIRED", "evidence_level": "none"}
    primary = originals[0]
    if primary["exposure"] != "none_reported" or not primary["independent_attestation"]:
        return None, {"status": "EXPOSED_REVIEW_DIAGNOSTIC_ONLY", "evidence_level": "exposed_human"}
    required = review.review_requirement(
        primary, seed=config["seed"], other_fraction=config["calibration_other_review_fraction"]
    )
    pending, conflicts, delayed = [], [], False
    adjudications = [
        x
        for x in human
        if x["disagreement"] == "adjudicated" and x["exposure"] == "none_reported" and x["independent_attestation"]
    ]
    final = json.loads(json.dumps(primary))
    for key in required:
        others = []
        for item in originals[1:]:
            if item["exposure"] != "none_reported" or not item["independent_attestation"]:
                continue
            different = item["reviewer"]["id"] != primary["reviewer"]["id"]
            hours = (
                datetime.fromisoformat(item["created_at"]) - datetime.fromisoformat(primary["created_at"])
            ).total_seconds() / 3600
            if different or hours >= config["minimum_delayed_review_hours"]:
                if item["reasons"][key]["decision"] != "not_reviewed":
                    others.append(item)
                    delayed |= not different
        if not others:
            pending.append(key)
        elif any(item["reasons"][key]["decision"] != primary["reasons"][key]["decision"] for item in others):
            adjudicated = [item for item in adjudications if item["reasons"][key]["decision"] != "not_reviewed"]
            if adjudicated:
                final["reasons"][key] = adjudicated[-1]["reasons"][key]
            else:
                conflicts.append(key)
    # Unresolved opinions are missing data, never silent consensus or absent.
    for key in pending + conflicts:
        final["reasons"][key]["decision"] = "not_reviewed"
    route_conflicts = []
    for axis in ("task", "domain"):
        primary_route = primary["routes"][axis]
        compared = [
            item["routes"][axis]
            for item in originals[1:]
            if item["exposure"] == "none_reported"
            and item["independent_attestation"]
            and item["routes"][axis]["status"] != "not_reviewed"
        ]
        if any(
            review.fingerprint([r["status"], sorted(r["accepted"])])
            != review.fingerprint([primary_route["status"], sorted(primary_route["accepted"])])
            for r in compared
        ):
            decisions = [a for a in adjudications if a["routes"][axis]["status"] != "not_reviewed"]
            if decisions:
                final["routes"][axis] = decisions[-1]["routes"][axis]
            else:
                final["routes"][axis].update(status="not_reviewed", accepted=[])
                route_conflicts.append(axis)
    return final, {
        "status": "READY" if not pending and not conflicts and not route_conflicts else "REVIEW_INCOMPLETE",
        "required_second_review": required,
        "pending_second_review": pending,
        "unresolved_conflicts": conflicts,
        "unresolved_route_conflicts": route_conflicts,
        "evidence_level": "single_human_delayed" if delayed else "human_initial_and_review",
        "raw_label_hashes": [review.fingerprint(x) for x in labels],
    }


def prediction_run(workspace, partition):
    if partition == "confirmation":
        frozen = batch.decode(batch.read(workspace.path / "confirmation.freeze.json"))
        review.require(review.fingerprint(frozen["manifest"]) == frozen["id"], "confirmation_freeze_changed")
        return frozen["manifest"]["primary_run"]
    return workspace.lock["binding"]["required_prediction_run"]


def validate_requests(record, value, catalog, policy):
    """Reconstruct complete wire hashes and outcomes from the persisted responses."""
    route, route_state, questions = v3.plan_route(value, catalog)
    expected = {"route": (route, questions)}
    stages = [s for s in record["stages"] if "request_sha256" in s]
    route_stage = next((s for s in stages if s["stage"] == "route" and s["status"] == "completed"), None)
    planned, routes = None, None
    if route_stage:
        routes = v3.select_routes(route_stage["answers"], policy)
        planned = v3.plan_reasons(value, routes, catalog)
        expected.update({p["record"]["stage"]: (p["record"], p["questions"]) for p in planned["packets"]})
    answers = {}
    for stage in stages:
        review.require(stage["stage"] in expected, "prediction_unplanned_packet")
        plan, question_set = expected[stage["stage"]]
        for field in ("request_sha256", "request_utf8_bytes", "question_ids", "information_scope"):
            review.require(stage[field] == plan[field], "prediction_request_binding_mismatch")
        review.require(plan["status"] == "ready", "prediction_sent_over_budget")
        if stage["status"] == "completed":
            response = {
                "model": v3.MODEL,
                "usage": stage["usage"],
                "answers": {k: a | {"type": "choice"} for k, a in stage["answers"].items()},
            }
            validated, _ = v3.legacy.validate_response(review.encoded(response), question_set)
            review.require(validated == stage["answers"], "prediction_answer_contract_mismatch")
            if stage["stage"] != "route":
                answers.update(validated)
    result = record["result"]
    if result:
        review.require(planned is not None, "prediction_result_without_route")
        expected_result = v3.outcome(
            value,
            catalog,
            policy,
            routes,
            planned["rows"],
            answers,
            result["stages"],
            technical_status=record["technical_status"],
        )
        review.require(result == expected_result, "prediction_outcome_changed")
        persisted = {
            s["stage"]: {k: v for k, v in s.items() if k not in {"held_reservation", "known_usage_estimate"}}
            for s in stages
        }
        review.require({s["stage"]: s for s in result["stages"]} == persisted, "prediction_stage_result_mismatch")


def predictions(workspace, path, *, partition="calibration"):
    if path is None:
        return {}, {}
    path = Path(path).resolve()
    manifest = batch.decode(batch.read(path / "plan.json"))
    review.require(
        manifest["runtime"] == workspace.lock["binding"]["runtime"], "prediction_protocol_does_not_match_review"
    )
    review.require(manifest["batch_id"] == prediction_run(workspace, partition), "predeclared_primary_run_required")
    policy = v3.Policy(**manifest["config"]["policy"])
    target_ids = collection.collection_scope(workspace, manifest["batch_id"], policy, live=True)
    review.require(
        manifest == collection.collect_plan(workspace, manifest["batch_id"], manifest["config"], target_ids=target_ids),
        "prediction_manifest_changed",
    )
    raw = batch.read(path / "live.results.jsonl", 256 * 1024 * 1024)
    records = [batch.decode(line) for line in raw.splitlines()]
    review.require(len({r["target_id"] for r in records}) == len(records), "duplicate_prediction_target")
    review.require(
        all(r.get("synthetic") is False and r.get("mode") == "live" for r in records), "mock_cannot_calibrate"
    )
    ledger = batch.Ledger(path / "live.sqlite3", manifest, "live", readonly=True)
    try:
        review.require(
            {r[0] for r in ledger.db.execute("SELECT id FROM targets")}
            == {t["target_id"] for t in manifest["targets"] if t["disposition"] == "runnable"},
            "prediction_ledger_population_mismatch",
        )
        review.require(records == collection.ledger_records(manifest, ledger), "prediction_ledger_mismatch")
    finally:
        ledger.close()
    selected = {r["target_id"] for r in workspace.sample["rows"] if r["selected"] and r["partition"] == partition}
    for record in records:
        if record["target_id"] in selected:
            _, value = workspace.case(record["target_id"])
            review.require(record["input_sha256"] == value["input_sha256"], "prediction_input_mismatch")
            validate_requests(record, value, workspace.catalog, policy)
    return {r["target_id"]: r for r in records}, {
        str(path / "live.results.jsonl"): batch.sha(raw),
        str(path / "plan.json"): batch.sha(batch.read(path / "plan.json")),
        str(path / "live.sqlite3"): batch.sha(batch.read(path / "live.sqlite3", 256 * 1024 * 1024)),
    }


def dataset(workspace, prediction_dir, *, partition):
    workspace.check()
    policy = calibration_review_policy(workspace) if partition == "calibration" else None
    labels, pins = read_labels(workspace, review_policy=policy)
    predicted, prediction_pins = predictions(workspace, prediction_dir, partition=partition)
    pins.update(prediction_pins)
    plan_rows = {
        r["target_id"]: r
        for r in (
            batch.decode(line)
            for line in batch.read(workspace.path / "size-plan.jsonl", 128 * 1024 * 1024).splitlines()
        )
    }
    records, evidence = [], {}
    config = cal.risk_config()
    for selected in workspace.sample["rows"]:
        if not selected["selected"] or selected["partition"] != partition:
            continue
        key = selected["target_id"]
        opinions = labels.get(key, [])
        if partition == "calibration":
            label, quality = calibration_label(
                opinions,
                config,
                review_policy=policy,
                required_reason_ids=policy["queue"].get(key, {}).get("reason_ids") if policy else None,
            )
        elif opinions:
            adjudicated = [item for item in opinions if item["disagreement"] == "adjudicated"]
            original = [item for item in opinions if item["disagreement"] != "adjudicated"]
            quality = (
                review.reconcile_labels(original, formal=True, adjudication=adjudicated[-1] if adjudicated else None)
                if original
                else {"status": "INSUFFICIENT_EVIDENCE"}
            )
            label = quality.get("final_label") if quality["status"] == "READY" else None
        else:
            label, quality = None, {"status": "PENDING_HUMAN_LABEL"}
        evidence[key] = {k: v for k, v in quality.items() if k not in {"raw_labels", "final_label"}}
        prediction = predicted.get(key)
        if prediction:
            review.require(prediction["input_sha256"] == selected["input_sha256"], "prediction_input_mismatch")
        records.append(
            {
                "target_id": key,
                "group_id": selected["group_id"],
                "partition": partition,
                "unit_aliases": selected["unit_aliases"],
                "source": selected["source"],
                "runtime": workspace.lock["binding"]["runtime"],
                "run_id": prediction_run(workspace, partition),
                "input_sha256": selected["input_sha256"],
                "expected_input_sha256": selected["input_sha256"],
                "synthetic": False,
                "prediction": prediction.get("result") if prediction else None,
                "prediction_status": prediction["technical_status"] if prediction else "not_collected",
                "label": label,
                "evidence_eligible_reason_ids": plan_rows[key]["evidence_eligible_reason_ids"],
            }
        )
    return records, evidence, pins


class ConfirmationRegistry:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS studies(id TEXT PRIMARY KEY, manifest TEXT NOT NULL, state TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS units(unit TEXT PRIMARY KEY, study TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY, study TEXT NOT NULL, event TEXT NOT NULL, payload TEXT NOT NULL);
        """)

    def close(self):
        self.db.close()

    def forbid_search(self, records):
        for row in records:
            for key in ["group:" + row["group_id"], "target:" + row["target_id"], *row.get("unit_aliases", [])]:
                review.require(
                    self.db.execute("SELECT 1 FROM units WHERE unit=?", (key,)).fetchone() is None,
                    "confirmation_unit_cannot_return_to_search",
                )

    def freeze(self, manifest):
        identity = review.fingerprint(manifest)
        with self.db:
            review.require(
                self.db.execute("SELECT 1 FROM studies WHERE id=?", (identity,)).fetchone() is None,
                "confirmation_already_frozen",
            )
            for unit in manifest["units"]:
                review.require(
                    self.db.execute("SELECT 1 FROM units WHERE unit=?", (unit,)).fetchone() is None,
                    "confirmation_group_already_used_new_batch_required",
                )
            self.db.execute("INSERT INTO studies VALUES (?,?,'frozen')", (identity, review.encoded(manifest).decode()))
            self.db.executemany("INSERT INTO units VALUES (?,?)", [(unit, identity) for unit in manifest["units"]])
            self.db.execute(
                "INSERT INTO events(study,event,payload) VALUES (?,'frozen',?)",
                (identity, review.encoded({"at": review.timestamp()}).decode()),
            )
        return identity

    def consume(self, identity, manifest):
        with self.db:
            found = self.db.execute("SELECT manifest,state FROM studies WHERE id=?", (identity,)).fetchone()
            review.require(
                found is not None and found[0] == review.encoded(manifest).decode(), "confirmation_freeze_changed"
            )
            review.require(found[1] == "frozen", "confirmation_consumed_new_predeclared_batch_required")
            changed = self.db.execute("UPDATE studies SET state='consumed' WHERE id=? AND state='frozen'", (identity,))
            review.require(changed.rowcount == 1, "confirmation_consumed_new_predeclared_batch_required")
            self.db.execute(
                "INSERT INTO events(study,event,payload) VALUES (?,'consumed_before_analysis',?)",
                (identity, review.encoded({"at": review.timestamp()}).decode()),
            )


def freeze(workspace, search_result, registry):
    workspace.check()
    review.require(
        search_result["status"] == "CANDIDATE_SELECTED_NOT_CONFIRMED" and search_result["selected_policy"],
        "no_selected_calibration_policy",
    )
    review.require(
        search_result["runtime"] == workspace.lock["binding"]["runtime"]
        and search_result["primary_run"] == workspace.lock["binding"]["required_prediction_run"],
        "search_freeze_binding_mismatch",
    )
    review.require(search_result["analysis"] == workspace.lock["binding"]["analysis"], "search_analysis_changed")
    for name, pin in search_result["data_pins"].items():
        review.require(batch.sha(batch.read(Path(name), 256 * 1024 * 1024)) == pin, "search_evidence_changed")
    selected = [r for r in workspace.sample["rows"] if r["partition"] == "confirmation" and r["selected"]]
    review.require(bool(selected), "confirmation_population_empty")
    for row in selected:
        review.require(
            not list((workspace.path / "labels").glob("*/" + row["target_id"] + ".*.json")),
            "confirmation_labels_exist_before_freeze",
        )
    units = sorted(
        {
            unit
            for row in selected
            for unit in ["group:" + row["group_id"], "target:" + row["target_id"], *row["unit_aliases"]]
        }
    )
    manifest = {
        "schema_version": "jev-fixed-confirmation-v1",
        "policy": search_result["selected_policy"],
        "search_sha256": review.fingerprint(search_result),
        "runtime": search_result["runtime"],
        "risk_config": search_result["risk_config"],
        "sample_sha256": workspace.sample_sha256,
        "units": units,
        "target_ids": [r["target_id"] for r in selected],
        "group_count": len(selected),
        "calibration_primary_run": search_result["primary_run"],
        "primary_run": "v3-confirm-"
        + review.fingerprint([search_result["selected_policy"], workspace.sample_sha256])[:16],
        "analysis": workspace.lock["binding"]["analysis"],
        "sampling": "equal_group_weight_one_preselected_target",
        "scope": workspace.sample["confirmation_scope"],
        "planned_effective_sizes": cal.sample_plan(search_result["risk_config"], available_groups=len(selected)),
        "fixed_at": review.timestamp(),
        "optional_stopping_allowed": False,
    }
    review.require(not (workspace.path / "confirmation.freeze.json").exists(), "confirmation_already_frozen")
    identity = registry.freeze(manifest)
    batch.save(workspace.path / "confirmation.freeze.json", {"id": identity, "manifest": manifest}, new=True)
    return manifest


def confirm(workspace, records, registry):
    frozen = batch.decode(batch.read(workspace.path / "confirmation.freeze.json"))
    manifest = frozen["manifest"]
    review.require(
        manifest["sample_sha256"] == workspace.sample_sha256
        and manifest["runtime"] == workspace.lock["binding"]["runtime"],
        "confirmation_binding_changed",
    )
    review.require(
        {r["target_id"] for r in records} == set(manifest["target_ids"]), "fixed_confirmation_population_changed"
    )
    cal.validate_units(
        records, partition="confirmation", binding=manifest["runtime"], primary_run=manifest["primary_run"]
    )
    registry.consume(frozen["id"], manifest)
    result = cal.evaluate(records, v3.Policy(**manifest["policy"]), workspace.catalog, manifest["risk_config"])
    statistical = (
        all(r["status"] == "PASS" for r in result["risks"].values())
        and not result["counterfactual_not_evaluated"]
        and not result["missing_prediction_targets"]
    )
    scope = manifest["scope"]
    return result | {
        "schema_version": "jev-confirmation-result-v1",
        "confirmation_id": frozen["id"],
        "fixed_policy": manifest["policy"],
        "scope": scope,
        "status": "INTERNAL_CONFIRMATION_PASS"
        if statistical and scope == "internal_exposed_population"
        else "PASS_ON_DECLARED_FRESH_DISTRIBUTION"
        if statistical
        else "INSUFFICIENT_EVIDENCE_OR_RISK_NOT_CONFIRMED",
        "formal_independent_validation": statistical and scope == "fresh_unexposed_case_groups",
        "policy_reselection_permitted": False,
        "reuse_confirmation_permitted": False,
        "production_approved": False,
    }
