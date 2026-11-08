"""`POST /v1/chat/completions`, streaming and complete, with tool calls and usage. `GET /health`,
`GET /v1/models`. Chat request bodies are recorded, in order, in `app.state.calls`, so a test
can check the messages a client sent, tool loop included. The log keeps the last `max_calls`
(suggested: 1000) in a `deque`, so a load run cannot grow it without bound (it got the server
OOM-killed); `app.state.calls_total` counts every call.

Failure rules (CH-16). A rule with `delay_s` answers that much later; while it waits, the handler
watches for the client to leave (a socket close, or the ASGI transport's cancel) and counts it in
`app.state.aborted`, so a test can show that a timed-out caller ended the upstream call. An error
rule with a `type` answers in the OpenAI error shape LiteLLM's proxy uses.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

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


@dataclass(frozen=True)
class _Turn:
    """What one chat call answers: the values every body and chunk of the reply share."""

    completion_id: str
    created: int
    model: str
    reply: str
    tool_calls: list[dict[str, Any]] | None
    usage: dict[str, int]

    @property
    def finish(self) -> str:
        return "tool_calls" if self.tool_calls else "stop"


def _usage(rule: Rule) -> dict[str, int]:
    return {
        "prompt_tokens": rule.usage.prompt_tokens,
        "completion_tokens": rule.usage.completion_tokens,
        "total_tokens": rule.usage.prompt_tokens + rule.usage.completion_tokens,
    }


def _completion_body(turn: _Turn) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": turn.reply or None}
    if turn.tool_calls:
        message["tool_calls"] = turn.tool_calls
    return {
        "id": turn.completion_id,
        "object": "chat.completion",
        "created": turn.created,
        "model": turn.model,
        "choices": [{"index": 0, "message": message, "finish_reason": turn.finish}],
        "usage": turn.usage,
    }


def _chunk(
    turn: _Turn, delta: dict[str, Any], finish_reason: str | None = None, **extra: Any
) -> str:
    payload = {
        "id": turn.completion_id,
        "object": "chat.completion.chunk",
        "created": turn.created,
        "model": turn.model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        **extra,
    }
    return f"data: {json.dumps(payload)}\n\n"


async def _stream(turn: _Turn) -> AsyncIterator[str]:
    yield _chunk(turn, {"role": "assistant", "content": ""})
    for piece in _pieces(turn.reply):
        yield _chunk(turn, {"content": piece})
    if turn.tool_calls:
        streamed = [{"index": i, **tc} for i, tc in enumerate(turn.tool_calls)]
        yield _chunk(turn, {"tool_calls": streamed})
    yield _chunk(turn, {}, turn.finish, usage=turn.usage)
    yield "data: [DONE]\n\n"


MAX_CALLS = 1000
"""suggested: how many request bodies `app.state.calls` keeps."""
DISCONNECT_POLL_S = 0.02
"""suggested: how often a delayed answer checks whether the client is still there."""


class _ClientGone(Exception):
    """The client left while the answer was delayed."""


async def _delay(request: Request, seconds: float) -> None:
    """Wait `seconds`, or raise `_ClientGone` as soon as the client disconnects. A cancel (the
    ASGI transport's way of saying the client gave up) is counted the same way and re-raised."""
    deadline = time.monotonic() + seconds
    try:
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                return
            if await request.is_disconnected():
                raise _ClientGone
            await asyncio.sleep(min(DISCONNECT_POLL_S, left))
    except asyncio.CancelledError:
        request.app.state.aborted += 1
        raise


def _error_response(error: Any) -> Any:
    """LiteLLM's `ProxyException` shape when the rule names a `type`; else FastAPI's detail."""
    if error.type is None:
        raise HTTPException(status_code=error.status, detail=error.message)
    body = {
        "error": {
            "message": error.message,
            "type": error.type,
            "param": None,
            "code": str(error.status),
        }
    }
    return JSONResponse(body, status_code=error.status)


def create_app(script: Script, *, max_calls: int = MAX_CALLS) -> FastAPI:
    app = FastAPI(title="fake-model-server", version="0.1.0")
    app.state.script = script
    app.state.calls = deque[dict[str, Any]](maxlen=max_calls)  # the last `max_calls`, oldest first
    app.state.calls_total = 0
    app.state.aborted = 0  # clients that left during a delayed answer (CH-16)

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
        app.state.calls.append(body)
        app.state.calls_total += 1
        rule = script.pick(messages, body.get("model"))
        if rule.delay_s > 0:
            try:
                await _delay(request, rule.delay_s)
            except _ClientGone:
                app.state.aborted += 1
                return Response(status_code=499)  # never seen: the client is gone
        if rule.error is not None:
            return _error_response(rule.error)
        turn = _Turn(
            completion_id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
            created=int(time.time()),
            model=body.get("model") or script.model,
            reply=rule.reply,
            tool_calls=_tool_calls(rule, _offered_names(body)),
            usage=_usage(rule),
        )
        if not body.get("stream"):
            return JSONResponse(_completion_body(turn))
        return StreamingResponse(_stream(turn), media_type="text/event-stream")

    return app
