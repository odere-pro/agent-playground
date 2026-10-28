"""`POST /v1/chat/completions`, streaming and complete, with tool calls and usage. `GET /health`,
`GET /v1/models`. Chat request bodies are recorded, in order, in `app.state.calls`, so a test
can check the messages a client sent, tool loop included. The log keeps the last `max_calls`
(suggested: 1000) in a `deque`, so a load run cannot grow it without bound (it got the server
OOM-killed); `app.state.calls_total` counts every call.
"""

from __future__ import annotations

import json
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from fake_model_server.script import Rule, Script


def _pieces(text: str) -> list[str]:
    out: list[str] = []
    word = ""
    for ch in text:
        word += ch
        if ch == " ":
            out.append(word)
            word = ""
    if word:
        out.append(word)
    return out


PREFIX_SEPARATORS = ("__", "-")
"""How a client joins a prefix to a tool name: the Claude CLI's `mcp__<server>__<name>`, LiteLLM's
MCP gateway `<server>-<name>`."""


def _offered_names(body: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for tool in body.get("tools") or []:
        function = tool.get("function") if isinstance(tool, dict) else None
        name = function.get("name") if isinstance(function, dict) else None
        if isinstance(name, str):
            names.append(name)
    return names


def _resolve(name: str, offered: list[str]) -> str:
    """The scripted name, or the ONE offered name that is it with a prefix (`mcp__chassis__` +
    name). An exact match, no match, or two candidates keep the scripted name: no guessing."""
    if not offered or name in offered:
        return name
    hits = [o for o in offered if any(o.endswith(sep + name) for sep in PREFIX_SEPARATORS)]
    return hits[0] if len(hits) == 1 else name


def _tool_calls(rule: Rule, offered: list[str]) -> list[dict[str, Any]] | None:
    if rule.tool_call is None:
        return None
    return [
        {
            "id": f"call_{uuid.uuid4().hex[:8]}",
            "type": "function",
            "function": {
                "name": _resolve(rule.tool_call.name, offered),
                "arguments": json.dumps(rule.tool_call.arguments),
            },
        }
    ]


MAX_CALLS = 1000
"""suggested: how many request bodies `app.state.calls` keeps."""


def create_app(script: Script, *, max_calls: int = MAX_CALLS) -> FastAPI:
    app = FastAPI(title="fake-model-server", version="0.1.0")
    app.state.script = script
    app.state.calls = deque[dict[str, Any]](maxlen=max_calls)  # the last `max_calls`, oldest first
    app.state.calls_total = 0

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/models")
    async def models() -> dict[str, Any]:
        return {
            "object": "list",
            "data": [{"id": script.model, "object": "model", "owned_by": "fake"}],
        }

    @app.post("/v1/chat/completions")
    async def chat(request: Request) -> Any:
        body = await request.json()
        messages: list[dict[str, Any]] = body.get("messages", [])
        model = body.get("model") or script.model
        app.state.calls.append(body)
        app.state.calls_total += 1
        rule = script.pick(messages)
        if rule.error is not None:
            raise HTTPException(status_code=rule.error.status, detail=rule.error.message)
        completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        created = int(time.time())
        usage = {
            "prompt_tokens": rule.usage.prompt_tokens,
            "completion_tokens": rule.usage.completion_tokens,
            "total_tokens": rule.usage.prompt_tokens + rule.usage.completion_tokens,
        }
        tool_calls = _tool_calls(rule, _offered_names(body))
        finish = "tool_calls" if tool_calls else "stop"

        if not body.get("stream"):
            message: dict[str, Any] = {"role": "assistant", "content": rule.reply or None}
            if tool_calls:
                message["tool_calls"] = tool_calls
            return JSONResponse(
                {
                    "id": completion_id,
                    "object": "chat.completion",
                    "created": created,
                    "model": model,
                    "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                    "usage": usage,
                }
            )

        def chunk(delta: dict[str, Any], finish_reason: str | None = None, **extra: Any) -> str:
            payload = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
                **extra,
            }
            return f"data: {json.dumps(payload)}\n\n"

        async def stream() -> AsyncIterator[str]:
            yield chunk({"role": "assistant", "content": ""})
            for piece in _pieces(rule.reply):
                yield chunk({"content": piece})
            if tool_calls:
                streamed = [{"index": i, **tc} for i, tc in enumerate(tool_calls)]
                yield chunk({"tool_calls": streamed})
            yield chunk({}, finish, usage=usage)
            yield "data: [DONE]\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app
