"""The simplifier: OpenAI-compatible HTTP to the chassis's model proxy, streamed back as chassis
events, with the chassis's tools over MCP in a small hand-written loop (`echo_python.tools`).

`handle(input, ctx)` is the wire form of the contract (docs/contracts/contract-v0.md): plain dicts
in, event dicts out, `schema_version: "0"` on each. The model call goes to `CHASSIS_MODEL_URL`, the
tools to `CHASSIS_TOOL_URL`. No model key lives here; the chassis holds the credential. In the
`remote` lane the chassis's listener wants a bearer: when `CHASSIS_API_TOKEN` is set and not empty,
every model and MCP call carries `Authorization: Bearer <token>`; unset (the `sidecar` lane), no
`Authorization` header is sent. The token is read from the environment on each run and is never
logged. `ctx["traceparent"]`, when set, is the `traceparent` header on every model and MCP call.
The input text goes to the model in its own user message, never in the system prompt.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx

from echo_python import tools

SCHEMA_VERSION = "0"
PROMPT_VERSION = "simplifier-v1"
SYSTEM_PROMPT = "Rewrite in plain words. Short sentences. Keep every fact."
DEFAULT_ROUTE = "big-default"
MODEL_URL_VAR = "CHASSIS_MODEL_URL"
TOKEN_VAR = "CHASSIS_API_TOKEN"
DEFAULT_MODEL_URL = "http://127.0.0.1:8090/v1"
# suggested: 30 s for the model call when `ctx.budget.timeout_ms` is not set; the epic gives none.
DEFAULT_TIMEOUT_S = 30.0
# suggested: at most 3 rounds of tool calls per run; a 4th ask is `tool_loop_exceeded`.
MAX_TOOL_ROUNDS = 3

transport: httpx.AsyncBaseTransport | None = None
"""Test-only hook. When set, the model call goes through this transport (for example an
`httpx.ASGITransport` on the chassis app or the fake model server) instead of a socket."""


@dataclass
class _Turn:
    """What one streamed model call left behind besides its deltas."""

    usage: tuple[int, int] = (0, 0)
    calls: dict[int, tools.ToolCall] = field(default_factory=dict)
    failed: bool = False


def _event(kind: str, **fields: Any) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "type": kind, **fields}


def _error(code: str, message: str, *, retryable: bool) -> dict[str, Any]:
    return _event("error", code=code, message=message, retryable=retryable)


def _usage(data: Any) -> tuple[int, int]:
    if not isinstance(data, dict):
        return 0, 0
    return int(data.get("prompt_tokens") or 0), int(data.get("completion_tokens") or 0)


def _timeout(ctx: dict[str, Any]) -> float:
    budget = ctx.get("budget")
    if isinstance(budget, dict) and budget.get("timeout_ms"):
        return float(budget["timeout_ms"]) / 1000
    return DEFAULT_TIMEOUT_S


def _messages(text: str) -> list[dict[str, Any]]:
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": text}]


def _headers(ctx: dict[str, Any]) -> dict[str, str]:
    headers: dict[str, str] = {}
    if ctx.get("traceparent"):
        headers["traceparent"] = str(ctx["traceparent"])
    token = os.environ.get(TOKEN_VAR)
    if token:
        headers["authorization"] = f"Bearer {token}"
    return headers


async def _stream(
    client: httpx.AsyncClient, body: dict[str, Any], turn: _Turn
) -> AsyncIterator[dict[str, Any]]:
    """One streamed model call: `delta` events, or one `error` (and `turn.failed`)."""
    async with client.stream("POST", "/chat/completions", json=body) as response:
        if response.status_code >= 400:
            detail = (await response.aread()).decode(errors="replace")[:200]
            turn.failed = True
            yield _error(
                f"http_{response.status_code}",
                detail or response.reason_phrase,
                retryable=response.status_code >= 500,
            )
            return
        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except ValueError:
                turn.failed = True
                yield _error("bad_response", "bad SSE payload", retryable=False)
                return
            if isinstance(chunk.get("error"), dict):
                err = chunk["error"]
                turn.failed = True
                yield _error(
                    str(err.get("code") or "model_error"),
                    str(err.get("message") or "model error"),
                    retryable=bool(err.get("retryable", False)),
                )
                return
            if chunk.get("usage"):
                turn.usage = _usage(chunk["usage"])
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                tools.add_deltas(turn.calls, delta.get("tool_calls") or [])
                if delta.get("content"):
                    yield _event("delta", text=delta["content"])


async def handle(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    """`start`, then per model call its `delta`s and a `tool_call` per tool it asked for, then one
    `metrics` summing every call, `end`; or `error`."""
    yield _event("start", request_id=str(ctx.get("request_id", "")))
    route = str(ctx.get("model_route") or DEFAULT_ROUTE)
    headers, timeout = _headers(ctx), _timeout(ctx)
    offered = await tools.list_openai_tools(headers, timeout)
    messages = _messages(str(input.get("text") or ""))
    base_url = os.environ.get(MODEL_URL_VAR, DEFAULT_MODEL_URL).rstrip("/")
    tokens_in = tokens_out = 0
    try:
        async with httpx.AsyncClient(
            base_url=base_url, transport=transport, timeout=timeout, headers=headers
        ) as client:
            for rounds in range(MAX_TOOL_ROUNDS + 1):
                body: dict[str, Any] = {
                    "model": route,
                    "messages": messages,
                    "stream": True,
                    "stream_options": {"include_usage": True},
                }
                if offered:
                    body["tools"] = offered
                turn = _Turn()
                async for event in _stream(client, body, turn):
                    yield event
                if turn.failed:
                    return
                tokens_in, tokens_out = tokens_in + turn.usage[0], tokens_out + turn.usage[1]
                if not turn.calls:
                    break
                if rounds == MAX_TOOL_ROUNDS:
                    message = f"the model asked for tools more than {MAX_TOOL_ROUNDS} times"
                    yield _error("tool_loop_exceeded", message, retryable=False)
                    return
                calls = [turn.calls[i] for i in sorted(turn.calls)]
                messages.append(tools.assistant_message(calls))
                for call in calls:
                    try:
                        arguments = tools.arguments(call)
                        result = await tools.call(call.name, arguments, headers, timeout)
                    except ValueError as exc:
                        yield _error("bad_response", str(exc), retryable=False)
                        return
                    except tools.ToolCallError as exc:
                        yield _error("tool_error", str(exc), retryable=False)
                        return
                    yield _event(
                        "tool_call",
                        call_id=call.call_id,
                        name=call.name,
                        arguments=arguments,
                        result=result,
                    )
                    messages.append(tools.tool_message(call.call_id, result))
    except httpx.TimeoutException as exc:
        yield _error("timeout", str(exc) or "model call timed out", retryable=True)
        return
    except httpx.HTTPError as exc:
        yield _error("connect_error", str(exc) or type(exc).__name__, retryable=True)
        return
    yield _event(
        "metrics", input_tokens=tokens_in, output_tokens=tokens_out, model_route=route, attempt=1
    )
    yield _event("end", status="ok")
