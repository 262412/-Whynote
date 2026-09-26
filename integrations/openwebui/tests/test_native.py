"""Actual FastAPI feedback routes and SQLite ORM; only identity/events are replaced."""

import asyncio
import json
import sqlite3
import uuid

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, text


async def user(native, role="user"):
    ident = str(uuid.uuid4())
    result = await native.users.Users.insert_new_user(ident, "虚构测试", f"{ident}@example.invalid", role=role)
    assert result is not None
    return result


async def chat(native, owner, folder_id=None):
    ident = str(uuid.uuid4())
    message_id = str(uuid.uuid4())
    result = await native.chats.Chats.insert_new_chat(
        ident,
        owner.id,
        native.chats.ChatForm(
            chat={
                "title": "虚构测试",
                "history": {
                    "currentId": message_id,
                    "messages": {message_id: {"id": message_id, "role": "user", "content": "虚构问题"}},
                },
            },
            folder_id=folder_id,
        ),
    )
    assert result is not None
    return ident


async def feedback(native, owner, chat_id=None, comment="synthetic-feedback-sentinel"):
    result = await native.feedbacks.Feedbacks.insert_new_feedback(
        owner.id,
        native.feedbacks.FeedbackForm(
            type="rating", data={"rating": -1, "comment": comment}, meta={"chat_id": chat_id} if chat_id else {}
        ),
    )
    assert result is not None
    return result


def client(native, identity, monkeypatch):
    app = FastAPI()
    app.include_router(native.evaluations.router)
    app.dependency_overrides[native.evaluations.get_verified_user] = lambda: identity
    app.dependency_overrides[native.evaluations.get_admin_user] = lambda: identity

    async def publish(*args, **kwargs):
        pass

    monkeypatch.setattr(native.evaluations, "publish_event", publish)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.parametrize("export_enabled", [False, True])
@pytest.mark.parametrize("role", ["admin", "user"])
@pytest.mark.parametrize("own", [False, True])
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_feedback_access_matrix(native, monkeypatch, export_enabled, role, own, method):
    async def scenario():
        actor = await user(native, role)
        owner = actor if own else await user(native)
        row = await feedback(native, owner)
        before = row.model_dump()
        monkeypatch.setattr(native.evaluations, "ENABLE_ADMIN_EXPORT", export_enabled)
        permitted = own or (role == "admin" and export_enabled)
        async with client(native, actor, monkeypatch) as api:
            kwargs = {"json": {"type": "rating", "data": {"rating": 1}}} if method == "POST" else {}
            response = await api.request(method, f"/feedback/{row.id}", **kwargs)
        assert response.status_code == (200 if permitted else 404), response.text
        after = await native.feedbacks.Feedbacks.get_feedback_by_id(row.id)
        if not permitted:
            assert after.model_dump() == before
            assert "synthetic-feedback-sentinel" not in response.text
        elif method == "POST":
            assert after.data["rating"] == 1
        else:
            assert response.json()["user_id"] == owner.id

    asyncio.run(scenario())


def test_create_update_snapshot_and_export(native, monkeypatch):
    async def scenario():
        actor = await user(native, "admin")
        monkeypatch.setattr(native.evaluations, "ENABLE_ADMIN_EXPORT", False)
        async with client(native, actor, monkeypatch) as api:
            response = await api.post("/feedback", json={"type": "rating", "data": {"rating": -1}})
            assert response.status_code == 200, response.text
            ident = response.json()["id"]
            assert response.json()["snapshot"] is None
            for route in ("/feedback", f"/feedback/{ident}"):
                bad = await api.post(route, json={"type": "rating", "snapshot": {"chat": {"secret": "synthetic"}}})
                assert bad.status_code == 422
            updated = await api.post(f"/feedback/{ident}", json={"type": "rating", "data": {"rating": 1}})
            assert updated.status_code == 200 and updated.json()["snapshot"] is None
            for route in ("/feedbacks/list", "/feedbacks/all/ids", "/feedbacks/all/export"):
                blocked = await api.get(route)
                assert blocked.status_code == 401, (route, blocked.text)
            # Reproduce the minimal-body read bypass, independently of mutation tests.
            other = await feedback(native, await user(native))
            blocked = await api.post(f"/feedback/{other.id}", json={"type": "rating"})
            assert blocked.status_code == 404 and "synthetic-feedback-sentinel" not in blocked.text

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["single", "single_admin", "all", "folder", "account"])
def test_deletion_scope_and_unrelated_feedback(native, operation):
    async def scenario():
        from open_webui.models.folders import FolderForm, Folders

        alice, bob = await user(native), await user(native)
        folder = await Folders.insert_new_folder(alice.id, FolderForm(name="虚构文件夹"))
        target = await chat(native, alice, folder.id)
        other_alice_chat = await chat(native, alice)
        bob_chat = await chat(native, bob)
        linked = await feedback(native, alice, target)
        linked_other_author = await feedback(native, bob, target)
        unrelated = await feedback(native, alice)
        foreign_chat = await feedback(native, alice, bob_chat)
        other_linked = await feedback(native, alice, other_alice_chat)
        bob_unrelated = await feedback(native, bob)
        async with native.db.get_async_db_context() as session:
            assert (
                await session.execute(
                    select(native.chats.ChatMessage).where(native.chats.ChatMessage.chat_id == target)
                )
            ).scalars().first() is not None
        assert not await native.chats.Chats.delete_chat_by_id_and_user_id(target, bob.id)
        assert await native.feedbacks.Feedbacks.get_feedback_by_id(linked.id) is not None
        if operation == "single":
            result = await native.chats.Chats.delete_chat_by_id_and_user_id(target, alice.id)
        elif operation == "single_admin":
            result = await native.chats.Chats.delete_chat_by_id(target)
        elif operation == "all":
            result = await native.chats.Chats.delete_chats_by_user_id(alice.id)
        elif operation == "folder":
            result = await native.chats.Chats.delete_chats_by_user_id_and_folder_id(alice.id, folder.id)
        else:
            result = await native.users.Users.delete_user_by_id(alice.id)
        assert result is True
        for row, keep in [
            (linked, False),
            (linked_other_author, False),
            (unrelated, operation != "account"),
            (foreign_chat, operation != "account"),
            (other_linked, operation not in {"all", "account"}),
            (bob_unrelated, True),
        ]:
            assert (await native.feedbacks.Feedbacks.get_feedback_by_id(row.id) is not None) == keep, row.id
        assert await native.chats.Chats.get_chat_by_id(target) is None
        assert await native.chats.Chats.get_chat_by_id(bob_chat) is not None
        async with native.db.get_async_db_context() as session:
            assert (
                await session.execute(
                    select(native.chats.ChatMessage).where(native.chats.ChatMessage.chat_id == target)
                )
            ).scalars().first() is None
        if operation == "account":
            assert await native.users.Users.get_user_by_id(alice.id) is None

    asyncio.run(scenario())


def test_delete_all_with_no_chats_preserves_unrelated_feedback(native):
    async def scenario():
        alice, bob = await user(native), await user(native)
        unrelated = await feedback(native, alice)
        foreign = await feedback(native, alice, await chat(native, bob))
        assert await native.chats.Chats.delete_chats_by_user_id(alice.id)
        for row in (unrelated, foreign):
            assert await native.feedbacks.Feedbacks.get_feedback_by_id(row.id) is not None
        assert await native.users.Users.delete_user_by_id(alice.id)
        for row in (unrelated, foreign):
            assert await native.feedbacks.Feedbacks.get_feedback_by_id(row.id) is None

    asyncio.run(scenario())


def test_activity_backup_export_and_log_boundaries(native, monkeypatch, caplog, tmp_path):
    async def scenario():
        alice = await user(native, "admin")
        target = await chat(native, alice)
        sentinel = f"synthetic-retention-{uuid.uuid4()}"
        row = await feedback(native, alice, target, sentinel)
        backup_path = tmp_path / "before-delete.db"
        with sqlite3.connect(native.data / "webui.db") as active, sqlite3.connect(backup_path) as backup:
            active.backup(backup)
        monkeypatch.setattr(native.evaluations, "ENABLE_ADMIN_EXPORT", True)
        async with client(native, alice, monkeypatch) as api:
            exported = await api.get("/feedbacks/all/export")
            assert exported.status_code == 200 and sentinel in exported.text
            export_path = tmp_path / "before-delete-export.json"
            export_path.write_text(exported.text, encoding="utf-8")
        assert await native.chats.Chats.delete_chat_by_id_and_user_id(target, alice.id)
        assert await native.feedbacks.Feedbacks.get_feedback_by_id(row.id) is None
        async with native.db.get_async_db_context() as session:
            assert (await session.execute(text("PRAGMA secure_delete"))).scalar() == 1
            assert (await session.execute(text("PRAGMA journal_mode"))).scalar() == "delete"
            assert (
                await session.execute(select(native.feedbacks.Feedback).where(native.feedbacks.Feedback.id == row.id))
            ).scalar() is None
        assert sentinel.encode() not in (native.data / "webui.db").read_bytes()
        assert sentinel.encode() in backup_path.read_bytes()
        assert sentinel in export_path.read_text(encoding="utf-8")
        assert sentinel not in caplog.text
        assert json.loads(export_path.read_text(encoding="utf-8"))

    asyncio.run(scenario())
