"""Reproducible loopback S1 synthetic host; always create a new synthetic data directory."""

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
    if subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip() != UPSTREAM:
        raise ValueError("Unexpected upstream revision")
    patch = ROOT / "integrations/openwebui/patches/native-v0.11.4-s0.patch"
    timing_patch = ROOT / "integrations/openwebui/patches/manual-v0.11.4-timing.patch"
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "apply",
            "--reverse",
            "--check",
            str(ROOT / "integrations/openwebui/patches" / getattr(args, "entry_patch", "s1-v0.11.4-trial.patch")),
        ],
        check=True,
    )
    if not (source / "build/index.html").is_file():
        raise ValueError("Build the pinned patched frontend first")
    data.mkdir(parents=True, exist_ok=False)
    (data / "static").mkdir()
    private = {key: secrets.token_urlsafe(32) for key in ("secret", "version_key", "admin_password")}
    private["tenant"] = "synthetic-" + secrets.token_hex(12)
    (data / "private.json").write_text(json.dumps(private), encoding="utf-8")
    config = dict(
        enabled=True,
        mode="mock",
        instance_id=private["tenant"],
        user_id="not-provisioned",
        db_path=str(data / "whynote.db"),
        version_key=private["version_key"],
        started_at=1,
        base_url="http://127.0.0.1:8126/v1",
        provider_model="whynote-s1-synthetic",
    )
    (data / "trial.json").write_text(json.dumps(config), encoding="utf-8")
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
        "ENABLE_MEMORIES": "false",
        "DATABASE_ENABLE_SQLITE_WAL": "false",
        "ENABLE_SIGNUP": "true",
        "DEFAULT_USER_ROLE": "user",
        "USER_PERMISSIONS_CHAT_SHARE": "false",
        "USER_PERMISSIONS_CHAT_EXPORT": "false",
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
        WHYNOTE_S1_CONFIG=str(data / "trial.json"),
        OPENAI_API_BASE_URL="http://127.0.0.1:8126/v1",
        OPENAI_API_KEY="s1-local-synthetic-not-a-cloud-key",
        ENABLE_TITLE_GENERATION="false",
        ENABLE_FOLLOW_UP_GENERATION="false",
        ENABLE_TAGS_GENERATION="false",
    )
    manifest = {
        "upstream": UPSTREAM,
        "backend": str(source / "backend"),
        "port": args.port,
        "patch_sha256": digest(patch),
        "timing_patch_sha256": digest(timing_patch),
        "frontend_index_sha256": digest(source / "build/index.html"),
        "action_sha256": digest(ROOT / "integrations/openwebui/s1_action.py"),
        "pipe_sha256": digest(ROOT / "integrations/openwebui/s1_pipe.py"),
        "flags": flags,
        "python": sys.version,
        "scope": "synthetic loopback; flags are not network isolation",
    }
    (data / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    sys.path[:0] = [str(source / "backend"), str(ROOT / "src"), str(ROOT)]
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
            json={"name": "虚构管理员", "email": "s1-admin@example.invalid", "password": private["admin_password"]},
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
                    "email": f"s1-{name}@example.invalid",
                    "password": "Synthetic-S1-20260927!only",
                    "role": "user",
                },
            )
            response.raise_for_status()
            identities[name] = response.json()["id"]
        config = json.loads((data / "trial.json").read_text(encoding="utf-8"))
        config["user_id"] = identities["alice"]
        (data / "trial.json").write_text(json.dumps(config), encoding="utf-8")
        for kind in ("pipe", "action", "retract_action"):
            ident = f"whynote_s1_{kind}"
            content = (ROOT / f"integrations/openwebui/s1_{kind}.py").read_text(encoding="utf-8")
            response = api.post(
                "/api/v1/functions/create",
                json={
                    "id": ident,
                    "name": {
                        "pipe": "知因 S1 虚构动态回答",
                        "action": "知因 S1 点踩及原因",
                        "retract_action": "知因 S1 撤回点踩",
                    }[kind],
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
                "id": "whynote_s1_pipe",
                "name": "知因 S1 虚构动态回答",
                "base_model_id": None,
                "params": {},
                "meta": {
                    "actionIds": ["whynote_s1_action", "whynote_s1_retract_action"],
                    "capabilities": {
                        "builtin_tools": False,
                        "vision": False,
                        "file_upload": False,
                        "web_search": False,
                        "code_interpreter": False,
                    },
                },
                "access_grants": [
                    {"principal_type": "user", "principal_id": ident, "permission": "read"}
                    for ident in [identities["alice"]]
                ],
            },
        )
        response.raise_for_status()
        (data / "synthetic-identities.json").write_text(json.dumps(identities), encoding="utf-8")
        print(
            "Synthetic S1 host, Function contents and non-global activation verified; two synthetic users provisioned."
        )


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
