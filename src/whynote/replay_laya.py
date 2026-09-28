"""Dedicated offline Laya process for bounded, versioned replay calls."""

import copy
import math
import multiprocessing
import time

from .laya_local import MODEL, LocalLaya


class ReplayError(ValueError):
    """Only fixed codes may cross the replay boundary."""


def require(value, code):
    if not value:
        raise ReplayError(code)


def validate_response(response, questions):
    require(isinstance(response, dict) and response.get("model") == "laya-rl-agent", "invalid_response")
    answers = response.get("answers")
    require(isinstance(answers, dict) and set(answers) == set(questions), "invalid_response")
    safe = {}
    for key, question in questions.items():
        answer = answers[key]
        require(isinstance(answer, dict) and answer.get("type") == question["type"], "invalid_response")

        def probability(value):
            require(type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1, "invalid_response")
            return value

        if question["type"] == "choice":
            probs = answer.get("probabilities")
            require(isinstance(probs, dict) and set(probs) == set(question["criteria"]), "invalid_response")
            require(abs(sum(probability(p) for p in probs.values()) - 1) <= 0.02, "invalid_response")
            choice = answer.get("choice")
            require(isinstance(choice, str) and choice in probs, "invalid_response")
            require(probs[choice] >= max(probs.values()) - 0.02, "invalid_response")
            safe[key] = {"choice": choice, "probabilities": dict(probs)}
        else:
            require(question["type"] == "noul", "invalid_response")
            safe[key] = {"noul": probability(answer.get("noul"))}
    return safe


def check_budget(agent, state, questions):
    """Reject before SDK option/head/state truncation can change the experiment."""
    from laya.common import encode_text, render_options

    require(isinstance(state, str) and bool(state.strip()), "invalid_state")
    require(len(state.encode("utf-8")) <= 8192, "input_bytes_exceeded")
    require(not any(token in state for token in agent.tok.all_special_tokens), "reserved_token")
    size = len(encode_text(agent.tok, state, add_special_tokens=False)["input_ids"])
    require(size <= 700, "input_tokens_exceeded")
    heads = {}
    for key, definition in questions.items():
        internal = agent._to_internal(definition)
        options = render_options(internal)
        lengths = [
            len(encode_text(agent.tok, " " + option, add_special_tokens=False)["input_ids"]) for option in options
        ]
        instructions = len(
            encode_text(agent.tok, f"{internal['t']} question: {internal['ins']}", add_special_tokens=False)[
                "input_ids"
            ]
        )
        option_total = sum(n + 1 for n in lengths)
        require(max(lengths) <= 48 and option_total <= 240, "question_tokens_exceeded")
        require(instructions + option_total <= 256, "question_tokens_exceeded")
        require(size + instructions + option_total + 4 <= 1024, "total_tokens_exceeded")
        heads[key] = {"instructions": instructions, "options": lengths}
    return {"state_tokens": size, "questions": heads}


def _worker(connection, model_dir, device):
    import importlib.metadata
    import socket

    def deny_network(*args, **kwargs):
        raise RuntimeError("network_disabled")

    socket.socket.connect = deny_network
    socket.socket.connect_ex = deny_network
    try:
        from .laya_manifest import SHA256

        adapter = LocalLaya.load(model_dir, device=device, enabled=True)
        import torch

        torch.manual_seed(42)
        if device == "cuda":
            torch.cuda.manual_seed_all(42)
        connection.send(
            {
                "status": "ready",
                "model": MODEL,
                "files_sha256": SHA256,
                "network_disabled": True,
                "runtime": {key: importlib.metadata.version(key) for key in ("laya", "torch", "transformers")},
                "device": device,
                "hardware": torch.cuda.get_device_name() if device == "cuda" else "cpu",
            }
        )
    except Exception:
        connection.send({"error": "model_load_failed"})
        return
    while True:
        try:
            item = connection.recv()
        except EOFError:
            return
        if item is None:
            return
        state, questions = item
        try:
            budget = check_budget(adapter.agent, state, questions)
            response = adapter.agent.predict(state, copy.deepcopy(questions))
            answers = validate_response(response, questions)
            connection.send({"answers": answers, "budget": budget})
        except ReplayError as exc:
            connection.send({"error": str(exc)})
        except Exception:
            connection.send({"error": "inference_failed"})


class LayaReplay:
    def __init__(self, model_dir, *, device="cuda", enabled=False, timeout=60):
        require(enabled is True, "model_disabled")
        require(device in ("cuda", "cpu"), "invalid_device")
        require(type(timeout) in (int, float) and 0 < timeout <= 60, "invalid_timeout")
        self.timeout = timeout
        self.alive = True
        context = multiprocessing.get_context("spawn")
        self.connection, child = context.Pipe()
        self.process = context.Process(target=_worker, args=(child, str(model_dir), device), daemon=True)
        started = time.perf_counter()
        self.process.start()
        child.close()
        try:
            self.metadata = self._receive()
            self.metadata["load_ms"] = round((time.perf_counter() - started) * 1000, 3)
            self.metadata["backend"] = "laya_local"
        except ReplayError:
            self.close()
            raise

    def _receive(self):
        try:
            if not self.connection.poll(self.timeout):
                self.close()
                raise ReplayError("timeout")
            result = self.connection.recv()
        except (EOFError, OSError):
            self.close()
            raise ReplayError("backend_unavailable") from None
        if "error" in result:
            raise ReplayError(result["error"])
        return result

    def predict(self, state, questions):
        require(self.alive, "backend_unavailable")
        try:
            self.connection.send((state, questions))
        except (OSError, BrokenPipeError):
            self.close()
            raise ReplayError("backend_unavailable") from None
        return self._receive()

    def close(self):
        self.alive = False
        if self.process.is_alive():
            self.process.terminate()
        self.process.join(timeout=5)
        if self.process.is_alive():
            self.process.kill()
            self.process.join(timeout=5)
        self.connection.close()
