import asyncio
import json
import runpy
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

FIXTURE = runpy.run_path(str(Path(__file__).parents[1] / "qa/s1_mock_provider.py"))
ANSWER, MODEL, TOKEN = (FIXTURE[key] for key in ("ANSWER", "MODEL", "TOKEN"))
create_app, messages_for = FIXTURE["create_app"], FIXTURE["messages_for"]


@pytest.fixture
def client():
    with TestClient(create_app(), headers={"Authorization": f"Bearer {TOKEN}"}) as session:
        yield session


def request_body(case="正常回答", stream=False):
    return {"model": MODEL, "messages": messages_for(f"S1 虚构：{case}"), "stream": stream}


def test_discovery_and_completion(client):
    assert client.get("/v1/models").json()["data"][0]["id"] == MODEL
    result = client.post("/v1/chat/completions", json=request_body())
    assert result.status_code == 200
    choice = result.json()["choices"][0]
    assert choice["message"]["content"] == ANSWER
    assert choice["finish_reason"] == "stop"
    assert "usage" not in result.json()  # Never manufacture actual token usage.


@pytest.mark.parametrize("case,finish", [("正常回答", "stop"), ("长度截断", "length"), ("中途断流", None)])
def test_stream_terminal_evidence(client, case, finish):
    result = client.post("/v1/chat/completions", json=request_body(case, True))
    assert result.status_code == 200
    frames = [line[6:] for line in result.text.splitlines() if line.startswith("data: ")]
    assert json.loads(frames[0])["choices"][0]["delta"]["content"] == ANSWER
    if finish is None:
        assert len(frames) == 1
    else:
        assert frames[-1] == "[DONE]"
        assert json.loads(frames[-2])["choices"][0]["finish_reason"] == finish


@pytest.mark.parametrize("case,status", [("限流", 429), ("服务失败", 503)])
def test_provider_failure_does_not_echo_messages(client, case, status):
    result = client.post("/v1/chat/completions", json=request_body(case))
    assert result.status_code == status
    assert "messages" not in result.text
    assert "content" not in result.text


@pytest.mark.parametrize("mutation", ["content", "history", "model", "tools", "stream"])
def test_non_fixture_requests_rejected_without_echo(client, mutation):
    body = request_body()
    sentinel = "NON_FIXTURE_CONTENT_MUST_NOT_BE_ECHOED"
    if mutation == "content":
        body["messages"][-1]["content"] = sentinel
    elif mutation == "history":
        body["messages"].insert(1, {"role": "user", "content": sentinel})
    elif mutation == "stream":
        body["stream"] = sentinel
    else:
        body[mutation] = sentinel
    result = client.post("/v1/chat/completions", json=body)
    assert result.status_code == 422
    assert sentinel not in result.text


def test_auth_and_network_boundary(client):
    assert client.get("/v1/models", headers={"Authorization": "Bearer not-the-fixture-key"}).status_code == 401

    async def remote():
        transport = httpx.ASGITransport(app=create_app(), client=("192.0.2.1", 1))
        async with httpx.AsyncClient(transport=transport, base_url="http://fixture") as session:
            return await session.get("/v1/models", headers={"Authorization": f"Bearer {TOKEN}"})

    assert asyncio.run(remote()).status_code == 403


def test_wait_case_is_cancellable():
    async def run():
        transport = httpx.ASGITransport(app=create_app(), client=("127.0.0.1", 1))
        async with httpx.AsyncClient(transport=transport, base_url="http://fixture") as session:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(
                    session.post(
                        "/v1/chat/completions",
                        json=request_body("等待取消", True),
                        headers={"Authorization": f"Bearer {TOKEN}"},
                    ),
                    timeout=0.05,
                )

    asyncio.run(run())
