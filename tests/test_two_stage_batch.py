import asyncio
import copy
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
from argparse import Namespace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from whynote import two_stage as core
from whynote import two_stage_batch as batch

ROOT = Path(__file__).resolve().parents[1]
BODY_MARKERS = ("PRIVATE_REQUEST", "PRIVATE_ANSWER", "PRIVATE_PRIOR", "PRIVATE_FEEDBACK", "PRIVATE_GOLD")


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(batch.encode(value))
    return pin(path)


def pin(path):
    raw = path.read_bytes()
    return {"path": str(path), "sha256": batch.sha(raw), "bytes": len(raw)}


def sample(source="helpsteer3", *, context=None, answer="PRIVATE_ANSWER", pointer=None, index=0):
    context = context or [{"role": "user", "content": "PRIVATE_REQUEST"}]
    pointer = pointer or {"helpsteer3": "response2", "wildfb": "messages/1", "wildfeedback": "utterance/1"}[source]
    identity = {"source": source, "revision": "pinned", "file_sha256": "1" * 64, "row_id": index, "target_id": pointer}
    key = batch.sha(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode())
    state = {"context": context, "answer": answer}
    original = identity | {
        "input_id": key,
        "mapping_version": "m55-input-v1",
        **state,
        "reference": {"feedback": ["PRIVATE_FEEDBACK"], "gold": "PRIVATE_GOLD"},
        "future": [{"role": "user", "content": "PRIVATE_FEEDBACK"}],
        "evidence_kinds": ["original_code", "reference", "tool_trace"],
    }
    target = {"target_id": key, "source": source, "eligible": True}
    return target, state, original


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    """Fixed 3 x 1000 synthetic denominator; only three have permitted body rows."""
    # Unit fixtures replace checkout admission; retain the already-validated catalog
    # in memory when pytest itself imports the installed wheel. Launcher tests below
    # exercise real source/resource loading without either replacement.
    catalog = core.load_catalog()
    monkeypatch.setattr(core, "load_catalog", lambda: copy.deepcopy(catalog))
    data_root = tmp_path / "中文 data root"
    prep = data_root / batch.PREP
    prep.mkdir(parents=True)
    expiry = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    sources, targets, originals, outbounds = [], [], [], []
    for source in sorted(batch.SOURCES):
        source_path = data_root / "var/research/sources" / (source + ".json")
        entry = write_json(source_path, {"synthetic": source}) | {
            "source": source,
            "revision": "pinned",
            "expires_at": expiry,
        }
        sources.append(entry)
        target, state, original = sample(source)
        original["file_sha256"] = entry["sha256"]
        identity = {k: original[k] for k in ("source", "revision", "file_sha256", "row_id", "target_id")}
        key = batch.sha(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode())
        original["input_id"] = key
        raw = batch.encode(state).decode()
        state_hash = batch.sha(raw.encode())
        target.update(
            target_id=key,
            input_key=key,
            group_id=key,
            expires_at=expiry,
            modified=False,
            screening_flags=[],
            exclusion_code=None,
            original_state_sha256=state_hash,
            outbound_state_sha256=state_hash,
        )
        targets.append(target)
        originals.append((key, batch.encode(original).decode()))
        outbounds.append((key, raw))
        for i in range(999):
            key = batch.sha(f"{source}-excluded-{i}".encode())
            targets.append(
                target
                | {
                    "target_id": key,
                    "input_key": None,
                    "eligible": False,
                    "exclusion_code": "original_sensitive_pattern",
                }
            )
    manifest = {
        "project_root": str(data_root),
        "research_root": str(data_root / "var/research"),
        "sources": [
            entry
            | {
                "status": "ADMITTED_FOR_EXPLORATION",
                "purpose": "local_unlabelled_exploration",
                "access": ["local_codex"],
                "max_targets": 1000,
            }
            for entry in sources
        ],
    }
    manifest_pin = write_json(data_root / "var/research/source-manifest.json", manifest)
    original_path = data_root / "var/research/inputs.sqlite3"
    with sqlite3.connect(original_path) as db:
        db.execute("CREATE TABLE inputs(input_id TEXT PRIMARY KEY,payload TEXT)")
        db.executemany("INSERT INTO inputs VALUES (?,?)", originals)
    outbound_path = prep / "outbound.sqlite3"
    with sqlite3.connect(outbound_path) as db:
        db.execute("CREATE TABLE inputs(target_id TEXT PRIMARY KEY,state_json TEXT)")
        db.executemany("INSERT INTO inputs VALUES (?,?)", outbounds)
    db_pin = pin(outbound_path)
    source_plan = {
        "schema_version": "m55-typesafe-offline-plan-v1",
        "seed": 42,
        "target_count": 3000,
        "contract_id": "m55-original-choice-v1",
        "expires_at": expiry,
        "targets": targets,
        "sources": sources,
        "input_pins": {"source_manifest": manifest_pin, "inputs": pin(original_path)},
        "db_sha256": db_pin["sha256"],
        "db_bytes": db_pin["bytes"],
    }
    plan_pin = write_json(prep / "plan.json", source_plan)["sha256"]
    monkeypatch.setattr(batch, "PLAN_SHA256", plan_pin)
    runtime = {"catalog_version": core.VERSION, "catalog_sha256": core.CATALOG_SHA256, "code_sha256": "1" * 64}
    monkeypatch.setattr(batch, "runtime_identity", lambda _: runtime)
    return Namespace(
        root=data_root,
        plan=source_plan,
        plan_sha=plan_pin,
        runtime=runtime,
        directory=data_root / batch.RESEARCH / "m55-two-stage-test",
    )


def args(bundle, mode="mock", **overrides):
    return Namespace(
        **{
            "data_root": str(bundle.root),
            "batch_id": "test",
            "mode": mode,
            "config_file": None,
            "keys_file": None,
            "report_mode": "mock",
            **overrides,
        }
    )


def invoke(bundle, mode="mock", **overrides):
    return batch.run(args(bundle, mode, **overrides), ROOT)


def approved_config(bundle):
    value = {
        "schema_version": batch.CONFIG_VERSION,
        "policy": {**batch.asdict(batch.OFFLINE_POLICY), "version": "synthetic-live-gate-test"},
        "live": {
            "enabled": True,
            "batch_id": "test",
            "outbound_approval_ref": "synthetic-only-approval",
            "source_plan_sha256": bundle.plan_sha,
            "contract_version": core.VERSION,
            "catalog_sha256": core.CATALOG_SHA256,
            "expires_at": bundle.plan["expires_at"],
            "provider_cap_confirmed": True,
            "provider_cap_usd": "8",
            "budget": {
                "kind": "new_dedicated",
                "pool_id": "synthetic-pool",
                "approval_ref": "synthetic-budget",
                "pricing_ref": "synthetic-pricing",
                "limit_usd": "8",
                "reserve_per_stage_usd": "1",
                "input_usd_per_million": "1",
                "output_usd_per_million": "0",
            },
        },
    }
    path = bundle.root / "var/research/config.json"
    write_json(path, value)
    return path, value


@pytest.mark.parametrize("source", sorted(batch.SOURCES))
def test_three_adapters_do_not_project_annotations_or_invent_materials(source):
    target, state, original = sample(source)
    result = batch.adapt(target, state, original)
    assert result == {"request": "PRIVATE_REQUEST", "answer": "PRIVATE_ANSWER"}
    assert not set(result) & {"original_code", "reference", "tool_trace", "gold", "feedback"}
    assert "PRIVATE_FEEDBACK" not in json.dumps(result)


@pytest.mark.parametrize("source", sorted(batch.SOURCES))
def test_multiturn_request_is_last_user_and_prior_context_ends_before_it(source):
    context = [
        {"role": "user", "content": "PRIVATE_PRIOR request"},
        {"role": "assistant", "content": "PRIVATE_PRIOR answer"},
        {"role": "user", "content": "PRIVATE_REQUEST"},
    ]
    target, state, original = sample(
        source, context=context, pointer="utterance/3" if source == "wildfeedback" else None
    )
    result = batch.adapt(target, state, original)
    assert json.loads(result["prior_context"]) == context[:-1]
    assert result["request"] == context[-1]["content"]
    assert result["answer"] == state["answer"]


@pytest.mark.parametrize("change", ["sibling", "future", "missing_answer", "missing_request", "extra", "pointer"])
def test_ambiguous_or_incomplete_targets_are_excluded(change):
    target, state, original = sample()
    if change == "sibling":
        original["answer"] = "another assistant branch"
    elif change == "future":
        state["context"].append({"role": "assistant", "content": "future target"})
    elif change == "missing_answer":
        state["answer"] = original["answer"] = ""
    elif change == "missing_request":
        state["context"] = original["context"] = []
    elif change == "extra":
        state["reference"] = original["reference"]
    else:
        original["target_id"] = "response3"
    with pytest.raises(ValueError):
        batch.adapt(target, state, original)


def test_preview_mock_restart_report_and_legacy_ledger_independence(bundle):
    old = bundle.root / batch.PREP.parent / "runner/original.live.benchmark-summary.json"
    write_json(old, {"remaining_targets": 0, "counts": {"success": 2469, "error": 61}})
    before = old.read_bytes()
    preview = invoke(bundle, "preview")
    assert preview["original_targets"] == 3000 and preview["original_excluded"] == 2997
    assert preview["runnable"] == preview["not_executed"] == 3
    assert preview["estimated_maximum_reservation_usd"] is None
    assert "approved_research_policy" in preview["live_missing"]
    result = invoke(bundle)
    assert result["technical_success"] == 3
    assert result["stage_requests_started"] == {"route": 3, "reasons": 3}
    assert result["cost_unit"] == "synthetic_units"
    assert result["accuracy"] is result["f1"] is None
    again = invoke(bundle)
    assert again["invocation_stage_starts"] == again["invocation_context_reads"] == 0
    assert again["technical_success"] == 3
    report = invoke(bundle, "report")
    assert report == json.loads((bundle.directory / "mock.summary.json").read_text(encoding="utf-8"))
    assert report["technical_success"] == 3
    assert not list(bundle.directory.glob("live.*")) and old.read_bytes() == before
    for path in bundle.directory.iterdir():
        raw = path.read_bytes()
        assert all(marker.encode() not in raw for marker in (*BODY_MARKERS, "synthetic-key-only"))


def test_callbacks_commit_before_network_and_between_stages(bundle, monkeypatch):
    requests = []
    original = batch.synthetic_response

    def response(request):
        value = json.loads(request.content)
        with sqlite3.connect(bundle.directory / "mock.sqlite3") as db:
            sent = db.execute("SELECT target,name,status,metadata FROM stages WHERE status='started'").fetchall()
            assert len(sent) == 1
            key, name, _, record = sent[0]
            assert json.loads(record)["request_sha256"] == batch.sha(request.content)
            if name == "reasons":
                assert (
                    db.execute("SELECT status FROM stages WHERE target=? AND name='route'", (key,)).fetchone()[0]
                    == "completed"
                )
            else:
                assert "answer" not in value["state"] and "tool_trace" not in value["state"]
        requests.append(value)
        return original(request)

    monkeypatch.setattr(batch, "synthetic_response", response)
    invoke(bundle)
    assert len(requests) == 6
    assert all(list(q["criteria"]) == ["yes", "no", "unknown"] for b in requests[1::2] for q in b["questions"].values())


@pytest.mark.parametrize(
    "stage,status", [("route", 429), ("reasons", 529), ("reasons", "timeout"), ("reasons", "invalid")]
)
def test_failed_stages_are_terminal_with_unknown_reservation_and_no_retries(bundle, monkeypatch, stage, status):
    original = batch.synthetic_response
    calls = []

    def response(request):
        name = "route" if "task" in json.loads(request.content)["questions"] else "reasons"
        calls.append(name)
        if name == stage:
            if status == "timeout":
                raise httpx.ReadTimeout("PRIVATE_PROVIDER_ERROR")
            if status == "invalid":
                return httpx.Response(200, text="PRIVATE_PROVIDER_ERROR")
            return httpx.Response(status, text="PRIVATE_PROVIDER_ERROR")
        return original(request)

    monkeypatch.setattr(batch, "synthetic_response", response)
    result = invoke(bundle)
    assert result["technical_error"] == 3 and result["technical_success"] == 0
    assert result["unknown_cost_reservations"] == "3"
    assert result["known_usage"]["route"]["input_tokens"] == (300 if stage == "reasons" else 0)
    assert result["all_success"] is False and result["no_automatic_work_remaining"] is True
    previous = len(calls)
    assert invoke(bundle)["invocation_stage_starts"] == 0 and len(calls) == previous
    assert b"PRIVATE_PROVIDER_ERROR" not in (bundle.directory / "mock.sqlite3").read_bytes()


class Crash(BaseException):
    pass


@pytest.mark.parametrize("crash_event", ["prepared", "started", "completed"])
def test_process_interruption_keeps_stage_state_and_only_never_sent_can_restart(bundle, monkeypatch, crash_event):
    original = batch.Ledger.stage
    crashed = []

    def interrupt(self, key, event, record):
        original(self, key, event, record)
        if event == crash_event and record["stage"] == "route" and not crashed:
            crashed.append(key)
            raise Crash

    monkeypatch.setattr(batch.Ledger, "stage", interrupt)
    with pytest.raises(Crash):
        invoke(bundle)
    monkeypatch.setattr(batch.Ledger, "stage", original)
    result = invoke(bundle)
    if crash_event == "prepared":
        assert result["technical_success"] == 3 and result["reconcile"] == 0
    else:
        assert result["technical_success"] == 2 and result["reconcile"] == 1
        assert result["invocation_stage_starts"] == 4
        assert result["unknown_cost_reservations"] == ("1" if crash_event == "started" else "0")
        assert result["known_usage"]["route"]["input_tokens"] == (200 if crash_event == "started" else 300)


@pytest.mark.parametrize("boundary", ["cancel", "expiry", "revocation"])
def test_first_response_revocation_prevents_second_and_suppresses_suggestions(bundle, monkeypatch, boundary):
    config_path, config = approved_config(bundle)
    original = batch.synthetic_response
    calls = []

    def response(request):
        calls.append(True)
        reply = original(request)
        if boundary == "cancel":
            (bundle.directory / "CANCEL").touch()
        elif boundary == "revocation":
            config["live"]["enabled"] = False
            write_json(config_path, config)
        else:
            monkeypatch.setattr(batch, "unexpired", lambda _: batch.require(False, "source_or_approval_expired"))
        return reply

    monkeypatch.setattr(batch, "synthetic_response", response)
    result = invoke(bundle, config_file=str(config_path))
    assert len(calls) == 1
    assert result["technical_error"] == 1 and result["not_executed"] == 2
    assert result["known_usage"]["route"]["input_tokens"] == 100
    assert result["held_reservations"] == "0"
    with sqlite3.connect(bundle.directory / "mock.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM targets WHERE result IS NOT NULL").fetchone()[0] == 0


@pytest.mark.parametrize(
    "problem", ["missing_config", "invalid_policy", "budget", "cancel", "expiry", "revoked", "cap"]
)
def test_live_gate_failure_reads_no_context_key_or_network(bundle, monkeypatch, problem):
    path, config = approved_config(bundle)
    if problem == "invalid_policy":
        config["policy"]["route_probability"] = True
    elif problem == "budget":
        config["live"]["budget"]["limit_usd"] = "1"
        config["live"]["provider_cap_usd"] = "1"
    elif problem == "revoked":
        config["live"]["enabled"] = False
    elif problem == "cap":
        config["live"]["provider_cap_confirmed"] = False
    elif problem == "expiry":
        config["live"]["expires_at"] = "2000-01-01T00:00:00+00:00"
    elif problem == "cancel":
        bundle.directory.mkdir(parents=True)
        (bundle.directory / "CANCEL").touch()
    write_json(path, config)
    counts = CounterReads()
    monkeypatch.setattr(batch.Inputs, "__enter__", counts.context)
    monkeypatch.setattr(core, "load_provider_key", counts.key)
    monkeypatch.setattr(core, "_request_bytes", counts.network)
    with pytest.raises(ValueError):
        invoke(
            bundle,
            "live",
            config_file=None if problem == "missing_config" else str(path),
            keys_file=str(bundle.root / "secret.json"),
        )
    assert counts.values == {"context": 0, "key": 0, "network": 0}


class CounterReads:
    def __init__(self):
        self.values = dict.fromkeys(("context", "key", "network"), 0)

    def context(self, *args):
        self.values["context"] += 1
        pytest.fail("context read after failed gate")

    def key(self, *args):
        self.values["key"] += 1
        pytest.fail("key read after failed gate")

    def network(self, *args):
        self.values["network"] += 1
        pytest.fail("network after failed gate")


def test_new_batch_policy_or_code_change_does_not_reuse_frozen_plan(bundle):
    invoke(bundle, "preview")
    config_path, _ = approved_config(bundle)
    with pytest.raises(batch.Closed, match="fingerprint_changed"):
        invoke(bundle, config_file=str(config_path))
    bundle.runtime["code_sha256"] = "2" * 64
    with pytest.raises(batch.Closed, match="fingerprint_changed"):
        invoke(bundle)


def test_mock_cannot_change_existing_live_reservations(bundle):
    config_path, _ = approved_config(bundle)
    invoke(bundle, "preview", config_file=str(config_path))
    plan = json.loads((bundle.directory / "plan.json").read_text(encoding="utf-8"))
    live_path = bundle.directory / "live.sqlite3"
    ledger = batch.Ledger(live_path, plan, "live")
    target = next(t for t in plan["targets"] if t["disposition"] == "runnable")
    ledger.claim(target)
    ledger.close()
    before = live_path.read_bytes()
    assert invoke(bundle, config_file=str(config_path))["technical_success"] == 3
    assert live_path.read_bytes() == before
    assert not (bundle.root / batch.RESEARCH / "two-stage-budget-pools.sqlite3").exists()


def test_exhausted_existing_live_budget_stops_before_context_or_key(bundle, monkeypatch):
    config_path, config = approved_config(bundle)
    config["live"]["budget"]["limit_usd"] = config["live"]["provider_cap_usd"] = "2"
    write_json(config_path, config)
    invoke(bundle, "preview", config_file=str(config_path))
    plan = json.loads((bundle.directory / "plan.json").read_text(encoding="utf-8"))
    ledger = batch.Ledger(bundle.directory / "live.sqlite3", plan, "live")
    target = next(t for t in plan["targets"] if t["disposition"] == "runnable")
    refs = ledger.claim(target)
    ledger.stage(
        target["target_id"],
        "failed",
        {
            "stage": "route",
            "status": "failed_or_unknown",
            "budget_reservation_ref": refs["route"],
            "usage": None,
        },
    )
    ledger.finish(target["target_id"], "error", "synthetic-unknown-cost")
    ledger.close()
    counts = CounterReads()
    monkeypatch.setattr(batch.Inputs, "__enter__", counts.context)
    monkeypatch.setattr(core, "load_provider_key", counts.key)
    monkeypatch.setattr(core, "_request_bytes", counts.network)
    result = invoke(bundle, "live", config_file=str(config_path), keys_file=str(bundle.root / "fake-unused.json"))
    assert result["stopped"] == "budget_insufficient" and result["unknown_cost_reservations"] == "1"
    assert counts.values == {"context": 0, "key": 0, "network": 0}


def test_measured_usage_above_reservation_stops_second_stage_without_losing_cost(bundle, monkeypatch):
    monkeypatch.setattr(batch, "MOCK_BUDGET", batch.MOCK_BUDGET | {"limit_usd": "2", "input_usd_per_million": "30000"})
    result = invoke(bundle)
    assert result["stopped"] == "budget_exhausted"
    assert result["stage_requests_started"] == {"route": 1, "reasons": 0}
    assert result["known_usage_estimate"] == "3" and result["held_reservations"] == "0"


def test_report_reads_only_new_records_and_does_not_mutate_ledger(bundle, monkeypatch):
    invoke(bundle)
    path = bundle.directory / "mock.sqlite3"
    before = path.read_bytes()
    monkeypatch.setattr(batch, "metadata", lambda *_: pytest.fail("source metadata accessed by Report"))
    monkeypatch.setattr(core, "load_provider_key", lambda *_: pytest.fail("key accessed by Report"))
    result = invoke(bundle, "report")
    assert result["technical_success"] == 3 and path.read_bytes() == before


def test_source_revocation_is_denied_before_context_key_or_network(bundle, monkeypatch):
    source = Path(bundle.plan["input_pins"]["source_manifest"]["path"])
    value = json.loads(source.read_text(encoding="utf-8"))
    value["sources"][0]["status"] = "REVOKED"
    write_json(source, value)
    counts = CounterReads()
    monkeypatch.setattr(batch.Inputs, "__enter__", counts.context)
    monkeypatch.setattr(core, "load_provider_key", counts.key)
    monkeypatch.setattr(core, "_request_bytes", counts.network)
    with pytest.raises(batch.Closed, match="fingerprint_changed"):
        invoke(bundle)
    assert counts.values == {"context": 0, "key": 0, "network": 0}


@pytest.mark.parametrize("missing", ["ledger", "wrong_batch"])
def test_bound_budget_failure_is_checked_before_any_context_or_key(bundle, monkeypatch, missing):
    path, config = approved_config(bundle)
    invoke(bundle, "preview", config_file=str(path))
    plan = json.loads((bundle.directory / "plan.json").read_text(encoding="utf-8"))
    batch.register_pool(bundle.root, bundle.directory, plan)
    overrides = {}
    if missing == "wrong_batch":
        config["live"]["batch_id"] = "different"
        write_json(path, config)
        overrides["batch_id"] = "different"
    counts = CounterReads()
    monkeypatch.setattr(batch.Inputs, "__enter__", counts.context)
    monkeypatch.setattr(core, "load_provider_key", counts.key)
    monkeypatch.setattr(core, "_request_bytes", counts.network)
    with pytest.raises(batch.Closed, match="bound"):
        invoke(bundle, "live", config_file=str(path), keys_file=str(bundle.root / "fake-unused.json"), **overrides)
    assert counts.values == {"context": 0, "key": 0, "network": 0}


def test_missing_frozen_plan_never_reinitializes_existing_run(bundle):
    invoke(bundle)
    (bundle.directory / "plan.json").unlink()  # Synthetic corruption, never a recovery operation.
    with pytest.raises(batch.Closed, match="frozen_plan_missing"):
        invoke(bundle)


def test_input_snapshot_tampering_is_rejected_before_key_or_request(bundle, monkeypatch):
    invoke(bundle, "preview")
    with (bundle.root / batch.PREP / "outbound.sqlite3").open("ab") as stream:
        stream.write(b"changed")
    monkeypatch.setattr(core, "load_provider_key", lambda *_: pytest.fail("key read"))
    result = invoke(bundle)
    assert result["stopped"] == "input_fingerprint_changed" and result["not_executed"] == 3


def test_mock_cannot_open_any_real_key_or_use_sockets(tmp_path):
    path = tmp_path / "real-provider.json"
    path.write_text('{"typesafe":"never-read-this"}', encoding="utf-8")
    with batch.OfflineGuard([], ROOT):
        with pytest.raises(batch.Closed, match="file_read_forbidden"):
            path.read_text(encoding="utf-8")
        with socket.socket() as sock, pytest.raises(batch.Closed, match="network_forbidden"):
            sock.connect(("127.0.0.1", 9))


def test_mock_rejects_explicit_real_key_argument_without_opening(bundle):
    with pytest.raises(batch.Closed, match="keys_file_only"):
        invoke(bundle, keys_file=str(bundle.root / "nonexistent-secret.json"))


def test_distinct_real_reservations_budget_stop_and_new_directory_cannot_reuse_pool(bundle):
    path, config = approved_config(bundle)
    invoke(bundle, "preview", config_file=str(path))
    plan = json.loads((bundle.directory / "plan.json").read_text(encoding="utf-8"))
    batch.register_pool(bundle.root, bundle.directory, plan)
    with pytest.raises(batch.Closed, match="already_bound"):
        batch.register_pool(bundle.root, bundle.directory.with_name("another-batch"), plan)
    config["live"]["budget"]["limit_usd"] = "2"
    plan["config"] = config
    ledger = batch.Ledger(bundle.directory / "live.sqlite3", plan, "live")
    targets = [t for t in plan["targets"] if t["disposition"] == "runnable"]
    try:
        refs = ledger.claim(targets[0])
        assert len(set(refs.values())) == 2 and ledger.held == 2
        with pytest.raises(batch.Closed, match="budget_insufficient"):
            ledger.claim(targets[1])
        ledger.stage(
            targets[0]["target_id"],
            "started",
            {
                "stage": "route",
                "status": "started",
                "budget_reservation_ref": refs["route"],
                "usage": None,
            },
        )
        ledger.finish(targets[0]["target_id"], "error", "synthetic-failure")
        assert ledger.held == 1  # Unsent reasons released; unknown route is not free.
        with pytest.raises(batch.Closed, match="budget_insufficient"):
            ledger.claim(targets[1])
    finally:
        ledger.close()


def test_request_byte_limit_is_wire_size_and_second_failure_keeps_first_cost(bundle, monkeypatch):
    original = batch.Inputs.state

    def large_answer(self, target):
        return original(self, target) | {"answer": "汉" * 10500}

    monkeypatch.setattr(batch.Inputs, "state", large_answer)
    result = invoke(bundle)
    assert result["new_excluded"] == 0
    assert result["technical_error"] == 3
    assert result["stage_requests_started"] == {"route": 3, "reasons": 0}
    assert result["known_usage"]["route"]["input_tokens"] == 300
    assert result["unknown_cost_reservations"] == result["held_reservations"] == "0"
    lines = [
        json.loads(line) for line in (bundle.directory / "mock.results.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    runnable = [r for r in lines if r["technical_status"] == "error"]
    assert all(r["stages"][1]["request_utf8_bytes"] > 32768 for r in runnable)
    assert all(r["stages"][1]["status"] == "not_sent" for r in runnable)
    assert all(set(r["routes"]) == {"task", "domain"} for r in runnable)
    assert all(r["route_scope"] in {"partial", "selected_task_and_domain"} for r in runnable)


def test_preview_adds_new_exclusions_without_reading_original_exclusions(bundle, monkeypatch):
    original = batch.Inputs.state
    seen = []

    def overlong(self, target):
        assert target["eligible"]
        seen.append(target["target_id"])
        state = original(self, target)
        return state | {"request": "汉" * 11000}

    monkeypatch.setattr(batch.Inputs, "state", overlong)
    result = invoke(bundle, "preview")
    assert len(seen) == 3
    assert result["original_excluded"] == 2997 and result["new_excluded"] == 3 and result["runnable"] == 0
    assert result["original_targets"] == result["original_excluded"] + result["new_excluded"] + result["runnable"]


def test_two_processes_cannot_enter_one_batch_lock(tmp_path):
    lock = tmp_path / "batch.lock"
    code = """import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from whynote.two_stage_batch import batch_lock, Closed
try:
    with batch_lock(Path(sys.argv[2])): pass
except Closed as exc:
    print(str(exc)); sys.exit(2)
"""
    with batch.batch_lock(lock):
        result = subprocess.run(
            [sys.executable, "-I", "-c", code, str(ROOT / "src"), str(lock)], capture_output=True, text=True
        )
        assert result.returncode == 2 and "batch_already_running" in result.stdout
    with batch.batch_lock(lock):
        pass  # The same lock is reusable after release, without deleting it.


def test_stage_callback_failure_before_send_never_calls_network(tmp_path):
    key = tmp_path / "fake.json"
    key.write_text('{"typesafe":"synthetic-key-only"}', encoding="utf-8")
    records = []

    def callback(event, record):
        records.append(copy.deepcopy(record))
        if event == "started":
            raise RuntimeError("synthetic persistence failure")

    with pytest.raises(RuntimeError):
        asyncio.run(
            core.classify(
                lambda: {"request": "synthetic", "answer": "synthetic"},
                keys_file=key,
                policy=batch.OFFLINE_POLICY,
                enabled=True,
                outbound_approval_ref="synthetic",
                budget_reservations={"route": "r", "reasons": "s"},
                admission_check=lambda: True,
                stage_callback=callback,
                transport=httpx.MockTransport(lambda _: pytest.fail("sent without durable callback")),
            )
        )
    assert [r["status"] for r in records] == ["not_sent", "started"]


@pytest.fixture
def launcher(bundle, tmp_path):
    executable = shutil.which("powershell.exe") or shutil.which("pwsh")
    if executable is None:
        pytest.skip("PowerShell is unavailable on this host; Python boundary tests still run")
    root = tmp_path / "中文 checkout with spaces"
    for name in (*batch.CODE_FILES, "pyproject.toml"):
        destination = root / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, destination)
    module = root / "src/whynote/two_stage_batch.py"
    text = module.read_text(encoding="utf-8")
    # Only this synthetic checkout changes the fixed input pin; production scope never changes.
    import re

    text = re.sub(r'PLAN_SHA256 = "[a-f0-9]{64}"', f'PLAN_SHA256 = "{bundle.plan_sha}"', text)
    module.write_text(text, encoding="utf-8")
    unrelated = tmp_path / "unrelated working directory"
    unrelated.mkdir()

    def launch(*arguments):
        return subprocess.run(
            [
                executable,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(root / "scripts/start_two_stage.ps1"),
                "-Python",
                sys.executable,
                "-DataRoot",
                str(bundle.root),
                "-BatchId",
                "entry-test",
                *arguments,
            ],
            cwd=unrelated,
            env={**os.environ, "PYTHONPATH": str(tmp_path / "wrong old site-packages")},
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )

    return root, launch


def test_actual_powershell_entry_source_binding_unicode_paths_and_resume(launcher):
    root, launch = launcher
    preview = launch()
    assert preview.returncode == 0, preview.stdout + preview.stderr
    value = json.loads(preview.stdout)
    assert Path(value["runtime"]["module_file"]) == root / "src/whynote/two_stage_batch.py"
    assert value["runnable"] == 3
    mocked = launch("-Mode", "Mock")
    assert mocked.returncode == 0, mocked.stdout + mocked.stderr
    assert json.loads(mocked.stdout)["technical_success"] == 3
    resumed = launch("-Mode", "Mock")
    assert resumed.returncode == 0
    assert json.loads(resumed.stdout)["invocation_stage_starts"] == 0


def test_actual_launcher_lock_blocks_competing_process_before_sends(launcher, bundle):
    _, launch = launcher
    assert launch().returncode == 0
    directory = bundle.directory.with_name("m55-two-stage-entry-test")
    with batch.batch_lock(directory / ".run.lock"):
        result = launch("-Mode", "Mock")
    assert result.returncode == 2 and "batch_already_running" in result.stdout
    assert not (directory / "mock.sqlite3").exists()


@pytest.mark.parametrize("change", ["missing", "version", "catalog", "fingerprint"])
def test_actual_launcher_rejects_wrong_source_before_any_live_read(launcher, change):
    root, launch = launcher
    if change == "fingerprint":
        assert launch().returncode == 0
        with (root / "src/whynote/two_stage_batch.py").open("a", encoding="utf-8") as stream:
            stream.write("\n# synthetic changed build\n")
        result = launch("-Mode", "Mock")
    else:
        path = root / "src/whynote/two_stage.py"
        if change == "missing":
            path.unlink()
        elif change == "version":
            path.write_text(
                path.read_text(encoding="utf-8").replace('VERSION = "jev-two-stage-v1"', 'VERSION = "wrong"'),
                encoding="utf-8",
            )
        else:
            (root / "src/whynote/two_stage_catalog.json").write_text("{}", encoding="utf-8")
        result = launch("-Mode", "Live", "-KeysFile", str(root / "never-created-key.json"))
    assert result.returncode == 2, result.stdout + result.stderr
    assert "BLOCKED" in result.stdout
    assert "never-created-key" not in result.stdout + result.stderr
