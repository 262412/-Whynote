import copy
import re
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from whynote.domain import REASON_CODES, NotFoundError
from whynote.laya_app import create_app
from whynote.laya_local import MODEL, LocalLaya


def response():
    return {
        "model": "laya-rl-agent",
        "answers": {
            "primary_reason": {
                "type": "choice",
                "choice": "factual_error",
                "confidence": 0.9,
                "probabilities": {key: float(key == "factual_error") for key in REASON_CODES},
            },
            "factual_error_signal": {"type": "noul", "noul": 0.0},
            "instruction_failure_signal": {"type": "noul", "noul": 1.0},
        },
        "usage": {"input_tokens": 40, "output_tokens": 0},
    }


class Agent:
    device = "cpu"
    tok = SimpleNamespace(all_special_tokens=["<mask>"], encode=lambda text, **kw: list(text))

    def __init__(self, result=None):
        self.calls = 0
        self.result = response() if result is None else result

    def predict(self, state, questions):
        self.calls += 1
        return copy.deepcopy(self.result)


def test_disabled_does_not_read_context_or_load():
    adapter = LocalLaya(Agent())
    with pytest.raises(NotFoundError):
        adapter.evaluate_reason(lambda: pytest.fail("context read"))
    with pytest.raises(NotFoundError):
        LocalLaya.load("missing")
    assert adapter.agent.calls == 0


def test_local_identity_and_unconfirmed_source():
    result = LocalLaya(Agent()).evaluate_reason(lambda: "synthetic", enabled=True)
    assert result["provider"] == "laya_local"
    assert result["model_version"] == MODEL
    assert result["attribution_source"] == "model_inferred_unconfirmed"
    assert result["calibrated"] is False
    assert result["binary_signals"] == {"factual_error_signal": 0.0, "instruction_failure_signal": 1.0}


@pytest.mark.parametrize("state", [None, "", " ", "a" * 701, "中" * 3000, "hi<mask>"])
def test_input_rejected_before_predict(state):
    agent = Agent()
    with pytest.raises(ValueError):
        LocalLaya(agent).evaluate_reason(lambda: state, enabled=True)
    assert agent.calls == 0


@pytest.mark.parametrize("bad", [None, {}, {"type": "noul", "noul": None}, {"type": "noul", "noul": True}])
@pytest.mark.parametrize("signal", ["factual_error_signal", "instruction_failure_signal"])
def test_invalid_requested_signal(bad, signal):
    data = response()
    data["answers"][signal] = bad
    agent = Agent(data)
    with pytest.raises(RuntimeError, match="contract"):
        LocalLaya(agent).evaluate_reason(lambda: "synthetic", enabled=True)
    assert agent.calls == 1


@pytest.mark.parametrize("fault", ["version", "missing", "probability", "confidence", "usage"])
def test_invalid_response(fault):
    data = response()
    if fault == "version":
        data["model"] = "jev-1.13.0"
    elif fault == "missing":
        del data["answers"]["factual_error_signal"]
    elif fault == "probability":
        data["answers"]["primary_reason"]["probabilities"]["style"] = float("nan")
    elif fault == "confidence":
        data["answers"]["primary_reason"]["confidence"] = -1
    else:
        data["usage"] = {}
    with pytest.raises(RuntimeError, match="contract"):
        LocalLaya(Agent(data)).evaluate_reason(lambda: "synthetic", enabled=True)


def page_headers(client):
    page = client.get("/")
    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store"
    return {"X-Local-Token": re.search(r'nonce="([^"]+)"', page.text)[1]}


def test_http_success_security_and_no_echo():
    agent = Agent()
    with TestClient(create_app(LocalLaya(agent)), base_url="http://127.0.0.1:8766") as client:
        headers = page_headers(client)
        body = {"question": "17+25?", "answer": "41"}
        assert client.post("/api/reason", json=body).status_code == 403
        assert (
            client.post("/api/reason", json=body, headers={**headers, "Origin": "https://evil.test"}).status_code == 403
        )
        assert client.get("/", headers={"Host": "evil.test"}).status_code == 403
        assert agent.calls == 0
        for _ in range(2):
            result = client.post("/api/reason", json=body, headers=headers)
            assert result.status_code == 200
            assert result.json()["provider"] == "laya_local"
            assert "17+25" not in result.text
        # This manual tester recomputes on deliberate resubmission; it creates no feedback events.
        assert agent.calls == 2
        result = client.post("/api/reason", json={**body, "secret": "private-marker"}, headers=headers)
        assert result.status_code == 422 and "private-marker" not in result.text
        assert client.post("/api/reason", json={**body, "answer": "x" * 40000}, headers=headers).status_code == 413


def test_timeout_retains_capacity_until_worker_finishes():
    release, finished = threading.Event(), threading.Event()

    class SlowAgent(Agent):
        def predict(self, state, questions):
            try:
                assert release.wait(5)
                return super().predict(state, questions)
            finally:
                finished.set()

    with TestClient(
        create_app(LocalLaya(SlowAgent()), timeout_seconds=0.02), base_url="http://127.0.0.1:8766"
    ) as client:
        headers = page_headers(client)
        try:
            result = client.post("/api/reason", json={"question": "q", "answer": "a"}, headers=headers)
            assert result.status_code == 504
            assert client.get("/health").json()["busy"]
            assert client.post("/api/reason", json={"question": "q", "answer": "a"}, headers=headers).status_code == 429
        finally:
            release.set()
        assert finished.wait(5)


def test_internal_error_is_sanitized():
    class BrokenAgent(Agent):
        def predict(self, state, questions):
            raise RuntimeError("private-marker " + state)

    with TestClient(create_app(LocalLaya(BrokenAgent())), base_url="http://127.0.0.1:8766") as client:
        result = client.post("/api/reason", json={"question": "q", "answer": "a"}, headers=page_headers(client))
        assert result.status_code == 503
        assert "private-marker" not in result.text
