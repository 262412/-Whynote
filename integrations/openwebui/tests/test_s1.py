"""Pinned host ORM and Pipe, real loopback SSE, synthetic data only."""

import asyncio
import json
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest

from whynote.domain import NotFoundError, Principal
from whynote.s1 import PIPE_ID, TrialStore

ROOT = Path(__file__).parents[3]


@pytest.mark.parametrize("case", ["stop", "length", "no_done", "invalid_json", "tools", "timeout", "redirect"])
def test_provider_validation_and_safe_errors(tmp_path, case):
    from whynote.s1_provider import complete_response

    config = dict(
        instance_id="validation",
        user_id="alice",
        db_path=str(tmp_path / "trial.db"),
        version_key="synthetic-test-key-at-least-32-chars",
        mode="cloud",
        provider_model="deepseek-flash",
        base_url="https://api.deepseek.com",
    )
    store = TrialStore(config)
    store.enroll("chat", "alice")
    attempt = store.reserve("chat", "alice", "answer", "parent", "synthetic prompt")

    class Content:
        async def __aiter__(self):
            if case == "invalid_json":
                yield b"data: {not-json-secret\n"
                return
            delta = {"content": "synthetic answer"}
            if case == "tools":
                delta["tool_calls"] = [{"id": "synthetic"}]
            for chunk in (
                {"choices": [{"delta": delta, "finish_reason": None}]},
                {"choices": [{"delta": {}, "finish_reason": "length" if case == "length" else "stop"}]},
                {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5}},
            ):
                yield ("data: " + json.dumps(chunk) + "\n").encode()
            if case != "no_done":
                yield b"data: [DONE]\n"

    class Response:
        status = 302 if case == "redirect" else 200
        headers = {"Content-Type": "text/event-stream"}
        content = Content()
        closed = False

        def close(self):
            self.closed = True

    class Session:
        calls = 0
        response = Response()

        async def request(self, method, url, **kwargs):
            self.calls += 1
            assert url == "https://api.deepseek.com/chat/completions"
            assert kwargs["allow_redirects"] is False
            assert kwargs["json"]["thinking"] == {"type": "disabled"}
            assert kwargs["json"]["max_tokens"] == 1024
            assert kwargs["timeout"].total == 60 and kwargs["timeout"].sock_read == 20
            if case == "timeout":
                raise asyncio.TimeoutError("synthetic-secret-provider-error")
            return self.response

    async def scenario():
        session = Session()
        response = await complete_response(session, config, store, attempt, "synthetic-private-key", [])
        output = "".join([chunk async for chunk in response.body_iterator])
        assert "secret" not in output and "synthetic-private-key" not in output
        assert ("[DONE]" in output) == (case == "stop")
        assert session.calls == 1
        assert session.response.closed == (case != "timeout")
        with store._transaction() as db:
            row = db.execute("SELECT * FROM s1_generations").fetchone()
            assert row["status"] == ("completed" if case == "stop" else "incomplete")
            if case in ("stop", "length", "no_done"):
                assert row["settled_micro"] == 60
            else:
                assert row["settled_micro"] is None

    asyncio.run(scenario())


def test_host_event_sinks_receive_no_trial_text(native, monkeypatch):
    from open_webui import events

    received = []

    async def local(self, app, payload, **kwargs):
        received.append(payload)

    async def forbidden(*args, **kwargs):
        pytest.fail("trial must not call external event sinks")

    monkeypatch.setenv("WHYNOTE_S1_CONFIG", "synthetic-enabled-marker")
    monkeypatch.setattr(events.SocketSessionEventSink, "handle_event", local)
    monkeypatch.setattr(events.EventFunctionSink, "handle_event", forbidden)
    monkeypatch.setattr(events.WebhookEventSink, "handle_event", forbidden)
    monkeypatch.setattr(events.NotificationEventSink, "handle_event", forbidden)
    asyncio.run(
        events.publish_event(
            object(),
            events.EVENTS.MESSAGE_CREATED,
            data={"content_preview": "synthetic-sensitive"},
            message="synthetic-sensitive",
        )
    )
    assert len(received) == 1
    assert "synthetic-sensitive" not in received[0].model_dump_json()


@pytest.fixture(scope="module")
def provider():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    proc = subprocess.Popen(
        [sys.executable, "-m", "qa.s1_mock_provider", "--port", str(port)],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    break
            except OSError:
                if proc.poll() is not None:
                    pytest.fail("synthetic provider exited")
                time.sleep(0.1)
        else:
            pytest.fail("synthetic provider did not start")
        yield f"http://127.0.0.1:{port}/v1"
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.mark.parametrize(
    "prompt,completed",
    [
        ("S1 虚构：正常回答", True),
        ("S1 虚构：长度截断", False),
        ("S1 虚构：限流", False),
        ("S1 虚构：服务失败", False),
        ("S1 虚构：中途断流", False),
        ("S1 虚构：等待取消", False),
    ],
)
def test_host_pipe_and_feedback(native, provider, tmp_path, monkeypatch, prompt, completed):
    import aiohttp
    from open_webui.routers import openai
    from open_webui.utils import session_pool

    from integrations.openwebui.s1_action import Action
    from integrations.openwebui.s1_pipe import Pipe
    from qa.s1_mock_provider import ANSWER, TOKEN

    async def scenario():
        user_id, chat_id, parent_id, message_id = (str(uuid.uuid4()) for _ in range(4))
        await native.users.Users.insert_new_user(user_id, "S1 虚构", f"{user_id}@example.invalid", role="user")
        config = dict(
            enabled=True,
            mode="mock",
            instance_id="s1-native",
            user_id=user_id,
            db_path=str(tmp_path / "trial.db"),
            version_key="synthetic-native-key-at-least-32-chars",
            started_at=1,
            base_url=provider,
            provider_model="whynote-s1-synthetic",
        )
        path = tmp_path / "config.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        monkeypatch.setenv("WHYNOTE_S1_CONFIG", str(path))
        messages = {
            parent_id: dict(id=parent_id, role="user", content=prompt, parentId=None),
            message_id: dict(
                id=message_id, role="assistant", content="", parentId=parent_id, model=PIPE_ID, done=False
            ),
        }
        await native.chats.Chats.insert_new_chat(
            chat_id, user_id, native.chats.ChatForm(chat={"history": {"messages": messages, "currentId": message_id}})
        )
        async with aiohttp.ClientSession() as session:

            async def connection(_):
                return provider, TOKEN, {}

            async def pool():
                return session

            monkeypatch.setattr(openai, "get_openai_connection", connection)
            monkeypatch.setattr(session_pool, "get_session", pool)
            response = await Pipe().pipe(
                {"model": PIPE_ID, "stream": True}, {"id": user_id}, {"chat_id": chat_id, "message_id": message_id}
            )
            output = []
            iterator = response.body_iterator
            if prompt.endswith("等待取消"):
                output.append(await anext(iterator))
                await iterator.aclose()
            else:
                output = [chunk async for chunk in iterator]
        store = TrialStore(config)
        with store._transaction() as db:
            row = db.execute("SELECT * FROM s1_generations").fetchone()
            assert row["status"] == ("completed" if completed else "incomplete")
            assert row["settled_micro"] is None  # The mock never invents billed tokens.
        assert ("[DONE]" in "".join(output)) == completed
        # Match the host's normal save; failed calls cannot forge eligibility by saving text.
        messages[message_id].update(content=ANSWER, done=True)
        await native.chats.Chats.update_chat_by_id(chat_id, {"history": {"messages": messages}})
        body = dict(
            chat_id=chat_id,
            id=message_id,
            model=PIPE_ID,
            messages=list(messages.values()),
            session_id="synthetic-native",
            whynote_click_id=str(uuid.uuid4()),
        )

        async def menu(event):
            return event["data"]["input"]["options"][0]["value"]

        if completed:
            result = await Action().action(body, {"id": user_id}, menu)
            assert result["result"] == "reason_submitted"
            events = store.get_events(Principal("s1-native", user_id), result["event_id"])
            assert len(events) > 1
            # Native delete hook revokes receipts before deleting the chat.
            await native.chats.Chats.delete_chat_by_id(chat_id)
            with pytest.raises(NotFoundError):
                with store._transaction() as db:
                    store.receipt(db, chat_id, user_id, message_id)
        else:
            with pytest.raises(NotFoundError):
                await Action().action(body, {"id": user_id}, menu)
            with store._transaction() as db:
                assert db.execute("SELECT COUNT(*) FROM actions").fetchone()[0] == 0

    asyncio.run(scenario())


def test_mock_rejects_real_text_in_history(provider):
    from qa.s1_mock_provider import ANSWER, MODEL, SYSTEM, TOKEN

    with httpx.Client() as client:
        response = client.post(
            provider + "/chat/completions",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": "not an allowed fixture"},
                    {"role": "assistant", "content": ANSWER},
                    {"role": "user", "content": "S1 虚构：正常回答"},
                ],
            },
        )
    assert response.status_code == 422
