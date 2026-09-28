"""Local, synthetic Chat Completions fixture for S1 host integration tests.

This server has no upstream client and accepts only the exact fixture messages.
It is not a cloud adapter or an enforcement mechanism for a real provider.
"""

from __future__ import annotations

import argparse
import asyncio
import hmac
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

MODEL = "whynote-s1-synthetic"
TOKEN = "s1-local-synthetic-not-a-cloud-key"
SYSTEM = "这是知因 S1 虚构接入测试。"
CASES = {
    "S1 虚构：正常回答": "stop",
    "S1 虚构：长度截断": "length",
    "S1 虚构：限流": "rate_limit",
    "S1 虚构：服务失败": "server_error",
    "S1 虚构：中途断流": "interrupted",
    "S1 虚构：等待取消": "wait",
}
ANSWER = "这是本地虚构回答，没有调用云模型。"


def messages_for(prompt: str) -> list[dict[str, str]]:
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]


def create_app(completion_gate: Path | None = None) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        if request.client is None or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
            return JSONResponse({"error": {"code": "loopback_required"}}, status_code=403)
        if not hmac.compare_digest(request.headers.get("authorization", ""), f"Bearer {TOKEN}"):
            return JSONResponse({"error": {"code": "synthetic_key_required"}}, status_code=401)
        return await call_next(request)

    @app.get("/v1/models")
    async def models():
        return {"object": "list", "data": [{"id": MODEL, "object": "model", "owned_by": "local-fixture"}]}

    @app.post("/v1/chat/completions")
    async def completions(request: Request):
        try:
            body = await request.json()
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(422, "invalid synthetic request") from None
        if not isinstance(body, dict) or body.get("model") != MODEL:
            raise HTTPException(422, "synthetic model required")
        if not isinstance(body.get("stream", False), bool):
            raise HTTPException(422, "stream must be boolean")
        allowed = {"model", "messages", "stream", "stream_options", "max_tokens", "temperature"}
        if body.keys() - allowed:
            raise HTTPException(422, "unsupported synthetic request fields")
        messages = body.get("messages")
        scenario = None
        if isinstance(messages, list) and len(messages) in (2, 4, 6, 8, 10):
            valid = messages[0] == {"role": "system", "content": SYSTEM}
            for index in range(1, len(messages) - 1, 2):
                valid &= messages[index] == {"role": "user", "content": "S1 虚构：正常回答"}
                valid &= messages[index + 1] == {"role": "assistant", "content": ANSWER}
            if valid and isinstance(messages[-1], dict) and messages[-1].get("role") == "user":
                prompt = messages[-1].get("content")
                if isinstance(prompt, str) and messages[-1] == {"role": "user", "content": prompt}:
                    scenario = CASES.get(prompt)
        if scenario is None:
            raise HTTPException(422, "exact synthetic messages required")
        if scenario in {"rate_limit", "server_error"}:
            return JSONResponse({"error": {"code": scenario}}, status_code=429 if scenario == "rate_limit" else 503)
        if scenario in {"interrupted", "wait"} and not body.get("stream"):
            raise HTTPException(422, "this synthetic case requires streaming")

        async def frames():
            # No real tokens, billing, network client, or content persistence.
            first = {
                "id": "chatcmpl-s1-synthetic",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": MODEL,
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": ANSWER}, "finish_reason": None}],
            }
            yield f"data: {json.dumps(first, ensure_ascii=False)}\n\n"
            if scenario == "wait":
                await asyncio.Event().wait()  # Host timeout / user cancellation must stop the request.
            if scenario == "interrupted":
                return  # EOF without a terminal finish reason or [DONE].
            if completion_gate is not None:
                completion_gate.with_suffix(".waiting").touch()
                while not completion_gate.exists():
                    await asyncio.sleep(0.05)
            first["choices"] = [{"index": 0, "delta": {}, "finish_reason": scenario}]
            yield f"data: {json.dumps(first)}\n\n"
            yield "data: [DONE]\n\n"

        if body.get("stream"):
            return StreamingResponse(frames(), media_type="text/event-stream")
        return {
            "id": "chatcmpl-s1-synthetic",
            "object": "chat.completion",
            "created": 0,
            "model": MODEL,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": ANSWER}, "finish_reason": scenario}],
        }

    return app


def main():
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8126)
    parser.add_argument("--completion-gate", type=Path, help="Synthetic shutdown test: wait for this file before stop")
    args = parser.parse_args()
    uvicorn.run(create_app(args.completion_gate), host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
