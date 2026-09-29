"""The simplifier: one model call over OpenAI-compatible HTTP, streamed back as chassis events.

`handle(input, ctx)` is the wire form of the contract (docs/contracts/contract-v0.md): plain dicts
in, event dicts out, `schema_version: "0"` on each. The model call goes to `CHASSIS_MODEL_URL`,
the chassis's model proxy. No key lives here and no `Authorization` header is sent; the chassis
holds the credential. The input text goes to the model in its own user message, never merged into
the system prompt.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from typing import Any

import httpx

SCHEMA_VERSION = "0"
PROMPT_VERSION = "simplifier-v1"
SYSTEM_PROMPT = "Rewrite in plain words. Short sentences. Keep every fact."
DEFAULT_ROUTE = "big-default"
MODEL_URL_VAR = "CHASSIS_MODEL_URL"
DEFAULT_MODEL_URL = "http://127.0.0.1:8080/v1"
DEFAULT_TIMEOUT_S = 30.0

transport: httpx.AsyncBaseTransport | None = None
"""Test-only hook. When set, the model call goes through this transport (for example an
`httpx.ASGITransport` on the chassis app or the fake model server) instead of a socket."""


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


def _messages(text: str) -> list[dict[str, str]]:
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": text}]


async def handle(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    """`start`, one `delta` per content chunk, `metrics` from the final chunk, `end`; or `error`."""
    yield _event("start", request_id=str(ctx.get("request_id", "")))
    route = str(ctx.get("model_route") or DEFAULT_ROUTE)
    text = str(input.get("text") or "")
    body = {
        "model": route,
        "messages": _messages(text),
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    base_url = os.environ.get(MODEL_URL_VAR, DEFAULT_MODEL_URL).rstrip("/")
    usage: tuple[int, int] = (0, 0)
    try:
        async with (
            httpx.AsyncClient(
                base_url=base_url, transport=transport, timeout=_timeout(ctx)
            ) as client,
            client.stream("POST", "/chat/completions", json=body) as response,
        ):
            if response.status_code >= 400:
                detail = (await response.aread()).decode(errors="replace")[:200]
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
                    yield _error("bad_response", "bad SSE payload", retryable=False)
                    return
                if isinstance(chunk.get("error"), dict):
                    err = chunk["error"]
                    yield _error(
                        str(err.get("code") or "model_error"),
                        str(err.get("message") or "model error"),
                        retryable=bool(err.get("retryable", False)),
                    )
                    return
                if chunk.get("usage"):
                    usage = _usage(chunk["usage"])
                for choice in chunk.get("choices") or []:
                    content = (choice.get("delta") or {}).get("content")
                    if content:
                        yield _event("delta", text=content)
    except httpx.TimeoutException as exc:
        yield _error("timeout", str(exc) or "model call timed out", retryable=True)
        return
    except httpx.HTTPError as exc:
        yield _error("connect_error", str(exc) or type(exc).__name__, retryable=True)
        return
    yield _event(
        "metrics", input_tokens=usage[0], output_tokens=usage[1], model_route=route, attempt=1
    )
    yield _event("end", status="ok")
