"""Fresh, authenticated Open WebUI with synthetic saved text and live local Laya."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT), str(ROOT / "qa")]
BUILD_RECEIPT = ".whynote-m53-build.json"


def validate_source(source):
    """Compare the complete patch stack in a temporary index without changing the checkout."""
    from s1_browser_host import UPSTREAM

    command = ["git", "-C", str(source)]
    if subprocess.check_output([*command, "rev-parse", "HEAD"], text=True).strip() != UPSTREAM:
        raise ValueError("Unexpected upstream revision")
    with tempfile.TemporaryDirectory(prefix="m53-index-") as directory:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(directory) / "index")}
        command = ["git", "-C", str(source)]
        subprocess.run([*command, "read-tree", "HEAD"], env=env, check=True)
        for name in ("native-v0.11.4-s0", "manual-v0.11.4-timing", "s1-v0.11.4-trial", "m5-v0.11.4-templates"):
            subprocess.run(
                [*command, "apply", "--cached", str(ROOT / "integrations/openwebui/patches" / (name + ".patch"))],
                env=env,
                check=True,
            )
        if subprocess.check_output([*command, "diff", "--name-only"], env=env).strip():
            raise ValueError("Patched source differs")
    if (source / "src/lib/whynote/suggestion_dialog.js").read_bytes() != (
        ROOT / "integrations/openwebui/suggestion_dialog.js"
    ).read_bytes():
        raise ValueError("Suggestion dialog differs")


def source_fingerprint(source):
    names = (
        subprocess.check_output(
            ["git", "-C", str(source), "ls-files", "--cached", "--others", "--exclude-standard", "-z"]
        )
        .decode("utf-8")
        .split("\0")
    )
    files = {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in sorted(set(names) - {""})}
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def build_files(source):
    directory = source / "build"
    files = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError("Build artifacts must not be symlinks")
        if path.is_file() and path != directory / BUILD_RECEIPT:
            files[path.relative_to(directory).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    if "index.html" not in files or not any(name.endswith(".js") for name in files):
        raise ValueError("Complete frontend build is required")
    return files


def build(source):
    """Only a successful build may issue a receipt; existing artifacts cannot be certified."""
    receipt = source / "build" / BUILD_RECEIPT
    receipt.unlink(missing_ok=True)
    validate_source(source)
    before = source_fingerprint(source)
    node = subprocess.check_output(["node", "--version"], text=True).strip()
    if not node.startswith("v22."):
        raise ValueError("Build requires Node22 on PATH")
    npm = shutil.which("npm")
    if not npm:
        raise ValueError("Build requires npm on PATH")
    subprocess.run([npm, "run", "build"], cwd=source, check=True)
    validate_source(source)
    if source_fingerprint(source) != before:
        raise ValueError("Source changed during build; rebuild from a stable checkout")
    record = {"version": 1, "source_sha256": before, "files": build_files(source), "node": node}
    receipt.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")


def validate_build(source):
    validate_source(source)
    try:
        record = json.loads((source / "build" / BUILD_RECEIPT).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("Missing/invalid build receipt; run the build command") from exc
    if (
        not isinstance(record, dict)
        or record.get("version") != 1
        or record.get("source_sha256") != source_fingerprint(source)
        or record.get("files") != build_files(source)
    ):
        raise ValueError("Build does not match current source/artifacts; rebuild required")


def validate_model_options(args):
    for name, is_file in (("model_python", True), ("model_dir", False)):
        value = getattr(args, name, None)
        if value is None:
            raise ValueError(f"--{name.replace('_', '-')} is required for provision")
        path = Path(value).resolve()
        if not (path.is_file() if is_file else path.is_dir()):
            raise ValueError(f"Invalid --{name.replace('_', '-')} path")
    # This child only imports the pinned SDK and hashes local files. It never loads a model.
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); from whynote.laya_local import preflight; preflight(sys.argv[2])"
    )
    try:
        subprocess.run(
            [
                str(Path(args.model_python).resolve()),
                "-I",
                "-B",
                "-c",
                code,
                str(ROOT / "src"),
                str(Path(args.model_dir).resolve()),
            ],
            check=True,
            timeout=60,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={
                **os.environ,
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "HF_HUB_DISABLE_TELEMETRY": "1",
                "USE_TF": "0",
            },
            **({"creationflags": 0x08000000} if sys.platform == "win32" else {}),
        )
    except (OSError, subprocess.SubprocessError):
        raise ValueError("Local runtime preflight failed; verify interpreter, pinned SDK and model files") from None


def provision(args):
    validate_model_options(args)
    import httpx
    import s1_browser_host

    from whynote.domain import Principal
    from whynote.laya_local import REVISION
    from whynote.research import register_source
    from whynote.s1 import PIPE_ID, TrialStore

    s1_browser_host.provision(args)
    data = args.data_dir.resolve()
    config_path = data / "trial.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    private = json.loads((data / "private.json").read_text(encoding="utf-8"))
    with httpx.Client(base_url=args.base_url, trust_env=False, timeout=60) as api:
        response = api.post(
            "/api/v1/auths/signin", json={"email": "s1-admin@example.invalid", "password": private["admin_password"]}
        )
        response.raise_for_status()
        api.headers["Authorization"] = "Bearer " + response.json()["token"]
        response = api.post(
            "/api/v1/functions/id/whynote_s1_action/update",
            json={
                "id": "whynote_s1_action",
                "name": "知因点踩 / 撤销",
                "content": (ROOT / "integrations/openwebui/local_chain_action.py").read_text(encoding="utf-8"),
                "meta": {},
            },
        )
        response.raise_for_status()
        model = api.get("/api/v1/models/model", params={"id": PIPE_ID})
        model.raise_for_status()
        model = model.json()
        model["meta"]["actionIds"] = ["whynote_s1_action"]
        response = api.post("/api/v1/models/model/update", json=model)
        response.raise_for_status()
        response = api.post(
            "/api/v1/auths/signin", json={"email": "s1-alice@example.invalid", "password": "Synthetic-S1-20260927!only"}
        )
        response.raise_for_status()
        api.headers["Authorization"] = "Bearer " + response.json()["token"]
        parent_id, message_id = str(uuid.uuid4()), str(uuid.uuid4())
        question = "请改写以下代码，保留函数名和参数。\n```python\ndef total(items):\n    return sum(items)\n```"
        answer = "```python\ndef total_values(values, extra):\n    return sum(values) + extra\n```"
        messages = {
            parent_id: {
                "id": parent_id,
                "role": "user",
                "content": question,
                "parentId": None,
                "childrenIds": [message_id],
                "timestamp": int(time.time()),
            },
            message_id: {
                "id": message_id,
                "role": "assistant",
                "content": answer,
                "parentId": parent_id,
                "childrenIds": [],
                "timestamp": int(time.time()),
                "done": True,
                "model": PIPE_ID,
                "modelName": "合成回答",
                "modelIdx": 0,
            },
        }
        response = api.post(
            "/api/v1/chats/new",
            json={
                "chat": {
                    "title": "M5-3b 合成代码改写",
                    "models": [PIPE_ID],
                    "history": {"messages": messages, "currentId": message_id},
                    "messages": list(messages.values()),
                    "timestamp": int(time.time() * 1000),
                }
            },
        )
        response.raise_for_status()
        saved = response.json()
    chat = SimpleNamespace(**{k: saved[k] for k in ("id", "user_id", "created_at", "chat")})
    config.update(research_enabled=True, research_study_ref=str(uuid.uuid4()), research_protocol_ref=str(uuid.uuid4()))
    store = TrialStore(config)
    principal = Principal(config["instance_id"], config["user_id"])
    store.enroll(chat.id, chat.user_id)
    register_source(
        store,
        principal,
        chat.id,
        {
            "registration_id": str(uuid.uuid4()),
            "study_ref": config["research_study_ref"],
            "protocol_ref": config["research_protocol_ref"],
            "context_ref": chat.id,
            "feedback_ref": None,
            "source_kind": "scripted",
            "public_label_origin": "none",
        },
    )
    attempt = store.reserve(chat.id, chat.user_id, message_id, parent_id, question)
    store.finish(attempt, answer=answer, complete=True, usage={"prompt_tokens": 0, "completion_tokens": 0})
    store.confirm_saved(chat, message_id)
    target = store.qualify(
        chat, {"chat_id": chat.id, "id": message_id, "model": PIPE_ID, "messages": list(messages.values())}
    )
    config.update(
        suggestion_research_enabled=True,
        suggestion_template_enabled=True,
        suggestion_backend="laya_local",
        suggestion_model_revision=REVISION,
        suggestion_python=str(args.model_python.resolve()),
        suggestion_model_dir=str(args.model_dir.resolve()),
        suggestion_synthetic_targets={target["object_id"]: target["object_version"]},
    )
    config_path.write_text(json.dumps(config), encoding="utf-8")
    (data / "synthetic-case.json").write_text(
        json.dumps(
            {
                "chat_id": chat.id,
                "message_id": message_id,
                "target": target,
                "answer_origin": "developer_synthetic_saved",
                "model_backend": "laya_local",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print("Registered synthetic case:", args.base_url + "/c/" + chat.id)


def main():
    import s1_browser_host

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("build", "serve", "provision"))
    parser.add_argument("--source", type=Path)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--port", type=int, default=8134)
    parser.add_argument("--base-url", default="http://127.0.0.1:8134")
    parser.add_argument("--model-python", type=Path)
    parser.add_argument("--model-dir", type=Path)
    args = parser.parse_args()
    if args.command in {"build", "serve"} and args.source is None:
        parser.error("--source is required for build/serve")
    if args.command in {"serve", "provision"} and args.data_dir is None:
        parser.error("--data-dir is required for serve/provision")
    if args.command == "build":
        build(args.source.resolve())
        return
    if args.command == "serve":
        validate_build(args.source.resolve())
        args.entry_patch = "m5-v0.11.4-templates.patch"
        os.environ.update(WHYNOTE_LOCAL_CHAIN="1", WHYNOTE_TEMPLATE_SYNTHETIC="1")
        args.native_ratings = False
        s1_browser_host.serve(args)
    else:
        try:
            provision(args)
        except ValueError as exc:
            parser.error(str(exc))


if __name__ == "__main__":
    main()
