"""Independent acceptance assertions for S1 receipt invalidation and rollback.

These assertions intentionally remain strict while the candidate is defective.
Only identity and the outbound event sink are replaced; chat routes, ORM,
receipt storage, and the S1 Action are real.
"""

import asyncio
import copy
import json
import uuid

import httpx
import pytest
from fastapi import FastAPI

from whynote.domain import NotFoundError
from whynote.s1 import PIPE_ID, TrialStore
from whynote.s1_host import confirm_saved_response


async def fixture(native, tmp_path, monkeypatch):
    user_id, chat_id, parent_id, answer_id = (str(uuid.uuid4()) for _ in range(4))
    user = await native.users.Users.insert_new_user(user_id, "QA fictional", f"{user_id}@example.invalid")
    config = dict(
        enabled=True,
        mode="mock",
        instance_id="independent-s1",
        user_id=user_id,
        db_path=str(tmp_path / "trial.db"),
        version_key="independent-synthetic-key-32-characters",
        started_at=1,
        base_url="http://127.0.0.1:8126/v1",
        provider_model="whynote-s1-synthetic",
    )
    path = tmp_path / "trial.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setenv("WHYNOTE_S1_CONFIG", str(path))
    messages = {
        parent_id: dict(id=parent_id, role="user", content="synthetic prompt", parentId=None),
        answer_id: dict(
            id=answer_id, role="assistant", content="synthetic answer", parentId=parent_id, model=PIPE_ID, done=True
        ),
    }
    chat = await native.chats.Chats.insert_new_chat(
        chat_id, user_id, native.chats.ChatForm(chat={"history": {"messages": messages, "currentId": answer_id}})
    )
    store = TrialStore(config)
    store.enroll(chat_id, user_id)
    attempt = store.reserve(chat_id, user_id, answer_id, parent_id, "synthetic prompt")
    store.finish(attempt, answer="synthetic answer", complete=True)
    return user, chat, store, path, config, parent_id, answer_id


@pytest.mark.parametrize("target", ["answer", "parent"])
def test_q23_null_restore_must_not_revive_receipt(native, tmp_path, monkeypatch, target):
    from open_webui.routers import chats
    from open_webui.utils.auth import get_verified_user

    from integrations.openwebui.s1_action import Action

    async def scenario():
        user, chat, store, _, _, parent_id, answer_id = await fixture(native, tmp_path, monkeypatch)
        confirm_saved_response(chat, answer_id)
        app = FastAPI()
        app.include_router(chats.router, prefix="/chats")
        app.dependency_overrides[get_verified_user] = lambda: user

        async def local_event(*args, **kwargs):
            pass

        monkeypatch.setattr(chats, "publish_event", local_event)
        changed = copy.deepcopy(chat.chat)
        changed["history"]["messages"][answer_id if target == "answer" else parent_id] = None
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            replaced = await client.post(f"/chats/{chat.id}", json={"chat": changed})
            assert replaced.status_code == 200
            observed = await native.chats.Chats.get_chat_by_id(chat.id)
            # The pinned host normalizes the submitted null by dropping that message.
            assert not isinstance(
                observed.chat["history"]["messages"].get(answer_id if target == "answer" else parent_id), dict
            )
            restored = await client.post(f"/chats/{chat.id}", json={"chat": chat.chat})
            assert restored.status_code == 200
        body = dict(
            chat_id=chat.id,
            id=answer_id,
            model=PIPE_ID,
            messages=list(chat.chat["history"]["messages"].values()),
            session_id="independent",
            whynote_click_id=str(uuid.uuid4()),
        )

        async def choose(menu):
            return menu["data"]["input"]["options"][0]["value"]

        result = None
        try:
            result = await Action().action(body, {"id": user.id}, choose)
        except NotFoundError:
            pass
        with store._transaction() as db:
            evidence = {
                "result": result and result.get("result"),
                "current_receipts": db.execute("SELECT COUNT(*) FROM s1_current").fetchone()[0],
                "events": [r[0] for r in db.execute("SELECT event_type FROM events ORDER BY seq")],
                "outbox": db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0],
            }
        print(json.dumps({"Q-23": target, **evidence}))
        assert evidence == dict(result=None, current_receipts=0, events=[], outbox=0)

    asyncio.run(scenario())


def test_q24_disabled_trial_must_not_throw_from_saved_hook(native, tmp_path, monkeypatch):
    async def scenario():
        _, chat, store, path, config, _, answer_id = await fixture(native, tmp_path, monkeypatch)
        path.write_text(json.dumps({**config, "enabled": False}), encoding="utf-8")
        # The actual host persistence succeeds before this hook in middleware.py.
        saved = await native.chats.Chats.upsert_message_to_chat_by_id_and_message_id(chat.id, answer_id, {"done": True})
        raised = None
        try:
            confirm_saved_response(saved, answer_id)
        except Exception as exc:
            raised = type(exc).__name__
        with store._transaction() as db:
            row = db.execute("SELECT status,saved_at FROM s1_generations").fetchone()
            evidence = dict(raised=raised, status=row["status"], saved_at=row["saved_at"])
        print(json.dumps({"Q-24": evidence}))
        assert raised is None, "Disabling entry must not abort host completion cleanup"
        assert evidence["status"] != "completed" and evidence["saved_at"] is None

    asyncio.run(scenario())
