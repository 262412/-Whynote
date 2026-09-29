"""Actual child processes with synthetic input; no model or external network."""

import asyncio
import json
import sys

import pytest
from test_live_suggestions import enabled as enabled
from test_suggestions import trial as trial
from test_template_suggestions import client, invoke
from test_template_suggestions import host as host

from whynote import live_suggestions as live
from whynote.domain import project


@pytest.mark.parametrize("error", [[], {}, None, 17, True, "unexpected", "timeout"])
def test_worker_error_envelope_has_fixed_failure_code(enabled, monkeypatch, error):
    spawn = asyncio.create_subprocess_exec
    children = []

    async def worker(*args, **kwargs):
        # Replace only the command; retain real pipes, process, decoder and Action.
        code = "import sys;sys.stdin.read();print(" + repr(json.dumps({"error": error})) + ")"
        child = await spawn(sys.executable, "-c", code, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", worker)
    f = enabled
    actual = invoke(f, client(f, []))
    records = f.store.get_events(f.principal, actual["event_id"])
    assert len(children) == 1 and children[0].returncode == 0
    assert actual["suggestion"]["result"] == "fallback" and "input" in f.client_events
    assert project(records)["action_status"] == "active"
    assert not any(e["event_type"] == "m52_suggestion_generated" for e in records)
    assert actual["suggestion"].get("failure_code") == ("timeout" if error == "timeout" else "invalid_response")


def test_worker_does_not_import_current_directory_package(enabled, tmp_path, monkeypatch):
    package = tmp_path / "whynote"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "live_suggestions.py").write_text(
        "import json,sys\nfrom pathlib import Path\n"
        "data=json.loads(sys.stdin.read())\n"
        "if data.get('request') and data.get('answer'):\n"
        " Path('shadow-selected').write_text('synthetic-payload-received')\n"
        "print(json.dumps({'error':'invalid_state'}))\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    config = {**enabled.config, "suggestion_python": sys.executable}
    try:
        asyncio.run(live.evaluate(config, "synthetic request", "synthetic answer"))
    except live.ReplayError:
        pass
    assert not (tmp_path / "shadow-selected").exists(), "Q-37: current-directory package received worker input"
