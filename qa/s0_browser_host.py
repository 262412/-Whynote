"""Reproducible loopback S0 fixture host; always create a new synthetic data directory."""

import argparse
import hashlib
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = "8bd8b4fac5e059578ac0c74b3c18d11139f88b7d"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def serve(args):
    source, data = args.source.resolve(), args.data_dir.resolve()
    assert subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip() == UPSTREAM
    patch = ROOT / "integrations/openwebui/patches/native-v0.11.4-s0.patch"
    subprocess.run(["git", "-C", str(source), "apply", "--reverse", "--check", str(patch)], check=True)
    timing_patch = ROOT / "integrations/openwebui/patches/manual-v0.11.4-timing.patch"
    subprocess.run(["git", "-C", str(source), "apply", "--reverse", "--check", str(timing_patch)], check=True)
    assert (source / "build/index.html").is_file(), "Build the pinned patched frontend first"
    data.mkdir(parents=True, exist_ok=False)
    (data / "static").mkdir()
    private = {key: secrets.token_urlsafe(32) for key in ("secret", "version_key", "admin_password")}
    private["tenant"] = "synthetic-" + secrets.token_hex(12)
    (data / "private.json").write_text(json.dumps(private), encoding="utf-8")
    flags = {
        "OFFLINE_MODE": "true",
        "HF_HUB_OFFLINE": "1",
        "USE_SLIM_DOCKER": "true",
        "ENABLE_OLLAMA_API": "false",
        "ENABLE_OPENAI_API": "false",
        "ENABLE_VERSION_UPDATE_CHECK": "false",
        "ENABLE_ADMIN_EXPORT": "false",
        "ENABLE_ADMIN_CHAT_ACCESS": "false",
        "ENABLE_PERSISTENT_CONFIG": "false",
        "DATABASE_ENABLE_SQLITE_WAL": "false",
        "ENABLE_SIGNUP": "true",
        "DEFAULT_USER_ROLE": "user",
        "USER_PERMISSIONS_CHAT_RATE_RESPONSE": str(args.native_ratings).lower(),
        "CORS_ALLOW_ORIGIN": f"http://127.0.0.1:{args.port}",
        "WEBSOCKET_EVENT_CALLER_TIMEOUT": "60",
    }
    os.environ.update(flags)
    os.environ.update(
        DATA_DIR=str(data),
        STATIC_DIR=str(data / "static"),
        DATABASE_URL=f"sqlite:///{(data / 'webui.db').as_posix()}",
        FRONTEND_BUILD_DIR=str(source / "build"),
        WEBUI_SECRET_KEY=private["secret"],
        WHYNOTE_S0_TENANT=private["tenant"],
        WHYNOTE_S0_VERSION_KEY=private["version_key"],
        WHYNOTE_S0_FIXTURE=str(ROOT / "fixtures/openwebui-s0-v1.json"),
        WHYNOTE_S0_DB=str(data / "whynote.db"),
    )
    manifest = {
        "upstream": UPSTREAM,
        "backend": str(source / "backend"),
        "port": args.port,
        "patch_sha256": digest(patch),
        "timing_patch_sha256": digest(timing_patch),
        "frontend_index_sha256": digest(source / "build/index.html"),
        "action_sha256": digest(ROOT / "integrations/openwebui/s0_action.py"),
        "pipe_sha256": digest(ROOT / "integrations/openwebui/s0_pipe.py"),
        "flags": flags,
        "python": sys.version,
        "scope": "synthetic loopback; flags are not network isolation",
    }
    (data / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    sys.path[:0] = [str(source / "backend"), str(ROOT / "src")]
    import uvicorn

    uvicorn.run("open_webui.main:app", host="127.0.0.1", port=args.port, access_log=False)


def provision(args):
    import httpx

    data = args.data_dir.resolve()
    private = json.loads((data / "private.json").read_text(encoding="utf-8"))
    # Publicly known fixture password only works for these new, loopback-only synthetic identities.
    with httpx.Client(base_url=args.base_url, timeout=60, trust_env=False) as api:
        assert api.get("/api/version").json()["version"] == "0.11.4"
        response = api.post(
            "/api/v1/auths/signup",
            json={"name": "虚构管理员", "email": "s0-admin@example.invalid", "password": private["admin_password"]},
        )
        response.raise_for_status()
        assert response.json()["role"] == "admin", "Requires an empty instance"
        api.headers["Authorization"] = "Bearer " + response.json()["token"]
        identities = {}
        for name in ("alice", "bob"):
            response = api.post(
                "/api/v1/auths/add",
                json={
                    "name": "虚构 " + name,
                    "email": f"s0-{name}@example.invalid",
                    "password": "Synthetic-S0-20260927!only",
                    "role": "user",
                },
            )
            response.raise_for_status()
            identities[name] = response.json()["id"]
        for kind in ("pipe", "action"):
            ident = f"whynote_s0_{kind}"
            content = (ROOT / f"integrations/openwebui/s0_{kind}.py").read_text(encoding="utf-8")
            response = api.post(
                "/api/v1/functions/create",
                json={
                    "id": ident,
                    "name": "知因 S0 点踩" if kind == "action" else "知因 S0 固定虚构回答",
                    "content": content,
                    "meta": {},
                },
            )
            response.raise_for_status()
            saved = api.get(f"/api/v1/functions/id/{ident}")
            saved.raise_for_status()
            assert saved.json()["content"] == content
            toggled = api.post(f"/api/v1/functions/id/{ident}/toggle")
            toggled.raise_for_status()
            assert toggled.json()["is_active"] and not toggled.json()["is_global"]
        response = api.post(
            "/api/v1/models/create",
            json={
                "id": "whynote_s0_pipe",
                "name": "知因 S0 固定虚构回答",
                "base_model_id": None,
                "params": {},
                "meta": {"actionIds": ["whynote_s0_action"]},
                "access_grants": [
                    {"principal_type": "user", "principal_id": ident, "permission": "read"}
                    for ident in identities.values()
                ],
            },
        )
        response.raise_for_status()
        (data / "synthetic-identities.json").write_text(json.dumps(identities), encoding="utf-8")
        print("Fixed host, Function contents and non-global activation verified; two synthetic users provisioned.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    host = sub.add_parser("serve")
    host.add_argument("--source", type=Path, required=True)
    host.add_argument("--data-dir", type=Path, required=True)
    host.add_argument("--port", type=int, default=8098)
    host.add_argument("--native-ratings", action="store_true")
    setup = sub.add_parser("provision")
    setup.add_argument("--data-dir", type=Path, required=True)
    setup.add_argument("--base-url", required=True)
    args = parser.parse_args()
    (serve if args.command == "serve" else provision)(args)


if __name__ == "__main__":
    main()
