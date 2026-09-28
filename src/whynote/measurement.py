"""Read-only synthetic cohort reporting for the signed manual-v1 contract."""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median

from .domain import project

WINDOW_SECONDS = 86400
RESPONSES = {"reason_selected", "reason_edited", "reason_none_matched", "reason_skipped", "reason_declined"}
FILLED = {"reason_selected", "reason_edited"}


def duration_summary(values: list[float]) -> dict:
    values = sorted(values)
    n = len(values)
    return {
        "n": n,
        "median": median(values) if n else None,
        "p90": values[math.ceil(n * 0.9) - 1] if n else None,
        "percentile_method": "nearest_rank",
        "unit": "explicit_response",
    }


def utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("report timestamps require a timezone")
    return parsed.astimezone(timezone.utc)


def timing_payload(timing: dict | None, display: dict, server_total_ms: float) -> dict:
    result = {"active_ms": None, "timing_status": "missing", "server_total_ms": max(0, server_total_ms)}
    if server_total_ms < 0:
        result.update(server_total_ms=None, timing_status="server_clock_reversed")
        return result
    if timing is None:
        return result
    if not display.get("client_session_ref"):
        result["timing_status"] = "legacy_display"
    elif timing.get("session_ref") != display["client_session_ref"]:
        result["timing_status"] = "cross_session"
    elif timing.get("display_id") != display["display_id"] or timing.get("version") != "active-v1":
        result["timing_status"] = "invalid_binding"
    else:
        active, elapsed = timing.get("active_ms"), timing.get("elapsed_ms")
        if (
            any(
                isinstance(v, bool)
                or not isinstance(v, (float, int))
                or (isinstance(v, float) and not math.isfinite(v))
                for v in (active, elapsed)
            )
            or not 0 <= active <= elapsed <= server_total_ms + 1000
        ):
            result["timing_status"] = "invalid_duration"
        else:
            result.update(active_ms=active, timing_status="client_reported")
    return result


def report(db_path: str | Path, tenant_ref: str, event_ids: list[str], as_of: str) -> dict:
    """Use an explicitly fixed synthetic cohort; never infer eligibility from gate results."""
    # No EventStore construction: reporting must not migrate or write to the database.
    with sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        return report_from_connection(db, tenant_ref, event_ids, as_of)


def report_from_connection(db: sqlite3.Connection, tenant_ref: str, event_ids: list[str], as_of: str) -> dict:
    """Reuse an existing read transaction so research cohorts and events share a snapshot."""
    at = utc(as_of)
    ids = sorted(set(event_ids))
    rows = []
    for eid in ids:
        owner = db.execute("SELECT tenant_ref FROM actions WHERE event_id=?", (eid,)).fetchone()
        if owner is None or owner[0] != tenant_ref:
            raise ValueError("cohort contains an unavailable action")
        events = [dict(e) for e in db.execute("SELECT * FROM events WHERE event_id=? ORDER BY seq", (eid,))]
        for event in events:
            event["payload"] = json.loads(event["payload"])
        accepted = next(e for e in events if e["event_type"] == "negative_feedback_action_recorded")
        start = utc(accepted["recorded_at"])
        if start > at:
            raise ValueError("cohort includes actions accepted after as_of")
        end = start + timedelta(seconds=WINDOW_SECONDS)
        observed = [e for e in events if utc(e["recorded_at"]) <= at]
        window = [e for e in observed if start <= utc(e["recorded_at"]) < end]
        explicit = [
            e for e in window if e["event_type"] in RESPONSES and e["payload"].get("explicit_submission") is True
        ]
        all_responses = [
            e for e in observed if e["event_type"] in RESPONSES and e["payload"].get("explicit_submission") is True
        ]
        displayed = any(e["event_type"] == "reason_displayed" for e in window)
        retracted = any(e["event_type"] == "action_retracted" for e in window)
        status = (
            "retracted"
            if retracted
            else (
                explicit[-1]["event_type"].removeprefix("reason_")
                if explicit
                else ("pending" if at < end else ("unresponded" if displayed else "display_unknown"))
            )
        )
        timing = [
            e["payload"].get("measurement", {"active_ms": None, "timing_status": "legacy", "server_total_ms": None})
            for e in all_responses
        ]
        rows.append(
            {
                "event_id": eid,
                "accepted_at": accepted["recorded_at"],
                "window_end": end.isoformat(),
                "window_status": status,
                "window_complete": at >= end,
                "first_response": all_responses[0]["event_type"] if all_responses else None,
                "first_response_in_window": explicit[0]["event_type"] if explicit else None,
                "first_response_at": all_responses[0]["recorded_at"] if all_responses else None,
                "filled_in_window": any(e["event_type"] in FILLED for e in explicit),
                "late_response_count": sum(utc(e["recorded_at"]) >= end for e in all_responses),
                "close_count": sum(e["event_type"] == "reason_menu_closed" for e in observed),
                "display_count": sum(e["event_type"] == "reason_displayed" for e in observed),
                "current_state": project(observed),
                "window_state": project(window),
                "response_counts_in_window": dict(Counter(e["event_type"] for e in explicit)),
                "late_response": any(utc(e["recorded_at"]) >= end for e in all_responses),
                "response_timings": timing,
                "action_events": sum(e["event_type"] == "negative_feedback_action_recorded" for e in observed),
                "gate_outbox": db.execute(
                    "SELECT COUNT(*) FROM outbox WHERE event_id=? AND topic='feedback.gate.requested'", (eid,)
                ).fetchone()[0],
                "contract": accepted["payload"].get("interaction_contract", "legacy"),
            }
        )
    durations = sorted(
        t["active_ms"] for row in rows for t in row["response_timings"] if t["timing_status"] == "client_reported"
    )
    numerator = sum(row["filled_in_window"] for row in rows)
    return {
        "metric_version": "manual-v1",
        "as_of": at.isoformat(),
        "window_seconds": WINDOW_SECONDS,
        "window_boundary": "accepted_at <= response_at < window_end",
        "cohort_kind": "explicit_synthetic",
        "cohort_event_ids": ids,
        "denominator": len(rows),
        "matured_action_count": sum(row["window_complete"] for row in rows),
        "provisional": any(not row["window_complete"] for row in rows),
        "filled_numerator": numerator,
        "fill_rate": numerator / len(rows) if rows else None,
        "window_status_counts": dict(Counter(r["window_status"] for r in rows)),
        "retracted_action_count": sum(r["current_state"]["action_status"] == "retracted" for r in rows),
        "response_action_counts_in_window": {
            kind: sum(kind in row["response_counts_in_window"] for row in rows) for kind in sorted(RESPONSES)
        },
        "close_count": sum(r["close_count"] for r in rows),
        "closed_action_count": sum(r["close_count"] > 0 for r in rows),
        "active_ms": duration_summary(durations),
        "server_total_ms": duration_summary(
            [t["server_total_ms"] for row in rows for t in row["response_timings"] if t["server_total_ms"] is not None]
        ),
        "timing_status_counts": dict(Counter(t["timing_status"] for r in rows for t in r["response_timings"])),
        "rows": rows,
    }


def main():
    parser = argparse.ArgumentParser(description="Read-only synthetic manual-v1 cohort report")
    parser.add_argument("--db", required=True)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--cohort", required=True, help="JSON array of explicitly eligible synthetic event IDs")
    parser.add_argument("--as-of", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            report(args.db, args.tenant, json.loads(Path(args.cohort).read_text(encoding="utf-8")), args.as_of),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
