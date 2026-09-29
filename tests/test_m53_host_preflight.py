"""Q-28 preflight and Q-29 source/artifact binding regressions."""

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from qa import m53_live_host as host


@pytest.mark.parametrize("missing", ["--model-python", "--model-dir"])
def test_cli_missing_option_leaves_data_untouched(tmp_path, missing):
    data = tmp_path / "untouched"
    command = [sys.executable, str(Path(host.__file__)), "provision", "--data-dir", str(data)]
    for key, value in (("--model-python", sys.executable), ("--model-dir", str(tmp_path))):
        if key != missing:
            command.extend([key, value])
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 2 and missing in result.stderr
    assert not data.exists()


@pytest.mark.parametrize("option", ["model_python", "model_dir"])
def test_invalid_model_path_rejected_before_host_call(tmp_path, monkeypatch, option):
    import s1_browser_host

    args = SimpleNamespace(model_python=Path(sys.executable), model_dir=tmp_path)
    setattr(args, option, tmp_path / "missing")
    monkeypatch.setattr(s1_browser_host, "provision", lambda _: pytest.fail("Unexpected host mutation"))
    with pytest.raises(ValueError, match="Invalid --model"):
        host.provision(args)


@pytest.fixture
def source(tmp_path, monkeypatch):
    # A minimal real Git index exercises source hashing. Actual patch validation
    # and a complete Node build are verified separately against the pinned host.
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / ".gitignore").write_text("build/\n")
    (tmp_path / "source.js").write_text("export const value = 1;")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    monkeypatch.setattr(host, "validate_source", lambda _: None)
    monkeypatch.setattr(host.shutil, "which", lambda _: "npm")

    def compile_frontend(command, **kwargs):
        assert command == ["npm", "run", "build"] and kwargs["check"]
        out = tmp_path / "build"
        out.mkdir(exist_ok=True)
        (out / "index.html").write_text('<script src="bundle.js"></script>')
        (out / "bundle.js").write_text((tmp_path / "source.js").read_text())

    monkeypatch.setattr(
        host,
        "subprocess",
        SimpleNamespace(
            check_output=lambda cmd, **kw: (
                "v22.0.0\n" if cmd == ["node", "--version"] else subprocess.check_output(cmd, **kw)
            ),
            run=compile_frontend,
        ),
    )
    return tmp_path


def test_successful_build_can_enter_host_startup(source, monkeypatch):
    import s1_browser_host

    host.build(source)
    calls = []
    monkeypatch.setattr(s1_browser_host, "serve", lambda args: calls.append(args))
    data = source / "data"
    monkeypatch.setattr(sys, "argv", ["m53", "serve", "--source", str(source), "--data-dir", str(data)])
    host.main()
    assert len(calls) == 1 and calls[0].source == source


@pytest.mark.parametrize(
    "change", ["index", "bundle", "missing_bundle", "extra", "missing_receipt", "source", "version"]
)
def test_stale_or_tampered_build_rejected_before_host_writes(source, monkeypatch, change):
    import s1_browser_host

    host.build(source)
    out = source / "build"
    if change == "index":
        (out / "index.html").write_text("Unverified stale QA artifact")
    elif change == "bundle":
        (out / "bundle.js").write_text("old javascript")
    elif change == "missing_bundle":
        (out / "bundle.js").unlink()
    elif change == "extra":
        (out / "extra.js").write_text("unrecorded")
    elif change == "missing_receipt":
        (out / host.BUILD_RECEIPT).unlink()
    elif change == "source":
        (source / "source.js").write_text("new source without a rebuild")
    else:
        p = out / host.BUILD_RECEIPT
        record = json.loads(p.read_text())
        record["version"] = 999
        p.write_text(json.dumps(record))
    monkeypatch.setattr(s1_browser_host, "serve", lambda _: pytest.fail("Unverified build reached host"))
    data = source / "data"
    monkeypatch.setattr(sys, "argv", ["m53", "serve", "--source", str(source), "--data-dir", str(data)])
    with pytest.raises(ValueError):
        host.main()
    assert not data.exists()


def test_failed_build_invalidates_previous_receipt(source, monkeypatch):
    host.build(source)

    def failure(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "npm")

    monkeypatch.setattr(host.subprocess, "run", failure)
    with pytest.raises(subprocess.CalledProcessError):
        host.build(source)
    assert not (source / "build" / host.BUILD_RECEIPT).exists()
    with pytest.raises(ValueError):
        host.validate_build(source)


def test_source_changed_during_build_cannot_get_receipt(source, monkeypatch):
    compile_frontend = host.subprocess.run

    def changing(*args, **kwargs):
        compile_frontend(*args, **kwargs)
        (source / "source.js").write_text("changed during compilation")

    monkeypatch.setattr(host.subprocess, "run", changing)
    with pytest.raises(ValueError, match="Source changed"):
        host.build(source)
    assert not (source / "build" / host.BUILD_RECEIPT).exists()
