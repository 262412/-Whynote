"""Opt-in loopback probe against the already running manual Laya tester."""

import argparse
import json
import re
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    results = []
    with httpx.Client(base_url=f"http://127.0.0.1:{args.port}", trust_env=False, timeout=65) as client:
        page = client.get("/")
        assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
        headers = {"X-Local-Token": re.search(r'nonce="([^"]+)"', page.text)[1]}
        body = {"question": "17 加 25 等于多少？", "answer": "17 加 25 等于 41。", "feedback": "计算结果不对"}
        cases = [
            ("normal", 200, body, headers),
            ("deliberate_repeat", 200, body, headers),
            ("missing_token", 403, body, {}),
            ("foreign_origin", 403, body, {**headers, "Origin": "https://example.invalid"}),
            ("foreign_host", 403, body, {**headers, "Host": "example.invalid"}),
            ("empty", 422, {**body, "answer": ""}, headers),
            ("unexpected_field", 422, {**body, "private": "DO_NOT_ECHO"}, headers),
            ("token_window", 422, {**body, "answer": "中文测试" * 500}, headers),
            ("reserved_token", 422, {**body, "answer": "<mask>"}, headers),
            ("body_limit", 413, {**body, "answer": "x" * 40000}, headers),
        ]
        for case_id, expected, data, request_headers in cases:
            response = client.post("/api/reason", json=data, headers=request_headers)
            assert response.status_code == expected, (case_id, response.status_code)
            assert "DO_NOT_ECHO" not in response.text
            result = {"case_id": case_id, "status": response.status_code}
            if expected == 200:
                actual = response.json()
                assert actual["provider"] == "laya_local"
                assert actual["attribution_source"] == "model_inferred_unconfirmed"
                result.update(reason=actual["primary_reason"], elapsed_ms=actual["elapsed_ms"])
            results.append(result)
        health = client.get("/health").json()
        assert health["status"] == "ready" and not health["busy"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"health": health, "cases": results}, indent=2) + "\n", encoding="utf-8")
    print(f"{len(results)}/{len(results)} HTTP checks passed")


if __name__ == "__main__":
    main()
