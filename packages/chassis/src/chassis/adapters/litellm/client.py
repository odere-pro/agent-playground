"""LiteLLMModel: `complete` and `stream` over `POST {base_url}/chat/completions`.

Errors map to `ModelError`: `http_<status>` (retryable at 5xx), `connect_error` and `timeout`
(retryable), `bad_response` (not), and `service_budget_exhausted` (not) when LiteLLM refuses for
the service's budget: a 4xx whose body is `{"error": {"type": "budget_exceeded", ...}}`, the
proxy's `BudgetExceededError`; the status has been 400 and 429 across releases, so the type is
what is matched (CH-16). The adapter never retries; `num_retries` and `fallbacks` per route in the
LiteLLM config do.

Per-call timeout (CH-16). `timeout_s` is the wall clock of the whole call. The request's httpx
timeout is the smaller of it and the adapter's default, so a hung connect or read ends by then;
a complete call is bounded by `asyncio.timeout`, and a stream checks its deadline on every read.
Past the deadline the call raises `timeout` and leaves the `httpx` response, which closes the
connection: the upstream sees the client go and stops generating.

The agent name, when set, rides along as a LiteLLM tag in
both the `x-litellm-tags` header and `metadata.tags`, so per-agent cost shows up in the router.
The httpx client ignores the environment (`trust_env=False`), as the sidecar connector's does: no
proxy variable can route the key elsewhere.

Each request carries the W3C `traceparent` of the current OpenTelemetry span, when there is one
(PoC-8): the model proxy calls the router inside `chassis.model.call`, so LiteLLM's own
OpenTelemetry spans join the run's trace (ADR-007 item 4). With no current span (the `memory`
telemetry) no header is sent. Only `traceparent` goes out; no baggage, so no user id.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from collections.abc import AsyncIterator, Awaitable, Sequence
from typing import Any

import httpx
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from chassis.ports.model import (
    SERVICE_BUDGET_EXHAUSTED,
    TIMEOUT,
    ModelChunk,
    ModelError,
    ModelMessage,
    ModelResult,
    ToolCallRequest,
    ToolSpec,
    Usage,
)
from chassis.redaction import redact_error_text

BASE_URL_VAR = "LITELLM_BASE_URL"
API_KEY_VAR = "LITELLM_API_KEY"


def _tool_schema(tool: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }


def _wire_message(message: ModelMessage) -> dict[str, Any]:
    """The OpenAI shape of a port message: `content` always present (null stays null), `name`,
    `tool_calls` with `function.arguments` as a JSON string, and `tool_call_id`, when set.
    """
    out: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.name is not None:
        out["name"] = message.name
    if message.tool_calls:
        out["tool_calls"] = [
            {
                "id": call.call_id,
                "type": "function",
                "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
            }
            for call in message.tool_calls
        ]
    if message.tool_call_id is not None:
        out["tool_call_id"] = message.tool_call_id
    return out


def _arguments(raw: str | None) -> dict[str, Any]:
    """Parse `function.arguments`. A model can emit broken JSON; keep it under `raw`."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {"raw": raw}
    return parsed if isinstance(parsed, dict) else {"raw": raw}


def _usage(data: dict[str, Any] | None) -> Usage:
    if not data:
        return Usage()
    return Usage(
        input_tokens=int(data.get("prompt_tokens") or 0),
        output_tokens=int(data.get("completion_tokens") or 0),
    )


_TRACE_CONTEXT = TraceContextTextMapPropagator()


async def _add_traceparent(request: httpx.Request) -> None:
    """Put the current span's `traceparent` on `request`; nothing when no span is current."""
    carrier: dict[str, str] = {}
    _TRACE_CONTEXT.inject(carrier)
    if carrier.get("traceparent"):
        request.headers["traceparent"] = carrier["traceparent"]


# suggested: 300 characters of upstream error text; enough to name the cause, short enough for an
# event and a log line. The epic gives no limit.
MESSAGE_CAP = 300


def _error_message(response: httpx.Response, body: bytes, api_key: str | None) -> str:
    """The upstream message, from `error.message` or `detail`, else the raw text; redacted and
    capped at `MESSAGE_CAP` chars.
    """
    try:
        data = json.loads(body)
    except ValueError:
        data = None
    message = ""
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict) and err.get("message"):
            message = str(err["message"])
        elif isinstance(err, str):
            message = err
        elif data.get("detail"):
            message = str(data["detail"])
    if not message:
        message = body.decode(errors="replace").strip()
    message = redact_error_text(message, api_key)[:MESSAGE_CAP]
    return message or response.reason_phrase or f"HTTP {response.status_code}"


BUDGET_EXCEEDED_TYPE = "budget_exceeded"
"""LiteLLM's `error.type` for its `BudgetExceededError` (key, team, user, or proxy budget)."""


def _is_budget_refusal(body: bytes) -> bool:
    try:
        data = json.loads(body)
    except ValueError:
        return False
    err = data.get("error") if isinstance(data, dict) else None
    return isinstance(err, dict) and err.get("type") == BUDGET_EXCEEDED_TYPE


def _from_http_status(response: httpx.Response, body: bytes, api_key: str | None) -> ModelError:
    status = response.status_code
    message = _error_message(response, body, api_key)
    if 400 <= status < 500 and _is_budget_refusal(body):
        return ModelError(SERVICE_BUDGET_EXHAUSTED, message, retryable=False)
    return ModelError(f"http_{status}", message, retryable=status >= 500)


def _deadline_error(timeout_s: float) -> ModelError:
    return ModelError(TIMEOUT, f"the call passed its {timeout_s:g} s deadline", retryable=True)


def _from_transport(exc: httpx.HTTPError) -> ModelError:
    if isinstance(exc, httpx.TimeoutException):
        return ModelError(TIMEOUT, str(exc) or "request timed out", retryable=True)
    if isinstance(exc, httpx.ConnectError):
        return ModelError("connect_error", str(exc) or "connection failed", retryable=True)
    return ModelError("transport_error", str(exc) or type(exc).__name__, retryable=True)


class _ToolCallBuffer:
    """Accumulates streamed tool-call fragments by `index` until the finish chunk."""

    def __init__(self) -> None:
        self._parts: dict[int, dict[str, Any]] = {}

    def add(self, fragments: list[dict[str, Any]]) -> None:
        for frag in fragments:
            index = int(frag.get("index", 0))
            part = self._parts.setdefault(index, {"id": "", "name": "", "arguments": ""})
            if frag.get("id"):
                part["id"] = frag["id"]
            fn = frag.get("function") or {}
            if fn.get("name"):
                part["name"] = fn["name"]
            if fn.get("arguments"):
                part["arguments"] += fn["arguments"]

    def flush(self) -> list[ToolCallRequest]:
        calls = [
            ToolCallRequest(
                call_id=part["id"] or f"call_{index}",
                name=part["name"],
                arguments=_arguments(part["arguments"]),
            )
            for index, part in sorted(self._parts.items())
        ]
        self._parts.clear()
        return calls


# suggested: 60 s per httpx operation (connect, read, write, pool) when the call names no
# `timeout_s`; the epic gives no number. The model proxy passes the run's remaining time as
# `timeout_s` (CH-16), which caps this per call.
DEFAULT_TIMEOUT_S = 60.0


async def _next_line(lines: AsyncIterator[str]) -> str | None:
    try:
        return await lines.__anext__()
    except StopAsyncIteration:
        return None


async def _by[T](deadline: float | None, awaitable: Awaitable[T], timeout_s: float | None) -> T:
    """`awaitable`, or `timeout` once `deadline` (monotonic) has passed. The wait runs in the
    caller's task, so a stream whose reads come from different tasks is still bounded."""
    if deadline is None:
        return await awaitable
    left = deadline - time.monotonic()
    try:
        async with asyncio.timeout(max(left, 0.0)):
            return await awaitable
    except TimeoutError as exc:
        raise _deadline_error(timeout_s if timeout_s is not None else 0.0) from exc


class LiteLLMModel:
    """`ModelPort` over OpenAI-compatible HTTP. `base_url` ends with `/v1`."""

    name = "litellm"

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        *,
        agent: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.agent = agent
        self.timeout = timeout
        self._api_key = api_key
        headers: dict[str, str] = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if agent:
            headers["x-litellm-tags"] = f"agent:{agent}"
        # trust_env=False: `HTTP(S)_PROXY`, `ALL_PROXY`, and `SSL_CERT_*` in the chassis
        # environment cannot route the bearer key through a proxy or swap the trust roots.
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            transport=transport,
            timeout=timeout,
            trust_env=False,
            event_hooks={"request": [_add_traceparent]},
        )

    def __repr__(self) -> str:
        key = "set" if self._api_key else "none"
        return f"LiteLLMModel(base_url={self.base_url!r}, agent={self.agent!r}, api_key={key})"

    @classmethod
    def from_env(
        cls, *, agent: str | None = None, timeout: float = DEFAULT_TIMEOUT_S
    ) -> LiteLLMModel:
        """Build from `LITELLM_BASE_URL` (required) and `LITELLM_API_KEY` (optional)."""
        base_url = os.environ.get(BASE_URL_VAR)
        if not base_url:
            raise LookupError(f"model: litellm needs {BASE_URL_VAR}")
        return cls(base_url, os.environ.get(API_KEY_VAR) or None, agent=agent, timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    def _request_timeout(self, timeout_s: float | None) -> httpx.Timeout:
        """The httpx timeout of one request: the call's `timeout_s` when it is the smaller."""
        if timeout_s is None or timeout_s >= self.timeout:
            return httpx.Timeout(self.timeout)
        return httpx.Timeout(max(timeout_s, 0.0))

    def _body(
        self,
        messages: Sequence[ModelMessage],
        route: str,
        tools: Sequence[ToolSpec] | None,
        temperature: float,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": route,
            "messages": [_wire_message(m) for m in messages],
            "temperature": temperature,
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if tools:
            body["tools"] = [_tool_schema(t) for t in tools]
        if self.agent:
            body["metadata"] = {"tags": [f"agent:{self.agent}"]}
        return body

    async def complete(
        self,
        messages: Sequence[ModelMessage],
        *,
        route: str,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        timeout_s: float | None = None,
    ) -> ModelResult:
        body = self._body(messages, route, tools, temperature, max_tokens)
        try:
            async with asyncio.timeout(timeout_s):
                response = await self._client.post(
                    "/chat/completions", json=body, timeout=self._request_timeout(timeout_s)
                )
        except TimeoutError as exc:
            raise _deadline_error(timeout_s if timeout_s is not None else 0.0) from exc
        except httpx.HTTPError as exc:
            raise _from_transport(exc) from exc
        if response.status_code >= 400:
            raise _from_http_status(response, response.content, self._api_key)
        try:
            data = response.json()
            message = data["choices"][0]["message"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ModelError("bad_response", f"unexpected completion shape: {exc}") from exc
        return ModelResult(
            text=message.get("content") or "",
            tool_calls=[
                ToolCallRequest(
                    call_id=tc.get("id") or f"call_{i}",
                    name=(tc.get("function") or {}).get("name", ""),
                    arguments=_arguments((tc.get("function") or {}).get("arguments")),
                )
                for i, tc in enumerate(message.get("tool_calls") or [])
            ],
            usage=_usage(data.get("usage")),
            model=str(data.get("model") or route),
        )

    async def stream(
        self,
        messages: Sequence[ModelMessage],
        *,
        route: str,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        timeout_s: float | None = None,
    ) -> AsyncIterator[ModelChunk]:
        body = self._body(messages, route, tools, temperature, max_tokens)
        body["stream"] = True
        body["stream_options"] = {"include_usage": True}
        usage: Usage | None = None
        buffer = _ToolCallBuffer()
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        try:
            async with contextlib.AsyncExitStack() as stack:
                request = self._client.stream(
                    "POST", "/chat/completions", json=body, timeout=self._request_timeout(timeout_s)
                )
                response = await _by(deadline, stack.enter_async_context(request), timeout_s)
                if response.status_code >= 400:
                    raise _from_http_status(response, await response.aread(), self._api_key)
                lines = response.aiter_lines()
                while True:
                    line = await _by(deadline, _next_line(lines), timeout_s)
                    if line is None:
                        break
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        event = json.loads(payload)
                    except ValueError as exc:
                        raise ModelError("bad_response", f"bad SSE payload: {exc}") from exc
                    if event.get("usage"):
                        usage = _usage(event["usage"])
                    choices = event.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = choice.get("delta") or {}
                    if delta.get("content"):
                        yield ModelChunk(text=delta["content"])
                    if delta.get("tool_calls"):
                        buffer.add(delta["tool_calls"])
                    if choice.get("finish_reason"):
                        for call in buffer.flush():
                            yield ModelChunk(tool_call=call)
        except httpx.HTTPError as exc:
            raise _from_transport(exc) from exc
        for call in buffer.flush():
            yield ModelChunk(tool_call=call)
        yield ModelChunk(usage=usage or Usage(), finish=True)
