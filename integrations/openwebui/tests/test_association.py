"""Q-16 signed association contract against pinned native routes and SQLite."""

import asyncio
import importlib.util
import uuid

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from test_native import chat, client, feedback, legacy_feedback, user


async def state(native):
    async with native.db.get_async_db_context() as session:
        return (await session.execute(sa.text("SELECT * FROM feedback ORDER BY id"))).all()


@pytest.mark.parametrize("export_enabled", [False, True])
@pytest.mark.parametrize("role", ["user", "admin"])
def test_target_matrix_and_immutable_updates(native, monkeypatch, export_enabled, role):
    async def scenario():
        actor, other = await user(native, role), await user(native)
        own, foreign, alternate = await chat(native, actor), await chat(native, other), await chat(native, actor)
        deleted = await chat(native, actor)
        assert await native.chats.Chats.delete_chat_by_id(deleted)
        async with native.db.get_async_db_context() as session:
            own_msg = await session.scalar(
                sa.select(native.chats.ChatMessage.id).where(native.chats.ChatMessage.chat_id == own)
            )
            other_msg = await session.scalar(
                sa.select(native.chats.ChatMessage.id).where(native.chats.ChatMessage.chat_id == foreign)
            )
        own_msg = own_msg.removeprefix(own + "-")
        other_msg = other_msg.removeprefix(foreign + "-")
        monkeypatch.setattr(native.evaluations, "ENABLE_ADMIN_EXPORT", export_enabled)
        async with client(native, actor, monkeypatch) as api:
            for meta, expected in [
                ({"chat_id": foreign}, 404),
                ({"chat_id": "missing"}, 404),
                ({"chat_id": deleted}, 404),
                ({"chat_id": own, "message_id": other_msg}, 404),
                ({"chat_id": own, "message_id": "missing"}, 404),
                ({"message_id": own_msg}, 422),
                ({"chat_id": []}, 422),
                ({"chat_id": " "}, 422),
                ({"chat_id": own, "message_id": 42}, 422),
            ]:
                before = await state(native)
                response = await api.post("/feedback", json={"type": "rating", "meta": meta})
                assert response.status_code == expected, response.text
                assert await state(native) == before
                assert "虚构问题" not in response.text
            meta = {"chat_id": own, "message_id": own_msg}
            created = await api.post(
                "/feedback",
                json={
                    "type": "rating",
                    "meta": meta,
                    "association_version": 999,
                    "verified_chat_id": foreign,
                    "user_id": other.id,
                },
            )
            assert created.status_code == 200, created.text
            ident = created.json()["id"]
            assert "association_version" not in created.json()
            async with native.db.get_async_db_context() as session:
                row = await session.get(native.feedbacks.Feedback, ident)
                assert (row.user_id, row.association_version, row.verified_chat_id, row.verified_message_id) == (
                    actor.id,
                    1,
                    own,
                    own_msg,
                )
            for rebound in ({"chat_id": alternate}, {"chat_id": foreign}, {}, None, {"chat_id": own}):
                before = await state(native)
                response = await api.post(
                    f"/feedback/{ident}", json={"type": "rating", "meta": rebound, "data": {"rating": 1}}
                )
                assert response.status_code == 409, response.text
                assert await state(native) == before
            assert (
                await api.post(f"/feedback/{ident}", json={"type": "rating", "data": {"rating": 1}})
            ).status_code == 200
            assert (
                await api.post(f"/feedback/{ident}", json={"type": "rating", "meta": {**meta, "tags": ["虚构"]}})
            ).status_code == 200
            unlinked = await feedback(native, actor)
            response = await api.post(f"/feedback/{unlinked.id}", json={"type": "rating", "meta": meta})
            assert response.status_code == 409
            # A removed message and changed ownership invalidate content-only updates too.
            async with native.db.get_async_db_context() as session:
                await session.execute(
                    sa.delete(native.chats.ChatMessage).where(native.chats.ChatMessage.id == f"{own}-{own_msg}")
                )
                await session.commit()
            before = await state(native)
            assert (await api.post(f"/feedback/{ident}", json={"type": "rating"})).status_code == 404
            assert await state(native) == before
            target_row = await feedback(native, actor, alternate)
            async with native.db.get_async_db_context() as session:
                await session.execute(
                    sa.update(native.chats.Chat).where(native.chats.Chat.id == alternate).values(user_id=other.id)
                )
                await session.commit()
            before = await state(native)
            assert (await api.post(f"/feedback/{target_row.id}", json={"type": "rating"})).status_code == 404
            assert await state(native) == before
            assert await native.chats.Chats.delete_chat_by_id(alternate)
            assert await native.feedbacks.Feedbacks.get_feedback_by_id(target_row.id) is not None
            async with native.db.get_async_db_context() as session:
                assert await session.get(native.feedbacks.FeedbackReview, target_row.id) is not None

    asyncio.run(scenario())


@pytest.mark.parametrize("export_enabled", [False, True])
def test_admin_content_update_and_legacy_no_promotion(native, monkeypatch, export_enabled):
    async def scenario():
        admin, alice, bob = await user(native, "admin"), await user(native), await user(native)
        target, foreign = await chat(native, alice), await chat(native, bob)
        trusted = await feedback(native, alice, target)
        legacy = await legacy_feedback(native, alice, target)
        bad = await legacy_feedback(native, alice, foreign)
        monkeypatch.setattr(native.evaluations, "ENABLE_ADMIN_EXPORT", export_enabled)
        async with client(native, admin, monkeypatch) as api:
            for row in (trusted, legacy):
                response = await api.post(f"/feedback/{row.id}", json={"type": "rating", "data": {"rating": 1}})
                assert response.status_code == (200 if export_enabled else 404)
            before = await state(native)
            assert (await api.post(f"/feedback/{bad.id}", json={"type": "rating"})).status_code == 404
            assert await state(native) == before
            rebound = await api.post(f"/feedback/{trusted.id}", json={"type": "rating", "meta": {"chat_id": foreign}})
            assert rebound.status_code == (409 if export_enabled else 404)
            assert await state(native) == before
        async with native.db.get_async_db_context() as session:
            assert (await session.get(native.feedbacks.Feedback, legacy.id)).association_version is None
        assert await native.chats.Chats.delete_chat_by_id(target)
        assert await native.feedbacks.Feedbacks.get_feedback_by_id(trusted.id) is None
        assert await native.feedbacks.Feedbacks.get_feedback_by_id(legacy.id) is not None

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["single", "all", "folder", "account"])
def test_delete_rollback_and_retry(native, monkeypatch, operation):
    async def scenario():
        from open_webui.models.folders import FolderForm, Folders
        from sqlalchemy.ext.asyncio import AsyncSession

        actor, other = await user(native), await user(native)
        folder = await Folders.insert_new_folder(actor.id, FolderForm(name="虚构回滚"))
        target = await chat(native, actor, folder.id)
        trusted = await feedback(native, actor, target)
        legacy = await legacy_feedback(native, other, target)
        execute = AsyncSession.execute

        async def fail_after_feedback(session, statement, *args, **kwargs):
            if isinstance(statement, sa.sql.dml.Delete) and statement.table.name == "chat_message":
                raise RuntimeError("synthetic transaction fault")
            return await execute(session, statement, *args, **kwargs)

        async def remove():
            if operation == "single":
                return await native.chats.Chats.delete_chat_by_id(target)
            if operation == "all":
                return await native.chats.Chats.delete_chats_by_user_id(actor.id)
            if operation == "folder":
                return await native.chats.Chats.delete_chats_by_user_id_and_folder_id(actor.id, folder.id)
            return await native.users.Users.delete_user_by_id(actor.id)

        before = await state(native)
        with monkeypatch.context() as patch:
            patch.setattr(AsyncSession, "execute", fail_after_feedback)
            assert await remove() is False
        assert await state(native) == before
        assert await native.chats.Chats.get_chat_by_id(target) is not None
        assert await native.users.Users.get_user_by_id(actor.id) is not None
        async with native.db.get_async_db_context() as session:
            assert await session.get(native.feedbacks.FeedbackReview, legacy.id) is None
        assert await remove() is True
        assert await native.feedbacks.Feedbacks.get_feedback_by_id(trusted.id) is None
        assert await native.feedbacks.Feedbacks.get_feedback_by_id(legacy.id) is not None
        after = await state(native)
        await remove()
        assert await state(native) == after

    asyncio.run(scenario())


@pytest.mark.parametrize("first", ["write", "delete", "revoke"])
@pytest.mark.parametrize("operation", ["create", "update"])
def test_sqlite_writer_interleaving(native, monkeypatch, first, operation):
    async def scenario():
        actor, other = await user(native), await user(native)
        target = await chat(native, actor)
        existing = await feedback(native, actor, target) if operation == "update" else None
        locked, release, second_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
        original_lock = native.feedbacks.Feedbacks._lock_target
        original_delete = native.chats.Chats.delete_chats_in_session

        async def pause_target(*args):
            await original_lock(*args)
            locked.set()
            await release.wait()

        async def pause_delete(*args, **kwargs):
            result = await original_delete(*args, **kwargs)
            locked.set()
            await release.wait()
            return result

        async def revoke():
            async with native.db.get_async_db_context() as session:
                await session.execute(
                    sa.update(native.chats.Chat).where(native.chats.Chat.id == target).values(user_id=other.id)
                )
                locked.set()
                await release.wait()
                await session.commit()

        async def create():
            if existing is not None:
                return await native.feedbacks.Feedbacks.update_feedback_by_id_and_user_id(
                    existing.id, actor.id, native.feedbacks.FeedbackForm(type="rating", data={"rating": 1})
                )
            return await feedback(native, actor, target)

        if first == "write":
            monkeypatch.setattr(native.feedbacks.Feedbacks, "_lock_target", pause_target)
            first_task = asyncio.create_task(create())

            async def second_action():
                return await native.chats.Chats.delete_chat_by_id(target)
        elif first == "delete":
            monkeypatch.setattr(native.chats.Chats, "delete_chats_in_session", pause_delete)
            first_task = asyncio.create_task(native.chats.Chats.delete_chat_by_id(target))
            second_action = create
        else:
            first_task = asyncio.create_task(revoke())
            second_action = create
        await asyncio.wait_for(locked.wait(), 5)

        async def second():
            second_started.set()
            return await second_action()

        second_task = asyncio.create_task(second())
        await second_started.wait()
        done, _ = await asyncio.wait({second_task}, timeout=0.15)
        assert not done, "second writer escaped the active transaction"
        release.set()
        result = await asyncio.wait_for(first_task, 5)
        if first == "write":
            assert await asyncio.wait_for(second_task, 5)
            assert await native.feedbacks.Feedbacks.get_feedback_by_id(result.id) is None
        else:
            if operation == "update" and first == "delete":
                assert await asyncio.wait_for(second_task, 5) is None
            else:
                with pytest.raises(native.feedbacks.FeedbackTargetNotFound):
                    await asyncio.wait_for(second_task, 5)
        async with native.db.get_async_db_context() as session:
            remaining = (
                await session.execute(
                    sa.select(native.feedbacks.Feedback.id).where(native.feedbacks.Feedback.verified_chat_id == target)
                )
            ).all()
            assert bool(remaining) == (operation == "update" and first == "revoke")
        if operation == "update" and first == "revoke":
            assert (
                await native.feedbacks.Feedbacks.get_feedback_by_id(existing.id)
            ).model_dump() == existing.model_dump()

    asyncio.run(scenario())


def test_migration_preserves_old_rows_and_downgrade(native, tmp_path):
    """Execute the shipped Alembic migration on an isolated pre-change feedback table."""
    from pathlib import Path

    path = Path(native.feedbacks.__file__).parents[1] / "migrations/versions/whynote_s0_v1_feedback_association.py"
    spec = importlib.util.spec_from_file_location("q16_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    metadata = sa.MetaData()
    old = sa.Table(
        "feedback",
        metadata,
        *(
            column._copy()
            for column in native.feedbacks.Feedback.__table__.columns
            if column.name not in {"association_version", "verified_chat_id", "verified_message_id"}
        ),
    )
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            old.insert().values(
                id=str(uuid.uuid4()),
                user_id="legacy",
                version=0,
                type="rating",
                meta={"chat_id": "unknown"},
                data={"comment": "虚构历史记录"},
                created_at=1,
                updated_at=1,
            )
        )
        before = conn.execute(sa.select(old)).all()
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        assert conn.execute(sa.select(old)).all() == before
        assert conn.execute(sa.text("SELECT association_version FROM feedback")).scalar() is None
        assert conn.execute(sa.text("SELECT reason FROM whynote_feedback_review")).scalar() == "legacy_unverified"
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
        assert conn.execute(sa.select(old)).all() == before
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        assert conn.execute(sa.text("SELECT association_version FROM feedback")).scalar() is None
    engine.dispose()


@pytest.mark.parametrize("operation", ["create", "update", "account"])
def test_commit_failure_is_atomic(native, monkeypatch, operation):
    async def scenario():
        from sqlalchemy.ext.asyncio import AsyncSession

        actor = await user(native)
        target = await chat(native, actor)
        row = await feedback(native, actor, target)
        before = await state(native)

        async def fail_commit(session):
            await session.flush()
            raise RuntimeError("synthetic commit failure")

        with monkeypatch.context() as patch:
            patch.setattr(AsyncSession, "commit", fail_commit)
            if operation == "account":
                assert await native.users.Users.delete_user_by_id(actor.id) is False
            else:
                with pytest.raises(RuntimeError, match="synthetic commit failure"):
                    if operation == "create":
                        await feedback(native, actor, target)
                    else:
                        await native.feedbacks.Feedbacks.update_feedback_by_id(
                            row.id, native.feedbacks.FeedbackForm(type="rating", data={"rating": 1})
                        )
        assert await state(native) == before
        assert await native.chats.Chats.get_chat_by_id(target) is not None
        assert await native.users.Users.get_user_by_id(actor.id) is not None

    asyncio.run(scenario())


def test_review_inventory_is_read_only_and_minimal(native, tmp_path):
    import hashlib
    import json
    import sqlite3
    from pathlib import Path

    async def scenario():
        actor, other = await user(native), await user(native)
        target = await chat(native, actor)
        foreign = await chat(native, other)
        return [(await legacy_feedback(native, actor, ident)).id for ident in (None, target, foreign, "missing")]

    ids = asyncio.run(scenario())
    database = tmp_path / "review-copy.db"
    with sqlite3.connect(native.data / "webui.db") as source, sqlite3.connect(database) as dest:
        source.backup(dest)
    path = Path(__file__).parents[1] / "review_feedback.py"
    spec = importlib.util.spec_from_file_location("review_feedback", path)
    report = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(report)
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    items = report.inventory(database)
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    selected = {item["feedback_id"]: item for item in items if item["feedback_id"] in ids}
    assert [selected[ident]["category"] for ident in ids] == [
        "unlinked",
        "unverified_current_match",
        "owner_conflict",
        "target_missing",
    ]
    for item in items:
        assert set(item) == {"feedback_id", "category", "task_reason", "outcome"}
        assert item["outcome"] == "pending_review"
    assert "虚构问题" not in json.dumps(items, ensure_ascii=False)


def test_auth_account_delete_rolls_back_at_credential_failure(native, monkeypatch):
    async def scenario():
        from open_webui.models.auths import Auth, Auths
        from sqlalchemy.ext.asyncio import AsyncSession

        actor = await user(native)
        target = await chat(native, actor)
        row = await feedback(native, actor, target)
        async with native.db.get_async_db_context() as session:
            session.add(Auth(id=actor.id, email=actor.email, password="synthetic-unused-hash", active=True))
            await session.commit()
        execute = AsyncSession.execute

        async def fail_credential(session, statement, *args, **kwargs):
            if isinstance(statement, sa.sql.dml.Delete) and statement.table.name == "auth":
                raise RuntimeError("synthetic credential deletion fault")
            return await execute(session, statement, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(AsyncSession, "execute", fail_credential)
            assert await Auths.delete_auth_by_id(actor.id) is False
        assert await native.chats.Chats.get_chat_by_id(target) is not None
        assert await native.users.Users.get_user_by_id(actor.id) is not None
        assert await native.feedbacks.Feedbacks.get_feedback_by_id(row.id) is not None
        async with native.db.get_async_db_context() as session:
            assert await session.get(Auth, actor.id) is not None
        assert await Auths.delete_auth_by_id(actor.id) is True
        assert await native.chats.Chats.get_chat_by_id(target) is None
        assert await native.users.Users.get_user_by_id(actor.id) is None
        assert await native.feedbacks.Feedbacks.get_feedback_by_id(row.id) is None
        async with native.db.get_async_db_context() as session:
            assert await session.get(Auth, actor.id) is None

    asyncio.run(scenario())
