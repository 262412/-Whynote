"""
title: 知因 个人试用反馈
author: Whynote
version: 0.1.0
required_open_webui_version: 0.11.4
"""

import os

from integrations.openwebui.s0_action import Action as FixtureAction
from whynote.domain import NotFoundError
from whynote.s1 import TrialStore, load_config


class Action(FixtureAction):
    menu_title = "知因・Whynote 原因"
    menu_message = "点踩已受理，原因可选。取消仅记录关闭，保留已有原因。"
    channel = "openwebui-s1"
    recorded_message = "反馈与原因已记录"

    def __init__(self):
        self.config_path = os.environ["WHYNOTE_S1_CONFIG"]
        self.config = load_config(self.config_path)
        self.tenant = self.config["instance_id"]
        self.version_key = self.config["version_key"].encode()
        self.store = TrialStore(self.config)

    def _current_config(self):
        current = load_config(self.config_path)
        if any(
            current.get(k) != self.config.get(k)
            for k in (
                "instance_id",
                "user_id",
                "version_key",
                "db_path",
                "mode",
                "base_url",
                "provider_model",
                "pricing_version",
            )
        ):
            raise NotFoundError("S1 configuration changed; reload the entry")
        return current

    async def _owned_chat(self, chat_id, user_id):
        if user_id != self._current_config()["user_id"]:
            raise NotFoundError("S1 subject is unavailable")
        return await super()._owned_chat(chat_id, user_id)

    def _target(self, chat, body):
        self._current_config()
        if chat is None or chat.user_id != self.config["user_id"]:
            raise NotFoundError("S1 chat is unavailable")
        return self.store.qualify(chat, body)

    def _store_for_target(self, principal, target):
        chat_id, message_id = target["object_id"].split("/")

        def guard(db):
            self._current_config()
            row = self.store.receipt(db, chat_id, principal.actor_ref, message_id)
            if row["version"] != target["object_version"]:
                raise NotFoundError("S1 generation changed")

        return TrialStore(self.config, guard=guard)
