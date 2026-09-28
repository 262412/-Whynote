"""Stop admission while a gated synthetic provider is still completing a response."""

import argparse
import asyncio
import json
import sqlite3
import uuid
from pathlib import Path

import httpx
import socketio


async def run(args):
    config_path = args.data_dir / "trial.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["mode"] == "mock" and config["enabled"] is True
    assert not args.gate.exists() and not args.gate.with_suffix(".waiting").exists()
    finished = asyncio.Event()
    sio = socketio.AsyncClient()

    @sio.on("events")
    async def event(data):
        if data.get("data", {}).get("type") == "chat:completion":
            if data["data"].get("data", {}).get("done"):
                finished.set()

    async with httpx.AsyncClient(base_url=args.base_url, trust_env=False, timeout=20) as api:
        login = await api.post(
            "/api/v1/auths/signin",
            json={
                "email": "s1-alice@example.invalid",
                "password": "Synthetic-S1-20260927!only",
            },
        )
        login.raise_for_status()
        token = login.json()["token"]
        api.headers["Authorization"] = "Bearer " + token
        await sio.connect(args.base_url, auth={"token": token}, socketio_path="ws/socket.io", transports=["websocket"])
        try:
            message, parent = str(uuid.uuid4()), str(uuid.uuid4())
            response = await api.post(
                "/api/chat/completions",
                json={
                    "model": "whynote_s1_pipe",
                    "stream": True,
                    "parent_id": None,
                    "id": message,
                    "session_id": sio.get_sid(),
                    "user_message": {"id": parent, "role": "user", "content": "S1 虚构：正常回答", "parentId": None},
                },
            )
            response.raise_for_status()
            chat = response.json()["chat_id"]
            async with asyncio.timeout(10):
                while not args.gate.with_suffix(".waiting").exists():
                    await asyncio.sleep(0.05)
            config["enabled"] = False
            config_path.write_text(json.dumps(config), encoding="utf-8")
            args.gate.touch()
            async with asyncio.timeout(10):
                while True:
                    saved = await api.get(f"/api/v1/chats/{chat}")
                    answer = saved.json()["chat"]["history"]["messages"].get(message, {})
                    if answer.get("done"):
                        break
                    await asyncio.sleep(0.05)
            assert answer["content"] == "这是本地虚构回答，没有调用云模型。" and not answer.get("error")
            await asyncio.wait_for(finished.wait(), timeout=10)
            with sqlite3.connect(args.data_dir / "whynote.db") as db:
                db.row_factory = sqlite3.Row
                row = db.execute("SELECT * FROM s1_generations WHERE message_id=?", (message,)).fetchone()
                assert row["status"] == "awaiting_save" and row["saved_at"] is None
                # s1_current points to the current attempt, including pending
                # candidates. Feedback eligibility additionally requires a saved,
                # completed generation, as in TrialStore.receipt.
                candidate_count = db.execute("SELECT COUNT(*) FROM s1_current WHERE chat_id=?", (chat,)).fetchone()[0]
                assert candidate_count == 1
                assert (
                    db.execute(
                        "SELECT COUNT(*) FROM s1_current c JOIN s1_generations g ON c.attempt_id=g.attempt_id "
                        "WHERE c.chat_id=? AND g.status='completed' AND g.saved_at IS NOT NULL",
                        (chat,),
                    ).fetchone()[0]
                    == 0
                )
                # Unknown usage keeps the full conservative reservation.
                assert row["reserved_micro"] == 4_008_192 and row["settled_micro"] is None
                before = {
                    table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    for table in ("actions", "events", "outbox")
                }
            refused = await api.post(
                "/api/chat/actions/whynote_s1_action",
                json={
                    "chat_id": chat,
                    "id": message,
                    "model": "whynote_s1_pipe",
                    "messages": [answer],
                    "session_id": sio.get_sid(),
                    "whynote_click_id": str(uuid.uuid4()),
                },
            )
            assert refused.status_code in (400, 401, 403, 404)
            with sqlite3.connect(args.data_dir / "whynote.db") as db:
                after = {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in before}
            assert after == before
            args.output.write_text(
                json.dumps(
                    {
                        "saved": True,
                        "receipt": "awaiting_save",
                        "saved_at": None,
                        "candidate_index_count": candidate_count,
                        "eligible_receipts": 0,
                        "budget_preserved": True,
                        "feedback_refused": True,
                        "feedback_status": refused.status_code,
                        "actions_events_outbox_unchanged": True,
                        "final_websocket_event": True,
                        "scope": "real login HTTP/WebSocket; synthetic provider; entry left disabled",
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            print("Shutdown probe passed; candidate unconfirmed, fees retained, feedback refused")
        finally:
            await sio.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--gate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8127")
    asyncio.run(run(parser.parse_args()))
