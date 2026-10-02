"""LiteLLMModel: `complete` and `stream` over `POST {base_url}/chat/completions`.

Errors map to `ModelError`: `http_<status>` (retryable at 5xx), `connect_error` and `timeout`
(retryable), `bad_response` (not). The agent name, when set, rides along as a LiteLLM tag in
both the `x-litellm-tags` header and `metadata.tags`, so per-agent cost shows up in the router.
The httpx client ignores the environment (`trust_env=False`), as the sidecar connector's does: no
proxy variable can route the key elsewhere.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from chassis.ports.model import (
    ModelChunk,
    ModelError,
    ModelMessage,
    ModelResult,
    ToolCallRequest,
    ToolSpec,
    Usage,
)

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


# suggested: 300 characters of upstream error text; enough to name the cause, short enough for an
# event and a log line. The epic gives no limit.
MESSAGE_CAP = 300
_KEY_SHAPE = re.compile(r"sk-[A-Za-z0-9_-]{8,}")


def _redact(text: str, api_key: str | None) -> str:
    """Strip the configured key and anything shaped like one. LiteLLM's 401 text quotes the key
    it received, and this message travels into events, the proxy body, and `/v1/run` output.
    """
    if api_key:
        text = text.replace(api_key, "[redacted]")
    return _KEY_SHAPE.sub("[redacted]", text)


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
    message = _redact(message, api_key)[:MESSAGE_CAP]
    return message or response.reason_phrase or f"HTTP {response.status_code}"


def _from_http_status(response: httpx.Response, body: bytes, api_key: str | None) -> ModelError:
    status = response.status_code
    return ModelError(
        f"http_{status}", _error_message(response, body, api_key), retryable=status >= 500
    )


def _from_transport(exc: httpx.HTTPError) -> ModelError:
    if isinstance(exc, httpx.TimeoutException):
        return ModelError("timeout", str(exc) or "request timed out", retryable=True)
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


# suggested: 60 s per router call; the epic gives no number. It is not capped at the run's
# `budget.timeout_ms`: `ModelPort.complete` and `.stream` take no per-call timeout, so the proxy
# cannot pass the run's remaining time down without a port change (backlog 013 CH-2, PoC-2
# budgets). Until then the workload's own client caps the call at `budget.timeout_ms`
# (`echo_python.handle`), and a run over its time budget is the workload's timeout, not this one.
DEFAULT_TIMEOUT_S = 60.0


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
    ) -> ModelResult:
        body = self._body(messages, route, tools, temperature, max_tokens)
        try:
            response = await self._client.post("/chat/completions", json=body)
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
    ) -> AsyncIterator[ModelChunk]:
        body = self._body(messages, route, tools, temperature, max_tokens)
        body["stream"] = True
        body["stream_options"] = {"include_usage": True}
        usage: Usage | None = None
        buffer = _ToolCallBuffer()
        try:
            async with self._client.stream("POST", "/chat/completions", json=body) as response:
                if response.status_code >= 400:
                    raise _from_http_status(response, await response.aread(), self._api_key)
                async for line in response.aiter_lines():
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
