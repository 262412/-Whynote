"""
title: 知因 S0 点踩
author: Whynote
version: 0.2.0
required_open_webui_version: 0.11.4
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import time
import uuid
from pathlib import Path

from whynote.domain import MANUAL_OPERATIONS, MANUAL_REASONS, MANUAL_UI_VERSION, Principal
from whynote.store import EventStore

REASONS = {label: code for code, label in MANUAL_REASONS}
OPTIONS = {**REASONS, **{label: operation for operation, label in MANUAL_OPERATIONS.items()}}
TICKET_SECONDS = 60
MENU_TITLE = "知因・Whynote S0 原因"
MENU_MESSAGE = "点踩已受理，原因可选。取消仅记录关闭，保留已有原因。仅限虚构数据测试。"
UI_VERSION = MANUAL_UI_VERSION


class Action:
    """Open WebUI 0.11.4 Action; only the synthetic S0 fixture is eligible."""

    def __init__(self):
        self.tenant = os.environ["WHYNOTE_S0_TENANT"]
        self.version_key = os.environ["WHYNOTE_S0_VERSION_KEY"].encode("utf-8")
        fixture = Path(os.environ["WHYNOTE_S0_FIXTURE"]).read_text(encoding="utf-8")
        self.fixture = json.loads(fixture)
        if not self.tenant or len(self.version_key) < 32 or self.fixture.get("synthetic") is not True:
            raise ValueError("S0 isolation configuration is invalid")
        self.store = EventStore(os.environ["WHYNOTE_S0_DB"])

    async def _owned_chat(self, chat_id: str, user_id: str):
        from open_webui.models.chats import Chats

        return await Chats.get_chat_by_id_and_user_id(chat_id, user_id)

    def _target(self, chat, body: dict) -> dict[str, str]:
        if chat is None:
            raise ValueError("S0 message is unavailable")
        messages = chat.chat.get("history", {}).get("messages", {})
        message = messages.get(body.get("id"))
        if not isinstance(message, dict) or message.get("role") != "assistant":
            raise ValueError("S0 message is unavailable")
        parent = messages.get(message.get("parentId"))
        submitted_messages = body.get("messages")
        if not isinstance(submitted_messages, list):
            raise ValueError("S0 displayed message list is required")
        rendered = next(
            (item for item in submitted_messages if isinstance(item, dict) and item.get("id") == body.get("id")),
            None,
        )
        if (
            not isinstance(parent, dict)
            or parent.get("role") != "user"
            or parent.get("content") != self.fixture["prompt"]
            or message.get("content") != self.fixture["response"]
            or not isinstance(rendered, dict)
            or rendered.get("role") != "assistant"
            or rendered.get("content") != message["content"]
            or body.get("model") != message.get("model")
        ):
            raise ValueError("S0 fixture or displayed version does not match")
        chat_id = str(uuid.UUID(body["chat_id"]))
        message_id = str(uuid.UUID(body["id"]))
        version_fields = {
            "content": message["content"],
            "model": message["model"],
            "parent_id": message["parentId"],
            "timestamp": message.get("timestamp"),
        }
        encoded = json.dumps(version_fields, ensure_ascii=False, sort_keys=True).encode("utf-8")
        version = hmac.new(self.version_key, encoded, hashlib.sha256).hexdigest()
        return {
            "object_type": "openwebui_assistant_message",
            "object_id": f"{chat_id}/{message_id}",
            "object_version": f"s0v1-{version}",
        }

    async def action(self, body: dict, __user__=None, __event_call__=None, __event_emitter__=None):
        if not isinstance(__user__, dict) or not __user__.get("id") or __event_call__ is None:
            raise ValueError("authenticated S0 browser session is required")
        session_id = body.get("session_id")
        if not isinstance(session_id, str) or not session_id or len(session_id) > 200:
            raise ValueError("S0 browser session is required")
        user_id = __user__["id"]
        principal = Principal(self.tenant, user_id)
        owned = await self._owned_chat(body.get("chat_id", ""), user_id)
        target = self._target(owned, body)
        click_id = body.get("whynote_click_id")
        if click_id is not None:
            if not isinstance(click_id, str):
                raise ValueError("S0 click ID must be a UUID string")
            click_id = str(uuid.UUID(click_id))
        key = hmac.new(
            self.version_key,
            f"{user_id}:{target['object_id']}:{target['object_version']}:{click_id or 'legacy:' + session_id}".encode(),
            hashlib.sha256,
        ).hexdigest()
        metadata = {
            "action_type": "negative_feedback",
            "channel": "openwebui-s0",
            "locale": "zh-CN",
            "client_occurred_at": None,
            "interaction_contract": "manual-v1",
        }
        display_id = str(uuid.uuid4())
        if click_id is not None:
            state, replay = self.store.begin_host_click(principal, target, metadata, key, display_id, time.time)
            if replay is not None:
                return replay
        else:
            state = self.store.create_action(principal, target, metadata, key, restart_retracted=True)
            if state["action_status"] != "active":
                return {"event_id": state["event_id"], "result": "retracted"}
            self.store.issue_display_ticket(principal, state["event_id"], display_id)
        event_id = state["event_id"]
        mode = "edit_menu" if state["attribution_status"] in {"selected", "edited"} else "manual_menu"
        session_ref = hashlib.sha256(session_id.encode()).hexdigest()
        # Database reservation/commit waits precede the menu's response interval.
        issued_at = time.monotonic()
        expires_at = time.time() + TICKET_SECONDS
        ticket_context = {
            "tenant_id": self.tenant,
            "user_id": user_id,
            "session_id": session_id,
            "event_id": event_id,
            "target": target,
            "display_id": display_id,
            "mode": mode,
            "expires_at": expires_at,
            "ui_version": UI_VERSION,
            "title": MENU_TITLE,
            "message": MENU_MESSAGE,
            "reasons": list(REASONS.items()),
        }
        choices = []
        for label, reason_code in OPTIONS.items():
            signed = json.dumps(
                {**ticket_context, "reason_code": reason_code},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            signature = hmac.new(self.version_key, signed, hashlib.sha256).hexdigest()
            choices.append((f"s0t1.{display_id}.{expires_at}.{reason_code}.{signature}", reason_code, label))
        deadline_task = None
        if click_id is not None:
            # Persist alongside the callback, without a database lock around UI work.
            deadline_task = asyncio.create_task(
                asyncio.to_thread(self.store.set_host_click_deadline, principal, event_id, key, display_id, expires_at)
            )

        async def receive_answer():
            answer = await __event_call__(
                {
                    "type": "input",
                    "data": {
                        "title": MENU_TITLE,
                        "message": MENU_MESSAGE,
                        "input": {
                            "type": "select",
                            "measurement": {
                                "version": "active-v1",
                                "display_id": display_id,
                                "session_ref": session_ref,
                            },
                            "options": [{"label": label, "value": value} for value, _, label in choices],
                        },
                    },
                }
            )
            # Capture in the callback task, before wait_for resumes its caller.
            # Other requests may block that caller's next turn on the event loop.
            expired = time.monotonic() - issued_at >= TICKET_SECONDS or time.time() >= expires_at
            return answer, expired

        try:
            answer, expired = await asyncio.wait_for(receive_answer(), timeout=TICKET_SECONDS)
        except TimeoutError:
            return {"event_id": event_id, "result": "ticket_expired"}
        finally:
            if deadline_task is not None:
                await deadline_task
        if isinstance(answer, dict) and answer.get("error"):
            return {"event_id": event_id, "result": "client_unavailable"}
        timing = None
        if isinstance(answer, dict) and "value" in answer:
            timing = answer.get("timing")
            if not isinstance(timing, dict):
                timing = None
            answer = answer["value"]
        if expired:
            return {"event_id": event_id, "result": "ticket_expired"}
        reason_code = None
        if isinstance(answer, str):
            reason_code = next((code for value, code, _ in choices if hmac.compare_digest(answer, value)), None)
        if answer is not False and reason_code is None:
            raise ValueError("S0 menu response is invalid")
        current = await self._owned_chat(body["chat_id"], user_id)
        if self._target(current, body) != target:
            return {"event_id": event_id, "result": "target_changed"}
        receipt = self.store.record_display(
            principal,
            event_id,
            display_id,
            mode,
            list(REASONS.values()),
            UI_VERSION,
            client_session_ref=session_ref,
        )
        if answer is False:
            reason_code = "reason_menu_closed"
        current = await self._owned_chat(body["chat_id"], user_id)
        if self._target(current, body) != target:
            return {"event_id": event_id, "display": receipt, "result": "target_changed"}
        updated = self.store.record_user_action(
            principal,
            event_id,
            reason_code
            if reason_code in MANUAL_OPERATIONS
            else ("reason_edited" if mode == "edit_menu" else "reason_selected"),
            None if reason_code in MANUAL_OPERATIONS else reason_code,
            display_id,
            True,
            f"s0-choice:{display_id}",
            timing=timing,
        )
        if __event_emitter__ is not None:
            message = "知因 S0 反馈与原因已记录"
            if reason_code in MANUAL_OPERATIONS:
                message = {
                    "reason_none_matched": "反馈已保存：都不是，当前原因已清空",
                    "reason_skipped": "反馈已保存：暂时跳过，当前原因保持不变",
                    "reason_declined": "反馈已保存：不愿说明，当前原因保持不变",
                    "reason_menu_closed": "原因菜单已关闭，当前原因保持不变",
                }[reason_code]
            await __event_emitter__({"type": "notification", "data": {"type": "success", "content": message}})
        return {
            "event_id": event_id,
            "display": receipt,
            "result": "response_recorded" if reason_code in MANUAL_OPERATIONS else "reason_submitted",
            "attribution_status": updated["attribution_status"],
        }
