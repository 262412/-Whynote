"""Loopback-only manual Laya tester. No persistence or feedback side effects."""

import argparse
import asyncio
import secrets
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .laya_local import MODEL, LocalLaya


class ManualInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    question: str = Field(min_length=1, max_length=3000)
    answer: str = Field(min_length=1, max_length=3000)
    feedback: str = Field(default="", max_length=1000)


def create_app(adapter, *, port=8766, timeout_seconds=60):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    token = secrets.token_urlsafe(32)
    origin = f"http://127.0.0.1:{port}"
    busy = threading.Lock()

    @app.middleware("http")
    async def local_only(request, call_next):
        if request.headers.get("host") != f"127.0.0.1:{port}" or request.headers.get("origin", origin) != origin:
            return JSONResponse({"detail": "Local origin required"}, status_code=403)
        if request.method == "POST":
            if not secrets.compare_digest(request.headers.get("x-local-token", ""), token):
                return JSONResponse({"detail": "Local page token required"}, status_code=403)
            if request.headers.get("content-type", "").split(";")[0] != "application/json":
                return JSONResponse({"detail": "JSON required"}, status_code=415)
            # Bound before JSON decoding, including chunked requests without Content-Length.
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 32768:
                    return JSONResponse({"detail": "Request too large"}, status_code=413)
            request._body = bytes(body)
        response = await call_next(request)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": (
                    f"default-src 'self'; script-src 'nonce-{token}'; style-src 'unsafe-inline'; "
                    "frame-ancestors 'none'; form-action 'none'; base-uri 'none'"
                ),
            }
        )
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, exc):
        return JSONResponse({"detail": "Invalid input fields"}, status_code=422)

    @app.get("/", response_class=HTMLResponse)
    def page():
        return Path(__file__).with_name("laya_local.html").read_text(encoding="utf-8").replace("__TOKEN__", token)

    @app.get("/health")
    def health():
        return {"status": "ready", "model": MODEL, "device": str(adapter.agent.device), "busy": busy.locked()}

    @app.post("/api/reason")
    async def reason(data: ManualInput):
        if not busy.acquire(blocking=False):
            raise HTTPException(429, "Local model is busy; wait for the current request")
        state = f"User question: {data.question}\nAssistant answer: {data.answer}\nUser feedback: {data.feedback}"

        def run():
            started = time.perf_counter()
            try:
                result = adapter.evaluate_reason(lambda: state, enabled=True)
                return {**result, "elapsed_ms": round((time.perf_counter() - started) * 1000, 1)}
            finally:
                busy.release()

        future = asyncio.get_running_loop().run_in_executor(None, run)
        future.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout_seconds)
        except TimeoutError:
            raise HTTPException(504, "Local inference timed out; wait until the model is idle") from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        except Exception:
            raise HTTPException(503, "Local inference failed; no feedback was saved") from None

    return app


def main():
    parser = argparse.ArgumentParser(description="Whynote local Laya manual tester")
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--enable-local-test", action="store_true", required=True)
    args = parser.parse_args()
    adapter = LocalLaya.load(args.model_dir, device=args.device, enabled=args.enable_local_test)
    import uvicorn

    uvicorn.run(create_app(adapter, port=args.port), host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
