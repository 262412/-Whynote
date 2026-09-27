import asyncio
import copy
import json
import runpy
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from whynote.domain import ConflictError, NotFoundError, Principal
from whynote.s1 import (
    BUDGET_MICRO_CNY,
    PIPE_ID,
    RESERVATION_MICRO_CNY,
    TrialStore,
    generation_input,
    load_config,
)
from whynote.s1_host import TrialBoundary, invalidate_edit

ROOT = Path(__file__).parents[1]


def test_candidate_needs_successful_host_save(trial):
    attempt = trial.store.reserve(trial.chat.id, "alice", trial.message_id, trial.parent_id, "S1 虚构：正常回答")
    trial.store.finish(attempt, answer="虚构动态回答", complete=True)
    # Even an exact client-written body with done=True cannot promote the candidate.
    with pytest.raises(NotFoundError):
        trial.store.qualify(trial.chat, trial.body)
    trial.store.confirm_saved(None, trial.message_id)  # Host save failure.
    with pytest.raises(NotFoundError):
        trial.store.qualify(trial.chat, trial.body)
    trial.store.confirm_saved(trial.chat, trial.message_id)
    assert trial.store.qualify(trial.chat, trial.body)


def test_remove_restore_history_never_revives_receipt(trial):
    incoming = copy.deepcopy(trial.chat.chat)
    del incoming["history"]["messages"][trial.message_id]
    invalidate_edit(trial.chat, incoming)
    # Original in-memory chat represents restoring the exact content and done flag.
    with pytest.raises(NotFoundError):
        trial.store.qualify(trial.chat, trial.body)


@pytest.mark.parametrize("mutation", ["edited", "fabricated"])
def test_history_requires_matching_completed_receipts(trial, mutation):
    messages = trial.chat.chat["history"]["messages"]
    user_id, answer_id = str(uuid.uuid4()), str(uuid.uuid4())
    messages[user_id] = dict(id=user_id, role="user", content="next synthetic question", parentId=trial.message_id)
    messages[answer_id] = dict(id=answer_id, role="assistant", content="", parentId=user_id, model=PIPE_ID)
    if mutation == "edited":
        messages[trial.message_id]["content"] = "fabricated prior answer"
    else:
        trial.store.invalidate(trial.chat.id)
    with pytest.raises(NotFoundError):
        generation_input(trial.chat, answer_id, trial.store)


def test_parallel_first_construction_has_atomic_migration(trial, tmp_path):
    import threading

    config = {**trial.config, "db_path": str(tmp_path / "parallel-first-use.db")}
    barrier = threading.Barrier(4)

    def construct(_):
        barrier.wait(timeout=10)
        store = TrialStore(config)
        with store._transaction() as db:
            return tuple(row["name"] for row in db.execute("PRAGMA table_info(s1_generations)"))

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(construct, range(4)))
    assert len(set(results)) == 1 and "saved_at" in results[0]


def test_pre_hook_legacy_receipt_is_not_promoted(trial):
    with trial.store._transaction() as db:
        db.execute("UPDATE s1_generations SET saved_at=NULL")
    restored = TrialStore(trial.config)
    with pytest.raises(NotFoundError):
        restored.qualify(trial.chat, trial.body)


def test_only_one_concurrent_reservation(trial):
    def reserve(_):
        try:
            return trial.store.reserve(trial.chat.id, "alice", trial.message_id, trial.parent_id, "synthetic")
        except ConflictError:
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(reserve, range(4)))
    assert sum(result is not None for result in results) == 1
    with trial.store._transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM s1_generations WHERE status='pending'").fetchone()[0] == 1


def test_invalidation_between_target_read_and_write(trial):
    action = action_for(trial)
    target = action._target(trial.chat, trial.body)
    guarded = action._store_for_target(Principal("synthetic-s1", "alice"), target)
    trial.store.invalidate(trial.chat.id)
    with pytest.raises(NotFoundError):
        with guarded._transaction():
            pytest.fail("guard must reject before the write transaction is exposed")
    with trial.store._transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM actions").fetchone()[0] == 0


@pytest.mark.parametrize("field,value", [("done", False), ("error", {"message": "synthetic failure"})])
def test_incomplete_host_save_is_not_feedback_eligible(trial, field, value):
    trial.chat.chat["history"]["messages"][trial.message_id][field] = value
    with pytest.raises(NotFoundError):
        trial.store.qualify(trial.chat, trial.body)


def test_saved_history_four_complete_pairs_only(trial):
    messages = trial.chat.chat["history"]["messages"]
    parent = messages[trial.parent_id]
    for index in range(6):
        user_id, answer_id = str(uuid.uuid4()), str(uuid.uuid4())
        parent["parentId"] = answer_id
        messages[answer_id] = dict(
            id=answer_id, role="assistant", content=f"answer-{index}", parentId=user_id, done=True, model=PIPE_ID
        )
        messages[user_id] = dict(role="user", content=f"prompt-{index}", parentId=None)
        parent = messages[user_id]
        attempt = trial.store.reserve(trial.chat.id, "alice", answer_id, user_id, messages[user_id]["content"])
        trial.store.finish(attempt, answer=messages[answer_id]["content"], complete=True)
        trial.store.confirm_saved(trial.chat, answer_id)
    _, _, history = generation_input(trial.chat, trial.message_id, trial.store)
    assert len(history) == 10
    assert history[1]["content"] == "prompt-3"
    assert all("prompt-4" != item["content"] for item in history)
    messages[messages[trial.parent_id]["parentId"]]["done"] = False
    with pytest.raises(NotFoundError):
        generation_input(trial.chat, trial.message_id, trial.store)


@pytest.fixture
def trial(tmp_path, monkeypatch):
    config = {
        "enabled": True,
        "mode": "mock",
        "instance_id": "synthetic-s1",
        "user_id": "alice",
        "db_path": str(tmp_path / "trial.db"),
        "version_key": "synthetic-key-at-least-32-characters",
        "started_at": 1,
        "base_url": "http://127.0.0.1:8126/v1",
        "provider_model": "whynote-s1-synthetic",
    }
    config_path = tmp_path / "trial.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setenv("WHYNOTE_S1_CONFIG", str(config_path))
    monkeypatch.syspath_prepend(str(ROOT))
    store = TrialStore(config)
    chat_id, parent_id, message_id = (str(uuid.uuid4()) for _ in range(3))
    messages = {
        parent_id: {"id": parent_id, "role": "user", "content": "S1 虚构：正常回答", "parentId": None},
        message_id: {
            "id": message_id,
            "role": "assistant",
            "content": "虚构动态回答",
            "done": True,
            "parentId": parent_id,
            "model": PIPE_ID,
            "timestamp": 1,
        },
    }
    chat = SimpleNamespace(id=chat_id, user_id="alice", created_at=2, chat={"history": {"messages": messages}})
    body = {
        "chat_id": chat_id,
        "id": message_id,
        "model": PIPE_ID,
        "session_id": "synthetic-browser",
        "whynote_click_id": str(uuid.uuid4()),
        "messages": list(messages.values()),
    }
    store.enroll(chat_id, "alice")
    attempt = store.reserve(chat_id, "alice", message_id, parent_id, messages[parent_id]["content"])
    store.finish(attempt, answer=messages[message_id]["content"], complete=True)
    store.confirm_saved(chat, message_id)
    return SimpleNamespace(
        config=config,
        config_path=config_path,
        store=store,
        chat=chat,
        body=body,
        parent_id=parent_id,
        message_id=message_id,
        attempt=attempt,
    )


def action_for(trial):
    action = runpy.run_path(str(ROOT / "integrations/openwebui/s1_action.py"))["Action"]()

    async def owned(chat_id, user_id):
        # Host lookup substitute; generation receipts and event store remain real.
        return trial.chat if chat_id == trial.chat.id and user_id == "alice" else None

    action._owned_chat = owned
    return action


def invoke(action, body, callback, user="alice"):
    return asyncio.run(action.action(body, {"id": user}, callback))


async def choose(menu):
    return menu["data"]["input"]["options"][0]["value"]


def test_dynamic_feedback_replay_and_parent_change(trial):
    action = action_for(trial)
    first = invoke(action, trial.body, choose)
    assert first["result"] == "reason_submitted"
    before = trial.store.get_events(Principal("synthetic-s1", "alice"), first["event_id"])

    async def no_menu(_):
        pytest.fail("completed click replay must not show a menu")

    again = invoke(action, {**trial.body, "session_id": "reconnected"}, no_menu)
    assert again["result"] == first["result"]
    assert trial.store.get_events(Principal("synthetic-s1", "alice"), first["event_id"]) == before
    trial.chat.chat["history"]["messages"][trial.parent_id]["content"] = "edited parent"
    with pytest.raises(NotFoundError):
        invoke(action, {**trial.body, "whynote_click_id": str(uuid.uuid4())}, choose)


@pytest.mark.parametrize("mutation", ["user", "answer", "parent", "model", "no_receipt", "revoked", "disabled"])
def test_rejection_has_zero_action_writes(trial, mutation):
    action = action_for(trial)
    messages = trial.chat.chat["history"]["messages"]
    if mutation == "answer":
        messages[trial.message_id]["content"] = "edited answer"
    elif mutation == "parent":
        messages[trial.parent_id]["content"] = "edited parent"
    elif mutation == "model":
        messages[trial.message_id]["model"] = "other"
    elif mutation in {"no_receipt", "revoked"}:
        trial.store.invalidate(trial.chat.id, revoke=mutation == "revoked")
    elif mutation == "disabled":
        trial.config_path.write_text(json.dumps({**trial.config, "enabled": False}), encoding="utf-8")
    with pytest.raises(NotFoundError):
        invoke(action, trial.body, choose, user="bob" if mutation == "user" else "alice")
    with trial.store._transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 0


def test_revocation_while_menu_open_cannot_append_reason(trial):
    action = action_for(trial)

    async def revoke(menu):
        trial.store.invalidate(trial.chat.id, revoke=True)
        return await choose(menu)

    with pytest.raises(NotFoundError):
        invoke(action, trial.body, revoke)
    with trial.store._transaction() as db:
        assert [r[0] for r in db.execute("SELECT event_type FROM events")] == ["negative_feedback_action_recorded"]


def test_regeneration_and_edit_invalidate_old_receipt(trial):
    old = trial.store.qualify(trial.chat, trial.body)
    attempt = trial.store.reserve(trial.chat.id, "alice", trial.message_id, trial.parent_id, "S1 虚构：正常回答")
    with pytest.raises(NotFoundError):
        trial.store.qualify(trial.chat, trial.body)
    trial.store.finish(attempt, answer="虚构动态回答", complete=True)
    trial.store.confirm_saved(trial.chat, trial.message_id)
    assert trial.store.qualify(trial.chat, trial.body)["object_version"] != old["object_version"]
    changed = copy.deepcopy(trial.chat.chat)
    changed["history"]["messages"][trial.parent_id]["content"] = "edit"
    invalidate_edit(trial.chat, changed)
    with pytest.raises(NotFoundError):
        trial.store.qualify(trial.chat, trial.body)  # Restoring old text cannot restore the receipt.


def test_retract_is_repeatable_and_new_click_creates_new_action(trial):
    feedback = action_for(trial)
    first = invoke(feedback, trial.body, choose)
    retract = runpy.run_path(str(ROOT / "integrations/openwebui/s1_retract_action.py"))["Action"]()
    retract._owned_chat = feedback._owned_chat
    once = asyncio.run(retract.action(trial.body, {"id": "alice"}))
    assert asyncio.run(retract.action(trial.body, {"id": "alice"})) == once
    assert invoke(feedback, trial.body, choose)["result"] == "retracted"
    new = invoke(feedback, {**trial.body, "whynote_click_id": str(uuid.uuid4())}, choose)
    assert new["event_id"] != first["event_id"]


def test_unknown_costs_persist_and_budget_caps_new_requests(trial):
    count = 1  # Existing completed request has no usage; its full reservation remains.
    while (count + 1) * RESERVATION_MICRO_CNY <= BUDGET_MICRO_CNY:
        attempt = trial.store.reserve(trial.chat.id, "alice", str(uuid.uuid4()), trial.parent_id, "synthetic")
        trial.store.finish(attempt)
        count += 1
    restored = TrialStore(trial.config)
    with pytest.raises(ConflictError, match="budget"):
        restored.reserve(trial.chat.id, "alice", str(uuid.uuid4()), trial.parent_id, "synthetic")
    with restored._transaction() as db:
        assert db.execute("SELECT SUM(reserved_micro) FROM s1_generations").fetchone()[0] <= BUDGET_MICRO_CNY


def test_inflight_reservation_survives_restart_and_invalid_usage_is_not_free(trial):
    attempt = trial.store.reserve(trial.chat.id, "alice", str(uuid.uuid4()), trial.parent_id, "synthetic")
    restored = TrialStore(trial.config)
    with pytest.raises(ConflictError, match="in flight"):
        restored.reserve(trial.chat.id, "alice", str(uuid.uuid4()), trial.parent_id, "synthetic")
    restored.finish(attempt, usage={"prompt_tokens": -1, "completion_tokens": True})
    with restored._transaction() as db:
        assert (
            db.execute("SELECT settled_micro FROM s1_generations WHERE attempt_id=?", (attempt,)).fetchone()[0] is None
        )


def test_source_text_not_persisted_and_input_limit(trial):
    data = Path(trial.config["db_path"]).read_bytes()
    assert "虚构动态回答".encode() not in data
    assert "S1 虚构：正常回答".encode() not in data
    trial.chat.chat["history"]["messages"][trial.parent_id]["content"] = "字" * 8192
    with pytest.raises(ValueError, match="limit"):
        generation_input(trial.chat, trial.message_id, trial.store)


def test_cloud_configuration_requires_review_and_exact_endpoint(trial):
    config = {
        **trial.config,
        "mode": "cloud",
        "provider_model": "deepseek-flash",
        "base_url": "https://api.deepseek.com",
    }
    trial.config_path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ValueError, match="review"):
        load_config(trial.config_path)


@pytest.mark.parametrize(
    "path",
    [
        "/openai/chat/completions",
        "/openai/responses",
        "/openai/anything",
        "/ollama/api/chat",
        "/api/v1/tasks/title/completions",
        "/api/v1/files",
    ],
)
def test_boundary_blocks_bypass_routes(trial, path):
    app = FastAPI()
    app.add_middleware(TrialBoundary)
    with TestClient(app) as client:
        assert client.post(path, json={}).status_code == 403


def test_boundary_only_allows_trial_generation_and_strips_background_tasks(trial):
    app = FastAPI()
    app.add_middleware(TrialBoundary)

    @app.post("/api/chat/completions")
    async def echo(request: Request):
        return await request.json()

    with TestClient(app) as client:
        assert client.post("/api/chat/completions", json={"model": "other"}).status_code == 403
        result = client.post(
            "/api/chat/completions", json={"model": PIPE_ID, "background_tasks": {"title_generation": True}}
        )
        assert result.status_code == 200
        assert result.json()["background_tasks"] == {}
