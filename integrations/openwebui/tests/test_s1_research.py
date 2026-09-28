"""Developer integration checks on the pinned host; synthetic content only."""

import asyncio
import json
import uuid
from datetime import datetime, timezone

import pytest

from whynote.domain import NotFoundError, Principal
from whynote.research import register_source
from whynote.research_report import report
from whynote.s1 import PIPE_ID, TrialStore
from whynote.s1_host import confirm_saved_response


@pytest.mark.parametrize("source", ["self_natural", "scripted", "public_replay"])
@pytest.mark.parametrize("templates", [False, True])
def test_native_save_feedback_and_delete_keep_denominator(native, tmp_path, monkeypatch, source, templates):
    from integrations.openwebui.s1_action import Action

    async def scenario():
        user_id, chat_id, protocol, study = (str(uuid.uuid4()) for _ in range(4))
        await native.users.Users.insert_new_user(user_id, "Fictional", f"{user_id}@example.invalid", role="user")
        config = dict(
            enabled=True,
            mode="mock",
            instance_id="native-research",
            user_id=user_id,
            db_path=str(tmp_path / "study.db"),
            version_key="synthetic-native-research-key-32-chars",
            started_at=1,
            base_url="http://127.0.0.1:8126/v1",
            provider_model="whynote-s1-synthetic",
            research_enabled=True,
            research_study_ref=study,
            research_protocol_ref=protocol,
        )
        if templates:
            config.update(
                suggestion_research_enabled=True,
                suggestion_template_enabled=True,
                suggestion_model_revision="a" * 40,
                suggestion_fixture={
                    "tasks": ["general"],
                    "outcome": "suggested",
                    "reason_ids": ["general.style"],
                    "citations": {},
                },
            )
            monkeypatch.setenv("WHYNOTE_TEMPLATE_SYNTHETIC", "1")
            monkeypatch.setenv("WHYNOTE_LOCAL_CHAIN", "1")
        path = tmp_path / "config.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        monkeypatch.setenv("WHYNOTE_S1_CONFIG", str(path))
        messages = {}
        pairs = [(str(uuid.uuid4()), str(uuid.uuid4())) for _ in range(3)]
        for parent, answer in pairs:
            messages[parent] = dict(id=parent, role="user", content="native fictional prompt", parentId=None)
            messages[answer] = dict(
                id=answer,
                role="assistant",
                content="native fictional answer",
                parentId=parent,
                model=PIPE_ID,
                done=True,
            )
        chat = await native.chats.Chats.insert_new_chat(
            chat_id, user_id, native.chats.ChatForm(chat={"history": {"messages": messages, "currentId": pairs[-1][1]}})
        )
        store = TrialStore(config)
        principal = Principal(config["instance_id"], user_id)
        store.enroll(chat.id, chat.user_id)
        register_source(
            store,
            principal,
            chat_id,
            dict(
                registration_id=str(uuid.uuid4()),
                study_ref=study,
                protocol_ref=protocol,
                context_ref=chat_id,
                feedback_ref=None,
                source_kind=source,
                public_label_origin="none",
            ),
        )
        for index, (parent, answer) in enumerate(pairs):
            attempt = store.reserve(chat_id, user_id, answer, parent, "native fictional prompt")
            store.finish(attempt, answer="native fictional answer", complete=index < 2)
            saved = await native.chats.Chats.upsert_message_to_chat_by_id_and_message_id(
                chat_id, answer, {"done": True}
            )
            confirm_saved_response(saved, answer)
            confirm_saved_response(saved, answer)
        body = dict(
            chat_id=chat_id,
            id=pairs[0][1],
            model=PIPE_ID,
            messages=list(messages.values()),
            session_id="native-research",
            whynote_click_id=str(uuid.uuid4()),
        )

        async def choose(menu):
            return menu["data"]["input"]["options"][0]["value"]

        async def template_callback(event):
            kind, data = event["type"], event["data"]
            if kind == "whynote:suggestion-render":
                return {"binding": data["binding"]}
            if kind == "whynote:suggestion-response":
                return {
                    "operation": "done" if data["confirmed"] else "yes",
                    "reason_id": None if data["confirmed"] else "general.style",
                    "timing": None,
                }
            assert kind == "whynote:suggestion-dismiss"
            return True

        async def emit(event):
            assert event["type"] == "notification"

        async def perform():
            if templates:
                from integrations.openwebui.local_chain_action import Action as TemplateAction

                return await TemplateAction().action(body, {"id": user_id}, emit, template_callback)
            return await Action().action(body, {"id": user_id}, choose)

        result = await perform()
        if templates:
            assert result["suggestion"]["result"] == "confirmed"
        else:
            assert result["result"] == "reason_submitted"
        cutoff = datetime.now(timezone.utc).isoformat()
        before = report(store.path, principal, "2026-01-01T00:00:00Z", cutoff)
        assert before["groups"][source]["eligible_answers"] == 2
        assert before["groups"][source]["negative_answers"] == 1
        if templates:
            assert before["groups"][source]["suggestions"]["confirmed"] == 1
        assert before["attempt_status_counts_in_interval"]["incomplete"] == 1
        # Actual pinned ORM deletion invokes the existing S1 invalidation hook.
        assert await native.chats.Chats.delete_chat_by_id(chat_id)
        after = report(store.path, principal, before["since"], datetime.now(timezone.utc).isoformat())
        assert after["groups"][source]["eligible_answers"] == 2
        assert after["groups"][source]["invalidated_eligible_answers"] == 2
        assert report(store.path, principal, before["since"], cutoff) == before
        with pytest.raises(NotFoundError):
            await perform()
        with store._transaction() as db:
            assert db.execute("SELECT count(*) FROM events").fetchone()[0] == (4 if templates else 3)
            assert db.execute("SELECT count(*) FROM outbox").fetchone()[0] == 1

    asyncio.run(scenario())
