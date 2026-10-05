"""Real login/HTTP regression checks on the fresh local QA server (port 8092).

Requires --data-dir pointing at that server's new SQLite data directory.
Refuses a populated user table; credentials live in memory and are never printed.
"""

import argparse
import json
import secrets
import sqlite3
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--admin-other-status", type=int, choices=(401, 404), default=401)
    args = parser.parse_args()
    database = args.data_dir.resolve() / "webui.db"
    assert database.is_file()
    with sqlite3.connect(database) as db:
        assert db.execute('SELECT count(*) FROM "user"').fetchone()[0] == 0, "Requires a fresh QA instance"
    checks = []

    def check(name, actual, expected):
        checks.append({"name": name, "actual": actual, "expected": expected, "pass": actual == expected})

    def stored(fid):
        with sqlite3.connect(database) as db:
            row = db.execute("SELECT data, meta, snapshot FROM feedback WHERE id=?", (fid,)).fetchone()
            return row

    with httpx.Client(base_url="http://127.0.0.1:8092", timeout=30, trust_env=False) as client:
        check("version", client.get("/api/version").json()["version"], "0.11.4")
        credentials = {name: secrets.token_urlsafe(32) for name in ("admin", "alice", "bob")}
        admin = client.post(
            "/api/v1/auths/signup",
            json={"name": "QA Admin", "email": "qa-admin@qa.invalid", "password": credentials["admin"]},
        )
        assert admin.status_code == 200
        assert admin.json()["role"] == "admin"
        admin_headers = {"Authorization": "Bearer " + admin.json()["token"]}
        for name in ("alice", "bob"):
            response = client.post(
                "/api/v1/auths/add",
                headers=admin_headers,
                json={
                    "name": "QA " + name,
                    "email": f"qa-{name}@qa.invalid",
                    "password": credentials[name],
                    "role": "user",
                },
            )
            assert response.status_code == 200, response.status_code
        headers = {}
        for name, password in credentials.items():
            response = client.post(
                "/api/v1/auths/signin", json={"email": f"qa-{name}@qa.invalid", "password": password}
            )
            assert response.status_code == 200
            headers[name] = {"Authorization": "Bearer " + response.json()["token"]}
        client.cookies.clear()
        prefix = "/api/v1/evaluations"
        ids = {}
        for name in ("alice", "admin"):
            response = client.post(
                prefix + "/feedback",
                headers=headers[name],
                json={"type": "rating", "data": {"comment": "synthetic-http"}},
            )
            assert response.status_code == 200
            ids[name] = response.json()["id"]
        check("unauthenticated-GET", client.get(prefix + "/feedback/" + ids["alice"]).status_code, 401)
        for name, target, expected_get, expected_post in (
            ("alice", "alice", 200, 200),
            ("bob", "alice", 404, 404),
            ("admin", "alice", args.admin_other_status, args.admin_other_status),
            ("admin", "admin", 200, 200),
        ):
            url = prefix + "/feedback/" + ids[target]
            check(name + "-GET-" + target, client.get(url, headers=headers[name]).status_code, expected_get)
            response = client.post(url, headers=headers[name], json={"type": "rating"})
            check(name + "-POST-" + target, response.status_code, expected_post)
            if expected_post != 200:
                check(name + "-no-disclosure", "synthetic-http" in response.text, False)
        before = stored(ids["alice"])
        response = client.post(
            prefix + "/feedback/" + ids["alice"],
            headers=headers["admin"],
            json={"type": "rating", "data": {"comment": "synthetic-http-mutated"}},
        )
        check("Q12-mutation-status", response.status_code, args.admin_other_status)
        check("Q12-mutation-no-write", stored(ids["alice"]), before)
        with sqlite3.connect(database) as db:
            check("empty-chat-fixture", db.execute("SELECT count(*) FROM chat").fetchone()[0], 0)
        check("bulk-delete-status", client.delete("/api/v1/chats/", headers=headers["alice"]).status_code, 200)
        check("Q14-unlinked-feedback-preserved", stored(ids["alice"]) is not None, True)
        check("other-user-feedback-preserved", stored(ids["admin"]) is not None, True)
    result = {"checks": checks, "passed": sum(c["pass"] for c in checks), "failed": sum(not c["pass"] for c in checks)}
    (args.data_dir / "http-results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return bool(result["failed"])


if __name__ == "__main__":
    raise SystemExit(main())
