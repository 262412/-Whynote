"""Q-31 optimization and Q-33 bounded read-only runtime preflight regressions."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from whynote import laya_local, laya_manifest


@pytest.fixture
def host(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "qa"))
    from qa import m53_live_host

    return m53_live_host


@pytest.fixture
def patched_source(tmp_path, host):
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", str(source)], check=True, capture_output=True)
    (source / ".gitignore").write_text("build/\n")
    patches = tmp_path / "integrations/openwebui/patches"
    patches.mkdir(parents=True)
    names = ("native-v0.11.4-s0", "manual-v0.11.4-timing", "s1-v0.11.4-trial", "m5-v0.11.4-templates")
    for name in names:
        (source / name).write_text("before\n")
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "-c",
            "user.name=QA",
            "-c",
            "user.email=qa@example.invalid",
            "commit",
            "-m",
            "Synthetic source validation fixture",
        ],
        check=True,
        capture_output=True,
    )
    pin = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    for name in names:
        (source / name).write_text("after\n")
        patch = subprocess.check_output(["git", "-C", str(source), "diff", "--", name])
        (patches / (name + ".patch")).write_bytes(patch)
    dialog = source / "src/lib/whynote/suggestion_dialog.js"
    dialog.parent.mkdir(parents=True)
    dialog.write_text("synthetic dialog")
    (patches.parent / "suggestion_dialog.js").write_bytes(dialog.read_bytes())
    out = source / "build"
    out.mkdir()
    (out / "index.html").write_text("synthetic index")
    (out / "bundle.js").write_text("synthetic bundle")
    # This receipt is fixture data, not a production build attestation.
    (out / host.BUILD_RECEIPT).write_text(
        json.dumps({"version": 1, "source_sha256": host.source_fingerprint(source), "files": host.build_files(source)})
    )
    return source, pin


@pytest.mark.parametrize("mode", ["normal", "-O", "environment"])
@pytest.mark.parametrize("change", ["valid", "source", "dialog", "head", "artifact"])
def test_source_and_build_checks_survive_optimization(patched_source, mode, change):
    source, pin = patched_source
    if change == "source":
        (source / "native-v0.11.4-s0").write_text("unexpected change")
    elif change == "dialog":
        (source / "src/lib/whynote/suggestion_dialog.js").write_text("old dialog")
    elif change == "head":
        pin = "0" * 40
    elif change == "artifact":
        (source / "build/bundle.js").unlink()
    code = (
        "import sys; from pathlib import Path; from qa import m53_live_host as h; "
        "import s1_browser_host as s; h.ROOT=Path(sys.argv[1]); s.UPSTREAM=sys.argv[2]; "
        "h.validate_build(Path(sys.argv[1])/'source')"
    )
    env = {**os.environ, "PYTHONOPTIMIZE": "1" if mode == "environment" else "0"}
    result = subprocess.run(
        [sys.executable, *(["-O"] if mode == "-O" else []), "-c", code, str(source.parent), pin],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert (result.returncode == 0) == (change == "valid"), result.stderr
    if change != "valid":
        assert "ValueError" in result.stderr


@pytest.mark.parametrize("change", ["valid", "version", "missing", "digest", "import"])
def test_preflight_checks_pinned_sdk_and_files_without_loading(tmp_path, monkeypatch, change):
    artifact = tmp_path / "model.bin"
    artifact.write_bytes(b"synthetic weights")
    monkeypatch.setattr(laya_manifest, "SHA256", {artifact.name: hashlib.sha256(artifact.read_bytes()).hexdigest()})
    monkeypatch.setattr(
        laya_local, "version", lambda name: "wrong" if change == "version" else laya_local.SDK_VERSIONS[name]
    )
    imports = []

    def import_module(name):
        imports.append(name)
        if change == "import":
            raise ImportError("Synthetic missing dependency")
        return SimpleNamespace(load=lambda *a, **kw: pytest.fail("Preflight must not load a model"))

    monkeypatch.setattr(laya_local.importlib, "import_module", import_module)
    if change == "missing":
        artifact.unlink()
    elif change == "digest":
        artifact.write_bytes(b"changed")
    if change == "valid":
        laya_local.preflight(tmp_path)
        assert imports == list(laya_local.SDK_VERSIONS)
    else:
        with pytest.raises((ValueError, OSError, ImportError)):
            laya_local.preflight(tmp_path)
        if change != "import":
            assert not imports


@pytest.mark.parametrize(
    "failure", [OSError(), subprocess.TimeoutExpired("python", 60), subprocess.CalledProcessError(1, "python")]
)
def test_preflight_child_failure_cannot_reach_host(tmp_path, monkeypatch, failure, host):
    import s1_browser_host

    def run(command, **kwargs):
        assert command[1:4] == ["-I", "-B", "-c"]
        assert kwargs["timeout"] == 60 and kwargs["env"]["HF_HUB_OFFLINE"] == "1"
        raise failure

    monkeypatch.setattr(host.subprocess, "run", run)
    monkeypatch.setattr(s1_browser_host, "provision", lambda _: pytest.fail("Unexpected host mutation"))
    with pytest.raises(ValueError, match="preflight failed"):
        host.provision(SimpleNamespace(model_python=Path(sys.executable), model_dir=tmp_path))
