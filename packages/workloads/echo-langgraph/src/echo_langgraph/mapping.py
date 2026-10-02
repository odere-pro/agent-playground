"""LangGraph stream parts to chassis events (`events.v0.json`), in its own file so its size can be
counted (PoC-2 exit criterion "Event mapping size").

Input: what `graph.astream(..., stream_mode=["messages", "updates"])` yields, `(mode, data)`.
- `messages`: `(AIMessageChunk, metadata)` per token chunk. Text becomes a `delta`.
- `updates`: `{node: {"messages": [...]}}` per finished node. The model node's `AIMessage` gives
  the tool calls it asked for and its `usage_metadata`; the tool node's `ToolMessage` gives each
  result, and the pending call becomes one `tool_call` event.
Failures become one `error` event with echo-python's codes.
"""

from __future__ import annotations

import json
from typing import Any

import httpx2
import openai
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

SCHEMA_VERSION = "0"


def event(kind: str, **fields: Any) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "type": kind, **fields}


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    parts = [p if isinstance(p, str) else p.get("text", "") for p in content or []]
    return "".join(p for p in parts if isinstance(p, str))


def _result(message: ToolMessage) -> dict[str, Any]:
    text = _text(message.content)
    if message.status == "error":
        return {"error": text}
    artifact = message.artifact
    if isinstance(artifact, dict) and isinstance(artifact.get("structured_content"), dict):
        return dict(artifact["structured_content"])
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = None
    return parsed if isinstance(parsed, dict) else {"text": text}


class EventMapper:
    """One per run. `on_part` maps a stream part; `finish` gives `metrics` and `end`."""

    def __init__(self, model_route: str) -> None:
        self.model_route = model_route
        self.input_tokens = 0
        self.output_tokens = 0
        self._pending: dict[str, dict[str, Any]] = {}

    def on_part(self, mode: str, data: Any) -> list[dict[str, Any]]:
        if mode == "messages":
            chunk = data[0]
            text = _text(chunk.content) if isinstance(chunk, AIMessageChunk) else ""
            return [event("delta", text=text)] if text else []
        if mode != "updates" or not isinstance(data, dict):
            return []
        out: list[dict[str, Any]] = []
        for update in data.values():
            for message in (update or {}).get("messages", []):
                out.extend(self._on_message(message))
        return out

    def _on_message(self, message: Any) -> list[dict[str, Any]]:
        if isinstance(message, AIMessage):
            if message.usage_metadata:
                self.input_tokens += message.usage_metadata["input_tokens"]
                self.output_tokens += message.usage_metadata["output_tokens"]
            for asked in message.tool_calls:
                self._pending[str(asked["id"])] = {
                    "name": asked["name"],
                    "arguments": asked["args"],
                }
            return []
        if isinstance(message, ToolMessage):
            unknown = {"name": message.name or "", "arguments": {}}
            call = self._pending.pop(message.tool_call_id, unknown)
            return [
                event("tool_call", call_id=message.tool_call_id, **call, result=_result(message))
            ]
        return []

    def finish(self) -> list[dict[str, Any]]:
        metrics = event(
            "metrics",
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            model_route=self.model_route,
            attempt=1,
        )
        return [metrics, event("end", status="ok")]


def _leaf(exc: BaseException) -> BaseException:
    """The first real error inside the exception groups anyio wraps MCP failures in."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


def error_event(exc: BaseException) -> dict[str, Any]:
    """One `error` event. HTTP status `http_<status>` (retryable for 5xx), timeouts `timeout`,
    other transport errors `connect_error` (both retryable), anything else `model_error`
    (suggested: the code name).
    """
    exc = _leaf(exc)
    message = str(exc) or type(exc).__name__
    status: int | None = None
    if isinstance(exc, openai.APIStatusError):
        status = exc.status_code
    elif isinstance(exc, httpx2.HTTPStatusError):
        status = exc.response.status_code
    if status is not None:
        return event("error", code=f"http_{status}", message=message, retryable=status >= 500)
    if isinstance(exc, openai.APITimeoutError | httpx2.TimeoutException | TimeoutError):
        return event("error", code="timeout", message=message, retryable=True)
    if isinstance(exc, openai.APIConnectionError | httpx2.HTTPError):
        return event("error", code="connect_error", message=message, retryable=True)
    return event("error", code="model_error", message=message, retryable=False)
