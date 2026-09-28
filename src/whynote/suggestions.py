"""M5-2a synthetic suggestion receipts in the existing S1 event ledger.

Local operator API only. No context reads, model calls or browser write routes.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections import Counter

from .domain import ConflictError, NotFoundError, Principal, project
from .measurement import utc
from .research import validate_config
from .task_reasons import CATALOG_SHA256, CRITERIA_VERSION, PACKAGE_VERSION, TEMPLATE_VERSION, validate_selection

CONTRACT = "m5-suggestion-v1"
UI_VERSION = "m5-suggestion-mock-v1"
TTL_SECONDS = 60
OPERATIONS = {"yes", "no", "none_matched", "skip", "close", "decline", "correct"}


def _uuid(value):
    if not isinstance(value, str):
        raise ValueError("Reference must be a UUID")
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError
    except ValueError:
        raise ValueError("Reference must be a canonical UUID") from None
    return value


def _versions(config):
    revision = config.get("suggestion_model_revision")
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Synthetic model revision is required")
    return {
        "model_revision": revision,
        "package_version": PACKAGE_VERSION,
        "criteria_version": CRITERIA_VERSION,
        "template_version": TEMPLATE_VERSION,
        "catalog_sha256": CATALOG_SHA256,
        "ui_version": UI_VERSION,
    }


def _admit(store, db, principal, event_id):
    config = store.config
    validate_config(config)
    if (
        config.get("enabled") is not True
        or config.get("research_enabled") is not True
        or config.get("suggestion_research_enabled") is not True
        or config.get("mode") != "mock"
        or principal != Principal(config["instance_id"], config["user_id"])
    ):
        raise NotFoundError("Synthetic suggestions are unavailable")
    action = store._require_owner(db, principal, event_id)
    events = store._events(db, event_id)
    if project(events)["action_status"] != "active":
        raise ConflictError("Feedback action is retracted")
    target = json.loads(action["target_ref"])
    if target.get("object_type") != "openwebui_assistant_message":
        raise NotFoundError("Research target is unavailable")
    chat_id, message_id = target["object_id"].split("/")
    receipt = store.receipt(db, chat_id, principal.actor_ref, message_id)
    tracked = db.execute(
        "SELECT a.object_version, r.data FROM s1_research_answers a "
        "JOIN s1_research_attempts t ON t.attempt_id=a.attempt_id "
        "JOIN s1_research_sources r ON r.registration_id=t.registration_id WHERE a.attempt_id=?",
        (receipt["attempt_id"],),
    ).fetchone()
    if (
        tracked is None
        or tracked["object_version"] != target["object_version"]
        or receipt["version"] != target["object_version"]
    ):
        raise NotFoundError("Current registered research answer is required")
    source = json.loads(tracked["data"])
    if source["study_ref"] != config["research_study_ref"] or source["protocol_ref"] != config["research_protocol_ref"]:
        raise ConflictError("Research protocol has changed")
    accepted = events[0]["payload"]
    if accepted.get("channel") != "openwebui-s1" or accepted.get("interaction_contract") != "manual-v1":
        raise ConflictError("Research action contract is unavailable")
    return events, {
        "target_ref": target,
        "attempt_id": receipt["attempt_id"],
        "tenant_ref": principal.tenant_ref,
        "actor_ref": principal.actor_ref,
        "event_id": event_id,
        **_versions(config),
    }


def _retry(events, request_id, kind, command):
    _uuid(request_id)
    for event in events:
        p = event["payload"]
        if p.get("contract") == CONTRACT and p.get("request_id") == request_id:
            if event["event_type"] != kind or p["command"] != command:
                raise ConflictError("Suggestion request ID conflicts")
            return event["record_id"]
    return None


def _append(store, db, event_id, kind, request_id, command, source, **data):
    return store._append(
        db,
        event_id,
        kind,
        {"contract": CONTRACT, "request_id": request_id, "command": command, **data},
        source,
    )


def generate(store, principal, event_id, request_id, suggestion_id, candidates, selection, *, display_id):
    """Record an operator-supplied synthetic selection, never an actual model claim."""
    with store._transaction() as db:
        events, binding = _admit(store, db, principal, event_id)
        _uuid(suggestion_id)
        _uuid(display_id)
        selected = validate_selection(selection, candidates)
        binding.update(
            suggestion_id=suggestion_id,
            display_id=display_id,
            candidate_set_id=selected["candidate_set_id"],
            reason_ids=selected["reason_ids"],
            outcome=selected["outcome"],
        )
        command = binding
        retry = _retry(events, request_id, "m52_suggestion_generated", command)
        if retry:
            return retry
        if any(
            e["event_type"] == "m52_suggestion_generated"
            and (
                e["payload"]["binding"]["suggestion_id"] == suggestion_id
                or e["payload"]["binding"]["display_id"] == display_id
            )
            for e in events
        ):
            raise ConflictError("Suggestion or display ID is already used")
        return _append(
            store,
            db,
            event_id,
            "m52_suggestion_generated",
            request_id,
            command,
            "synthetic_model",
            binding=binding,
            expires_at=time.time() + TTL_SECONDS,
        )


def _current(events, suggestion_id, binding, received_at=None):
    generated = [e for e in events if e["event_type"] == "m52_suggestion_generated"]
    if not generated or generated[-1]["payload"]["binding"]["suggestion_id"] != suggestion_id:
        raise ConflictError("Suggestion is stale or unavailable")
    suggestion = generated[-1]["payload"]
    if any(suggestion["binding"].get(k) != v for k, v in binding.items()):
        raise ConflictError("Suggestion versions have changed")
    observed_at = time.time() if received_at is None else received_at
    if not suggestion["expires_at"] - TTL_SECONDS <= observed_at < suggestion["expires_at"]:
        raise ConflictError("Suggestion has expired")
    if any(
        e["event_type"] == "m52_suggestion_invalidated" and e["payload"]["command"]["suggestion_id"] == suggestion_id
        for e in events
    ):
        raise ConflictError("Suggestion is invalidated")
    return suggestion


def render(store, principal, event_id, request_id, display_id, binding):
    """Binding is the exact generated snapshot reported by the synthetic client."""
    with store._transaction() as db:
        events, current_binding = _admit(store, db, principal, event_id)
        _uuid(display_id)
        if not isinstance(binding, dict):
            raise ValueError("Invalid display binding")
        suggestion = _current(events, binding.get("suggestion_id"), current_binding)
        if binding != suggestion["binding"] or binding["outcome"] != "suggested" or binding["display_id"] != display_id:
            raise ConflictError("Display does not match the suggested candidates")
        command = {"display_id": display_id, "binding": binding}
        retry = _retry(events, request_id, "m52_render_reported", command)
        if retry:
            return retry
        existing = [
            e
            for e in events
            if e["event_type"] == "m52_render_reported" and e["payload"]["command"]["display_id"] == display_id
        ]
        if existing:
            raise ConflictError("Display already recorded; retry the original request ID")
        return _append(store, db, event_id, "m52_render_reported", request_id, command, "client_reported")


def respond(
    store,
    principal,
    event_id,
    request_id,
    suggestion_id,
    display_id,
    operation,
    reason_id=None,
    previous_response_id=None,
):
    # Capture arrival before waiting for SQLite, as required by the Q-22 contract.
    received_at = time.time()
    with store._transaction() as db:
        events, binding = _admit(store, db, principal, event_id)
        suggestion = _current(events, suggestion_id, binding, received_at)
        if operation not in OPERATIONS:
            raise ValueError("Invalid suggestion operation")
        displays = [e for e in events if e["event_type"] == "m52_render_reported"]
        if not displays or displays[-1]["payload"]["command"] != {
            "display_id": display_id,
            "binding": suggestion["binding"],
        }:
            raise ConflictError("Current render receipt is required")
        if operation in {"yes", "no", "correct"}:
            if reason_id not in suggestion["binding"]["reason_ids"]:
                raise ConflictError("Reason is not a displayed candidate")
        elif reason_id is not None:
            raise ValueError("This operation cannot select a reason")
        if previous_response_id is not None:
            _uuid(previous_response_id)
        command = {
            "suggestion_id": suggestion_id,
            "display_id": display_id,
            "operation": operation,
            "reason_id": reason_id,
            "previous_response_id": previous_response_id,
        }
        retry = _retry(events, request_id, "m52_response_recorded", command)
        if retry:
            return retry
        responses = [e for e in events if e["event_type"] == "m52_response_recorded"]
        if (responses[-1]["record_id"] if responses else None) != previous_response_id:
            raise ConflictError("Response has changed")
        used_display = any(e["payload"]["command"]["display_id"] == display_id for e in responses)
        if used_display and operation != "correct":
            raise ConflictError("Display already has a response")
        state = project_suggestions(events)
        if operation == "correct" and (state["reason_id"] is None or state["reason_id"] == reason_id):
            raise ConflictError("Correction requires a different confirmed reason")
        return _append(
            store, db, event_id, "m52_response_recorded", request_id, command, "user", received_at=received_at
        )


def invalidate(store, principal, event_id, request_id, suggestion_id):
    with store._transaction() as db:
        events, binding = _admit(store, db, principal, event_id)
        command = {"suggestion_id": suggestion_id}
        retry = _retry(events, request_id, "m52_suggestion_invalidated", command)
        if retry:
            return retry
        _current(events, suggestion_id, binding)
        return _append(store, db, event_id, "m52_suggestion_invalidated", request_id, command, "user")


def project_suggestions(events):
    state = {"reason_id": None, "source": "none", "status": "none", "response_id": None}
    for event in events:
        if event["event_type"] == "action_retracted":
            return {**state, "reason_id": None, "source": "none", "status": "action_retracted"}
        if event["event_type"] == "m52_response_recorded":
            command = event["payload"]["command"]
            state["response_id"] = event["record_id"]
            if command["operation"] in {"yes", "correct"}:
                state.update(
                    reason_id=command["reason_id"],
                    source="user",
                    status="confirmed" if command["operation"] == "yes" else "corrected",
                )
    return state


def report_from_connection(db, event_ids, as_of):
    """Called inside the existing research report's read-only snapshot transaction."""
    cutoff = utc(as_of).timestamp()
    rows, states, missing = [], {}, 0
    for event_id in event_ids:
        events = []
        for row in db.execute("SELECT * FROM events WHERE event_id=? ORDER BY seq", (event_id,)):
            if utc(row["recorded_at"]).timestamp() <= cutoff:
                events.append({**dict(row), "payload": json.loads(row["payload"])})
        generated = [e for e in events if e["event_type"] == "m52_suggestion_generated"]
        missing += not generated
        states[event_id] = project_suggestions(events)
        for event in generated:
            p = event["payload"]
            sid = p["binding"]["suggestion_id"]
            renders = [
                e
                for e in events
                if e["event_type"] == "m52_render_reported"
                and e["payload"]["command"]["binding"]["suggestion_id"] == sid
            ]
            responses = [
                e["payload"]["command"]["operation"]
                for e in events
                if e["event_type"] == "m52_response_recorded" and e["payload"]["command"]["suggestion_id"] == sid
            ]
            valid = any(op != "close" for op in responses)
            status = (
                "responded"
                if valid
                else ("pending" if cutoff < p["expires_at"] else ("unresponded" if renders else "render_unknown"))
            )
            if p["binding"]["outcome"] != "suggested":
                status = "abstained"
            rows.append(
                {
                    "event_id": event_id,
                    "suggestion_id": sid,
                    "outcome": p["binding"]["outcome"],
                    "render_reported": bool(renders),
                    "valid_response": valid,
                    "confirmed": any(op in {"yes", "correct"} for op in responses),
                    "operations": dict(Counter(responses)),
                    "status": status,
                    "versions": {
                        k: p["binding"][k]
                        for k in (
                            "model_revision",
                            "package_version",
                            "criteria_version",
                            "template_version",
                            "catalog_sha256",
                            "ui_version",
                            "candidate_set_id",
                        )
                    },
                    "action_retracted": any(e["event_type"] == "action_retracted" for e in events),
                    "superseded": event["record_id"] != generated[-1]["record_id"],
                    "invalidated": any(
                        e["event_type"] == "m52_suggestion_invalidated"
                        and e["payload"]["command"]["suggestion_id"] == sid
                        for e in events
                    ),
                }
            )
    displayable = sum(r["outcome"] == "suggested" for r in rows)
    rendered = sum(r["render_reported"] for r in rows)
    responded = sum(r["valid_response"] for r in rows)
    strata = {}
    for row in rows:
        key = json.dumps(row["versions"], sort_keys=True)
        stratum = strata.setdefault(
            key,
            {"versions": row["versions"], "generated": 0, "displayable": 0, "render_reported": 0, "valid_response": 0},
        )
        stratum["generated"] += 1
        stratum["displayable"] += row["outcome"] == "suggested"
        stratum["render_reported"] += row["render_reported"]
        stratum["valid_response"] += row["valid_response"]
    return {
        "contract": CONTRACT,
        "unit": "suggestion_group",
        "scope": "synthetic_development_only",
        "generated": len(rows),
        "displayable": displayable,
        "render_reported": rendered,
        "valid_response": responded,
        "confirmed": sum(r["confirmed"] for r in rows),
        "render_rate": rendered / displayable if displayable else None,
        "response_rate": responded / rendered if rendered else None,
        "actions_without_generation": missing,
        "missing_render": displayable - rendered,
        "missing_response": rendered - responded,
        "outcome_counts": dict(Counter(r["outcome"] for r in rows)),
        "status_counts": dict(Counter(r["status"] for r in rows)),
        "operation_counts": dict(sum((Counter(r["operations"]) for r in rows), Counter())),
        "invalidated": sum(r["invalidated"] for r in rows),
        "retracted_action_count": sum(state["status"] == "action_retracted" for state in states.values()),
        "strata": list(strata.values()),
        "current_confirmations": states,
        "rows": rows,
    }
