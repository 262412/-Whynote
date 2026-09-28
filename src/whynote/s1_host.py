"""Optional hooks for the pinned, isolated Open WebUI S1 instance."""

import json
import os
from pathlib import Path

from starlette.responses import JSONResponse

from .domain import NotFoundError
from .research import BROWSER_FIELDS
from .s1 import PIPE_ID, TrialStore, load_config


def invalidate_chat(chat_id, *, revoke=False):
    path = os.environ.get("WHYNOTE_S1_CONFIG")
    if path:
        # Deletion must also work with the entry disabled. Never enable outbound.
        config = json.loads(Path(path).read_text(encoding="utf-8"))
        # A stopped/misconfigured research entry must not prevent host deletion.
        # Cleanup only appends invalidation for attempts already tracked.
        config["research_enabled"] = False
        config["suggestion_research_enabled"] = False
        config.pop("research_versions", None)
        TrialStore(config).invalidate(chat_id, revoke=revoke)


def invalidate_edit(chat, incoming):
    if not os.environ.get("WHYNOTE_S1_CONFIG"):
        return
    if "history" not in incoming:
        return
    before = chat.chat.get("history", {}).get("messages", {})
    history = incoming.get("history")
    after = history.get("messages") if isinstance(history, dict) else None
    fields = ("role", "content", "parentId", "model", "timestamp", "done", "error", "output")
    # The host drops null messages. Revoke before that normalization so restoring
    # the original bytes cannot revive a receipt that crossed a deletion/edit.
    if not isinstance(after, dict) or any(
        not isinstance(after.get(mid), dict)
        or not isinstance(old, dict)
        or any(old.get(k) != after[mid].get(k) for k in fields)
        for mid, old in before.items()
    ):
        invalidate_chat(chat.id)


def confirm_saved_response(chat, message_id):
    path = os.environ.get("WHYNOTE_S1_CONFIG")
    if path:
        try:
            config = load_config(path)
        except NotFoundError:
            # Stopping admission must not abort the host's final stream cleanup.
            # Leave the candidate unconfirmed and retain its budget charge.
            return
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
                    or data.get("tool_ids")
                    or data.get("files")
                    or any(data.get("features", {}).values())
                    or data.get("filter_ids")
                    or bool(BROWSER_FIELDS & set(data))
                ):
                    raise ValueError("unsupported S1 generation")
                item = data.pop("model_item", None)
                if item is not None and (not isinstance(item, dict) or item.get("id") != PIPE_ID or item.get("direct")):
                    raise ValueError("unsupported S1 model metadata")
                # Browser metadata is only a display hint. Removing it forces
                # the host to resolve the model and permissions server-side.
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
