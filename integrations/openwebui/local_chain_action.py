"""
title: 知因点踩 / 撤销
author: Whynote
version: 0.2.0
required_open_webui_version: 0.11.4
"""

import os
import uuid
from urllib.parse import quote

from integrations.openwebui.laya_action import Action as LayaAction
from whynote.domain import NotFoundError, Principal

icon_url = "data:image/svg+xml," + quote(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="black" stroke-width="1.8"'
    ' stroke-linecap="round" stroke-linejoin="round"><path d="M17 3h4v11h-4zM17 13l-5 8H9v-7H4'
    'a2 2 0 0 1-2-2l2-7a2 2 0 0 1 2-2h11z"/></svg>'
)


class Action(LayaAction):
    icon_url = icon_url
    suggestion_message = "知因原因分析：{label}。由 Laya 推测，未确认、未经校准。再次点击点踩按钮可撤销。"

    async def action(self, body, __user__=None, __event_emitter__=None):
        if (
            os.environ.get("WHYNOTE_LOCAL_CHAIN") != "1"
            or not isinstance(__user__, dict)
            or not __user__.get("id")
            or __event_emitter__ is None
        ):
            raise NotFoundError("知因本机入口不可用")
        click_id = body.get("whynote_click_id")
        if not isinstance(click_id, str):
            raise ValueError("知因点击标识必须是 UUID")
        click_id = str(uuid.UUID(click_id))
        principal = Principal(self.tenant, __user__["id"])
        chat = await self._owned_chat(body.get("chat_id", ""), principal.actor_ref)
        target = self._target(chat, body)
        store = self._store_for_target(principal, target)
        state, fresh = store.toggle_action(
            principal,
            target,
            {
                "action_type": "negative_feedback",
                "channel": "openwebui-local-chain",
                "locale": "zh-CN",
                "client_occurred_at": None,
                "interaction_contract": "manual-v1",
            },
            "local-toggle:" + click_id,
        )
        result = {"event_id": state["event_id"], "result": state["action_status"]}
        if not fresh:
            return {**result, "replayed": True}
        retracted = state["action_status"] == "retracted"
        await __event_emitter__(
            {
                "type": "notification",
                "data": {
                    "type": "success",
                    "content": "知因点踩已撤销，原原因建议不再适用。"
                    if retracted
                    else "知因点踩已保存，正在分析原因；再次点击可撤销。",
                },
            }
        )
        if retracted:
            return result
        # Never keep a transaction open while awaiting Laya. A later click can retract.
        try:
            _, current_target, current_event = await self._eligible(body, __user__)
            if current_target != target or current_event != state["event_id"]:
                return {**result, "result": "superseded"}
            suggestion = await super().action(body, __user__, __event_emitter__, expected_event=state["event_id"])
        except NotFoundError:
            return {**result, "result": "superseded"}
        return {**result, "suggestion": suggestion}
