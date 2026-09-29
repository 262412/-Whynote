"""Approved source additions and post-provision deployment receipts."""

import json
from types import SimpleNamespace

import httpx
import pytest
from test_q3133_preflight import host as host
from test_q3133_preflight import patched_source as patched_source


@pytest.mark.parametrize("missing", [False, True])
def test_patch_created_file_is_allowed_and_required(patched_source, host, monkeypatch, missing):
    import s1_browser_host

    source, pin = patched_source
    monkeypatch.setattr(host, "ROOT", source.parent)
    monkeypatch.setattr(s1_browser_host, "UPSTREAM", pin)
    patch = source.parent / "integrations/openwebui/patches/m5-v0.11.4-templates.patch"
    patch.write_bytes(
        patch.read_bytes()
        + (
            "diff --git a/approved.js b/approved.js\nnew file mode 100644\n"
            "--- /dev/null\n+++ b/approved.js\n@@ -0,0 +1 @@\n+approved\n"
        ).encode()
    )
    if not missing:
        (source / "approved.js").write_bytes(b"approved\n")
        host.validate_source(source)
    else:
        with pytest.raises(ValueError, match="Patched source differs"):
            host.validate_source(source)


@pytest.fixture
def deployment(tmp_path, host, monkeypatch):
    monkeypatch.setattr(host, "support_modules", lambda: {"src/whynote/synthetic.py": "a" * 64})
    manifest = host.initial_manifest()
    manifest["historical_field"] = "preserve"
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return tmp_path, path, manifest


@pytest.mark.parametrize("problem", [None, "content", "inactive", "global", "module", "http"])
def test_deployment_receipt_requires_actual_readback(deployment, host, monkeypatch, problem):
    data, path, manifest = deployment
    original = path.read_bytes()
    content = "synthetic action\n"
    reply = {"content": content, "is_active": True, "is_global": False}
    if problem == "content":
        reply["content"] = "old action\n"
    elif problem == "inactive":
        reply["is_active"] = False
    elif problem == "global":
        reply["is_global"] = True
    elif problem == "module":
        monkeypatch.setattr(host, "support_modules", lambda: {"src/whynote/synthetic.py": "b" * 64})

    def handle(request):
        assert request.method == "GET" and request.url.path == "/api/v1/functions/id/whynote_s1_action"
        return httpx.Response(500 if problem == "http" else 200, json=reply)

    with httpx.Client(transport=httpx.MockTransport(handle), base_url="http://localhost") as api:
        if problem:
            with pytest.raises((ValueError, httpx.HTTPStatusError)):
                host.verify_deployment(api, data, content, manifest)
            assert path.read_bytes() == original
        else:
            host.verify_deployment(api, data, content, manifest)
            record = json.loads(path.read_text())
            assert record["deployment_state"] == "verified"
            assert record["action_sha256"] == host.action_digest(content)
            assert record["support_modules_sha256"] == manifest["support_modules_sha256"]
            assert record["historical_field"] == "preserve" and record["verified_at"] > 0
    assert set(p.name for p in data.iterdir()) == {"manifest.json"}


def test_action_hash_has_explicit_newline_encoding(host):
    assert host.action_digest("知因\r\nA\rB\n") == host.action_digest("知因\nA\nB\n")


@pytest.mark.parametrize("failure", ["stale_manifest", "provision"])
def test_failed_initialization_has_no_verified_manifest(deployment, host, monkeypatch, failure):
    import s1_browser_host

    data, path, manifest = deployment
    monkeypatch.setattr(host, "validate_model_options", lambda _: None)
    calls = []

    def failing_provision(_):
        calls.append(True)
        raise RuntimeError("Synthetic partial initialization failure")

    monkeypatch.setattr(s1_browser_host, "provision", failing_provision)
    if failure == "stale_manifest":
        manifest["support_modules_sha256"] = {}
        path.write_text(json.dumps(manifest), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises((ValueError, RuntimeError)):
        host.provision(SimpleNamespace(data_dir=data))
    assert calls == ([] if failure == "stale_manifest" else [True])
    assert path.read_bytes() == before and json.loads(before)["deployment_state"] == "not_verified"
