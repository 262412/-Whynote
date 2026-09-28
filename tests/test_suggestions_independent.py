"""Independent mock ledger checks using the existing isolated synthetic setup."""

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest
from test_suggestions import events, generate, ref, render, respond, snapshot
from test_suggestions import trial as trial

from whynote import suggestions as s
from whynote.domain import ConflictError, NotFoundError, Principal, project


def logical_database(f):
    with sqlite3.connect(f.store.path) as db:
        return list(db.iterdump())


@pytest.mark.parametrize("operation", ["render", "respond", "invalidate"])
def test_interrupted_command_rolls_back_entire_database(trial, monkeypatch, operation):
    f = trial
    binding = generate(f)
    if operation != "render":
        render(f, binding)
    before = logical_database(f)
    original = f.store._append

    def fail_after_insert(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("synthetic interrupted commit")

    monkeypatch.setattr(f.store, "_append", fail_after_insert)
    with pytest.raises(RuntimeError, match="synthetic interrupted commit"):
        if operation == "render":
            render(f, binding)
        elif operation == "respond":
            respond(f)
        else:
            s.invalidate(f.store, f.principal, f.event_id, ref("undo"), ref("one"))
    assert logical_database(f) == before


@pytest.mark.parametrize("correct", [False, True])
def test_two_competing_intents_with_same_cas_commit_only_one(trial, correct):
    f = trial
    f.selection["reason_ids"].append("general.instruction_not_followed")
    render(f, generate(f))
    previous = respond(f) if correct else None
    before = events(f)
    barrier = threading.Barrier(2)

    def competing(index):
        barrier.wait(timeout=5)
        try:
            return respond(
                f,
                "correct" if correct else "yes",
                name=f"competing-{index}",
                reason=["general.style", "general.instruction_not_followed"][index],
                previous=previous,
            )
        except ConflictError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(competing, range(2)))
    assert sum(value is not None for value in results) == 1
    after = events(f)
    assert after[:-1] == before
    assert after[-1]["record_id"] in results
    assert after[-1]["payload"]["command"]["previous_response_id"] == previous
    group = snapshot(f)["groups"]["scripted"]["suggestions"]
    assert group["valid_response"] == group["confirmed"] == 1


def test_retry_of_earlier_confirmation_does_not_undo_later_correction(trial):
    f = trial
    render(f, generate(f))
    first = respond(f)
    correction = respond(f, "correct", name="correction", reason="general.style", previous=first)
    before = logical_database(f)
    assert respond(f) == first
    assert logical_database(f) == before
    state = s.project_suggestions(events(f))
    assert state["reason_id"] == "general.style"
    assert state["response_id"] == state["last_response_id"] == correction


@pytest.mark.parametrize("change", ["expired", "stop", "model", "delete", "revoke", "retract"])
def test_committed_retries_cannot_bypass_current_admission(trial, change):
    f = trial
    binding = generate(f)
    render(f, binding)
    respond(f)
    if change == "expired":
        f.clock += 60
    elif change == "stop":
        f.config["suggestion_research_enabled"] = False
    elif change == "model":
        f.config["suggestion_model_revision"] = "b" * 40
    elif change in {"delete", "revoke"}:
        f.store.invalidate(f.chat_record.id, revoke=change == "revoke")
    else:
        f.store.retract_action(f.principal, f.event_id, "retract")
    before = logical_database(f)
    for retry in (lambda: render(f, binding), lambda: respond(f)):
        with pytest.raises((ValueError, ConflictError, NotFoundError)):
            retry()
        assert logical_database(f) == before


@pytest.mark.parametrize("outcome", ["unknown", "no_match"])
def test_abstaining_new_generation_keeps_confirmation_but_rejects_old_display(trial, outcome):
    f = trial
    old = generate(f)
    render(f, old)
    confirmed = respond(f)
    generate(f, "next", outcome=outcome, display="next-display")
    before = logical_database(f)
    for operation in (lambda: render(f, old), lambda: respond(f)):
        with pytest.raises(ConflictError):
            operation()
    assert logical_database(f) == before
    state = s.project_suggestions(events(f))
    assert state["reason_id"] == "code.behavior_changed" and state["response_id"] == confirmed
    group = snapshot(f)["groups"]["scripted"]["suggestions"]
    assert (group["generated"], group["displayable"], group["valid_response"]) == (2, 1, 1)
    assert group["outcome_counts"][outcome] == 1


@pytest.mark.parametrize(
    "foreign", [Principal("foreign", "synthetic-alice"), Principal("synthetic-s1-study", "foreign")]
)
def test_foreign_render_response_and_invalidation_leave_database_unchanged(trial, foreign):
    f = trial
    binding = generate(f)
    render(f, binding)
    before = logical_database(f)
    for operation in (
        lambda: s.render(f.store, foreign, f.event_id, ref("foreign-render"), ref("display"), binding),
        lambda: s.respond(
            f.store, foreign, f.event_id, ref("foreign-response"), ref("one"), ref("display"), "yes", "general.style"
        ),
        lambda: s.invalidate(f.store, foreign, f.event_id, ref("foreign-invalidate"), ref("one")),
    ):
        with pytest.raises(NotFoundError):
            operation()
        assert logical_database(f) == before


@pytest.mark.parametrize("transition", ["stop", "delete", "replace"])
def test_timely_queued_response_rechecks_non_time_guards(trial, monkeypatch, transition):
    f = trial
    render(f, generate(f))
    f.clock += 59.5
    reached = threading.Event()
    proceed = threading.Event()
    original = f.store._transaction

    @contextmanager
    def queued():
        if threading.current_thread().name.startswith("ThreadPoolExecutor"):
            reached.set()
            assert proceed.wait(5)
        with original() as db:
            yield db

    monkeypatch.setattr(f.store, "_transaction", queued)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(respond, f)
        assert reached.wait(5)
        try:
            if transition == "stop":
                f.config["suggestion_research_enabled"] = False
            elif transition == "delete":
                f.store.invalidate(f.chat_record.id)
            else:
                f.clock += 0.1
                generate(f, "new", display="new-display")
            before = logical_database(f)
        finally:
            proceed.set()
        with pytest.raises((ConflictError, NotFoundError)):
            future.result(timeout=10)
    assert logical_database(f) == before


def test_historical_report_preserves_confirmation_before_later_correction_and_retraction(trial):
    f = trial
    legacy_before = project(events(f))
    render(f, generate(f))
    first = respond(f)
    early = snapshot(f)
    f.clock += 1
    correction = respond(f, "correct", name="correction", reason="general.style", previous=first)
    middle = snapshot(f)
    assert project(events(f)) == legacy_before
    f.clock += 1
    f.store.retract_action(f.principal, f.event_id, "retract")
    before = logical_database(f)
    assert snapshot(f, early["as_of"]) == early
    assert snapshot(f, middle["as_of"]) == middle
    current = snapshot(f)["groups"]["scripted"]["suggestions"]
    assert current["confirmed"] == current["valid_response"] == 1
    assert current["current_confirmations"][f.event_id]["reason_id"] is None
    assert middle["groups"]["scripted"]["suggestions"]["current_confirmations"][f.event_id]["response_id"] == correction
    assert logical_database(f) == before
