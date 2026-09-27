"""One bounded Chat Completions request through the host's existing HTTP pool."""

from __future__ import annotations

import asyncio
import json

from fastapi.responses import StreamingResponse

from .s1 import PIPE_ID


async def complete_response(session, config, store, attempt, credential, messages):
    import aiohttp  # Supplied by the pinned Open WebUI host, not a core dependency.

    payload = {
        "model": config["provider_model"],
        "messages": messages,
        "stream": True,
        "max_tokens": 1024,
        "stream_options": {"include_usage": True},
    }
    if config["mode"] == "cloud":
        payload["thinking"] = {"type": "disabled"}
    else:
        # The mock rejects arbitrary text. Only use its exact synthetic cases.
        payload["messages"] = [dict(message) for message in messages]
        payload["messages"][0]["content"] = "这是知因 S1 虚构接入测试。"

    async def stream():
        answer, finish, usage, terminal = "", None, None, False
        response = None
        try:
            async with asyncio.timeout(60):
                response = await session.request(
                    "POST",
                    config["base_url"] + "/chat/completions",
                    json=payload,
                    headers={"Authorization": f"Bearer {credential}", "Content-Type": "application/json"},
                    allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=60, sock_connect=10, sock_read=20),
                )
                if response.status != 200 or "text/event-stream" not in response.headers.get("Content-Type", ""):
                    raise ValueError("provider rejected request")
                received = 0
                async for raw in response.content:
                    received += len(raw)
                    if received > 262144:
                        raise ValueError("provider response limit exceeded")
                    line = raw.decode("utf-8").strip()
                    if not line or line.startswith(":"):
                        continue
                    if not line.startswith("data:"):
                        raise ValueError("invalid provider stream")
                    value = line[5:].strip()
                    if value == "[DONE]":
                        terminal = finish == "stop" and bool(answer.strip())
                        break
                    chunk = json.loads(value)
                    if not isinstance(chunk, dict) or chunk.get("error"):
                        raise ValueError("provider stream failed")
                    if chunk.get("model") != config["provider_model"]:
                        raise ValueError("provider model does not match the approved configuration")
                    if chunk.get("usage") is not None:
                        usage = chunk["usage"]
                    choices = chunk.get("choices", [])
                    if not isinstance(choices, list) or len(choices) > 1:
                        raise ValueError("unsupported provider choices")
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = choice.get("delta", {})
                    if delta.get("tool_calls") or delta.get("reasoning_content") or delta.get("function_call"):
                        raise ValueError("unsupported provider output")
                    content = delta.get("content") or ""
                    if not isinstance(content, str) or (finish is not None and content):
                        raise ValueError("invalid provider content")
                    answer += content
                    if len(answer.encode("utf-8")) > 65536:
                        raise ValueError("provider output limit exceeded")
                    if choice.get("finish_reason") is not None:
                        if finish is not None:
                            raise ValueError("duplicate provider terminal")
                        finish = choice["finish_reason"]
                    if content:
                        safe = {
                            "id": attempt,
                            "object": "chat.completion.chunk",
                            "created": 0,
                            "model": PIPE_ID,
                            "choices": [
                                {"index": 0, "delta": {"role": "assistant", "content": content}, "finish_reason": None}
                            ],
                        }
                        yield f"data: {json.dumps(safe, ensure_ascii=False)}\n\n"
                # The receipt exists before emitting the terminal event. Host save
                # plus matching content/parent is required again at feedback time.
                store.finish(attempt, answer=answer, complete=terminal, usage=usage)
                if not terminal:
                    raise ValueError("generation did not finish naturally")
                safe = {
                    "id": attempt,
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": PIPE_ID,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                }
                yield f"data: {json.dumps(safe)}\n\ndata: [DONE]\n\n"
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never copy a provider error body, exception repr, key, or prompt.
            yield 'data: {"error":{"message":"S1 generation unavailable; existing feedback remains available"}}\n\n'
        finally:
            if response is not None:
                response.close()
            store.finish(attempt)  # Pending -> incomplete, retaining unknown costs.

    return StreamingResponse(stream(), media_type="text/event-stream")
