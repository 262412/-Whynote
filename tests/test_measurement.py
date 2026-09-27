import hashlib
import sqlite3

import pytest

from whynote.domain import MANUAL_REASONS, MANUAL_UI_VERSION, Principal
from whynote.measurement import report, timing_payload
from whynote.store import EventStore

OWNER = Principal("synthetic", "alice")


def test_window_boundary_late_close_retraction_and_fixed_denominator(tmp_path, monkeypatch):
    path = tmp_path / "events.db"
    store = EventStore(path)
    clock = ["2026-09-01T00:00:00Z"]
    monkeypatch.setattr("whynote.store._now", lambda: clock[0])
    ids = []
    for i in range(5):
        ids.append(
            store.create_action(OWNER, {"object_id": str(i)}, {"interaction_contract": "manual-v1"}, str(i))["event_id"]
        )

    def respond(index, kind, code=None, display="d"):
        eid = ids[index]
        store.issue_display_ticket(OWNER, eid, display)
        store.record_display(
            OWNER, eid, display, "manual_menu", [c for c, _ in MANUAL_REASONS], MANUAL_UI_VERSION, "session"
        )
        return store.record_user_action(
            OWNER,
            eid,
            kind,
            code,
            display,
            True,
            str(index) + display,
            timing={
                "version": "active-v1",
                "display_id": display,
                "session_ref": "session",
                "active_ms": 100,
                "elapsed_ms": 200,
            },
        )

    clock[0] = "2026-09-01T00:00:01Z"
    respond(0, "reason_selected", "other_or_unknown")
    respond(1, "reason_menu_closed")
    respond(3, "reason_skipped")
    store.retract_action(OWNER, ids[4], "undo")
    pending = report(path, OWNER.tenant_ref, ids, "2026-09-01T23:59:59Z")
    assert pending["window_status_counts"] == {"selected": 1, "pending": 2, "skipped": 1, "retracted": 1}
    clock[0] = "2026-09-02T00:00:00Z"
    respond(1, "reason_selected", "style", "late")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    result = report(path, OWNER.tenant_ref, ids + ids, clock[0])
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert result["denominator"] == 5 and result["filled_numerator"] == 1
    assert result["fill_rate"] == 0.2
    assert result["window_status_counts"] == {
        "selected": 1,
        "unresponded": 1,
        "display_unknown": 1,
        "skipped": 1,
        "retracted": 1,
    }
    late = next(row for row in result["rows"] if row["event_id"] == ids[1])
    assert late["late_response_count"] == 1
    assert late["first_response_in_window"] is None
    assert late["current_state"]["reason_code"] == "style"
    assert result["close_count"] == result["closed_action_count"] == 1
    assert result["active_ms"]["n"] == 3
    assert result["active_ms"]["median"] == result["active_ms"]["p90"] == 100
    assert all(r["gate_outbox"] == r["action_events"] == 1 for r in result["rows"])
    # Historical as_of ignores the late submission even after it has been stored.
    assert report(path, OWNER.tenant_ref, ids, "2026-09-01T23:59:59Z") == pending
    with pytest.raises(ValueError, match="unavailable"):
        report(path, "foreign", ids, clock[0])
    with pytest.raises(ValueError, match="after as_of"):
        report(path, OWNER.tenant_ref, ids, "2026-08-31T23:59:59Z")
    with sqlite3.connect(path) as db:
        assert db.execute("select count(*) from events where event_type='reason_unresponded'").fetchone()[0] == 0


@pytest.mark.parametrize(
    "changes,status",
    [
        ({"session_ref": "different"}, "cross_session"),
        ({"display_id": "stale"}, "invalid_binding"),
        ({"active_ms": float("nan")}, "invalid_duration"),
        ({"active_ms": True}, "invalid_duration"),
        ({"active_ms": -1}, "invalid_duration"),
        ({"active_ms": 1001}, "invalid_duration"),
        ({"elapsed_ms": 100000}, "invalid_duration"),
        ({}, "client_reported"),
    ],
)
def test_active_duration_missing_binding_and_invalid_values(changes, status):
    timing = {
        "version": "active-v1",
        "session_ref": "same",
        "display_id": "d",
        "active_ms": 500,
        "elapsed_ms": 1000,
        **changes,
    }
    result = timing_payload(timing, {"display_id": "d", "client_session_ref": "same"}, 2000)
    assert result["timing_status"] == status
    assert result["active_ms"] == (500 if status == "client_reported" else None)
    assert timing_payload(None, {}, 2000)["timing_status"] == "missing"
    assert timing_payload(timing, {}, 2000)["timing_status"] == "legacy_display"
    assert timing_payload(timing, {}, -1) == {
        "active_ms": None,
        "server_total_ms": None,
        "timing_status": "server_clock_reversed",
    }


def test_zero_click_legacy_timeout_does_not_become_explicit_response(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    store = EventStore(path)
    monkeypatch.setattr("whynote.store._now", lambda: "2026-09-01T00:00:00Z")
    eid = store.create_action(OWNER, {"object_id": "old"}, {}, "old")["event_id"]
    with store._transaction() as db:
        store._append(db, eid, "reason_unresponded", {}, "system")
    result = report(path, OWNER.tenant_ref, [eid], "2026-09-02T00:00:00Z")
    assert result["filled_numerator"] == 0
    assert result["rows"][0]["first_response"] is None
    assert result["rows"][0]["contract"] == "legacy"
    assert result["active_ms"]["n"] == 0
