"""Loopback synthetic browser/Action/ledger harness; never connect a real host or model."""

import argparse
import asyncio
import json
import os
import secrets
import sys
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "qa")]


def create_app(directory, port):
    from s1_research_fixture import Fixture

    from integrations.openwebui.local_chain_action import Action
    from whynote.research_report import report
    from whynote.template_suggestions import digest

    f = Fixture(directory)
    f.clock = time.time()
    question = "请改写以下代码，保留函数名和参数。\n```python\ndef total(items):\n    return sum(items)\n```"
    answer = "```python\ndef total_values(values, extra):\n    return sum(values) + extra\n```"
    f.config.update(
        suggestion_research_enabled=True,
        suggestion_template_enabled=True,
        suggestion_model_revision="a" * 40,
        suggestion_fixture={
            "tasks": ["code_rewrite"],
            "outcome": "suggested",
            "reason_ids": ["code.interface_changed", "general.instruction_not_followed"],
            "citations": {
                reason: [
                    {"source": "request", "start": 0, "end": len(question), "source_sha256": digest(question)},
                    {"source": "answer", "start": 0, "end": len(answer), "source_sha256": digest(answer)},
                ]
                for reason in ("code.interface_changed", "general.instruction_not_followed")
            },
        },
    )
    f.config_path.write_text(json.dumps(f.config), encoding="utf-8")
    os.environ.update(WHYNOTE_S1_CONFIG=str(f.config_path), WHYNOTE_LOCAL_CHAIN="1", WHYNOTE_TEMPLATE_SYNTHETIC="1")
    chat = f.chat("browser-template", "scripted")
    saved = f.answer(chat, "template", prompt=question, response=answer)
    action = Action()

    async def owned(chat_id, user_id):
        return chat if (chat_id, user_id) == (chat.id, chat.user_id) else None

    action._owned_chat = owned
    pending, queue = {}, asyncio.Queue()
    token = secrets.token_hex(32)
    origin = f"http://127.0.0.1:{port}"
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def boundary(request, next_handler):
        if request.headers.get("host") != f"127.0.0.1:{port}" or request.headers.get("origin", origin) != origin:
            return Response(status_code=403)
        if request.url.path.startswith("/api/") and request.headers.get("x-fixture-token") != token:
            return Response(status_code=403)
        if int(request.headers.get("content-length", "0")) > 32768:
            return Response(status_code=413)
        response = await next_handler(request)
        response.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
        return response

    async def call(event):
        ident = str(uuid.uuid4())
        future = asyncio.get_running_loop().create_future()
        pending[ident] = future
        await queue.put({"id": ident, **event})
        try:
            return await asyncio.wait_for(future, 61)
        finally:
            pending.pop(ident, None)

    async def emit(event):
        await queue.put({"id": None, **event})

    @app.get("/")
    async def page():
        return HTMLResponse(
            (ROOT / "qa/m5_template_browser.html").read_text(encoding="utf-8").replace("FIXTURE_TOKEN", token)
        )

    @app.get("/suggestion_dialog.js")
    async def javascript():
        return Response(
            (ROOT / "integrations/openwebui/suggestion_dialog.js").read_text(encoding="utf-8"),
            media_type="text/javascript",
        )

    @app.get("/api/event")
    async def event():
        try:
            return await asyncio.wait_for(queue.get(), 10)
        except TimeoutError:
            return Response(status_code=204)

    @app.post("/api/callback/{ident}")
    async def callback(ident: str, request: Request):
        future = pending.get(ident)
        if future is None or future.done():
            return Response(status_code=409)
        future.set_result(await request.json())
        return {"accepted": True}

    @app.post("/api/click")
    async def click():
        body = {**saved.body, "whynote_click_id": str(uuid.uuid4())}
        return await action.action(body, {"id": f.principal.actor_ref}, emit, call)

    @app.get("/api/report")
    async def results():
        f.clock = time.time()
        result = report(f.store.path, f.principal, "2026-09-01T00:00:00Z", f.now())
        (f.directory / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return JSONResponse(result)

    return app


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8132)
    args = parser.parse_args()
    uvicorn.run(create_app(args.data_dir, args.port), host="127.0.0.1", port=args.port, access_log=False)
