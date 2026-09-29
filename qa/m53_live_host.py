"""Fresh, authenticated Open WebUI with synthetic saved text and live local Laya."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT), str(ROOT / "qa")]


def validate_source(source):
    """Compare the complete patch stack in a temporary index without changing the checkout."""
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
        assert not subprocess.check_output([*command, "diff", "--name-only"], env=env).strip(), "Patched source differs"
    assert (source / "src/lib/whynote/suggestion_dialog.js").read_bytes() == (
        ROOT / "integrations/openwebui/suggestion_dialog.js"
    ).read_bytes()


def provision(args):
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
    parser.add_argument("command", choices=("serve", "provision"))
    parser.add_argument("--source", type=Path)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8134)
    parser.add_argument("--base-url", default="http://127.0.0.1:8134")
    parser.add_argument("--model-python", type=Path)
    parser.add_argument("--model-dir", type=Path)
    args = parser.parse_args()
    if args.command == "serve":
        validate_source(args.source.resolve())
        args.entry_patch = "m5-v0.11.4-templates.patch"
        os.environ.update(WHYNOTE_LOCAL_CHAIN="1", WHYNOTE_TEMPLATE_SYNTHETIC="1")
        args.native_ratings = False
        s1_browser_host.serve(args)
    else:
        provision(args)


if __name__ == "__main__":
    main()
