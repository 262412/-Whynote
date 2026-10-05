import json
import shutil
import socket
import sqlite3

import pytest
from test_two_stage_batch import ROOT, args, invoke, sample
from test_two_stage_batch import bundle as bundle

from whynote import two_stage as core
from whynote import two_stage_batch as batch
from whynote import two_stage_materials as materials
from whynote import two_stage_offline as offline


@pytest.fixture
def saved(bundle):
    """A wholly synthetic saved-live-format fixture; never a real API result."""
    pins = {name: offline.fingerprint(ROOT / name)["sha256"] for name in batch.CODE_FILES}
    bundle.runtime.update(
        project_root=str(ROOT),
        contract_version=core.VERSION,
        model=batch.MODEL,
        code_pins=pins,
        code_sha256=batch.sha(batch.encode(pins)),
        input_version=batch.INPUT_VERSION,
        material_rule_version=materials.VERSION,
    )
    invoke(bundle)
    source = bundle.directory
    plan = batch.decode((source / "plan.json").read_bytes())
    records = [batch.decode(line) for line in (source / "mock.results.jsonl").read_bytes().splitlines()]
    for record in records:
        record.update(mode="live", synthetic=False)
    offline.write_lines(source / "live.results.jsonl", records)
    summary = batch.decode((source / "mock.summary.json").read_bytes()) | {"mode": "live", "synthetic": False}
    batch.save(source / "live.summary.json", summary, new=True)
    shutil.copyfile(source / "mock.sqlite3", source / "live.sqlite3")
    with sqlite3.connect(source / "live.sqlite3") as db:
        db.execute(
            "UPDATE meta SET value=? WHERE key='identity'",
            (batch.encode({"plan_sha256": batch.sha(batch.encode(plan)), "mode": "live"}).decode(),),
        )
    return bundle, plan, records, summary


@pytest.mark.parametrize("mode", ["derive", "materials"])
def test_offline_modes_preserve_saved_files_and_produce_no_predictions_for_unasked(saved, mode):
    bundle, plan, records, summary = saved
    source = bundle.directory
    output = source.with_name("new-" + mode)
    before = {p.name: p.read_bytes() for p in source.iterdir()}
    value = batch.run(args(bundle, mode, source_batch=str(source), output_dir=str(output)), ROOT)
    assert value["real_api_requests"] == value["real_key_reads"] == value["network_attempts_denied"] == 0
    assert value["live_authorized"] is False
    assert value["source_batch_id"] == plan["batch_id"]
    assert {p.name: p.read_bytes() for p in source.iterdir()} == before
    assert not (bundle.root / batch.RESEARCH / "two-stage-budget-pools.sqlite3").exists()
    if mode == "derive":
        derived = [json.loads(line) for line in (output / "derived.results.jsonl").read_bytes().splitlines()]
        assert [row["source_record"] for row in derived] == records
        assert all(
            row["derived"]["assessment"] is None
            for row in derived
            if row["source_record"]["technical_status"] != "success"
        )
        assert value["candidate_reasons"] == sum(
            r["decision"] == "yes" for record in records if record["result"] for r in record["result"]["reasons"]
        )
    else:
        assert value["eligible_targets"] == 3 and value["original_excluded"] == 2997
    for path in output.iterdir():
        assert all(
            marker not in path.read_bytes()
            for marker in (
                b"PRIVATE_REQUEST",
                b"PRIVATE_ANSWER",
                b"PRIVATE_FEEDBACK",
                b"PRIVATE_GOLD",
                b"synthetic-key-only",
            )
        )
    with pytest.raises(batch.Closed, match="new_independent_output"):
        batch.run(args(bundle, mode, source_batch=str(source), output_dir=str(output)), ROOT)


@pytest.mark.parametrize("change", ["batch", "input", "version", "policy", "decision", "stage"])
def test_derivation_rejects_changed_source_identity_or_saved_predictions(saved, change):
    bundle, plan, records, summary = saved
    record = next(row for row in records if row["technical_status"] == "success")
    if change == "batch":
        record["batch_id"] = "different"
    elif change == "input":
        record["input_sha256"] = "0" * 64
    elif change == "version":
        record["contract_version"] = "unsupported"
    elif change == "policy":
        record["policy"]["reason_probability"] = 0.1
    elif change == "stage":
        record["stages"][0]["request_sha256"] = "0" * 64
    else:
        record["result"]["reasons"][0]["decision"] = "not_asked"
    with pytest.raises(batch.Closed):
        offline.validate_history(plan, records, bundle.plan, summary, (bundle.directory / "live.sqlite3").read_bytes())


def test_offline_guard_denies_network_credentials_and_writes_to_source(tmp_path):
    key = tmp_path / "fake-never-read-key.json"
    key.write_text("synthetic only", encoding="utf-8")
    source = tmp_path / "original.json"
    source.write_text("preserve", encoding="utf-8")
    output = tmp_path / "new"
    output.mkdir()
    with batch.OfflineGuard([source], ROOT, write_root=output) as guard:
        with pytest.raises(batch.Closed, match="network_forbidden"):
            socket.getaddrinfo("example.invalid", 443)
        with pytest.raises(batch.Closed, match="file_read_forbidden"):
            key.read_bytes()
        with pytest.raises(batch.Closed, match="write_outside_output"):
            source.write_text("changed", encoding="utf-8")
        assert source.read_text() == "preserve"
        (output / "safe.json").write_text("{}", encoding="utf-8")
    assert guard.network_denied == 1


def test_offline_rejects_key_argument_before_reading_sources(bundle, monkeypatch):
    monkeypatch.setattr(batch, "read", lambda *_: pytest.fail("must reject before any source read"))
    with pytest.raises(batch.Closed, match="offline_keys_and_live_config_forbidden"):
        batch.run(
            args(bundle, "derive", source_batch="missing", output_dir="missing", keys_file="never-read-key"), ROOT
        )


def test_target_answer_future_and_annotations_never_supply_material():
    text = "Verification reference:\n```\nPRIVATE_POISON\n```\nSource text:\n```\nPRIVATE_POISON\n```\n|x|y|\n|---|---|\n|1|2|"
    target, state, original = sample(answer=text)
    original["future"] = [{"role": "user", "content": text}]
    original["reference"] = {"gold": text, "feedback": text, "tool_trace": text}
    projected, meta = batch.adapt_materials(target, state, original)
    assert set(projected) == {"request", "answer"}
    assert all(meta[key]["status"] == "not_found" for key in materials.FIELDS)
    assert projected["answer"] == text and "PRIVATE_POISON" not in json.dumps(meta)


def test_material_projection_preserves_original_state_and_has_safe_locators():
    text = "Please fix this code:\n```python\nx=1\n```"
    target, state, original = sample(context=[{"role": "user", "content": text}])
    before = batch.encode(state)
    projected, meta = batch.adapt_materials(target, state, original)
    assert projected["original_code"] == "x=1\n" and projected["request"] == text
    assert batch.encode(state) == before
    assert meta["original_code"]["locator"]["message_index"] == 0
    assert "x=1" not in json.dumps(meta)


def test_material_audit_projection_uses_the_same_order_as_wire_projection():
    text = "Refactor this code using the supplied text.\nSource text:\n```text\nsource\n```\nOriginal code:\n```python\nx=1\n```"
    target, state, original = sample(context=[{"role": "user", "content": text}])
    projected, _ = batch.adapt_materials(target, state, original)
    assert {"source_text", "original_code"} <= projected.keys()
    assert core._encode(projected) == core._encode(core._project(projected))
