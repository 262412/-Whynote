"""Independent S1-3 boundaries using fictitious data and the real SQLite store."""

import hashlib
import runpy
import sqlite3
from pathlib import Path

import pytest

from whynote.research import register_source
from whynote.research_report import report
from whynote.s1 import TrialStore

ROOT = Path(__file__).parents[1]
SETUP = runpy.run_path(str(ROOT / "qa/s1_research_fixture.py"))


@pytest.fixture
def independent(tmp_path, monkeypatch):
    study = SETUP["Fixture"](tmp_path / "independent")
    monkeypatch.syspath_prepend(str(ROOT))
    monkeypatch.setenv("WHYNOTE_S1_CONFIG", str(study.config_path))
    monkeypatch.setattr("whynote.s1.time.time", lambda: study.clock)
    monkeypatch.setattr("whynote.store._now", study.now)
    return study


def snapshot(study, since="2026-09-01T00:00:00Z"):
    return report(study.store.path, study.principal, since, study.now())


def test_source_and_versions_freeze_before_save(independent):
    s = independent
    chat = s.chat("freeze", "self_natural")
    answer = s.answer(chat, "pending-freeze", save=False)
    changed = {**SETUP["registration"]("changed", "public_replay"), "context_ref": chat.id}
    register_source(s.store, s.principal, chat.id, changed, SETUP["ref"]("freeze"))
    s.config["research_versions"]["model_revision"] = "synthetic-revision-new"
    s.clock += 1
    s.store.confirm_saved(chat, answer.message_id)
    s.answer(chat, "new-registration")
    data = snapshot(s)
    first = next(a for a in data["answers"] if a["attempt_id"] == answer.attempt)
    assert first["source_kind"] == "self_natural" and first["actor_role"] == "self_report"
    assert first["versions"]["model_revision"] is None
    assert data["groups"]["public_replay"]["actor_roles"] == {"evaluator": 1}
    assert data["groups"]["public_replay"]["strata"][0]["versions"]["model_revision"] == "synthetic-revision-new"


def test_closed_interval_uses_eligibility_not_creation(independent):
    s = independent
    answer = s.answer(s.chat("interval", "scripted"), "interval", save=False)
    s.clock += 0.25
    since = s.now()
    assert snapshot(s, since)["answers"] == []
    s.store.confirm_saved(answer.chat, answer.message_id)
    data = snapshot(s, since)
    assert data["groups"]["scripted"]["eligible_answers"] == 1
    assert data["attempt_status_counts_in_interval"] == {}
    frozen = s.now()
    s.clock += 1
    s.respond(answer, "事实有误")
    s.store.invalidate(answer.chat.id, revoke=True)
    assert report(s.store.path, s.principal, since, frozen) == data
    assert snapshot(s, since)["groups"]["scripted"]["negative_answers"] == 1
    assert snapshot(s, since)["groups"]["scripted"]["invalidated_eligible_answers"] == 1


def test_disabled_capture_finishes_existing_attempt_only(independent):
    s = independent
    chat = s.chat("stop-research", "self_natural")
    answer = s.answer(chat, "already-tracked", save=False)
    s.config["research_enabled"] = False
    s.store = TrialStore(s.config)
    s.store.confirm_saved(chat, answer.message_id)
    s.clock += 1
    s.answer(chat, "untracked-after-stop")
    data = snapshot(s)
    assert data["groups"]["self_natural"]["eligible_answers"] == 1
    assert data["untracked_attempt_count_through_cutoff"] == 1
    assert len(data["answers"]) == 1


@pytest.mark.parametrize("table", ["sources", "attempts", "states", "answers"])
def test_append_only_tables_reject_update_and_delete(independent, table):
    s = independent
    s.answer(s.chat("append-only", "scripted"), "append-only")
    path = Path(s.store.path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    with sqlite3.connect(path) as db:
        column = db.execute(f"PRAGMA table_info(s1_research_{table})").fetchone()[1]
        for sql in (f"UPDATE s1_research_{table} SET {column}={column}", f"DELETE FROM s1_research_{table}"):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                db.execute(sql)
            db.rollback()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_zero_feedback_denominators_and_readonly_snapshot(independent):
    s = independent
    for source in ("self_natural", "scripted", "public_replay"):
        s.answer(s.chat("zero-" + source, source), "zero-" + source)
    s.answer(s.chat("zero-missing"), "zero-missing")
    before = Path(s.store.path).read_bytes()
    result = snapshot(s)
    assert result["participant_count"] == 1
    for group in result["groups"].values():
        assert group["eligible_answers"] == 1
        assert group["negative_answers"] == 0 and group["negative_answer_rate"] == 0
        assert group["manual"]["action_count"] == 0
        assert group["manual"]["fill_rate"] is None
    assert snapshot(s) == result
    assert Path(s.store.path).read_bytes() == before
