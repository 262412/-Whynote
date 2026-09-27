"""Independent QA checks for the merged Q16 patch on the pinned host."""

import asyncio

import pytest
import sqlalchemy as sa
from test_native import chat, client, feedback, user


@pytest.mark.parametrize("operation", ["single", "all", "folder", "account"])
def test_shared_snapshot_cleanup_and_unrelated_share(native, operation):
    """Reproduce the review concern through real share creation, without invented grants."""

    async def scenario():
        from open_webui.models.access_grants import AccessGrant
        from open_webui.models.folders import FolderForm, Folders
        from open_webui.models.shared_chats import SharedChats

        actor, other = await user(native), await user(native)
        folder = await Folders.insert_new_folder(actor.id, FolderForm(name="synthetic QA folder"))
        target, control = await chat(native, actor, folder.id), await chat(native, other)
        linked, unrelated = await feedback(native, actor, target), await feedback(native, other, control)
        async with native.db.get_async_db_context() as session:
            before = (await session.execute(sa.select(AccessGrant.id).order_by(AccessGrant.id))).all()
        shared = await native.chats.Chats.insert_shared_chat_by_chat_id(target)
        other_shared = await native.chats.Chats.insert_shared_chat_by_chat_id(control)
        assert shared is not None and other_shared is not None
        async with native.db.get_async_db_context() as session:
            assert (await session.execute(sa.select(AccessGrant.id).order_by(AccessGrant.id))).all() == before
        if operation == "single":
            result = await native.chats.Chats.delete_chat_by_id_and_user_id(target, actor.id)
        elif operation == "all":
            result = await native.chats.Chats.delete_chats_by_user_id(actor.id)
        elif operation == "folder":
            result = await native.chats.Chats.delete_chats_by_user_id_and_folder_id(actor.id, folder.id)
        else:
            result = await native.users.Users.delete_user_by_id(actor.id)
        assert result is True
        assert await SharedChats.get_by_chat_id(target) is None
        assert await SharedChats.get_by_chat_id(control) is not None
        assert await native.feedbacks.Feedbacks.get_feedback_by_id(linked.id) is None
        assert await native.feedbacks.Feedbacks.get_feedback_by_id(unrelated.id) is not None
        async with native.db.get_async_db_context() as session:
            assert (await session.execute(sa.select(AccessGrant.id).order_by(AccessGrant.id))).all() == before

    asyncio.run(scenario())


def test_shared_snapshot_restored_on_account_credential_failure(native, monkeypatch):
    async def scenario():
        from open_webui.models.auths import Auth, Auths
        from open_webui.models.shared_chats import SharedChats
        from sqlalchemy.ext.asyncio import AsyncSession

        actor = await user(native)
        target = await chat(native, actor)
        linked = await feedback(native, actor, target)
        await native.chats.Chats.insert_shared_chat_by_chat_id(target)
        before = (await SharedChats.get_by_chat_id(target)).model_dump()
        async with native.db.get_async_db_context() as session:
            session.add(Auth(id=actor.id, email=actor.email, password="synthetic-unused", active=True))
            await session.commit()
        execute = AsyncSession.execute

        async def fail_auth(session, statement, *args, **kwargs):
            if isinstance(statement, sa.sql.dml.Delete) and statement.table.name == "auth":
                raise RuntimeError("synthetic QA credential failure")
            return await execute(session, statement, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(AsyncSession, "execute", fail_auth)
            assert await Auths.delete_auth_by_id(actor.id) is False
        assert (await SharedChats.get_by_chat_id(target)).model_dump() == before
        assert await native.feedbacks.Feedbacks.get_feedback_by_id(linked.id) is not None
        assert await native.chats.Chats.get_chat_by_id(target) is not None
        assert await Auths.delete_auth_by_id(actor.id) is True
        assert await SharedChats.get_by_chat_id(target) is None

    asyncio.run(scenario())


@pytest.mark.parametrize("field", ["chat_id", "message_id"])
def test_identifier_length_boundary_and_zero_write(native, monkeypatch, field):
    async def scenario():
        actor = await user(native)
        target = await chat(native, actor)
        async with client(native, actor, monkeypatch) as api:
            async with native.db.get_async_db_context() as session:
                before = (await session.execute(sa.select(native.feedbacks.Feedback.id))).all()
            for length, expected in ((200, 404), (201, 422)):
                meta = {"chat_id": target, field: "x" * length}
                response = await api.post("/feedback", json={"type": "rating", "meta": meta})
                assert response.status_code == expected, response.text
                async with native.db.get_async_db_context() as session:
                    assert (await session.execute(sa.select(native.feedbacks.Feedback.id))).all() == before

    asyncio.run(scenario())
