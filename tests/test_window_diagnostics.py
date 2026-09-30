"""Research probes fail before inference when SDK encoding changes the question or state."""

import importlib.util
import io
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from whynote import window_diagnostics as wd
from whynote.replay_laya import ReplayError


@pytest.fixture
def engine(monkeypatch):
    class Tokenizer:
        all_special_tokens = ["[MASK]"]
        cls_token_id, sep_token_id, mask_token_id = 1, 2, 3

        def __call__(self, text, **kwargs):
            return {"input_ids": [sum(map(ord, word)) for word in text.split()]}

    class Agent:
        cpu_fallback_count = 0
        corrupt_sdk = False
        corrupt_forward = False
        called = False

        @staticmethod
        def _to_internal(q):
            return {"t": q["type"], "ins": q["instructions"], "crit": q["criteria"]}

        def _encode_state(self, state, keys, internal, **kwargs):
            rows = [
                wd.full_sequence(tok, state, {"type": q["t"], "instructions": q["ins"], "criteria": q["crit"]})
                for q in internal.values()
            ]
            if self.corrupt_sdk:
                rows[0] = rows[0][:-1]
            return [{"ids": row} for row in rows]

        def _forward(self, batch):
            self.called = True

        def predict(self, state, qs, **kwargs):
            assert kwargs == {"max_len": 2048, "head_max_len": 256}
            rows = [wd.full_sequence(tok, state, q) for q in qs.values()]
            if self.corrupt_forward:
                rows[0][2] += 1
            self._forward(
                {
                    "input_ids": SimpleNamespace(tolist=lambda: rows),
                    "attention_mask": SimpleNamespace(tolist=lambda: [[1] * len(row) for row in rows]),
                }
            )
            return {
                "model": "laya-rl-agent",
                "answers": {
                    k: {
                        "type": "choice",
                        "choice": next(iter(q["criteria"])),
                        "probabilities": {key: float(i == 0) for i, key in enumerate(q["criteria"])},
                    }
                    for k, q in qs.items()
                },
            }

    mod, common = ModuleType("laya.agent"), ModuleType("laya.common")
    mod.Agent = Agent
    common.render_options = lambda q: [f"{k}: {v}" for k, v in q["crit"].items()]
    monkeypatch.setitem(sys.modules, "laya", ModuleType("laya"))
    monkeypatch.setitem(sys.modules, "laya.agent", mod)
    monkeypatch.setitem(sys.modules, "laya.common", common)
    tok = Tokenizer()
    return SimpleNamespace(
        tokenizer=tok, agent=Agent(), device="cpu", torch=SimpleNamespace(manual_seed=lambda _: None)
    )


@pytest.mark.parametrize("window", wd.WINDOWS)
def test_three_independent_limits(window):
    assert wd.fits(window - 260, window, wd.BYTE_LIMIT, window)
    assert not wd.fits(window - 259, window, wd.BYTE_LIMIT, window)
    assert not wd.fits(1, window + 1, 1, window)
    assert not wd.fits(1, 2, wd.BYTE_LIMIT + 1, window)


@pytest.mark.parametrize("variant", ["original", "explicit"])
def test_full_forward_and_no_cross_question_winner(engine, variant):
    result = wd.predict_checked(engine, "synthetic complete answer", variant, 2048)
    assert engine.agent.called and result["full_encoding_verified"]
    assert result["primary_reason"] is None
    assert result["selection_status"] == "cross_question_ranking_not_validated"
    assert set(result["answers"]) == set(wd.questions(variant))


@pytest.mark.parametrize("where,error", [("corrupt_sdk", "sdk_truncation"), ("corrupt_forward", "forward_truncation")])
def test_changed_token_rejected_before_model(engine, where, error):
    setattr(engine.agent, where, True)
    with pytest.raises(ReplayError, match=error):
        wd.predict_checked(engine, "synthetic", "explicit", 2048)
    assert not engine.agent.called


@pytest.mark.parametrize(
    "change,error", [("head", "head_truncated"), ("option", "option_truncated"), ("state", "reserved_token")]
)
def test_question_and_reserved_token_rejection(engine, change, error):
    q = {"type": "choice", "instructions": "Choose", "criteria": {"A": "yes", "B": "no"}}
    state = "synthetic"
    if change == "head":
        q["instructions"] = "word " * 256
    elif change == "option":
        q["criteria"]["A"] = "word " * 49
    else:
        state += " [MASK]"
    with pytest.raises(ReplayError, match=error):
        wd.full_sequence(engine.tokenizer, state, q)


def test_feedback_and_labels_cannot_enter_worker(engine, monkeypatch, capsys):
    monkeypatch.setattr(wd, "Engine", lambda *args: engine)
    monkeypatch.setattr(sys, "argv", ["worker", "--model-dir", "synthetic"])
    raw = (
        json.dumps(
            {"state": "synthetic", "variant": "explicit", "window": 2048, "feedback": "future", "expected": "yes"}
        ).encode()
        + b"\n"
    )
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(raw)))
    wd.main()
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result == {"error": "diagnostic_failed"} and not engine.agent.called


@pytest.mark.parametrize("boundary", ["expired", "changed"])
def test_supervisor_rejects_before_tokenizer_or_body_read(tmp_path, monkeypatch, boundary):
    path = Path(__file__).resolve().parents[1] / "scripts/m55_window_validation.py"
    spec = importlib.util.spec_from_file_location("window_supervisor_test", path)
    supervisor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(supervisor)
    for name in ("inputs.sqlite3", "manifest.json", "source"):
        (tmp_path / name).write_text("synthetic")
    plan = {
        "research_root": str(tmp_path),
        "sources": [
            {
                "path": str(tmp_path / "source"),
                "expires_at": "2000-01-01T00:00:00+00:00" if boundary == "expired" else "2099-01-01T00:00:00+00:00",
            }
        ],
    }
    monkeypatch.setattr(supervisor, "read_plan", lambda _: plan)
    calls = []

    def fingerprint():
        if calls and boundary == "changed":
            (tmp_path / "inputs.sqlite3").write_text("changed synthetic body")
        calls.append(True)
        return "same-code"

    monkeypatch.setattr(supervisor, "source_hash", fingerprint)
    monkeypatch.setattr(supervisor, "load_tokenizer", lambda _: pytest.fail("tokenizer reached before admission"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "supervisor",
            "theory",
            "--batch",
            str(tmp_path),
            "--output",
            str(tmp_path / "output"),
            "--model-dir",
            str(tmp_path),
            "--python",
            sys.executable,
            "--base-python",
            str(tmp_path),
        ],
    )
    with pytest.raises(ReplayError, match="source_expired" if boundary == "expired" else "input_fingerprint_changed"):
        supervisor.main()
