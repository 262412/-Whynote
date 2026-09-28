"""Deterministic synthetic S1-3 reconciliation; no server, keys or provider calls."""

import argparse
import asyncio
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from whynote.domain import Principal
from whynote.research import register_source
from whynote.research_report import report
from whynote.s1 import PIPE_ID, TrialStore

ROOT = Path(__file__).parents[1]


def ref(name):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "whynote-s1-research-synthetic/" + name))


def registration(name, source):
    return {
        "registration_id": ref(name),
        "study_ref": ref("study"),
        "protocol_ref": ref("protocol"),
        "context_ref": ref("chat-" + name),
        "feedback_ref": None,
        "source_kind": source,
        "public_label_origin": "automated" if source == "public_replay" else "none",
    }


class Fixture:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=False)
        self.clock = datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp()
        self.principal = Principal("synthetic-s1-study", "synthetic-alice")
        self.config = {
            "enabled": True,
            "mode": "mock",
            "instance_id": self.principal.tenant_ref,
            "user_id": self.principal.actor_ref,
            "db_path": str(self.directory / "events.db"),
            "version_key": "synthetic-only-research-key-32-characters",
            "started_at": 1,
            "base_url": "http://127.0.0.1:8126/v1",
            "provider_model": "whynote-s1-synthetic",
            "research_enabled": True,
            "research_study_ref": ref("study"),
            "research_protocol_ref": ref("protocol"),
            "research_versions": {"config_ref": ref("config")},
        }
        self.config_path = self.directory / "config.json"
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")
        self.store = TrialStore(self.config)
        self.chats = {}

    def now(self):
        return datetime.fromtimestamp(self.clock, timezone.utc).isoformat()

    def chat(self, name, source=None):
        chat = SimpleNamespace(
            id=ref("chat-" + name), user_id=self.principal.actor_ref, created_at=2, chat={"history": {"messages": {}}}
        )
        self.chats[chat.id] = chat
        self.store.enroll(chat.id, chat.user_id)
        if source:
            register_source(self.store, self.principal, chat.id, registration(name, source))
        return chat

    def answer(self, chat, name, *, complete=True, save=True, prompt="虚构研究问题", response="虚构研究回答"):
        parent_id, message_id = ref(name + "-parent"), ref(name + "-answer")
        messages = chat.chat["history"]["messages"]
        messages[parent_id] = {"id": parent_id, "role": "user", "content": prompt, "parentId": None}
        messages[message_id] = {
            "id": message_id,
            "role": "assistant",
            "content": response,
            "done": True,
            "parentId": parent_id,
            "model": PIPE_ID,
        }
        attempt = self.store.reserve(chat.id, chat.user_id, message_id, parent_id, messages[parent_id]["content"])
        self.store.finish(
            attempt,
            answer=messages[message_id]["content"],
            complete=complete,
            usage={"prompt_tokens": 10, "completion_tokens": 10},
        )
        if complete and save:
            self.store.confirm_saved(chat, message_id)
        body = {
            "chat_id": chat.id,
            "id": message_id,
            "model": PIPE_ID,
            "session_id": "synthetic-browser",
            "whynote_click_id": ref(name + "-click"),
            "messages": list(messages.values()),
        }
        return SimpleNamespace(chat=chat, body=body, attempt=attempt, message_id=message_id)

    def action(self):
        from integrations.openwebui.s1_action import Action

        action = Action()

        async def owned(chat_id, user_id):
            return self.chats.get(chat_id) if user_id == self.principal.actor_ref else None

        action._owned_chat = owned
        return action

    def respond(self, answer, label, *, timing=False, new_click=False):
        body = dict(answer.body)
        if new_click:
            body["whynote_click_id"] = str(uuid.uuid4())

        async def choose(menu):
            self.clock += 1
            data = menu["data"]["input"]
            value = next(item["value"] for item in data["options"] if item["label"] == label)
            if timing:
                return {"value": value, "timing": {**data["measurement"], "active_ms": 100, "elapsed_ms": 500}}
            return value

        return asyncio.run(self.action().action(body, {"id": self.principal.actor_ref}, choose))


def build_fixture(directory):
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    fixture = Fixture(directory)
    sequence = iter(range(1000))
    with (
        patch.dict("os.environ", {"WHYNOTE_S1_CONFIG": str(fixture.config_path)}),
        patch("whynote.s1.time.time", lambda: fixture.clock),
        patch("whynote.store._now", fixture.now),
        patch("uuid.uuid4", lambda: uuid.UUID(ref("generated-" + str(next(sequence))))),
    ):
        natural = fixture.chat("natural", "self_natural")
        first = fixture.answer(natural, "natural-1")
        fixture.answer(natural, "natural-2")
        selected = fixture.respond(first, "事实有误", timing=True)
        # Same click, including reconnection, must not open the callback again.
        before_retry = fixture.store.get_events(fixture.principal, selected["event_id"])

        async def forbidden(_):
            raise AssertionError("completed click retried the menu")

        retried = asyncio.run(
            fixture.action().action(
                {**first.body, "session_id": "reconnected"}, {"id": fixture.principal.actor_ref}, forbidden
            )
        )
        assert retried == selected
        assert fixture.store.get_events(fixture.principal, selected["event_id"]) == before_retry
        fixture.respond(first, "表达方式不合适", new_click=True)
        scripted = fixture.chat("scripted", "scripted")
        for index, label in enumerate(("暂时跳过", "不愿说明", "关闭")):
            answer = fixture.answer(scripted, "scripted-" + str(index))
            fixture.respond(answer, label)
            if index == 0:
                fixture.respond(answer, "都不是", new_click=True)
        public = fixture.chat("public", "public_replay")
        fixture.respond(fixture.answer(public, "public-1"), "事实有误")
        late = fixture.answer(public, "public-2")
        late_target = fixture.store.qualify(public, late.body)
        late_action = fixture.store.create_action(
            fixture.principal, late_target, {"interaction_contract": "manual-v1", "channel": "openwebui-s1"}, "late"
        )
        missing = fixture.chat("missing")
        fixture.answer(missing, "missing-1")
        fixture.answer(natural, "aborted", complete=False)
        fixture.answer(natural, "length", complete=False)
        fixture.answer(natural, "unsaved", save=False)
        early = report(fixture.store.path, fixture.principal, "2026-09-01T00:00:00Z", fixture.now())
        fixture.clock += 86401
        fixture.respond(late, "表达方式不合适")
        fixture.store.retract_action(fixture.principal, selected["event_id"], "late-undo")
        fixture.store.invalidate(natural.id, revoke=True)
        result = report(fixture.store.path, fixture.principal, "2026-09-01T00:00:00Z", fixture.now())
        assert report(fixture.store.path, fixture.principal, early["since"], early["as_of"]) == early
        assert late_action["event_id"] in next(
            a["action_ids"] for a in result["answers"] if a["attempt_id"] == late.attempt
        )
    (fixture.directory / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return fixture, result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="New directory; existing data is never overwritten")
    args = parser.parse_args()
    _, result = build_fixture(args.output)
    print(
        json.dumps(
            {
                "snapshot_sha256": result["snapshot_sha256"],
                "groups": {
                    source: {k: group[k] for k in ("eligible_answers", "negative_answers", "actor_roles")}
                    for source, group in result["groups"].items()
                },
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
