"""Real login, HTTP/WebSocket and SQLite reconciliation; synthetic S1 host only."""

import argparse
import asyncio
import json
import sqlite3
import uuid
from pathlib import Path

import httpx
import socketio


async def run(args):
    from qa.s1_mock_provider import ANSWER

    checks = []
    with sqlite3.connect(args.data_dir / "whynote.db") as db:
        initial_actions = db.execute("SELECT COUNT(*) FROM actions").fetchone()[0]
        initial_generations = db.execute("SELECT COUNT(*) FROM s1_generations").fetchone()[0]

    def check(name, actual, expected):
        checks.append(dict(name=name, passed=actual == expected))
        assert actual == expected, f"{name}: {actual!r} != {expected!r}"

    def events():
        with sqlite3.connect(args.data_dir / "whynote.db") as db:
            return db.execute("SELECT event_type,payload FROM events ORDER BY seq").fetchall()

    async with httpx.AsyncClient(base_url=args.base_url, trust_env=False, timeout=70) as api:
        tokens = {}
        for name in ("alice", "bob"):
            login = await api.post(
                "/api/v1/auths/signin",
                json={"email": f"s1-{name}@example.invalid", "password": "Synthetic-S1-20260927!only"},
            )
            check(name + "-login", login.status_code, 200)
            tokens[name] = login.json()["token"]
        api.cookies.clear()
        api.headers["Authorization"] = "Bearer " + tokens["alice"]
        sio = socketio.AsyncClient()
        menus = []
        selected = 0

        @sio.on("events")
        async def callback(event):
            data = event.get("data", {})
            if data.get("type") == "input":
                menus.append(data)
                if selected == -1:
                    return False
                return data["data"]["input"]["options"][selected]["value"]

        await sio.connect(
            args.base_url, auth={"token": tokens["alice"]}, socketio_path="ws/socket.io", transports=["websocket"]
        )
        try:
            model = "whynote_s1_pipe"
            parent_id, message_id = str(uuid.uuid4()), str(uuid.uuid4())
            request = dict(
                model=model,
                stream=True,
                parent_id=None,
                id=message_id,
                session_id=sio.get_sid(),
                messages=[{"role": "user", "content": "S1 虚构：正常回答"}],
                user_message=dict(id=parent_id, role="user", content="S1 虚构：正常回答", parentId=None),
                background_tasks={"title_generation": True},
            )
            if args.browser_request:
                config = await api.get("/api/config")
                check("browser-memory-disabled", config.json()["features"]["enable_memories"], False)
                models = await api.get("/api/models")
                item = next(item for item in models.json()["data"] if item["id"] == model)
                request.update(
                    model_item=item,
                    features={
                        "voice": False,
                        "image_generation": False,
                        "code_interpreter": False,
                        "web_search": False,
                    },
                    params={},
                    tool_servers=[],
                    message_ids=[{"model_id": model, "message_id": message_id, "modelIdx": 0}],
                )
            generated = await api.post("/api/chat/completions", json=request)
            check("generation-http", generated.status_code, 200)
            result = generated.json()
            chat_id = result["chat_id"]
            for _ in range(200):
                saved = await api.get(f"/api/v1/chats/{chat_id}")
                saved.raise_for_status()
                messages = saved.json()["chat"]["history"]["messages"]
                answer = messages.get(message_id, {})
                if answer.get("done"):
                    break
                await asyncio.sleep(0.1)
            check("saved-complete-answer", answer.get("content"), ANSWER)
            check("saved-without-error", bool(answer.get("error")), False)
            next_user, next_answer = str(uuid.uuid4()), str(uuid.uuid4())
            followup = {
                **request,
                "chat_id": chat_id,
                "parent_id": message_id,
                "id": next_answer,
                "user_message": dict(id=next_user, role="user", content="S1 虚构：正常回答", parentId=message_id),
            }
            if args.browser_request:
                followup["message_ids"] = [{"model_id": model, "message_id": next_answer, "modelIdx": 0}]
            response = await api.post("/api/chat/completions", json=followup)
            check("trusted-history-generation", response.status_code, 200)
            for _ in range(200):
                saved = await api.get(f"/api/v1/chats/{chat_id}")
                saved.raise_for_status()
                messages = saved.json()["chat"]["history"]["messages"]
                if messages.get(next_answer, {}).get("done"):
                    break
                await asyncio.sleep(0.1)
            check("trusted-history-answer", messages[next_answer].get("content"), ANSWER)
            check("trusted-history-no-error", bool(messages[next_answer].get("error")), False)
            action_body = dict(
                chat_id=chat_id,
                id=message_id,
                model=model,
                messages=list(messages.values()),
                session_id=sio.get_sid(),
                whynote_click_id=str(uuid.uuid4()),
            )
            endpoint = "/api/chat/actions/whynote_s1_action"
            response = await api.post(endpoint, json=action_body)
            check("feedback-http", response.status_code, 200)
            check("feedback-result", response.json().get("result"), "reason_submitted")
            check("menu-called", len(menus), 1)
            before = events()
            response = await api.post(endpoint, json=action_body)
            check("same-click-retry", response.status_code, 200)
            check("retry-no-menu", len(menus), 1)
            check("retry-no-events", events(), before)
            await sio.disconnect()
            await sio.connect(
                args.base_url, auth={"token": tokens["alice"]}, socketio_path="ws/socket.io", transports=["websocket"]
            )
            action_body["session_id"] = sio.get_sid()
            response = await api.post(endpoint, json=action_body)
            check("reconnect-retry", response.status_code, 200)
            check("reconnect-no-menu", len(menus), 1)
            check("reconnect-no-events", events(), before)
            for selected in range(1, len(menus[0]["data"]["input"]["options"])):
                action_body["whynote_click_id"] = str(uuid.uuid4())
                response = await api.post(endpoint, json=action_body)
                check(f"menu-option-{selected}", response.status_code, 200)
            selected = -1
            action_body["whynote_click_id"] = str(uuid.uuid4())
            response = await api.post(endpoint, json=action_body)
            check("close-menu", response.status_code, 200)
            check("close-menu-event", events()[-1][0], "reason_menu_closed")
            before = events()
            response = await api.post(endpoint, json=action_body, headers={"Authorization": "Bearer " + tokens["bob"]})
            check("bob-refused", response.status_code in (400, 401, 403, 404), True)
            check("bob-zero-write", events(), before)
            retract = "/api/chat/actions/whynote_s1_retract_action"
            response = await api.post(retract, json=action_body)
            check("retract", response.status_code, 200)
            before = events()
            response = await api.post(retract, json=action_body)
            check("retract-retry", response.status_code, 200)
            check("retract-retry-no-write", events(), before)
            action_body["whynote_click_id"] = str(uuid.uuid4())
            selected = 0
            response = await api.post(endpoint, json=action_body)
            check("new-click-after-retract", response.status_code, 200)
            with sqlite3.connect(args.data_dir / "whynote.db") as db:
                check(
                    "two-actions-after-retract",
                    db.execute("SELECT COUNT(*) FROM actions").fetchone()[0] - initial_actions,
                    2,
                )
                check(
                    "two-explicit-generations-only",
                    db.execute("SELECT COUNT(*) FROM s1_generations").fetchone()[0] - initial_generations,
                    2,
                )
            before = events()
            edited = await api.post(f"/api/v1/chats/{chat_id}/messages/{message_id}", json={"content": "虚构编辑"})
            check("edit-http", edited.status_code, 200)
            response = await api.post(endpoint, json=action_body)
            check("stale-version-refused", response.status_code in (400, 401, 403, 404), True)
            check("stale-version-zero-write", events(), before)
            for path in ("/openai/chat/completions", "/openai/responses", "/api/v1/tasks/title/completions"):
                response = await api.post(path, json={})
                check("bypass-refused:" + path, response.status_code, 403)
            with sqlite3.connect(args.data_dir / "whynote.db") as db:
                rows = "\n".join(db.iterdump())
                check("receipt-no-answer-text", ANSWER in rows, False)
                check("receipt-no-prompt-text", "S1 虚构：正常回答" in rows, False)
        finally:
            await sio.disconnect()
    result = dict(
        scope="Developer real-login HTTP/WebSocket; no browser UI or live cloud",
        checks=checks,
        passed=sum(x["passed"] for x in checks),
        failed=sum(not x["passed"] for x in checks),
        event_types=[row[0] for row in events()],
    )
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("passed", "failed")}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8127")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--browser-request", action="store_true", help="Replay native browser metadata over HTTP")
    asyncio.run(run(parser.parse_args()))
