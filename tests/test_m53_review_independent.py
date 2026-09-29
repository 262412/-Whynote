"""Independent checks of review findings; no real model or host mutations."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_live_suggestions import enabled as enabled
from test_live_suggestions import result, stub
from test_suggestions import trial as trial
from test_template_suggestions import client, invoke
from test_template_suggestions import host as host

from whynote.domain import project


@pytest.mark.parametrize(
    "reply",
    [result(outcome="unknown"), result(route="general", reason_ids=["code.behavior_changed"])],
    ids=["abstention-with-reason", "reason-outside-route"],
)
def test_inconsistent_worker_selection_has_invalid_response_code(enabled, monkeypatch, reply):
    f = enabled
    stub(monkeypatch, f, reply=reply)
    actual = invoke(f, client(f, []))
    records = f.store.get_events(f.principal, actual["event_id"])
    assert actual["suggestion"]["result"] == "fallback"
    assert "input" in f.client_events
    assert project(records)["action_status"] == "active"
    assert not any(e["event_type"] == "m52_suggestion_generated" for e in records)
    assert actual["suggestion"].get("failure_code") == "invalid_response"


@pytest.mark.parametrize("invalid", ["interpreter", "model-directory"])
def test_unusable_runtime_rejected_before_host_provisioning(tmp_path, monkeypatch, invalid):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "qa"))
    import s1_browser_host

    from qa import m53_live_host

    ordinary_file = tmp_path / "not-python.txt"
    ordinary_file.write_text("synthetic, not executable", encoding="utf-8")
    model_dir = tmp_path / "empty-model"
    model_dir.mkdir()
    args = SimpleNamespace(
        data_dir=tmp_path / "fresh-host",
        base_url="http://127.0.0.1:1",
        model_python=ordinary_file if invalid == "interpreter" else Path(sys.executable),
        model_dir=model_dir,
    )

    def forbidden_write(_):
        pytest.fail("Unusable runtime reached host provisioning before validation")

    monkeypatch.setattr(s1_browser_host, "provision", forbidden_write)
    with pytest.raises((ValueError, SystemExit)):
        m53_live_host.provision(args)
