"""Synthetic-only template continuation of the existing single downvote action."""

import asyncio
import hashlib
import time
import uuid

from integrations.openwebui.s1_action import Action as ManualAction
from whynote import live_suggestions, suggestions
from whynote.domain import ConflictError, NotFoundError, Principal
from whynote.research import validate_config
from whynote.template_suggestions import prepare


def gate(action, user):
    config = action._current_config()
    validate_config(config)
    if (
        config.get("enabled") is not True
        or config.get("suggestion_template_enabled") is not True
        or config.get("user_id") != user.get("id")
    ):
        raise NotFoundError("Synthetic template suggestions are unavailable")
    return config


async def run(action, body, user, call, emitter, target, event_id):
    principal = Principal(action.tenant, user["id"])
    initial = gate(action, user)

    async def current():
        config = gate(action, user)
        if config != initial:
            raise ConflictError("Synthetic configuration changed")
        chat = await action._owned_chat(body["chat_id"], user["id"])
        if action._target(chat, body) != target:
            raise ConflictError("Suggestion target changed")
        store = action._store_for_target(principal, target)
        store.config = config
        original_guard = store.guard

        def guard(db):
            if gate(action, user) != initial:
                raise ConflictError("Synthetic configuration changed")
            if original_guard is not None:
                original_guard(db)

        store.guard = guard
        suggestions.admit(store, principal, event_id)
        return chat, store

    async def manual():
        await current()
        manual_body = {**body, "whynote_click_id": str(uuid.uuid5(uuid.UUID(body["whynote_click_id"]), "manual"))}
        return await ManualAction.action(action, manual_body, user, call, emitter)

    if call is None:
        return {"result": "client_unavailable"}
    session = body.get("session_id")
    if not isinstance(session, str) or not 1 <= len(session) <= 200:
        raise ValueError("Synthetic browser session is required")
    display_id, suggestion_id = str(uuid.uuid4()), str(uuid.uuid4())
    session_ref = hashlib.sha256(session.encode()).hexdigest()
    confirmed = None

    async def dismiss():
        try:
            await asyncio.wait_for(call({"type": "whynote:suggestion-dismiss", "data": {"display_id": display_id}}), 1)
        except (TimeoutError, ValueError, ConnectionError):
            pass

    try:
        chat, store = await current()
        messages = chat.chat["history"]["messages"]
        message = messages[body["id"]]
        # No text selection, fixture evaluation or provider call precedes admission.
        question, answer = messages[message["parentId"]]["content"], message["content"]
        if live_suggestions.live(initial):
            result = await live_suggestions.evaluate(initial, question, answer)
            candidates, selection, cards, presentation = live_suggestions.presentation(
                question, answer, target["object_version"], result
            )
            _, store = await current()
        else:
            candidates, selection, cards, presentation = prepare(
                question, answer, target["object_version"], initial.get("suggestion_fixture")
            )
        record = suggestions.generate(
            store,
            principal,
            event_id,
            str(uuid.uuid4()),
            suggestion_id,
            candidates,
            selection,
            display_id=display_id,
            presentation=presentation,
        )
        payload = next(e["payload"] for e in store.get_events(principal, event_id) if e["record_id"] == record)
        binding = payload["binding"]
        if selection["outcome"] != "suggested":
            return {"result": "abstained", "manual": await manual()}
        deadline = time.monotonic() + max(0, payload["expires_at"] - time.time())

        async def receive(kind, data):
            remaining = min(deadline - time.monotonic(), payload["expires_at"] - time.time())
            if remaining <= 0:
                raise TimeoutError

            async def callback():
                value = await call({"type": kind, "data": data})
                received_at = time.time()
                if time.monotonic() >= deadline or received_at >= payload["expires_at"]:
                    raise TimeoutError
                return value, received_at

            return await asyncio.wait_for(callback(), remaining)

        await current()
        rendered, received_at = await receive(
            "whynote:suggestion-render",
            {
                "binding": binding,
                "cards": cards,
                "expires_at": payload["expires_at"],
                "session_ref": session_ref,
            },
        )
        if not isinstance(rendered, dict) or rendered != {"binding": binding}:
            raise ValueError("Invalid client render receipt")
        _, store = await current()
        suggestions.render(
            store, principal, event_id, str(uuid.uuid4()), display_id, binding, server_received_at=received_at
        )
        previous = suggestions.project_suggestions(store.get_events(principal, event_id))["last_response_id"]
        while True:
            response, received_at = await receive(
                "whynote:suggestion-response", {"display_id": display_id, "confirmed": confirmed}
            )
            if not isinstance(response, dict) or set(response) != {"operation", "reason_id", "timing"}:
                raise ValueError("Invalid template response")
            operation = response["operation"]
            _, store = await current()
            if operation == "done" and confirmed is not None:
                return {"result": "confirmed"}
            if (confirmed is None and operation in {"correct", "done"}) or (
                confirmed is not None and operation != "correct"
            ):
                raise ValueError("Invalid confirmation transition")
            manual_requested = operation == "manual"
            previous = suggestions.respond(
                store,
                principal,
                event_id,
                str(uuid.uuid4()),
                suggestion_id,
                display_id,
                "skip" if manual_requested else operation,
                response["reason_id"],
                previous,
                timing=response["timing"],
                client_session_ref=session_ref,
                server_received_at=received_at,
            )
            if operation in {"yes", "correct"}:
                confirmed = response["reason_id"]
                continue
            if manual_requested or operation in {"no", "none_matched"}:
                await dismiss()
                return {"result": operation, "manual": await manual()}
            return {"result": operation}
    except (NotFoundError, ConflictError):
        return {"result": "superseded"}
    except (TimeoutError, ValueError, KeyError, TypeError, ConnectionError) as exc:
        if confirmed is not None and isinstance(exc, (TimeoutError, ConnectionError)):
            try:
                await current()
                return {"result": "confirmed"}
            except (NotFoundError, ConflictError):
                return {"result": "superseded"}
        # Errors are constant and content-free. The already committed action survives.
        await emitter(
            {
                "type": "notification",
                "data": {"type": "info", "content": "建议未完成，点踩已保存。可使用常规理由菜单。"},
            }
        )
        await dismiss()
        try:
            result = {"result": "fallback", "manual": await manual()}
            if isinstance(exc, live_suggestions.ReplayError):
                result["failure_code"] = str(exc) if str(exc) in live_suggestions.ERRORS else "invalid_response"
            return result
        except (NotFoundError, ConflictError):
            return {"result": "superseded"}
    finally:
        await dismiss()
