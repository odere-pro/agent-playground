"""The model pass-through: `POST /v1/chat/completions`, OpenAI-compatible, over `ports.model`.

A workload points its model base URL here. The proxy maps the OpenAI body (`messages`, `model` as
the route, `temperature`, `max_tokens`, `tools`, `stream`) to `ModelPort.complete` or `.stream`
and maps the result back to the OpenAI shape: chunks, `usage` on the last chunk, `[DONE]`. It never
forwards an inbound `Authorization` header and adds none of its own; the model adapter holds the
key (ADR-001 hard requirement 1). Every call is counted as `chassis.model_calls` per route.

Pulled forward from PoC-2 as far as PoC-1 needs it. Request correlation (`traceparent` in, one span
per call) and per-request budgets come in PoC-2. In PoC-1 this route is mounted on the chassis's
public port, so anything that can reach that port can spend with the chassis key; PoC-2 splits the
proxies onto a localhost-only listener (`deploy/CLAUDE.md`; debt in
`pocs/poc-01-walking-skeleton/notes/2026-09-29-inprocess-debt.md`).
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator, Sequence
from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from chassis.ports.bundle import PortBundle
from chassis.ports.model import (
    ModelChunk,
    ModelError,
    ModelMessage,
    ModelResult,
    ToolCallRequest,
    ToolSpec,
    Usage,
)


class ToolFunction(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)


class ToolParam(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: str = "function"
    function: ToolFunction


class ChatCompletionRequest(BaseModel):
    """The OpenAI body, the fields the port takes. Others are accepted and ignored."""

    model_config = ConfigDict(extra="ignore")
    model: str
    messages: list[dict[str, Any]]
    temperature: float = 0.0
    max_tokens: int | None = None
    tools: list[ToolParam] | None = None
    stream: bool = False

    def port_messages(self) -> list[ModelMessage]:
        out: list[ModelMessage] = []
        for m in self.messages:
            content = m.get("content")
            out.append(
                {
                    "role": str(m.get("role", "user")),
                    "content": content if isinstance(content, str) else json.dumps(content),
                }
            )
        return out

    def port_tools(self) -> list[ToolSpec] | None:
        if not self.tools:
            return None
        return [
            ToolSpec(
                name=t.function.name,
                description=t.function.description,
                parameters=t.function.parameters,
            )
            for t in self.tools
        ]


def _usage(usage: Usage | None) -> dict[str, int]:
    u = usage or Usage()
    return {
        "prompt_tokens": u.input_tokens,
        "completion_tokens": u.output_tokens,
        "total_tokens": u.input_tokens + u.output_tokens,
    }


def _tool_call(call: ToolCallRequest, index: int | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": call.call_id,
        "type": "function",
        "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
    }
    if index is not None:
        out = {"index": index, **out}
    return out


def _error_body(exc: ModelError) -> dict[str, Any]:
    return {
        "error": {
            "message": exc.message,
            "type": "model_error",
            "code": exc.code,
            "retryable": exc.retryable,
        }
    }


def _completion(
    result: ModelResult, route: str, completion_id: str, created: int
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": result.text or None}
    if result.tool_calls:
        message["tool_calls"] = [_tool_call(c) for c in result.tool_calls]
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        "model": result.model or route,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if result.tool_calls else "stop",
            }
        ],
        "usage": _usage(result.usage),
    }


async def _sse(
    chunks: AsyncIterator[ModelChunk], route: str, completion_id: str, created: int
) -> AsyncIterator[str]:
    def frame(delta: dict[str, Any], finish: str | None = None, **extra: Any) -> str:
        payload = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": route,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            **extra,
        }
        return f"data: {json.dumps(payload)}\n\n"

    tool_index = 0
    usage: Usage | None = None
    try:
        yield frame({"role": "assistant", "content": ""})
        async for chunk in chunks:
            if chunk.text:
                yield frame({"content": chunk.text})
            if chunk.tool_call is not None:
                yield frame({"tool_calls": [_tool_call(chunk.tool_call, tool_index)]})
                tool_index += 1
            if chunk.usage is not None:
                usage = chunk.usage
        yield frame({}, "tool_calls" if tool_index else "stop", usage=_usage(usage))
    except ModelError as exc:
        yield f"data: {json.dumps(_error_body(exc))}\n\n"
    yield "data: [DONE]\n\n"


def model_proxy_router(app: FastAPI) -> APIRouter:
    """The router; `app.state.ports` is read per request, so it works after the lifespan."""
    router = APIRouter()

    @router.post("/v1/chat/completions")
    async def chat_completions(body: ChatCompletionRequest) -> Any:
        if not getattr(app.state, "ready", False):
            return JSONResponse({"detail": "model proxy not ready"}, status_code=503)
        bundle: PortBundle = app.state.ports
        route = body.model
        bundle.telemetry.counter("chassis.model_calls", route=route)
        messages: Sequence[ModelMessage] = body.port_messages()
        tools = body.port_tools()
        completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        created = int(time.time())

        if body.stream:
            chunks = bundle.model.stream(
                messages,
                route=route,
                tools=tools,
                temperature=body.temperature,
                max_tokens=body.max_tokens,
            )
            return StreamingResponse(
                _sse(chunks, route, completion_id, created), media_type="text/event-stream"
            )
        try:
            result = await bundle.model.complete(
                messages,
                route=route,
                tools=tools,
                temperature=body.temperature,
                max_tokens=body.max_tokens,
            )
        except ModelError as exc:
            return JSONResponse(_error_body(exc), status_code=502 if exc.retryable else 500)
        return JSONResponse(_completion(result, route, completion_id, created))

    return router
