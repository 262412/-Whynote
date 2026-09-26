"""Create a database with baseline code, then verify it using candidate code.

Run twice with --stage baseline / candidate and explicit --source src paths.
Only the baseline stage creates the output directory; all data is synthetic.
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("baseline", "candidate"), required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.source.resolve()))
    from whynote.domain import Principal
    from whynote.store import EventStore

    output = args.output.resolve()
    database = output / "upgrade.db"

    def persisted():
        with sqlite3.connect(database) as db:
            tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            return {
                table: [list(r) for r in db.execute('SELECT * FROM "' + table + '" ORDER BY rowid')]
                for table in tables
                if table != "display_tickets"
            }

    principal = Principal("qa-upgrade", "alice")
    if args.stage == "baseline":
        output.mkdir(parents=True, exist_ok=False)
        store = EventStore(database)
        state = store.create_action(
            principal,
            {"object_type": "synthetic", "object_id": "message", "object_version": "v1"},
            {"action_type": "negative_feedback", "channel": "qa", "locale": "zh-CN"},
            "create",
        )
        event_id = state["event_id"]
        store.record_display(principal, event_id, "old-display", "manual_menu", ["style"], "qa-v1")
        state = store.record_user_action(principal, event_id, "reason_selected", "style", "old-display", True, "choose")
        snapshot = {"event_id": event_id, "state": state, "rows": persisted()}
        with sqlite3.connect(database) as db:
            assert db.execute("SELECT name FROM sqlite_master WHERE name='display_tickets'").fetchone() is None
        (output / "before.json").write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
        print("baseline database created without display_tickets")
        return
    snapshot = json.loads((output / "before.json").read_text(encoding="utf-8"))
    store = EventStore(database)
    event_id = snapshot["event_id"]
    assert persisted() == snapshot["rows"], "Upgrade must preserve all old rows"
    assert store.get_action(principal, event_id) == snapshot["state"]
    store.issue_display_ticket(principal, event_id, "new-ticket")
    assert persisted() == snapshot["rows"], "Issuing a ticket must not append an event or modify Outbox"
    store.record_user_action(principal, event_id, "reason_selected", "style", "old-display", True, "choose")
    assert persisted() == snapshot["rows"], "A committed old choice retry must remain idempotent"
    receipt = store.record_display(principal, event_id, "old-display", "manual_menu", ["style"], "qa-v1")
    assert receipt["receipt_status"] == "historical" and receipt["actionable"] is False
    assert persisted() == snapshot["rows"]
    reopened = EventStore(database)
    assert reopened.get_action(principal, event_id) == snapshot["state"]
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT display_id FROM display_tickets WHERE event_id=?", (event_id,)).fetchone() == (
            "new-ticket",
        )
    result = {
        "old_rows_preserved": True,
        "projection_preserved": True,
        "issue_no_event_or_outbox_write": True,
        "committed_choice_retry_idempotent": True,
        "old_receipt_historical": True,
        "ticket_survives_reopen": True,
    }
    (output / "results.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
