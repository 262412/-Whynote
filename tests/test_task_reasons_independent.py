"""Independent M5-0b contract checks; synthetic inputs, no model quality claims."""

import itertools
import json
import socket
import subprocess
import sys

import pytest

from whynote import task_reasons as reasons

EVIDENCE = ("request", "answer", "original_code", "reference")
CODE = {"code.interface_changed", "code.behavior_changed", "code.incomplete_rewrite", "code.scope_exceeded"}
COMMON = {
    "general.instruction_not_followed",
    "general.incomplete",
    "general.irrelevant",
    "general.style",
    "general.unnecessary_refusal",
}
REFERENCE = {"general.factual_error", "general.outdated"}


def payload(current, outcome="suggested", ids=()):
    return {"candidate_set_id": current.candidate_set_id, "outcome": outcome, "reason_ids": list(ids)}


@pytest.mark.parametrize("mask", range(16))
def test_all_material_combinations_across_routes(mask):
    available = {kind for index, kind in enumerate(EVIDENCE) if mask & (1 << index)}
    for task in ("general", "code_rewrite", "mixed", "unknown", "other"):
        current = reasons.prepare_candidates([task], list(available))
        routed = COMMON | REFERENCE | (CODE if task != "general" else set())
        expected = set()
        if {"request", "answer"} <= available:
            expected |= COMMON
            if "reference" in available:
                expected |= REFERENCE
            if task != "general" and "original_code" in available:
                expected |= CODE
        assert set(current.routed_reason_ids) == routed
        assert set(current.reason_ids) == expected
        missing = dict(current.unavailable)
        assert set(missing) == routed - expected
        for reason_id in routed:
            required = {"request", "answer"}
            if reason_id in CODE:
                required.add("original_code")
            if reason_id in REFERENCE:
                required.add("reference")
            if reason_id in expected:
                assert reasons.validate_selection(payload(current, ids=[reason_id]), current)["reason_ids"] == [
                    reason_id
                ]
            else:
                assert set(missing[reason_id]) == required - available
                with pytest.raises(reasons.ReasonContractError, match="^reason_not_selectable$"):
                    reasons.validate_selection(payload(current, ids=[reason_id]), current)


def test_every_three_reason_combination_preserves_order_and_provenance():
    current = reasons.prepare_candidates(["code_rewrite"], EVIDENCE)
    combinations = list(itertools.combinations(current.reason_ids, 3))
    assert len(combinations) == 165
    for ids in combinations:
        result = reasons.validate_selection(payload(current, ids=reversed(ids)), current)
        assert result["reason_ids"] == list(reversed(ids))
        assert (result["selection_origin"], result["confirmation_status"]) == ("caller_supplied", "unconfirmed")


def test_old_selection_cannot_cross_route_or_material_identity_even_for_common_reason():
    original = reasons.prepare_candidates(["general"], ["request", "answer"])
    selected = payload(original, ids=["general.style"])
    changed = [
        reasons.prepare_candidates(["general"], EVIDENCE),
        reasons.prepare_candidates(["general"], ["request", "answer"], ["code_rewrite"]),
        reasons.prepare_candidates(["mixed"], ["request", "answer"]),
    ]
    for current in changed:
        assert "general.style" in current.reason_ids
        with pytest.raises(reasons.ReasonContractError, match="^candidate_set_mismatch$"):
            reasons.validate_selection(selected, current)
    assert reasons.validate_selection(selected, original)["reason_ids"] == ["general.style"]


@pytest.mark.parametrize("size,code", [(8191, None), (8192, None), (8193, "selection_too_large")])
def test_installed_cli_exact_file_size_boundary(tmp_path, size, code):
    current = reasons.prepare_candidates(["general"], [])
    raw = json.dumps(payload(current, "unknown")).encode()
    path = tmp_path / "choice.json"
    path.write_bytes(raw + b" " * (size - len(raw)))
    before = path.read_bytes()
    result = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            "-m",
            "whynote.task_reasons",
            "--task",
            "general",
            "--selection-file",
            str(path),
        ],
        cwd=tmp_path,
        capture_output=True,
        encoding="utf-8",
        check=False,
    )
    response = json.loads(result.stdout)
    assert result.stderr == ""
    assert result.returncode == (2 if code else 0)
    if code:
        assert response == {"status": "rejected", "code": code}
    else:
        assert response["outcome"] == "unknown"
        assert response["confirmation_status"] == "unconfirmed"
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


def test_cli_deep_json_and_invalid_encoding_are_bounded_rejections(tmp_path, capsys):
    path = tmp_path / "choice.json"
    for raw in (b"[" * 2000 + b"]" * 2000, b"\xffSYNTHETIC_PRIVATE_TEXT"):
        path.write_bytes(raw)
        assert reasons.main(["--task", "general", "--selection-file", str(path)]) == 2
        assert json.loads(capsys.readouterr().out) == {"status": "rejected", "code": "invalid_selection_file"}
        assert path.read_bytes() == raw


def test_offline_execution_does_not_open_network_or_import_host_or_model(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Offline reasons attempted network access")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    before = set(sys.modules)
    current = reasons.prepare_candidates(["general"], EVIDENCE, ["code_rewrite"])
    reasons.describe_candidates(current)
    reasons.validate_selection(payload(current, ids=["code.behavior_changed"]), current)
    added = set(sys.modules) - before
    assert not any(name.startswith(("open_webui", "whynote.laya", "whynote.store")) for name in added)
