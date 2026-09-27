"""Replay a captured synthetic host click; do not persist auth or request contents."""

import argparse
import json
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--body", type=Path, required=True)
    parser.add_argument("--expected", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    assert urlsplit(args.base_url).hostname == "127.0.0.1"
    assert args.db.is_file() and not args.output.exists()

    def snapshot():
        with sqlite3.connect(f"{args.db.resolve().as_uri()}?mode=ro", uri=True) as db:
            return list(db.iterdump())

    before = snapshot()
    with httpx.Client(base_url=args.base_url, timeout=5, trust_env=False) as api:
        login = api.post(
            "/api/v1/auths/signin",
            json={"email": "s0-alice@example.invalid", "password": "Synthetic-S0-20260927!only"},
        )
        login.raise_for_status()
        api.headers["Authorization"] = "Bearer " + login.json()["token"]
        response = api.post("/api/chat/actions/whynote_s0_action", json=json.loads(args.body.read_text("utf-8")))
    unchanged = snapshot() == before
    same_result = response.status_code == 200 and response.json() == json.loads(args.expected.read_text("utf-8"))
    result = {
        "http_status": response.status_code,
        "same_result": same_result,
        "all_whynote_tables_unchanged": unchanged,
    }
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))
    assert same_result and unchanged


if __name__ == "__main__":
    main()
