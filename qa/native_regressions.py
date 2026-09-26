"""Run with an Open WebUI 0.11.4 Python environment and patched source path.

Usage: python qa/native_regressions.py --source var/qa-openwebui-v0114 --output var/native-run
Creates a new synthetic SQLite database; refuses an existing output directory.
Exits nonzero for unmet acceptance criteria. Does not mock ORM or route branches.
"""

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import sqlite3
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--admin-other-status", type=int, choices=(401, 404), default=401)
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    os.environ.update(
        DATA_DIR=str(output),
        DATABASE_URL="sqlite:///" + (output / "webui.db").as_posix(),
        WEBUI_SECRET_KEY=secrets.token_hex(32),
        OFFLINE_MODE="true",
        HF_HUB_OFFLINE="1",
        ENABLE_OLLAMA_API="false",
        ENABLE_OPENAI_API="false",
        ENABLE_VERSION_UPDATE_CHECK="false",
        ENABLE_ADMIN_EXPORT="false",
        ENABLE_ADMIN_CHAT_ACCESS="false",
        ENABLE_PERSISTENT_CONFIG="false",
        DATABASE_ENABLE_SQLITE_WAL="false",
        CORS_ALLOW_ORIGIN="http://127.0.0.1:8092",
    )
    sys.path.insert(0, str(source / "backend"))
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from open_webui.internal.db import async_engine, get_async_db
    from open_webui.models.chats import ChatForm, Chats
    from open_webui.models.users import Users
    from open_webui.routers import evaluations
    from sqlalchemy import text

    assert Path(evaluations.__file__).resolve().is_relative_to(source)
    checks = []

    def check(name, actual, expected):
        checks.append({"name": name, "actual": actual, "expected": expected, "pass": actual == expected})

    def rows(table):
        assert table in {"feedback", "chat", "user"}
        with sqlite3.connect(output / "webui.db") as connection:
            connection.row_factory = sqlite3.Row
            return {row["id"]: dict(row) for row in connection.execute(f'SELECT * FROM "{table}"')}

    async def run():
        users = {}
        for name in ("alice", "bob", "admin", "folder", "account", "empty"):
            users[name] = await Users.insert_new_user(
                name, name, f"{name}@qa.invalid", role="admin" if name == "admin" else "user"
            )
            assert users[name] is not None
        actor = users["alice"]

        async def identity():
            return actor

        async def no_notification(*args, **kwargs):
            pass

        app = FastAPI()
        app.include_router(evaluations.router, prefix="/api/v1/evaluations")
        app.dependency_overrides[evaluations.get_verified_user] = identity
        app.dependency_overrides[evaluations.get_admin_user] = identity
        evaluations.publish_event = no_notification
        prefix = "/api/v1/evaluations"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://qa.invalid") as client:

            async def create(owner, meta=None):
                nonlocal actor
                actor = users[owner]
                payload = {"type": "rating", "data": {"rating": -1, "comment": "synthetic-comment"}}
                if meta is not None:
                    payload["meta"] = meta
                response = await client.post(prefix + "/feedback", json=payload)
                assert response.status_code == 200, response.text
                return response.json()["id"]

            async def chat(owner, chat_id, folder=None):
                result = await Chats.insert_new_chat(
                    chat_id, owner, ChatForm(chat={"title": "synthetic", "history": {"messages": {}}}, folder_id=folder)
                )
                assert result is not None

            fid = await create("alice", {"chat_id": "permission-chat", "message_id": "synthetic-message"})
            admin_fid = await create("admin")
            check("snapshot-null", json.loads(rows("feedback")[fid]["snapshot"]), None)
            actor = users["alice"]
            before = rows("feedback")
            for suffix in ("/feedback", "/feedback/" + fid):
                response = await client.post(prefix + suffix, json={"type": "rating", "snapshot": {"chat": {}}})
                check("snapshot-reject-" + suffix, response.status_code, 422)
            check("snapshot-reject-no-write", rows("feedback"), before)

            for who, target, get_expected, post_expected in (
                ("alice", fid, 200, 200),
                ("bob", fid, 404, 404),
                ("admin", fid, args.admin_other_status, args.admin_other_status),
                ("admin", admin_fid, 200, 200),
                ("alice", admin_fid, 404, 404),
            ):
                actor = users[who]
                before = rows("feedback")[target]
                label = who + ("-own" if before["user_id"] == who else "-other")
                read = await client.get(prefix + "/feedback/" + target)
                update = await client.post(prefix + "/feedback/" + target, json={"type": "rating"})
                check(label + "-GET", read.status_code, get_expected)
                check(label + "-POST", update.status_code, post_expected)
                if post_expected != 200:
                    check(label + "-no-write", rows("feedback")[target], before)
                    check(label + "-no-disclosure", "synthetic-comment" in update.text, False)
            actor = users["admin"]
            before = rows("feedback")[fid]
            changed = await client.post(
                prefix + "/feedback/" + fid,
                json={"type": "rating", "data": {"comment": "synthetic-unauthorized-change"}},
            )
            check("Q12-mutation-status", changed.status_code, args.admin_other_status)
            check("Q12-mutation-no-write", rows("feedback")[fid], before)
            for endpoint in ("/feedbacks/all/export", "/feedbacks/list", "/feedbacks/all/ids"):
                response = await client.get(prefix + endpoint)
                check("admin-blocked-" + endpoint, response.status_code, 401)

            await chat("alice", "a-chat")
            await chat("bob", "b-chat")
            linked = await create("alice", {"chat_id": "a-chat"})
            orphan = await create("alice")
            other_chat = await create("alice", {"chat_id": "b-chat"})
            bob_feedback = await create("bob", {"chat_id": "b-chat"})
            before = rows("feedback")
            check("unauthorized-single-delete", await Chats.delete_chat_by_id_and_user_id("a-chat", "bob"), False)
            check("unauthorized-single-no-write", rows("feedback"), before)
            check("bulk-delete-return", await Chats.delete_chats_by_user_id("alice"), True)
            check("bulk-linked-removed", linked in rows("feedback"), False)
            check("Q14-bulk-orphan-preserved", orphan in rows("feedback"), True)
            check("Q14-other-chat-rating-preserved", other_chat in rows("feedback"), True)
            check("bulk-bob-rating-preserved", bob_feedback in rows("feedback"), True)
            check("bulk-bob-chat-preserved", "b-chat" in rows("chat"), True)
            empty_orphan = await create("empty")
            check("empty-bulk-return", await Chats.delete_chats_by_user_id("empty"), True)
            check("Q14-no-chats-orphan-preserved", empty_orphan in rows("feedback"), True)

            await chat("folder", "in-folder", "folder-1")
            await chat("folder", "out-folder", "folder-2")
            in_f = await create("folder", {"chat_id": "in-folder"})
            out_f = await create("folder", {"chat_id": "out-folder"})
            orphan_f = await create("folder")
            check("folder-delete-return", await Chats.delete_chats_by_user_id_and_folder_id("folder", "folder-1"), True)
            check("folder-linked-removed", in_f in rows("feedback"), False)
            check("folder-other-preserved", out_f in rows("feedback"), True)
            check("folder-orphan-preserved", orphan_f in rows("feedback"), True)
            await chat("account", "account-chat")
            account_linked = await create("account", {"chat_id": "account-chat"})
            account_orphan = await create("account")
            check("account-delete-return", await Users.delete_user_by_id("account"), True)
            check("account-user-removed", "account" in rows("user"), False)
            check("account-chat-removed", "account-chat" in rows("chat"), False)
            check("account-linked-removed", account_linked in rows("feedback"), False)
            check("account-orphan-removed", account_orphan in rows("feedback"), False)
            check("account-unrelated-preserved", bob_feedback in rows("feedback"), True)
            async with get_async_db() as session:
                check("secure-delete", (await session.execute(text("PRAGMA secure_delete"))).scalar(), 1)
                check("journal-mode", (await session.execute(text("PRAGMA journal_mode"))).scalar(), "delete")
        await async_engine.dispose()

    asyncio.run(run())
    result = {
        "source": str(source),
        "admin_other_status_contract": args.admin_other_status,
        "route_sha256": hashlib.sha256(Path(evaluations.__file__).read_bytes()).hexdigest(),
        "checks": checks,
        "passed": sum(item["pass"] for item in checks),
        "failed": sum(not item["pass"] for item in checks),
    }
    (output / "results.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "checks"}))
    for item in checks:
        if not item["pass"]:
            print(json.dumps(item, ensure_ascii=False))
    return bool(result["failed"])


if __name__ == "__main__":
    sys.exit(main())
