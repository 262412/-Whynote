"""
title: 获取 Laya 原因建议
author: Whynote
version: 0.1.0
required_open_webui_version: 0.11.4
"""

import os
import re

import httpx

from integrations.openwebui.s1_action import Action as FeedbackAction
from whynote.domain import MANUAL_REASONS, NotFoundError, Principal
from whynote.laya_local import MODEL, PROMPT_VERSION


async def local_suggestion(question, answer):
    # Fixed loopback service and its existing page-token protocol; no cloud key.
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8766", trust_env=False, timeout=65) as client:
        page = await client.get("/")
        page.raise_for_status()
        token = re.search(r'<script nonce="([A-Za-z0-9_-]+)">', page.text)
        if token is None:
            raise ValueError("Local service protocol mismatch")
        response = await client.post(
            "/api/reason",
            headers={"X-Local-Token": token.group(1)},
            json={"question": question, "answer": answer, "feedback": ""},
        )
        response.raise_for_status()
        result = response.json()
        if (
            not isinstance(result, dict)
            or result.get("provider") != "laya_local"
            or result.get("model_version") != MODEL
            or result.get("prompt_version") != PROMPT_VERSION
            or result.get("attribution_source") != "model_inferred_unconfirmed"
            or result.get("calibrated") is not False
            or result.get("primary_reason") not in dict(MANUAL_REASONS)
        ):
            raise ValueError("Local model identity mismatch")
        return result["primary_reason"]


class Action(FeedbackAction):
    async def _eligible(self, body, user):
        if os.environ.get("WHYNOTE_LOCAL_CHAIN") != "1" or not isinstance(user, dict) or not user.get("id"):
            raise NotFoundError("Local chain is disabled or unauthenticated")
        principal = Principal(self.tenant, user["id"])
        chat = await self._owned_chat(body.get("chat_id", ""), principal.actor_ref)
        target = self._target(chat, body)
        state = self._store_for_target(principal, target).current_action(principal, target)
        if state["action_status"] != "active":
            raise NotFoundError("先通过知因菜单点踩，再获取建议")
        return chat, target, state["event_id"]

    async def action(self, body, __user__=None, __event_emitter__=None):
        if __event_emitter__ is None:
            raise NotFoundError("Browser session required")
        chat, target, event_id = await self._eligible(body, __user__)
        messages = chat.chat["history"]["messages"]
        answer = messages[body["id"]]
        question = messages[answer["parentId"]]["content"]
        try:
            code = await local_suggestion(question, answer["content"])
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            await __event_emitter__(
                {
                    "type": "notification",
                    "data": {"type": "error", "content": "Laya 暂不可用、输入过长或返回无效；已记录的点踩不受影响。"},
                }
            )
            return {"result": "unavailable"}
        _, current_target, current_event = await self._eligible(body, __user__)
        if current_target != target or current_event != event_id:
            raise NotFoundError("Feedback changed while Laya was running")
        label = dict(MANUAL_REASONS)[code]
        await __event_emitter__(
            {
                "type": "notification",
                "data": {
                    "type": "info",
                    "content": f"Laya 建议：{label}。模型推测，未确认、未经校准；请在知因原因菜单中自行选择。",
                },
            }
        )
        return {"result": "model_inferred_unconfirmed", "reason": code, "model_version": MODEL}
