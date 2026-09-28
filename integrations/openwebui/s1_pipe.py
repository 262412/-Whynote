"""
title: 知因 个人文本试用
author: Whynote
version: 0.1.0
required_open_webui_version: 0.11.4
"""

import os
import uuid

from whynote.domain import NotFoundError
from whynote.provider_keys import load_provider_key
from whynote.s1 import PIPE_ID, TrialStore, generation_input, load_config
from whynote.s1_provider import complete_response


class Pipe:
    name = "知因 个人文本试用"

    async def pipe(self, body, __user__=None, __metadata__=None, __task__=None):
        from open_webui.models.chats import Chats
        from open_webui.routers.openai import get_openai_connection
        from open_webui.utils.session_pool import get_session

        config_path = os.environ["WHYNOTE_S1_CONFIG"]
        config = load_config(config_path)
        if (
            __task__ is not None
            or not isinstance(__user__, dict)
            or __user__.get("id") != config["user_id"]
            or body.get("model") != PIPE_ID
        ):
            raise NotFoundError("S1 authenticated text task is required")
        metadata = __metadata__ or {}
        if any(metadata.get(k) for k in ("files", "tools", "tool_ids", "tool_servers", "assistant_message_id")):
            raise NotFoundError("S1 supports text generation only")
        if any(metadata.get("features", {}).values()):
            raise NotFoundError("S1 auxiliary generation is disabled")
        chat_id = str(uuid.UUID(metadata.get("chat_id", "")))
        message_id = str(uuid.UUID(metadata.get("message_id", "")))
        store = TrialStore(config)
        store.check_available()
        chat = await Chats.get_chat_by_id_and_user_id(chat_id, config["user_id"])
        if chat is None or chat.user_id != config["user_id"] or chat.created_at < config["started_at"]:
            raise NotFoundError("S1 requires a new owned trial chat")
        parent_id, prompt, messages = generation_input(chat, message_id, store)
        store.enroll(chat_id, config["user_id"])
        # Reuse the host's server-side connection and HTTP pool, not a second SDK.
        url, key, _ = await get_openai_connection(0)
        key_file = os.environ.get("WHYNOTE_PROVIDER_KEYS_FILE")
        if config["mode"] == "cloud" and key_file:
            key = load_provider_key(key_file, "deepseek")
        if url.rstrip("/") != config["base_url"] or not key:
            raise ValueError("S1 host connection does not match the approved endpoint")
        session = await get_session()
        attempt = store.reserve(chat_id, config["user_id"], message_id, parent_id, prompt)
        return await complete_response(session, config, store, attempt, key, messages)
