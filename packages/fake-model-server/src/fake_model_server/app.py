"""`POST /v1/chat/completions`, streaming and complete, with tool calls and usage. `GET /health`,
`GET /v1/models`.
"""

from __future__ import annotations

import json
import time
import uuid
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


def _tool_calls(rule: Rule) -> list[dict[str, Any]] | None:
    if rule.tool_call is None:
        return None
    return [
        {
            "id": f"call_{uuid.uuid4().hex[:8]}",
            "type": "function",
            "function": {
                "name": rule.tool_call.name,
                "arguments": json.dumps(rule.tool_call.arguments),
            },
        }
    ]


def create_app(script: Script) -> FastAPI:
    app = FastAPI(title="fake-model-server", version="0.1.0")
    app.state.script = script
    app.state.calls = []

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
        tool_calls = _tool_calls(rule)
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
