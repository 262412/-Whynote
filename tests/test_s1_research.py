import hashlib
import json
import runpy
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from whynote.domain import ConflictError, NotFoundError, Principal
from whynote.research import BROWSER_FIELDS, register_source
from whynote.research_report import report
from whynote.s1 import PIPE_ID, TrialStore
from whynote.s1_host import TrialBoundary, invalidate_chat

ROOT = Path(__file__).parents[1]
FIXTURE = runpy.run_path(str(ROOT / "qa/s1_research_fixture.py"))
ref, registration = FIXTURE["ref"], FIXTURE["registration"]


@pytest.fixture
def study(tmp_path, monkeypatch):
    fixture = FIXTURE["Fixture"](tmp_path / "study")
    monkeypatch.syspath_prepend(str(ROOT))
    monkeypatch.setenv("WHYNOTE_S1_CONFIG", str(fixture.config_path))
    monkeypatch.setattr("whynote.s1.time.time", lambda: fixture.clock)
    monkeypatch.setattr("whynote.store._now", fixture.now)
    return fixture


def snapshot(study):
    return report(study.store.path, study.principal, "2026-09-01T00:00:00Z", study.now())


def test_fixed_sources_actions_and_read_only_report(tmp_path, capsys):
    study, result = FIXTURE["build_fixture"](tmp_path / "cases")
    natural, scripted, public = (result["groups"][s] for s in ("self_natural", "scripted", "public_replay"))
    assert (natural["negative_answers"], natural["eligible_answers"], natural["negative_answer_rate"]) == (1, 2, 0.5)
    assert (scripted["negative_answers"], scripted["eligible_answers"]) == (3, 3)
    assert public["eligible_answers"] == 2 and public["actor_roles"] == {"evaluator": 2}
    assert public["public_label_origins"] == {"automated": 2}
    assert result["groups"]["missing"]["eligible_answers"] == 1
    assert result["participant_count"] == 1
    assert result["attempt_status_counts_in_interval"] == {"eligible": 8, "incomplete": 2, "awaiting_save": 1}
    assert natural["manual"]["action_count"] == natural["manual"]["filled_numerator"] == 1
    assert natural["edited_actions"] == natural["retracted_after_window"] == 1
    assert natural["invalidated_eligible_answers"] == 2
    assert natural["manual"]["active_ms"]["n"] == 1 and natural["manual"]["active_ms"]["median"] == 100
    assert natural["missing_active_timing"] == 1
    assert scripted["manual"]["response_action_counts_in_window"]["reason_skipped"] == 1
    assert scripted["manual"]["response_action_counts_in_window"]["reason_none_matched"] == 1
    assert scripted["manual"]["response_action_counts_in_window"]["reason_declined"] == 1
    assert scripted["manual"]["window_status_counts"]["unresponded"] == 1
    assert public["late_responses"] == 1 and public["manual"]["filled_numerator"] == 1
    assert public["manual"]["window_status_counts"]["display_unknown"] == 1
    assert public["historical_public_timing"] == "not_applicable"
    assert public["manual"]["active_ms"]["median"] is None
    assert public["model_source_confusion"] is None
    before = Path(study.store.path).read_bytes()
    repeated = report(study.store.path, study.principal, result["since"], result["as_of"])
    assert repeated == result and Path(study.store.path).read_bytes() == before
    assert result["snapshot_sha256"]
    for forbidden in ("虚构研究问题", "虚构研究回答", study.config["version_key"]):
        assert forbidden not in json.dumps(result, ensure_ascii=False)
        assert forbidden.encode() not in before
        assert forbidden not in capsys.readouterr().out
    with sqlite3.connect(study.store.path) as db:
        assert dict(db.execute("SELECT topic,count(*) FROM outbox GROUP BY topic")) == {
            "feedback.gate.requested": 6,
            "feedback.action.retracted": 1,
        }
        assert db.execute("SELECT count(*) FROM events WHERE event_type='reason_unresponded'").fetchone()[0] == 0


def test_registration_history_freezes_at_attempt_and_cas(study):
    chat = study.chat("history", "self_natural")
    original = registration("history", "self_natural")
    first = study.answer(chat, "first")
    earlier = snapshot(study)
    study.clock += 1
    changed = registration("second-registration", "scripted")
    changed["context_ref"] = chat.id
    with pytest.raises(ConflictError):
        register_source(study.store, study.principal, chat.id, changed)
    register_source(study.store, study.principal, chat.id, changed, original["registration_id"])
    # Replay an old successful command without restoring it as current.
    assert register_source(study.store, study.principal, chat.id, original) == original["registration_id"]
    study.answer(chat, "second")
    result = snapshot(study)
    assert result["groups"]["self_natural"]["eligible_answers"] == 1
    assert result["groups"]["scripted"]["eligible_answers"] == 1
    assert next(a for a in result["answers"] if a["attempt_id"] == first.attempt)["source_kind"] == "self_natural"
    assert report(study.store.path, study.principal, earlier["since"], earlier["as_of"]) == earlier
    with pytest.raises(ConflictError):
        register_source(study.store, study.principal, chat.id, {**original, "source_kind": "scripted"})


def test_parallel_registration_has_one_winner_and_retry_is_safe(study):
    chat = study.chat("race")

    def register(i):
        try:
            data = {**registration("race-" + str(i), "scripted"), "context_ref": chat.id}
            return register_source(study.store, study.principal, chat.id, data)
        except ConflictError:
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(register, range(4)))
    assert sum(r is not None for r in results) == 1
    with study.store._transaction() as db:
        assert db.execute("SELECT count(*) FROM s1_research_sources").fetchone()[0] == 1


@pytest.mark.parametrize("subject", [Principal("foreign", "synthetic-alice"), Principal("synthetic-s1-study", "bob")])
def test_foreign_registration_and_report_are_denied(study, subject):
    chat = study.chat("owner")
    with pytest.raises(NotFoundError):
        register_source(study.store, subject, chat.id, registration("bad-owner", "self_natural"))
    with pytest.raises(NotFoundError):
        report(study.store.path, subject, "2026-09-01T00:00:00Z", study.now())
    with study.store._transaction() as db:
        assert db.execute("SELECT count(*) FROM s1_research_sources").fetchone()[0] == 0


@pytest.mark.parametrize("mutation", ["extra", "raw-reference", "bad-source", "private-label", "missing", "role"])
def test_manifest_rejects_untrusted_fields(study, mutation):
    chat = study.chat("validation")
    data = registration("invalid", "self_natural")
    if mutation == "extra":
        data["answer"] = "raw text"
    elif mutation == "raw-reference":
        data["context_ref"] = "raw question"
    elif mutation == "bad-source":
        data["source_kind"] = "natural_by_default"
    elif mutation == "private-label":
        data["public_label_origin"] = "automated"
    elif mutation == "role":
        data["actor_role"] = "evaluator"
    else:
        del data["protocol_ref"]
    with pytest.raises(ValueError):
        register_source(study.store, study.principal, chat.id, data)


def test_missing_registration_is_not_backfilled(study):
    chat = study.chat("missing")
    study.answer(chat, "before-registration")
    study.clock += 1
    register_source(
        study.store, study.principal, chat.id, {**registration("new", "self_natural"), "context_ref": chat.id}
    )
    study.answer(chat, "after-registration")
    result = snapshot(study)
    assert result["groups"]["missing"]["eligible_answers"] == 1
    assert result["groups"]["self_natural"]["eligible_answers"] == 1


@pytest.mark.parametrize("field", ["study_ref", "protocol_ref", "context_ref", "feedback_ref"])
def test_well_formed_but_unavailable_references_are_rejected(study, field):
    chat = study.chat("references")
    data = {**registration("references", "self_natural"), field: ref("unavailable")}
    with pytest.raises((ValueError, NotFoundError)):
        register_source(study.store, study.principal, chat.id, data)


def test_source_feedback_reference_must_belong_to_the_same_chat(study):
    chat = study.chat("own", "self_natural")
    answer = study.answer(chat, "own")
    action = study.respond(answer, "事实有误")
    data = {
        **registration("own", "self_natural"),
        "registration_id": ref("owned-feedback"),
        "feedback_ref": action["event_id"],
    }
    register_source(study.store, study.principal, chat.id, data, ref("own"))
    other = study.chat("another")
    with pytest.raises(NotFoundError):
        register_source(
            study.store,
            study.principal,
            other.id,
            {**registration("another", "self_natural"), "feedback_ref": action["event_id"]},
        )


def test_out_of_scope_actions_do_not_enter_denominator(study):
    answer = study.answer(study.chat("isolation", "self_natural"), "isolation")
    target = study.store.qualify(answer.chat, answer.body)
    for i, changes in enumerate(({"object_version": "other"}, {"object_type": "other"})):
        study.store.create_action(
            study.principal,
            {**target, **changes},
            {"interaction_contract": "manual-v1", "channel": "openwebui-s1"},
            str(i),
        )
    study.store.create_action(study.principal, target, {"interaction_contract": "manual-v1", "channel": "s0"}, "s0")
    result = snapshot(study)
    assert result["unmatched_actions_in_interval"] == 3
    assert result["groups"]["self_natural"]["negative_answers"] == 0


def test_saving_retry_regeneration_and_revocation_preserve_history(study):
    chat = study.chat("lifecycle", "self_natural")
    first = study.answer(chat, "version")
    before = snapshot(study)
    study.store.confirm_saved(chat, first.message_id)
    assert snapshot(study) == before
    study.clock += 1
    second = study.answer(chat, "version")
    assert first.attempt != second.attempt
    study.clock += 1
    study.store.invalidate(chat.id)
    study.store.invalidate(chat.id, revoke=True)
    study.store.invalidate(chat.id, revoke=True)
    result = snapshot(study)
    assert result["groups"]["self_natural"]["eligible_answers"] == 2
    assert result["groups"]["self_natural"]["lifecycle_counts"] == {"superseded": 1, "invalidated": 2, "revoked": 2}
    assert report(study.store.path, study.principal, before["since"], before["as_of"]) == before
    with pytest.raises(NotFoundError):
        study.store.qualify(chat, second.body)
    with pytest.raises(NotFoundError):
        register_source(
            study.store, study.principal, chat.id, {**registration("revoked", "scripted"), "context_ref": chat.id}
        )


def test_answer_ledger_and_save_promotion_roll_back_together(study, monkeypatch):
    chat = study.chat("atomic", "self_natural")
    answer = study.answer(chat, "atomic", save=False)

    def fail(*_):
        raise RuntimeError("synthetic ledger failure")

    with monkeypatch.context() as m:
        m.setattr("whynote.research.record_answer", fail)
        with pytest.raises(RuntimeError):
            study.store.confirm_saved(chat, answer.message_id)
    with study.store._transaction() as db:
        assert (
            db.execute("SELECT status FROM s1_generations WHERE attempt_id=?", (answer.attempt,)).fetchone()[0]
            == "awaiting_save"
        )
        assert db.execute("SELECT count(*) FROM s1_research_answers").fetchone()[0] == 0
    study.store.confirm_saved(chat, answer.message_id)
    assert snapshot(study)["groups"]["self_natural"]["eligible_answers"] == 1


def test_retracted_new_intent_does_not_duplicate_answer_numerator(study):
    answer = study.answer(study.chat("repeat", "self_natural"), "repeat")
    first = study.respond(answer, "事实有误")
    study.store.retract_action(study.principal, first["event_id"], "undo")
    study.respond(answer, "事实有误", new_click=True)
    group = snapshot(study)["groups"]["self_natural"]
    assert group["negative_answers"] == group["eligible_answers"] == 1
    assert group["manual"]["action_count"] == group["manual"]["filled_numerator"] == 2


def test_old_database_read_is_non_migrating_and_upgrade_does_not_backfill(study):
    study.config["research_enabled"] = False
    study.store = TrialStore(study.config)
    study.answer(study.chat("legacy"), "legacy")
    with sqlite3.connect(study.store.path) as db:
        for table in ("answers", "states", "attempts", "sources"):
            db.execute(f"DROP TABLE s1_research_{table}")
    before = hashlib.sha256(Path(study.store.path).read_bytes()).digest()
    result = snapshot(study)
    assert result["untracked_attempt_count_through_cutoff"] == 1 and result["answers"] == []
    assert hashlib.sha256(Path(study.store.path).read_bytes()).digest() == before
    study.store = TrialStore(study.config)
    assert snapshot(study) == result


@pytest.mark.parametrize("field", sorted(BROWSER_FIELDS))
def test_browser_cannot_supply_source_metadata(study, field):
    app = FastAPI()
    app.add_middleware(TrialBoundary)
    with TestClient(app) as client:
        assert client.post("/api/chat/completions", json={"model": PIPE_ID, field: "forged"}).status_code == 403
    answer = study.answer(study.chat("forged-" + field), field)
    with pytest.raises(NotFoundError):
        study.action()._target(answer.chat, {**answer.body, field: "forged"})
    with study.store._transaction() as db:
        assert db.execute("SELECT count(*) FROM actions").fetchone()[0] == 0


@pytest.mark.parametrize(
    "changes", [{"mode": "cloud"}, {"research_enabled": "true"}, {"research_versions": {"host_sha": "raw content"}}]
)
def test_research_cannot_enable_cloud_or_accept_invalid_versions(study, changes):
    with pytest.raises(ValueError):
        TrialStore({**study.config, **changes})


def test_observation_cutoff_and_version_missing_are_explicit(study):
    answer = study.answer(study.chat("cutoff", "scripted"), "cutoff", save=False)
    before = snapshot(study)
    assert before["answers"] == []
    study.clock += 1
    study.store.confirm_saved(answer.chat, answer.message_id)
    result = snapshot(study)
    assert result["groups"]["scripted"]["version_missing_counts"]["model_revision"] == 1
    assert report(study.store.path, study.principal, before["since"], before["as_of"]) == before
    with pytest.raises(ValueError):
        report(study.store.path, study.principal, "2027-01-01T00:00:00Z", study.now())


@pytest.mark.parametrize("changes", [{"mode": "cloud"}, {"research_versions": {"host_sha": "invalid"}}])
def test_cleanup_remains_available_when_research_configuration_is_invalid(study, changes):
    answer = study.answer(study.chat("cleanup", "self_natural"), "cleanup")
    study.config_path.write_text(json.dumps({**study.config, "enabled": False, **changes}), encoding="utf-8")
    invalidate_chat(answer.chat.id, revoke=True)
    result = snapshot(study)
    assert result["groups"]["self_natural"]["eligible_answers"] == 1
    assert result["groups"]["self_natural"]["invalidated_eligible_answers"] == 1
    with pytest.raises(NotFoundError):
        study.store.qualify(answer.chat, answer.body)
