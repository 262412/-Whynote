"""Loopback-only synthetic host for exercising the manual feedback flow."""

from __future__ import annotations

import argparse
import hmac
import json
import secrets
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from .api import create_app
from .domain import ConflictError, NotFoundError, Principal

DEMO_TARGET = {"object_type": "assistant_response", "object_id": "demo-response", "object_version": "v1"}
DEMO_REASONS = [
    {"code": "factual_error", "label": "事实错误"},
    {"code": "irrelevant", "label": "内容不相关"},
    {"code": "style", "label": "表达方式"},
]
DEMO_CODES = [reason["code"] for reason in DEMO_REASONS]


class RenderedDisplay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1)
    display_id: str = Field(min_length=1)
    mode: Literal["manual_menu", "edit_menu"]
    ui_version: Literal["demo-v1"]
    shown_reason_codes: list[str]


def _require_loopback(request: Request) -> None:
    if request.client is None or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(403, "local demo accepts loopback requests only")


def create_demo_app(db_path: str | Path = "var/whynote-demo.db") -> FastAPI:
    token = secrets.token_urlsafe(32)
    demo_principal = Principal("demo-tenant", f"demo-{secrets.token_hex(16)}")

    def authenticate(request: Request) -> Principal:
        _require_loopback(request)
        submitted = request.headers.get("X-Demo-Session", "")
        if not hmac.compare_digest(submitted, token):
            raise HTTPException(401, "invalid local demo session")
        return demo_principal

    def authorize_target(principal: Principal, target: Mapping[str, str]) -> bool:
        return principal == demo_principal and dict(target) == DEMO_TARGET

    app = create_app(db_path, authenticate, authorize_target)
    app.state.demo_token = token
    app.state.demo_principal = demo_principal
    store = app.state.store

    @app.get("/demo", response_class=HTMLResponse)
    def page(request: Request) -> HTMLResponse:
        _require_loopback(request)
        template = Path(__file__).with_name("demo.html").read_text(encoding="utf-8")
        config = {"token": token, "target": DEMO_TARGET, "reasons": DEMO_REASONS}
        html = template.replace("__DEMO_CONFIG__", json.dumps(config, ensure_ascii=False))
        return HTMLResponse(
            html,
            headers={
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'",
            },
        )

    @app.post("/demo/rendered-displays")
    def rendered_display(body: RenderedDisplay, request: Request) -> dict[str, str | bool]:
        principal = authenticate(request)
        if body.shown_reason_codes != DEMO_CODES:
            raise HTTPException(409, "display does not match the local demo menu")
        try:
            target = store.get_target_ref(principal, body.event_id)
            if not authorize_target(principal, target):
                raise NotFoundError("feedback action not found")
            receipt = store.record_display(
                principal,
                body.event_id,
                body.display_id,
                body.mode,
                body.shown_reason_codes,
                body.ui_version,
            )
        except NotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
        return receipt

    return app


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Run the synthetic Whynote feedback demo on loopback")
    parser.add_argument("--db", default="var/whynote-demo.db")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    uvicorn.run(create_demo_app(args.db), host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
