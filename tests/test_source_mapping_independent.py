"""Independent synthetic CLI and reference-integrity acceptance checks."""

import copy
import gzip
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from whynote import source_review

FIXTURES = Path(__file__).parents[1] / "fixtures/m5-synthetic"


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / "synthetic"
    shutil.copytree(FIXTURES, root)
    return root, json.loads((root / "batch.json").read_text(encoding="utf-8"))


def save_manifest(root, manifest):
    (root / "batch.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")


def replace_file(root, entry, kind, data):
    (root / entry[f"{kind}_path"]).write_bytes(data)
    entry[f"{kind}_sha256"] = hashlib.sha256(data).hexdigest()


def run_cli(root, output):
    return subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            "-m",
            "whynote.source_review",
            "--manifest",
            str(root / "batch.json"),
            "--output",
            str(output),
        ],
        capture_output=True,
        encoding="utf-8",
        check=False,
    )


def resolve(row, pointer):
    for part in pointer.removeprefix("/").split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        row = row[int(part)] if isinstance(row, list) else row[part]
    return row


@pytest.mark.parametrize("name", ["wildfb", "helpsteer3", "wildfeedback"])
def test_cli_references_resolve_to_hashed_original_values(bundle, tmp_path, name):
    root, manifest = bundle
    output = tmp_path / "output"
    assert run_cli(root, output).returncode == 0
    entry = next(s for s in manifest["sources"] if s["source"] == name)
    source_bytes = (root / entry["data_path"]).read_bytes()
    rows = json.loads(source_bytes)
    records = json.loads((output / f"{name}-records.json").read_text(encoding="utf-8"))
    for record in records:
        assert record["data_sha256"] == hashlib.sha256(source_bytes).hexdigest()
        row = rows[record["row_id"]]
        refs = record["context_refs"] + [record["target_ref"]] + record["feedback_refs"] + record["label_refs"]
        for ref in refs:
            value = resolve(row, ref["pointer"])
            digest = hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            assert digest == ref["sha256"]
        prediction = record["prediction_refs"]
        assert prediction == {"context": record["context_refs"], "target": record["target_ref"]}
        predicted = {r["pointer"] for r in prediction["context"] + [prediction["target"]]}
        assert predicted.isdisjoint(r["pointer"] for r in record["feedback_refs"] + record["label_refs"])
        text = "".join(resolve(row, r["pointer"])["content"] for r in record["context_refs"])
        text += resolve(row, record["target_ref"]["pointer"])
        assert record["input_utf8_bytes"] == len(text.encode("utf-8"))


@pytest.mark.parametrize("change", [{"mode": "public"}, {"status": "HOLD"}, {"status": "excluded"}])
def test_admission_denial_reaches_no_file_open(bundle, monkeypatch, change):
    root, manifest = bundle
    entry = {**manifest["sources"][0], **change}
    monkeypatch.setattr(Path, "open", lambda *_args, **_kwargs: pytest.fail("denied source opened a file"))
    result = source_review.run_source(root, entry)
    assert result["status"] == "HOLD" and result["rows_read"] is None
    assert result["records"] == result["rejected"] == []


def test_review_change_keeps_identity_without_mutating_prior_output(bundle, tmp_path):
    root, manifest = bundle
    old = tmp_path / "old"
    assert run_cli(root, old).returncode == 0
    preserved = {p.name: p.read_bytes() for p in old.iterdir()}
    entry = next(s for s in manifest["sources"] if s["source"] == "helpsteer3")
    replace_file(root, entry, "reviews", b"{}")
    save_manifest(root, manifest)
    new = tmp_path / "new"
    assert run_cli(root, new).returncode == 0
    before = json.loads(preserved["helpsteer3-records.json"])
    after = json.loads((new / "helpsteer3-records.json").read_text(encoding="utf-8"))
    assert [r["record_id"] for r in before] == [r["record_id"] for r in after]
    assert all(r["review"]["diagnoses"] is None and not r["ready_for_replay"] for r in after)
    assert {p.name: p.read_bytes() for p in old.iterdir()} == preserved
    assert run_cli(root, old).returncode == 1
    assert {p.name: p.read_bytes() for p in old.iterdir()} == preserved


def test_cli_hash_error_isolated_and_partial_exit_is_nonzero(bundle, tmp_path):
    root, manifest = bundle
    entry = manifest["sources"][0]
    (root / entry["data_path"]).write_bytes(b"synthetic corruption, not a credential")
    output = tmp_path / "partial"
    completed = run_cli(root, output)
    assert completed.returncode == 2
    result = json.loads(completed.stdout)
    assert result["sources"][0]["reason"] == "file_hash_mismatch"
    assert [s["mapped_targets"] for s in result["sources"][1:]] == [2, 1]
    assert "synthetic corruption" not in completed.stdout + completed.stderr
    assert len(list(output.glob("*.json"))) == 7


@pytest.mark.parametrize("count", [1000, 1001])
def test_exact_row_limit(bundle, count):
    root, manifest = bundle
    entry = next(s for s in manifest["sources"] if s["source"] == "helpsteer3")
    row = json.loads((root / entry["data_path"]).read_text(encoding="utf-8"))[0]
    replace_file(root, entry, "data", json.dumps([row] * count).encode())
    replace_file(root, entry, "reviews", b"{}")
    result = source_review.run_source(root, entry)
    if count == 1000:
        assert result["status"] == "mapped" and len(result["records"]) == 2000
    else:
        assert result["status"] == "HOLD" and result["reason"] == "too_many_rows"
        assert result["records"] == []


def test_gzip_physical_rows_and_input_immutability(bundle):
    root, manifest = bundle
    entry = copy.deepcopy(next(s for s in manifest["sources"] if s["source"] == "helpsteer3"))
    row = json.loads((root / entry["data_path"]).read_text(encoding="utf-8"))[0]
    raw = gzip.compress(b"\n" + json.dumps(row).encode() + b"\r\n{bad\n")
    replace_file(root, entry, "data", raw)
    replace_file(root, entry, "reviews", b"{}")
    entry["format"] = "jsonl.gz"
    before = copy.deepcopy(entry)
    result = source_review.run_source(root, entry)
    assert result["status"] == "partial" and result["rows_read"] == 3
    assert [r["row_id"] for r in result["records"]] == [1, 1]
    assert [r["row_id"] for r in result["rejected"]] == [0, 2]
    assert entry == before and (root / entry["data_path"]).read_bytes() == raw
