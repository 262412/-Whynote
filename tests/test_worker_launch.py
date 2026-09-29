"""Worker launch isolation and real-process error/reaping boundaries."""

import asyncio
import json
import sqlite3
import sys

import pytest
from test_live_suggestions import enabled as enabled
from test_suggestions import trial as trial
from test_template_suggestions import client, invoke
from test_template_suggestions import host as host

from whynote import live_suggestions as live
from whynote.domain import project


@pytest.mark.parametrize("error", sorted(live.ERRORS - {"timeout"}) + [["nested"], {"nested": []}])
def test_fixed_errors_preserve_outbox_and_action(enabled, monkeypatch, error):
    spawn = asyncio.create_subprocess_exec

    async def worker(*args, **kwargs):
        code = "import sys;sys.stdin.read();print(" + repr(json.dumps({"error": error})) + ")"
        return await spawn(sys.executable, "-I", "-c", code, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", worker)
    f = enabled
    actual = invoke(f, client(f, []))
    records = f.store.get_events(f.principal, actual["event_id"])
    assert actual["suggestion"]["failure_code"] == (error if isinstance(error, str) else "invalid_response")
    assert project(records)["action_status"] == "active" and "input" in f.client_events
    assert not any(e["event_type"] == "m52_suggestion_generated" for e in records)
    with sqlite3.connect(f.store.path) as db:
        assert db.execute("SELECT count(*) FROM outbox WHERE event_id=?", (actual["event_id"],)).fetchone()[0] == 1


@pytest.mark.parametrize("origin", ["ordinary", "cwd", "environment"])
def test_real_worker_ignores_shadow_packages_and_startup_hooks(enabled, tmp_path, monkeypatch, origin):
    marker = tmp_path / "untrusted-import"
    if origin != "ordinary":
        package = tmp_path / "whynote"
        package.mkdir()
        trap = "from pathlib import Path; Path(" + repr(str(marker)) + ").write_text('synthetic-marker')"
        (package / "__init__.py").write_text(trap, encoding="utf-8")
        (package / "live_suggestions.py").write_text(trap, encoding="utf-8")
        (tmp_path / "sitecustomize.py").write_text(trap, encoding="utf-8")
        (tmp_path / "json.py").write_text(trap, encoding="utf-8")
    if origin == "environment":
        monkeypatch.setenv("PYTHONPATH", str(tmp_path))
        monkeypatch.setenv("PYTHONHOME", str(tmp_path / "not-a-runtime"))
        monkeypatch.setenv("PYTHONUSERBASE", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config = {**enabled.config, "suggestion_python": sys.executable, "suggestion_model_dir": str(tmp_path / "missing")}
    # A precise error from the real worker proves it started; a launch failure is insufficient.
    with pytest.raises(live.ReplayError, match="^model_load_failed$"):
        asyncio.run(live.evaluate(config, "synthetic request", "synthetic answer"))
    assert not marker.exists()


@pytest.mark.parametrize("cancel", [False, True])
def test_real_child_is_reaped_after_timeout_or_cancel(enabled, monkeypatch, cancel):
    spawn, wait_for = asyncio.create_subprocess_exec, asyncio.wait_for
    children = []

    async def exercise():
        started = asyncio.Event()

        async def worker(*args, **kwargs):
            process = await spawn(
                sys.executable, "-I", "-c", "import sys,time;sys.stdin.read();time.sleep(30)", **kwargs
            )
            children.append(process)
            started.set()
            return process

        async def bounded(awaitable, timeout):
            return await wait_for(awaitable, 0.1)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", worker)
        if not cancel:
            monkeypatch.setattr(asyncio, "wait_for", bounded)
        task = asyncio.create_task(live.evaluate(enabled.config, "synthetic request", "synthetic answer"))
        await started.wait()
        if cancel:
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else live.ReplayError):
            await task
        assert len(children) == 1 and children[0].returncode is not None

    asyncio.run(exercise())
