"""Synthetic M5-2a ledger integration, including transaction and replay boundaries."""

import json
import runpy
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest

from whynote import suggestions as s
from whynote.domain import MANUAL_REASONS, MANUAL_UI_VERSION, ConflictError, NotFoundError, Principal, project
from whynote.research_report import report
from whynote.task_reasons import prepare_candidates

FIXTURE = runpy.run_path(str(Path(__file__).parents[1] / "qa/s1_research_fixture.py"))
ref = FIXTURE["ref"]


@pytest.fixture
def trial(tmp_path, monkeypatch):
    f = FIXTURE["Fixture"](tmp_path / "study")
    f.config.update(suggestion_research_enabled=True, suggestion_model_revision="a" * 40)
    monkeypatch.setattr("whynote.s1.time.time", lambda: f.clock)
    monkeypatch.setattr("whynote.store._now", f.now)
    f.chat_record = f.chat("suggestion", "scripted")
    f.answer_record = f.answer(f.chat_record, "answer")
    target = f.store.qualify(f.chat_record, f.answer_record.body)
    f.event_id = f.store.create_action(
        f.principal, target, {"channel": "openwebui-s1", "interaction_contract": "manual-v1"}, "action"
    )["event_id"]
    f.candidates = prepare_candidates(["code_rewrite"], ["request", "answer", "original_code"])
    f.selection = {
        "candidate_set_id": f.candidates.candidate_set_id,
        "outcome": "suggested",
        "reason_ids": ["code.behavior_changed", "general.style"],
    }
    return f


def events(f):
    return f.store.get_events(f.principal, f.event_id)


def generate(f, name="one", outcome="suggested", display="display"):
    selection = dict(f.selection)
    if outcome != "suggested":
        selection.update(outcome=outcome, reason_ids=[])
    record = s.generate(
        f.store,
        f.principal,
        f.event_id,
        ref("generate-" + name),
        ref(name),
        f.candidates,
        selection,
        display_id=ref(display),
    )
    return next(e["payload"]["binding"] for e in events(f) if e["record_id"] == record)


def render(f, binding, name="display"):
    return s.render(f.store, f.principal, f.event_id, ref("render-" + name), ref(name), binding)


def respond(
    f,
    operation="yes",
    *,
    name="response",
    display="display",
    suggestion="one",
    reason="code.behavior_changed",
    previous=None,
):
    return s.respond(
        f.store,
        f.principal,
        f.event_id,
        ref(name),
        ref(suggestion),
        ref(display),
        operation,
        reason if operation in {"yes", "no", "correct"} else None,
        previous,
    )


def snapshot(f, as_of=None):
    return report(f.store.path, f.principal, "2026-09-01T00:00:00Z", as_of or f.now())


def test_explicit_confirmation_correction_and_old_projection(trial):
    f = trial
    before = project(events(f))
    binding = generate(f)
    assert s.project_suggestions(events(f))["reason_id"] is None
    assert binding["reason_ids"] == f.selection["reason_ids"]
    render(f, binding)
    first = respond(f)
    second = respond(f, "correct", name="correction", reason="general.style", previous=first)
    assert s.project_suggestions(events(f)) == {
        "reason_id": "general.style",
        "source": "user",
        "status": "corrected",
        "response_id": second,
    }
    assert project(events(f)) == before
    result = snapshot(f)["groups"]["scripted"]["suggestions"]
    assert (result["generated"], result["render_reported"], result["valid_response"], result["confirmed"]) == (
        1,
        1,
        1,
        1,
    )
    assert result["operation_counts"] == {"yes": 1, "correct": 1}
    assert result["render_rate"] == result["response_rate"] == 1
    with sqlite3.connect(f.store.path) as db:
        assert db.execute("SELECT count(*) FROM outbox").fetchone()[0] == 1


@pytest.mark.parametrize("operation", ["no", "none_matched", "skip", "close", "decline"])
def test_nonconfirmation_does_not_clear_previously_confirmed_reason(trial, operation):
    f = trial
    binding = generate(f)
    render(f, binding)
    previous = respond(f)
    next_binding = generate(f, "two", display="next-display")
    render(f, next_binding, "next-display")
    respond(f, operation, name="next-response", display="next-display", suggestion="two", previous=previous)
    assert s.project_suggestions(events(f))["reason_id"] == "code.behavior_changed"
    assert project(events(f))["action_status"] == "active"


@pytest.mark.parametrize("operation", ["no", "none_matched", "skip", "close", "decline"])
def test_nonconfirmation_never_becomes_user_reason(trial, operation):
    f = trial
    render(f, generate(f))
    respond(f, operation)
    assert s.project_suggestions(events(f))["reason_id"] is None
    result = snapshot(f)["groups"]["scripted"]["suggestions"]
    assert result["valid_response"] == (operation != "close")
    assert result["confirmed"] == 0


@pytest.mark.parametrize("outcome", ["unknown", "no_match"])
def test_abstention_is_not_no_reason_or_rendered(trial, outcome):
    binding = generate(trial, outcome=outcome)
    before = events(trial)
    with pytest.raises(ConflictError):
        render(trial, binding)
    assert events(trial) == before
    result = snapshot(trial)["groups"]["scripted"]["suggestions"]
    assert result["outcome_counts"] == {outcome: 1}
    assert result["displayable"] == 0 and result["render_rate"] is None


def test_duplicate_commands_and_conflicting_ids(trial):
    f = trial
    binding = generate(f)
    assert generate(f) == binding
    receipt = render(f, binding)
    assert render(f, binding) == receipt
    with pytest.raises(ConflictError):
        s.render(f.store, f.principal, f.event_id, ref("other-request"), ref("display"), binding)
    response = respond(f)
    assert respond(f) == response
    before = events(f)
    with pytest.raises(ConflictError):
        respond(f, "no")
    with pytest.raises(ConflictError):
        respond(f, name="new-id")
    with pytest.raises(ConflictError):
        respond(f, "correct", name="correction", reason="general.style")
    assert events(f) == before
    assert len(before) == 4


@pytest.mark.parametrize(
    "field,value",
    [
        ("reason_ids", ["general.style", "code.behavior_changed"]),
        ("ui_version", "other-ui"),
        ("model_revision", "b" * 40),
        ("target_ref", {"object_version": "stale"}),
        ("actor_ref", "foreign"),
        ("candidate_set_id", "0" * 64),
        ("raw_answer", "do not persist"),
    ],
)
def test_render_requires_exact_order_and_all_bindings(trial, field, value):
    binding = generate(trial)
    before = events(trial)
    with pytest.raises(ConflictError):
        render(trial, {**binding, field: value})
    assert events(trial) == before


def test_stale_suggestion_and_display_and_correction_validation(trial):
    f = trial
    binding = generate(f)
    render(f, binding)
    with pytest.raises(ConflictError):
        respond(f, "correct", reason="general.style")
    with pytest.raises(ConflictError):
        render(f, binding, "new-display")
    next_binding = generate(f, "two", display="new-display")
    render(f, next_binding, "new-display")
    with pytest.raises(ConflictError):
        respond(f)
    with pytest.raises(ConflictError):
        respond(f, suggestion="two", display="new-display", reason="general.factual_error")
    with pytest.raises(ConflictError):
        respond(f, display="new-display")
    with pytest.raises(ConflictError):
        render(f, binding)


@pytest.mark.parametrize(
    "change", ["expired", "revoke", "delete", "regenerate", "retract", "stop", "study", "model", "cloud", "disabled"]
)
def test_invalidated_requests_cannot_append(trial, change):
    f = trial
    binding = generate(f)
    render(f, binding)
    if change == "expired":
        f.clock += s.TTL_SECONDS
    elif change in {"revoke", "delete"}:
        f.store.invalidate(f.chat_record.id, revoke=change == "revoke")
    elif change == "regenerate":
        a = f.answer_record
        f.store.reserve(
            f.chat_record.id,
            f.principal.actor_ref,
            a.message_id,
            a.body["messages"][-1]["parentId"],
            "new synthetic input",
        )
    elif change == "retract":
        f.store.retract_action(f.principal, f.event_id, "undo")
    elif change == "stop":
        f.config["suggestion_research_enabled"] = False
    elif change == "study":
        f.config["research_protocol_ref"] = ref("new-protocol")
    elif change == "model":
        f.config["suggestion_model_revision"] = "b" * 40
    elif change == "cloud":
        f.config["mode"] = "cloud"
    else:
        f.config["enabled"] = False
    before = events(f)
    for operation in (lambda: respond(f), lambda: render(f, binding)):
        with pytest.raises((ValueError, NotFoundError, ConflictError)):
            operation()
    assert events(f) == before


def test_suggestion_retraction_preserves_confirmation_action_retraction_clears_it(trial):
    f = trial
    render(f, generate(f))
    respond(f)
    record = s.invalidate(f.store, f.principal, f.event_id, ref("invalidate"), ref("one"))
    assert s.invalidate(f.store, f.principal, f.event_id, ref("invalidate"), ref("one")) == record
    assert s.project_suggestions(events(f))["reason_id"] == "code.behavior_changed"
    with pytest.raises(ConflictError):
        respond(f, "correct", reason="general.style")
    f.store.retract_action(f.principal, f.event_id, "undo")
    assert s.project_suggestions(events(f))["status"] == "action_retracted"
    assert s.project_suggestions(events(f))["reason_id"] is None
    result = snapshot(f)["groups"]["scripted"]["suggestions"]
    assert result["confirmed"] == result["invalidated"] == 1


@pytest.mark.parametrize(
    "principal", [Principal("foreign", "synthetic-alice"), Principal("synthetic-s1-study", "foreign")]
)
def test_foreign_principal_denied_before_records(trial, principal):
    f = trial
    before = events(f)
    with pytest.raises(NotFoundError):
        s.generate(
            f.store, principal, f.event_id, ref("g"), ref("one"), f.candidates, f.selection, display_id=ref("display")
        )
    assert events(f) == before


def test_readonly_asof_missing_no_backfill_and_no_raw_content(trial):
    f = trial
    before = snapshot(f)
    assert before["groups"]["scripted"]["suggestions"]["actions_without_generation"] == 1
    f.clock += 1
    binding = generate(f)
    f.clock += 1
    render(f, binding)
    respond(f, "close")
    f.clock += s.TTL_SECONDS
    data = Path(f.store.path).read_bytes()
    result = snapshot(f)
    assert Path(f.store.path).read_bytes() == data
    assert snapshot(f, before["as_of"]) == before
    group = result["groups"]["scripted"]["suggestions"]
    assert group["status_counts"] == {"unresponded": 1}
    assert group["valid_response"] == 0
    for forbidden in ("虚构研究问题", "虚构研究回答", f.config["version_key"]):
        assert forbidden.encode() not in data
        assert forbidden not in json.dumps(result, ensure_ascii=False)
    assert not any(e["event_type"] == "reason_unresponded" for e in events(f))


def test_concurrent_identical_responses_commit_once(trial):
    f = trial
    render(f, generate(f))
    with ThreadPoolExecutor(max_workers=4) as pool:
        records = list(pool.map(lambda _: respond(f), range(4)))
    assert len(set(records)) == 1
    assert len(events(f)) == 4


def test_inflight_response_checks_retraction_after_acquiring_transaction(trial):
    f = trial
    render(f, generate(f))
    started = threading.Event()

    def waiting_response():
        started.set()
        with pytest.raises(ConflictError):
            respond(f)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with f.store._transaction() as db:
            future = pool.submit(waiting_response)
            assert started.wait(5)
            f.store._append(db, f.event_id, "action_retracted", {}, "user")
        future.result(timeout=10)
    assert not any(e["event_type"] == "m52_response_recorded" for e in events(f))


def test_disabled_gate_precedes_candidate_or_context_access(trial, monkeypatch):
    f = trial
    f.config["suggestion_research_enabled"] = False
    monkeypatch.setattr(s, "validate_selection", lambda *_: pytest.fail("read candidates before gate"))
    with pytest.raises(NotFoundError):
        generate(f)


def test_no_registration_is_not_backfilled(trial):
    f = trial
    chat = f.chat("unregistered")
    answer = f.answer(chat, "unregistered-answer")
    target = f.store.qualify(chat, answer.body)
    f.event_id = f.store.create_action(
        f.principal, target, {"channel": "openwebui-s1", "interaction_contract": "manual-v1"}, "unregistered"
    )["event_id"]
    with pytest.raises(NotFoundError):
        generate(f)


def test_reproducible_fixture_reconciles_sources_and_denominators(tmp_path):
    fixture = runpy.run_path(str(Path(__file__).parents[1] / "qa/m52_suggestion_fixture.py"))
    result = fixture["build_fixture"](tmp_path / "m52-fixture")
    natural, scripted, public = (
        result["groups"][k]["suggestions"] for k in ("self_natural", "scripted", "public_replay")
    )
    assert (natural["generated"], natural["confirmed"], natural["invalidated"], natural["retracted_action_count"]) == (
        1,
        1,
        1,
        1,
    )
    assert (
        scripted["generated"],
        scripted["render_reported"],
        scripted["valid_response"],
        scripted["missing_response"],
    ) == (5, 5, 4, 1)
    assert scripted["status_counts"] == {"responded": 4, "unresponded": 1}
    assert (
        public["generated"],
        public["displayable"],
        public["missing_render"],
        public["actions_without_generation"],
    ) == (3, 1, 1, 1)
    assert public["status_counts"] == {"abstained": 2, "render_unknown": 1}
    assert public["strata"][0]["versions"]["model_revision"] == "a" * 40
    assert result["groups"]["public_replay"]["actor_roles"] == {"evaluator": 4}
    assert sum(group["manual"]["filled_numerator"] for group in result["groups"].values()) == 0


def test_new_events_are_append_only_and_failed_transaction_rolls_back(trial, monkeypatch):
    f = trial
    original_append = f.store._append

    def interrupted(*args, **kwargs):
        original_append(*args, **kwargs)
        raise RuntimeError("synthetic failure before commit")

    before = events(f)
    monkeypatch.setattr(f.store, "_append", interrupted)
    with pytest.raises(RuntimeError):
        generate(f)
    assert events(f) == before
    monkeypatch.setattr(f.store, "_append", original_append)
    generate(f)
    with sqlite3.connect(f.store.path) as db:
        for statement in (
            "UPDATE events SET source='gold' WHERE event_type='m52_suggestion_generated'",
            "DELETE FROM events WHERE event_type='m52_suggestion_generated'",
        ):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                db.execute(statement)


@pytest.mark.parametrize("value", [True, "yes", 1, None])
def test_missing_mock_research_cannot_enable_suggestions(trial, value):
    f = trial
    f.config.update(research_enabled=False, suggestion_research_enabled=value)
    with pytest.raises(ValueError):
        generate(f)


@pytest.mark.parametrize("operation", ["no", "none_matched"])
def test_rejecting_suggestion_preserves_existing_manual_reason(trial, operation):
    f = trial
    f.store.record_display(
        f.principal, f.event_id, "manual", "manual_menu", [code for code, _ in MANUAL_REASONS], MANUAL_UI_VERSION
    )
    f.store.record_user_action(f.principal, f.event_id, "reason_selected", "style", "manual", True, "manual-select")
    before = project(events(f))
    render(f, generate(f))
    respond(f, operation)
    assert project(events(f)) == before
    assert project(events(f))["reason_code"] == "style"


@pytest.mark.parametrize("elapsed,allowed", [(59.999, True), (60, False), (60.001, False), (-1, False)])
def test_exact_deadline_and_reversed_clock(trial, elapsed, allowed):
    render(trial, generate(trial))
    trial.clock += elapsed
    if allowed:
        respond(trial)
    else:
        with pytest.raises(ConflictError):
            respond(trial)


def test_timely_response_survives_database_wait_without_extending_deadline(trial, monkeypatch):
    f = trial
    render(f, generate(f))
    f.clock += 59.999
    received = f.clock
    waiting = threading.Event()
    transaction = f.store._transaction

    @contextmanager
    def blocked_transaction():
        waiting.set()
        with transaction() as db:
            yield db

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction():
            monkeypatch.setattr(f.store, "_transaction", blocked_transaction)
            future = pool.submit(respond, f)
            assert waiting.wait(5)
            f.clock += 2
        future.result(timeout=10)
    assert events(f)[-1]["payload"]["received_at"] == received
    assert s.project_suggestions(events(f))["reason_id"] == "code.behavior_changed"


def test_new_reservation_blocks_late_old_render_even_before_new_render(trial):
    f = trial
    old = generate(f)
    current = generate(f, "two", display="new-display")
    with pytest.raises(ConflictError):
        render(f, old)
    render(f, current, "new-display")
    respond(f, "close", suggestion="two", display="new-display")
    with pytest.raises(ConflictError):
        render(f, old)
    assert snapshot(f)["groups"]["scripted"]["suggestions"]["render_reported"] == 1
