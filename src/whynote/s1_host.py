"""Optional hooks for the pinned, isolated Open WebUI S1 instance."""

import json
import os
from pathlib import Path

from starlette.responses import JSONResponse

from .s1 import PIPE_ID, TrialStore, load_config


def invalidate_chat(chat_id, *, revoke=False):
    path = os.environ.get("WHYNOTE_S1_CONFIG")
    if path:
        # Deletion must also work with the entry disabled. Never enable outbound.
        config = json.loads(Path(path).read_text(encoding="utf-8"))
        TrialStore(config).invalidate(chat_id, revoke=revoke)


def invalidate_edit(chat, incoming):
    if not os.environ.get("WHYNOTE_S1_CONFIG"):
        return
    if "history" not in incoming:
        return
    before = chat.chat.get("history", {}).get("messages", {})
    after = incoming.get("history", {}).get("messages", {})
    fields = ("role", "content", "parentId", "model", "timestamp", "done", "error", "output")
    if before.keys() - after.keys() or any(
        old is not None and any(old.get(k) != value.get(k) for k in fields)
        for mid, value in after.items()
        if isinstance(value, dict)
        for old in [before.get(mid)]
    ):
        invalidate_chat(chat.id)


def confirm_saved_response(chat, message_id):
    path = os.environ.get("WHYNOTE_S1_CONFIG")
    if path:
        config = load_config(path)
        TrialStore(config).confirm_saved(chat, message_id)


class TrialBoundary:
    """Deny host bypass routes; generation still authenticates inside the Pipe."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"].rstrip("/")
        denied = (
            "/openai",
            "/ollama",
            "/api/v1/pipelines",
            "/api/v1/audio",
            "/api/v1/images",
            "/api/v1/retrieval",
            "/api/v1/tools",
            "/api/v1/knowledge",
            "/api/v1/files",
            "/api/v1/memories",
            "/api/v1/tasks",
            "/api/message",
            "/api/v1/messages",
            "/api/embeddings",
            "/api/v1/embeddings",
            "/api/events/webhooks",
        )
        copy_route = path.startswith("/api/v1/chats/") and bool(
            {"share", "shared", "export", "import", "fork", "clone", "compact", "all"} & set(path.split("/"))
        )
        if copy_route or (any(path == p or path.startswith(p + "/") for p in denied) and path != "/openai/config"):
            return await JSONResponse({"detail": "S1 direct provider and auxiliary routes are disabled"}, 403)(
                scope, receive, send
            )
        if path in {"/api/chat/completions", "/api/v1/chat/completions"}:
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > 1_048_576:
                    return await JSONResponse({"detail": "S1 request too large"}, 413)(scope, receive, send)
                if not message.get("more_body"):
                    break
            try:
                load_config(os.environ["WHYNOTE_S1_CONFIG"])
                data = json.loads(body)
                if (
                    not isinstance(data, dict)
                    or data.get("model") != PIPE_ID
                    or data.get("models")
                    or data.get("model_item")
                    or data.get("tool_ids")
                    or data.get("files")
                    or any(data.get("features", {}).values())
                    or data.get("filter_ids")
                ):
                    raise ValueError("unsupported S1 generation")
                data["background_tasks"] = {}  # Suppress hidden title/tag/follow-up calls.
                data["tools"] = []  # Explicitly opt out of the host's builtin tools.
                encoded = json.dumps(data).encode()
            except Exception:
                return await JSONResponse({"detail": "S1 generation is not admitted"}, 403)(scope, receive, send)
            delivered = False

            async def replay():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": encoded, "more_body": False}
                return await receive()

            scope = dict(scope)
            scope["headers"] = [(k, v) for k, v in scope["headers"] if k.lower() != b"content-length"]
            scope["headers"].append((b"content-length", str(len(encoded)).encode()))
            return await self.app(scope, replay, send)
        return await self.app(scope, receive, send)
