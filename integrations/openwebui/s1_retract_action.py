"""
title: 撤销知因点踩
author: Whynote
version: 0.1.0
required_open_webui_version: 0.11.4
"""

from integrations.openwebui.s1_action import Action as FeedbackAction
from whynote.domain import NotFoundError, Principal


class Action(FeedbackAction):
    async def action(self, body, __user__=None, __event_emitter__=None):
        if not isinstance(__user__, dict) or not __user__.get("id"):
            raise NotFoundError("S1 authenticated subject is required")
        principal = Principal(self.tenant, __user__["id"])
        chat = await self._owned_chat(body.get("chat_id", ""), principal.actor_ref)
        target = self._target(chat, body)
        store = self._store_for_target(principal, target)
        state = store.current_action(principal, target)
        result = store.retract_action(principal, state["event_id"], f"s1-retract:{state['event_id']}")
        if __event_emitter__:
            await __event_emitter__({"type": "notification", "data": {"type": "success", "content": "知因点踩已撤销"}})
        return {"event_id": result["event_id"], "result": "retracted"}
