"""Read-only S1 synthetic source, eligible-answer and manual-feedback reconciliation."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path

from .domain import NotFoundError, Principal
from .measurement import report_from_connection, utc
from .research import SOURCES
from .suggestions import report_from_connection as suggestion_report

INVALIDATIONS = {"invalidated", "revoked", "superseded"}


def _group(db, principal, answers, as_of, source):
    ids = [eid for answer in answers for eid in answer["action_ids"]]
    manual = report_from_connection(db, principal.tenant_ref, ids, as_of)
    rows = manual.pop("rows")
    manual.pop("cohort_kind")
    manual.pop("cohort_event_ids")
    manual["action_count"] = manual.pop("denominator")
    negative = sum(bool(answer["action_ids"]) for answer in answers)
    strata = {}
    for answer in answers:
        versions = {**answer["versions"], "protocol_ref": answer["protocol_ref"], "study_ref": answer["study_ref"]}
        key = json.dumps(versions, sort_keys=True)
        stratum = strata.setdefault(key, {"versions": versions, "eligible_answers": 0, "negative_answers": 0})
        stratum["eligible_answers"] += 1
        stratum["negative_answers"] += bool(answer["action_ids"])
    return {
        "eligible_answers": len(answers),
        "negative_answers": negative,
        "negative_answer_rate": negative / len(answers) if answers else None,
        "actor_roles": dict(Counter(a["actor_role"] for a in answers)),
        "public_label_origins": dict(Counter(a["public_label_origin"] for a in answers)),
        "invalidated_eligible_answers": sum(bool(set(a["lifecycle"]) & INVALIDATIONS) for a in answers),
        "lifecycle_counts": dict(Counter(k for a in answers for k in a["lifecycle"] if k in INVALIDATIONS)),
        "version_missing_counts": dict(Counter(k for a in answers for k, v in a["versions"].items() if v is None)),
        "displayed_actions": sum(r["display_count"] > 0 for r in rows),
        "display_coverage": sum(r["display_count"] > 0 for r in rows) / len(rows) if rows else None,
        "edited_actions": sum(r["response_counts_in_window"].get("reason_edited", 0) > 0 for r in rows),
        "late_responses": sum(r["late_response_count"] for r in rows),
        "retracted_after_window": sum(
            r["current_state"]["action_status"] == "retracted" and r["window_state"]["action_status"] != "retracted"
            for r in rows
        ),
        "missing_active_timing": sum(
            t["timing_status"] != "client_reported" for r in rows for t in r["response_timings"]
        ),
        "missing_total_timing": sum(t["server_total_ms"] is None for r in rows for t in r["response_timings"]),
        "historical_public_timing": "not_applicable" if source == "public_replay" else None,
        "timing_unit": "current_operator_explicit_response",
        "inference_retractions": None,
        "model_source_confusion": None,
        "manual": manual,
        "suggestions": suggestion_report(db, ids, as_of),
        "strata": list(strata.values()),
    }


def report(db_path: str | Path, principal: Principal, since: str, as_of: str) -> dict:
    start, cutoff = utc(since), utc(as_of)
    if start > cutoff:
        raise ValueError("Research observation interval is reversed")
    lower, upper = start.timestamp(), cutoff.timestamp()
    with sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        instance = db.execute("SELECT id FROM s1_instance").fetchone()
        subject = db.execute("SELECT 1 FROM s1_chats WHERE user_id=? LIMIT 1", (principal.actor_ref,)).fetchone()
        if not instance or instance[0] != principal.tenant_ref or not subject:
            raise NotFoundError("Research report is unavailable")
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        tracked = "s1_research_attempts" in tables
        generations = db.execute(
            "SELECT * FROM s1_generations WHERE user_id=? AND created_at<=? ORDER BY created_at,attempt_id",
            (principal.actor_ref, upper),
        ).fetchall()
        answers, counts, legacy_count = [], Counter(), 0
        source_counts = {s: Counter() for s in (*SOURCES, "missing")}
        for generation in generations:
            attempt_id = generation["attempt_id"]
            attempt = (
                db.execute("SELECT * FROM s1_research_attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
                if tracked
                else None
            )
            if attempt is None:
                legacy_count += 1
                continue
            states = db.execute(
                "SELECT kind FROM s1_research_states WHERE attempt_id=? AND recorded_at<=? ORDER BY recorded_at,seq",
                (attempt_id, upper),
            ).fetchall()
            kinds = [r[0] for r in states]
            progress = [kind for kind in kinds if kind not in INVALIDATIONS]
            registration = db.execute(
                "SELECT data FROM s1_research_sources WHERE registration_id=?", (attempt["registration_id"],)
            ).fetchone()
            source = json.loads(registration[0]) if registration else {}
            if generation["created_at"] >= lower:
                counts[progress[-1] if progress else "unknown"] += 1
                source_counts[source.get("source_kind", "missing")][progress[-1] if progress else "unknown"] += 1
            eligible = db.execute(
                "SELECT * FROM s1_research_answers WHERE attempt_id=? AND eligible_at>=? AND eligible_at<=?",
                (attempt_id, lower, upper),
            ).fetchone()
            if eligible is None:
                continue
            answers.append(
                {
                    "attempt_id": attempt_id,
                    "target": {
                        "object_type": "openwebui_assistant_message",
                        "object_id": f"{generation['chat_id']}/{generation['message_id']}",
                        "object_version": eligible["object_version"],
                    },
                    "eligible_at": eligible["eligible_at"],
                    "registration_id": attempt["registration_id"],
                    "source_kind": source.get("source_kind", "missing"),
                    "actor_role": source.get("actor_role", "unknown"),
                    "public_label_origin": source.get("public_label_origin", "unknown"),
                    "study_ref": source.get("study_ref"),
                    "protocol_ref": source.get("protocol_ref"),
                    "context_ref": source.get("context_ref"),
                    "feedback_ref": source.get("feedback_ref"),
                    "versions": json.loads(attempt["versions"]),
                    "lifecycle": kinds,
                    "action_ids": [],
                }
            )
        by_target = {json.dumps(a["target"], sort_keys=True): a for a in answers}
        unmatched = 0
        for action in db.execute(
            "SELECT * FROM actions WHERE tenant_ref=? AND actor_ref=? ORDER BY created_at,event_id",
            (principal.tenant_ref, principal.actor_ref),
        ):
            accepted_at = utc(action["created_at"]).timestamp()
            if not lower <= accepted_at <= upper:
                continue
            answer = by_target.get(json.dumps(json.loads(action["target_ref"]), sort_keys=True))
            event = db.execute(
                "SELECT payload FROM events WHERE event_id=? AND event_type='negative_feedback_action_recorded'",
                (action["event_id"],),
            ).fetchone()
            payload = json.loads(event[0]) if event else {}
            if (
                answer is None
                or accepted_at < answer["eligible_at"]
                or payload.get("interaction_contract") != "manual-v1"
                or payload.get("channel") != "openwebui-s1"
            ):
                unmatched += 1
                continue
            answer["action_ids"].append(action["event_id"])
        result = {
            "metric_version": "s1-research-v1",
            "scope": "synthetic_development_only",
            "since": start.isoformat(),
            "as_of": cutoff.isoformat(),
            "boundary": "since <= first_eligible_at <= as_of; feedback observed through as_of",
            "denominator_policy": "historically_eligible; invalidation does not remove answers",
            "participant_count": 1 if answers else 0,
            "untracked_attempt_count_through_cutoff": legacy_count,
            "attempt_status_counts_in_interval": dict(counts),
            "unmatched_actions_in_interval": unmatched,
            "groups": {
                s: {
                    **_group(db, principal, [a for a in answers if a["source_kind"] == s], as_of, s),
                    "attempt_status_counts_in_interval": dict(source_counts[s]),
                }
                for s in (*SOURCES, "missing")
            },
            "answers": answers,
        }
    result["snapshot_sha256"] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    return result


def main():
    parser = argparse.ArgumentParser(description="Read-only synthetic S1 source report; no training export")
    parser.add_argument("--db", required=True)
    parser.add_argument("--instance", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--since", required=True)
    parser.add_argument("--as-of", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            report(args.db, Principal(args.instance, args.user), args.since, args.as_of), ensure_ascii=False, indent=2
        )
    )


if __name__ == "__main__":
    main()
