"""Launch the explicitly authorized single-user cloud/local integration test."""

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def serve(args):
    source, data = args.source.resolve(), args.data_dir.resolve()
    expected = "8bd8b4fac5e059578ac0c74b3c18d11139f88b7d"
    actual = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if actual != expected or not (source / "build/index.html").is_file():
        raise ValueError("Build the pinned patched Open WebUI first")
    if not (data / "trial.json").exists():
        data.mkdir(parents=True, exist_ok=False)
        private = {k: secrets.token_urlsafe(32) for k in ("secret", "version_key", "admin_password", "password")}
        (data / "private.json").write_text(json.dumps(private), encoding="utf-8")
        config = dict(
            enabled=True,
            mode="cloud",
            research_enabled=False,
            instance_id="local-chain-" + secrets.token_hex(12),
            user_id="not-provisioned",
            db_path=str(data / "whynote.db"),
            version_key=private["version_key"],
            started_at=int(time.time()),
            base_url="https://api.deepseek.com",
            provider_model="deepseek-flash",
            pricing_version="deepseek-flash-cny-2026-09-27",
            outbound_approval_ref="user-20260928-explicit-local-full-chain-test",
            provider_data_terms_ref="docs/s1-2r-provider-review.md; unresolved terms accepted for manual test",
        )
        (data / "trial.json").write_text(json.dumps(config), encoding="utf-8")
    private = json.loads((data / "private.json").read_text(encoding="utf-8"))
    (data / "static").mkdir(exist_ok=True)
    os.environ.update(
        {
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
            "ENABLE_SIGNUP": "false",
            "DEFAULT_USER_ROLE": "user",
            "USER_PERMISSIONS_CHAT_SHARE": "false",
            "USER_PERMISSIONS_CHAT_EXPORT": "false",
            "USER_PERMISSIONS_CHAT_RATE_RESPONSE": "false",
            "CORS_ALLOW_ORIGIN": f"http://127.0.0.1:{args.port}",
            "WEBSOCKET_EVENT_CALLER_TIMEOUT": "60",
            "DATA_DIR": str(data),
            "STATIC_DIR": str(data / "static"),
            "DATABASE_URL": f"sqlite:///{(data / 'webui.db').as_posix()}",
            "FRONTEND_BUILD_DIR": str(source / "build"),
            "WEBUI_SECRET_KEY": private["secret"],
            "WHYNOTE_S1_CONFIG": str(data / "trial.json"),
            "WHYNOTE_PROVIDER_KEYS_FILE": str(args.keys_file.resolve()),
            "WHYNOTE_LOCAL_CHAIN": "1",
            "OPENAI_API_BASE_URL": "https://api.deepseek.com",
            "OPENAI_API_KEY": "server-private-file-only",
            "ENABLE_TITLE_GENERATION": "false",
            "ENABLE_FOLLOW_UP_GENERATION": "false",
            "ENABLE_TAGS_GENERATION": "false",
        }
    )
    sys.path[:0] = [str(source / "backend"), str(ROOT / "src"), str(ROOT)]
    import uvicorn

    uvicorn.run("open_webui.main:app", host="127.0.0.1", port=args.port, access_log=False)


def provision(args):
    import httpx

    data = args.data_dir.resolve()
    private = json.loads((data / "private.json").read_text(encoding="utf-8"))
    config_path = data / "trial.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config["user_id"] != "not-provisioned":
        raise ValueError("Already provisioned; preserve the existing account and budget")
    with httpx.Client(base_url=f"http://127.0.0.1:{args.port}", trust_env=False, timeout=60) as api:
        r = api.post(
            "/api/v1/auths/signup",
            json={
                "name": "本机测试管理员",
                "email": "chain-admin@example.invalid",
                "password": private["admin_password"],
            },
        )
        r.raise_for_status()
        if r.json()["role"] != "admin":
            raise ValueError("Requires a fresh instance")
        api.headers["Authorization"] = "Bearer " + r.json()["token"]
        r = api.post(
            "/api/v1/auths/add",
            json={
                "name": "知因本机测试",
                "email": "local-test@example.invalid",
                "password": private["password"],
                "role": "user",
            },
        )
        r.raise_for_status()
        user_id = r.json()["id"]
        config["user_id"] = user_id
        config_path.write_text(json.dumps(config), encoding="utf-8")
        functions = {
            "whynote_s1_pipe": ("s1_pipe.py", "知因 DeepSeek 本机联调"),
            "whynote_s1_action": ("s1_action.py", "知因点踩及原因"),
            "whynote_s1_retract_action": ("s1_retract_action.py", "撤回知因点踩"),
            "whynote_laya_action": ("laya_action.py", "获取 Laya 原因建议"),
        }
        for ident, (filename, name) in functions.items():
            content = (ROOT / "integrations/openwebui" / filename).read_text(encoding="utf-8")
            r = api.post("/api/v1/functions/create", json={"id": ident, "name": name, "content": content, "meta": {}})
            r.raise_for_status()
            r = api.post(f"/api/v1/functions/id/{ident}/toggle")
            r.raise_for_status()
            if not r.json()["is_active"] or r.json()["is_global"]:
                raise ValueError("Unexpected function activation")
        r = api.post(
            "/api/v1/models/create",
            json={
                "id": "whynote_s1_pipe",
                "name": "知因 DeepSeek 本机联调",
                "base_model_id": None,
                "params": {},
                "meta": {
                    "actionIds": [k for k in functions if k != "whynote_s1_pipe"],
                    "capabilities": {
                        "builtin_tools": False,
                        "vision": False,
                        "file_upload": False,
                        "web_search": False,
                        "code_interpreter": False,
                    },
                },
                "access_grants": [{"principal_type": "user", "principal_id": user_id, "permission": "read"}],
            },
        )
        r.raise_for_status()
        (data / "login.txt").write_text(
            f"http://127.0.0.1:{args.port}\nEmail: local-test@example.invalid\nPassword: {private['password']}\n",
            encoding="utf-8",
        )
    print("Local cloud test provisioned; credentials are in the private login.txt file.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("serve", "provision"))
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--keys-file", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8128)
    args = parser.parse_args()
    (serve if args.command == "serve" else provision)(args)
