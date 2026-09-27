"""Run against a newly started local synthetic demo; never output its session token."""

import argparse
import hashlib
import json
import re
import sqlite3
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8106")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    assert args.db.is_file() and not args.output.exists()

    def events():
        with sqlite3.connect(args.db) as db:
            return db.execute("SELECT event_type,payload FROM events ORDER BY seq").fetchall()

    assert not events(), "Requires an empty synthetic demo"
    checks = []

    def check(name, actual, expected):
        checks.append({"name": name, "actual": actual, "expected": expected, "passed": actual == expected})

    with httpx.Client(base_url=args.base_url, trust_env=False) as api:
        html = api.get("/demo").text
        config = json.loads(re.search(r"const config = (.+);", html).group(1))
        api.headers["X-Demo-Session"] = config["token"]

        def post(url, body, key=None):
            return api.post(url, json=body, headers={"Idempotency-Key": key} if key else {})

        body = {
            "target_ref": config["target"],
            "action_type": "negative_feedback",
            "channel": "synthetic-qa",
            "locale": "zh-CN",
            "interaction_contract": "manual-v1",
        }
        created = post("/v1/feedback-actions", body, "create-a")
        check("create", created.status_code, 202)
        eid = created.json()["event_id"]
        again = post("/v1/feedback-actions", body, "create-b")
        check("new-request-same-active-intent", again.json()["event_id"], eid)
        reason = None
        for i, kind in enumerate(
            ["reason_selected", "reason_skipped", "reason_declined", "reason_menu_closed", "reason_none_matched"]
        ):
            did = f"display-{i}"
            data = {
                "event_id": eid,
                "display_id": did,
                "mode": "edit_menu" if reason else "manual_menu",
                "ui_version": "manual-menu-v1",
                "shown_reason_codes": [r["code"] for r in config["reasons"]],
                "client_session_ref": "qa-session",
            }
            check(kind + "-ticket", post("/demo/display-tickets", data).status_code, 200)
            check(kind + "-rendered", post("/demo/rendered-displays", data).status_code, 200)
            response_body = {
                "user_action": kind,
                "reason_code": "style" if kind == "reason_selected" else None,
                "display_id": did,
                "explicit_submission": True,
                "timing": {
                    "version": "active-v1",
                    "display_id": did,
                    "session_ref": "qa-session",
                    "active_ms": 1,
                    "elapsed_ms": 1,
                },
            }
            response = post(f"/v1/feedback-actions/{eid}/attribution-events", response_body, did)
            check(kind + "-status", response.status_code, 200)
            reason = None if kind == "reason_none_matched" else "style"
            check(kind + "-reason", response.json()["reason_code"], reason)
            before = events()
            check(
                kind + "-retry",
                post(f"/v1/feedback-actions/{eid}/attribution-events", response_body, did).status_code,
                200,
            )
            check(kind + "-retry-no-append", events() == before, True)
        data.update(display_id="huge", mode="manual_menu")
        assert post("/demo/display-tickets", data).status_code == 200
        assert post("/demo/rendered-displays", data).status_code == 200
        response_body.update(user_action="reason_selected", reason_code="style", display_id="huge")
        response_body["timing"].update(display_id="huge", active_ms=10**400)
        before = events()
        response = post(f"/v1/feedback-actions/{eid}/attribution-events", response_body, "huge")
        check("oversized-optional-timing-accepted", response.status_code, 200)
        check("oversized-optional-timing-response-persisted", len(events()) - len(before), 1)
        response_body["timing"]["active_ms"] = 1
        check(
            "valid-retry-after-invalid-timing",
            post(f"/v1/feedback-actions/{eid}/attribution-events", response_body, "huge").status_code,
            200,
        )
    result = {
        "checks": checks,
        "passed": sum(c["passed"] for c in checks),
        "failed": sum(not c["passed"] for c in checks),
        "event_types": [e[0] for e in events()],
        "db_sha256": hashlib.sha256(args.db.read_bytes()).hexdigest(),
        "scope": "Loopback demo HTTP; scripted client timings; no browser or real host login",
    }
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("passed", "failed")}))
    return bool(result["failed"])


if __name__ == "__main__":
    raise SystemExit(main())
