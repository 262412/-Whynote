import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from whynote.domain import MANUAL_REASONS, MANUAL_UI_VERSION, ConflictError, NotFoundError, Principal
from whynote.store import EventStore

ALICE = Principal("synthetic", "alice")
TARGET = {"object_type": "answer", "object_id": "fiction", "object_version": "1"}
METADATA = {"interaction_contract": "manual-v1"}


def respond(store, event_id, action, code=None, display="display", mode="manual_menu", principal=ALICE):
    store.issue_display_ticket(principal, event_id, display)
    store.record_display(principal, event_id, display, mode, [code for code, _ in MANUAL_REASONS], MANUAL_UI_VERSION)
    return store.record_user_action(principal, event_id, action, code, display, True, display)


@pytest.mark.parametrize(
    "operation", ["reason_none_matched", "reason_skipped", "reason_declined", "reason_menu_closed"]
)
def test_explicit_operation_preservation_reopen_and_retry(tmp_path, operation):
    store = EventStore(tmp_path / "events.db")
    eid = store.create_action(ALICE, TARGET, METADATA, "create")["event_id"]
    respond(store, eid, "reason_selected", "style", "select")
    state = respond(store, eid, operation, display="operation", mode="edit_menu")
    assert state["reason_code"] == (None if operation == "reason_none_matched" else "style")
    assert state["response_status"] == operation.removeprefix("reason_")
    events = store.get_events(ALICE, eid)
    assert events[-1]["payload"]["response_source"] == "user_explicit"
    assert events[-1]["event_version"] == 3
    assert store.record_user_action(ALICE, eid, operation, None, "operation", True, "operation") == state
    assert store.get_events(ALICE, eid) == events
    mode = "manual_menu" if operation == "reason_none_matched" else "edit_menu"
    action = "reason_selected" if mode == "manual_menu" else "reason_edited"
    assert respond(store, eid, action, "other_or_unknown", "reopen", mode)["reason_code"] == "other_or_unknown"
    assert store.get_events(ALICE, eid)[: len(events)] == events


def test_cross_session_concurrent_intent_and_outbox_then_retraction(tmp_path):
    path = tmp_path / "events.db"
    store = EventStore(path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(
            pool.map(
                lambda i: EventStore(path).create_action(ALICE, TARGET, METADATA, f"session-{i}")["event_id"], range(8)
            )
        )
    assert len(set(ids)) == 1
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM actions").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 1
    store.retract_action(ALICE, ids[0], "undo")
    assert store.create_action(ALICE, TARGET, METADATA, "session-0")["action_status"] == "retracted"
    assert store.create_action(ALICE, TARGET, METADATA, "new-intent")["event_id"] != ids[0]
    assert store.create_action(Principal("other", "alice"), TARGET, METADATA, "other")["event_id"] != ids[0]
    assert store.create_action(ALICE, {**TARGET, "object_version": "2"}, METADATA, "version")["event_id"] != ids[0]


def test_legacy_ambiguity_does_not_choose_or_merge_history(tmp_path):
    store = EventStore(tmp_path / "events.db")
    a = store.create_action(ALICE, TARGET, {}, "old-a")["event_id"]
    b = store.create_action(ALICE, TARGET, {}, "old-b")["event_id"]
    with pytest.raises(ConflictError, match="legacy"):
        store.create_action(ALICE, TARGET, METADATA, "new")
    assert len(store.get_events(ALICE, a)) == len(store.get_events(ALICE, b)) == 1


def test_new_operations_reject_foreign_stale_legacy_and_retracted(tmp_path):
    store = EventStore(tmp_path / "events.db")
    eid = store.create_action(ALICE, TARGET, METADATA, "create")["event_id"]
    store.record_display(ALICE, eid, "legacy", "manual_menu", ["style"], "ui-v1")
    before = store.get_events(ALICE, eid)
    with pytest.raises(ConflictError):
        store.record_user_action(ALICE, eid, "reason_none_matched", None, "legacy", True, "bad")
    with pytest.raises(NotFoundError):
        store.record_user_action(
            Principal("synthetic", "bob"), eid, "reason_menu_closed", None, "legacy", True, "foreign"
        )
    assert store.get_events(ALICE, eid) == before
    respond(store, eid, "reason_skipped")
    store.issue_display_ticket(ALICE, eid, "newer")
    before = store.get_events(ALICE, eid)
    with pytest.raises(ConflictError):
        store.record_user_action(ALICE, eid, "reason_menu_closed", None, "display", True, "stale")
    assert store.get_events(ALICE, eid) == before
    store.retract_action(ALICE, eid, "undo")
    with pytest.raises(ConflictError):
        respond(store, eid, "reason_none_matched", display="retracted")
