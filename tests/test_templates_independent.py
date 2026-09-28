"""Independent M5-2b contract probes; included in default CI; preserve failing assertions."""

import json
import sqlite3

import pytest
from test_suggestions import trial as trial
from test_template_suggestions import client, fixture_config, invoke
from test_template_suggestions import host as host

from whynote import suggestions
from whynote.domain import project
from whynote.template_suggestions import digest, prepare


@pytest.mark.parametrize("source", ["request", "answer"])
def test_unicode_offsets_and_literal_markup(source):
    text = "前🙂e\u0301<script>window.syntheticProbe=1</script>后"
    config = fixture_config(text)
    config["citations"]["general.style"] = [
        {"source": source, "start": 1, "end": len(text) - 1, "source_sha256": digest(text)}
    ]
    _, _, cards, metadata = prepare(text, text, "bound-version", config)
    assert cards[0]["quotes"] == [{"source": source, "text": text[1:-1]}]
    reference = metadata["cards"][0]["references"][0]
    assert reference["quote_sha256"] == digest(text[1:-1])
    assert reference["object_version"] == "bound-version"
    assert text[1:-1] not in json.dumps(metadata, ensure_ascii=False)


@pytest.mark.parametrize("length,has_quote", [(1200, True), (1201, False)])
def test_quote_length_boundary(length, has_quote):
    text = "界" * length
    config = fixture_config(text)
    _, _, cards, _ = prepare(text, "answer", "v1", config)
    assert bool(cards[0]["quotes"]) is has_quote
    assert bool(cards[0]["template"]) is has_quote


@pytest.mark.parametrize("text", ["x" * 8193, "界" * 2731])
def test_source_limit_counts_utf8_bytes(text):
    with pytest.raises(ValueError, match="Invalid synthetic"):
        prepare(text, "answer", "v1", fixture_config(text))


@pytest.mark.parametrize("field,value", [("source", []), ("end", 2.0), ("source_sha256", None)])
def test_malformed_reference_downgrades_without_fabrication(field, value):
    config = fixture_config()
    config["citations"]["general.style"][0][field] = value
    _, _, cards, metadata = prepare("虚构研究问题", "answer", "v1", config)
    assert cards[0]["quotes"] == [] and cards[0]["template"] is None
    assert metadata["cards"][0]["references"] == []


@pytest.mark.parametrize("phase", ["first_confirmation", "correction"])
@pytest.mark.parametrize("boundary", ["disable", "retract", "replace", "revoke"])
def test_invalidation_during_response_keeps_prior_ledger(host, phase, boundary):
    f = host
    operations = [("yes", "general.style")]
    if phase == "correction":
        operations += [("correct", "general.instruction_not_followed")]
    callback = client(f, operations)
    responses = 0
    before = None

    async def receive(event):
        nonlocal responses, before
        value = await callback(event)
        if event["type"] != "whynote:suggestion-response":
            return value
        responses += 1
        if responses != (1 if phase == "first_confirmation" else 2):
            return value
        if boundary == "disable":
            f.config["suggestion_template_enabled"] = False
            f.config_path.write_text(json.dumps(f.config), encoding="utf-8")
        elif boundary == "retract":
            f.store.retract_action(f.principal, f.template_event, "qa-inflight-retract")
        elif boundary == "replace":
            f.chat_record.chat["history"]["messages"][f.template_answer.message_id]["content"] = "QA changed version"
        else:
            f.store.invalidate(f.chat_record.id, revoke=True)
        before = f.store.get_events(f.principal, f.template_event)
        return value

    result = invoke(f, receive)
    after = f.store.get_events(f.principal, result["event_id"])
    assert result["suggestion"]["result"] == "superseded"
    assert after == before
    assert sum(e["event_type"] == "m52_response_recorded" for e in after) == (phase == "correction")
    assert "input" not in f.client_events


def test_arrived_render_receipt_survives_slow_host_recheck(host):
    """The same receipt-arrival contract applies to render and response callbacks."""
    f = host
    callback = client(f, [])
    owned = f.action._owned_chat
    arrived = False
    received_at = None

    async def receive(event):
        nonlocal arrived, received_at
        value = await callback(event)
        if event["type"] == "whynote:suggestion-render":
            f.clock += 59.5
            arrived, received_at = True, f.clock
        return value

    async def slow_owned(*args):
        nonlocal arrived
        if arrived:
            f.clock += 1
            arrived = False
        return await owned(*args)

    f.action._owned_chat = slow_owned
    result = invoke(f, receive)
    current = f.store.get_events(f.principal, result["event_id"])
    expires_at = next(e["payload"]["expires_at"] for e in current if e["event_type"] == "m52_suggestion_generated")
    assert received_at < expires_at < f.clock
    assert project(current)["action_status"] == "active"
    with sqlite3.connect(f.store.path) as db:
        outbox = db.execute("SELECT count(*) FROM outbox WHERE event_id=?", (result["event_id"],)).fetchone()[0]
    assert outbox == 1
    print(
        json.dumps(
            {
                "received_at": received_at,
                "expires_at": expires_at,
                "checked_at": f.clock,
                "result": result["suggestion"],
                "outbox": outbox,
                "event_types": [e["event_type"] for e in current],
            }
        )
    )
    assert any(e["event_type"] == "m52_render_reported" for e in current), (
        "Timely render receipt was discarded after host recheck; "
        f"received={received_at}, expiry={expires_at}, now={f.clock}, result={result['suggestion']}"
    )


@pytest.mark.parametrize("arrival,expected_render", [(59.5, True), (60, False), (60.5, False)])
def test_render_receipt_real_deadline_boundary(host, arrival, expected_render):
    f = host
    callback = client(f, [("close", None)])

    async def receive(event):
        value = await callback(event)
        if event["type"] == "whynote:suggestion-render":
            f.clock += arrival
        return value

    result = invoke(f, receive)
    current = f.store.get_events(f.principal, result["event_id"])
    assert any(e["event_type"] == "m52_render_reported" for e in current) is expected_render
    assert not any(e["event_type"] == "m52_response_recorded" for e in current)
    assert project(current)["action_status"] == "active"


def test_manual_fallback_uses_same_action_and_distinct_projection(host):
    f = host
    callback = client(f, [("none_matched", None)])

    async def choose(event):
        if event["type"] == "input":
            return event["data"]["input"]["options"][0]["value"]
        return await callback(event)

    result = invoke(f, choose)
    current = f.store.get_events(f.principal, result["event_id"])
    assert result["suggestion"]["manual"]["event_id"] == result["event_id"]
    assert result["suggestion"]["manual"]["result"] == "reason_submitted"
    assert project(current)["reason_code"] is not None
    assert suggestions.project_suggestions(current)["reason_id"] is None
    assert sum(e["event_type"] == "negative_feedback_action_recorded" for e in current) == 1


def test_retry_of_confirmed_click_does_not_reopen_or_overwrite(host):
    f = host
    callback = client(f, [("yes", "general.style"), ("correct", "general.instruction_not_followed"), ("done", None)])
    result = invoke(f, callback)
    before = f.store.get_events(f.principal, result["event_id"])
    count = len(f.client_events)
    replay = invoke(f, callback)
    assert replay["replayed"] is True
    assert f.store.get_events(f.principal, result["event_id"]) == before
    assert len(f.client_events) == count
    assert suggestions.project_suggestions(before)["reason_id"] == "general.instruction_not_followed"
