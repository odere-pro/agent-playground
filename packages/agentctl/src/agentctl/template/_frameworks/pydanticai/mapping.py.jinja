"""PydanticAI stream events to chassis events (`packages/chassis/schemas/events.v0.json`).

The one framework-specific mapping in this workload, kept in its own file so its size can be
counted (PoC-2 exit criterion). Input: what `Agent.run_stream_events` yields. Output: event dicts
in the wire form, `schema_version: "0"` on each.

- `PartStartEvent` with a `TextPart`, and `PartDeltaEvent` with a `TextPartDelta`: one `delta`.
- `FunctionToolCallEvent` is held until its `FunctionToolResultEvent`, then one `tool_call` with
  `call_id`, `name`, `arguments`, and `result`.
- `AgentRunResultEvent`: `metrics` from the run's usage, then `end {status: ok}`.
- A failure: one `error` (`error_event`), with the same codes as echo-python.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import httpx2
from openai import APITimeoutError
from pydantic_ai import (
    AgentRunResultEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.exceptions import ModelHTTPError, UnexpectedModelBehavior

SCHEMA_VERSION = "0"
_TIMEOUTS = (httpx2.TimeoutException, httpx.TimeoutException, APITimeoutError, TimeoutError)
_TRANSPORT = (httpx2.TransportError, httpx.TransportError)


def event(kind: str, **fields: Any) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "type": kind, **fields}


class EventMapper:
    """Turns one run's PydanticAI events into chassis events. One instance per run."""

    def __init__(self, route: str) -> None:
        self.route = route
        self._pending: dict[str, ToolCallPart] = {}

    def map(self, ev: Any) -> list[dict[str, Any]]:
        if isinstance(ev, PartStartEvent) and isinstance(ev.part, TextPart) and ev.part.content:
            return [event("delta", text=ev.part.content)]
        if isinstance(ev, PartDeltaEvent) and isinstance(ev.delta, TextPartDelta):
            return [event("delta", text=ev.delta.content_delta)] if ev.delta.content_delta else []
        if isinstance(ev, FunctionToolCallEvent):
            self._pending[ev.part.tool_call_id] = ev.part
            return []
        if isinstance(ev, FunctionToolResultEvent):
            call = self._pending.pop(ev.part.tool_call_id, None)
            return [
                event(
                    "tool_call",
                    call_id=ev.part.tool_call_id,
                    name=call.tool_name if call else ev.part.tool_name,
                    arguments=call.args_as_dict() if call else {},
                    result=_result(ev.part),
                )
            ]
        if isinstance(ev, AgentRunResultEvent):
            usage = ev.result.usage
            return [
                event(
                    "metrics",
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    model_route=self.route,
                    attempt=1,
                ),
                event("end", status="ok"),
            ]
        return []


def _result(part: Any) -> dict[str, Any]:
    """A tool result as a JSON object: the MCP structured content, or `{"error": ...}` when the
    tool failed (a `RetryPromptPart`, which PydanticAI sends back to the model).
    """
    if isinstance(part, ToolReturnPart):
        structured = part.structured_content()
        if isinstance(structured, dict):
            return structured
        return part.model_response_object()
    return {"error": str(getattr(part, "content", "tool failed"))}


def _chain(exc: BaseException) -> Iterator[BaseException]:
    """The exception, its causes and contexts, and the members of any exception group."""
    seen: set[int] = set()
    stack = [exc]
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        if isinstance(current, BaseExceptionGroup):
            stack.extend(current.exceptions)
        stack.extend(e for e in (current.__cause__, current.__context__) if e is not None)


def error_event(exc: BaseException) -> dict[str, Any]:
    """One `error`: `http_<status>` (retryable for 5xx), `timeout` and `connect_error`
    (retryable), `bad_response`, else `model_error`. Never raises.
    """
    chain = list(_chain(exc))
    for e in chain:
        if isinstance(e, ModelHTTPError):
            detail = e.body.get("detail") if isinstance(e.body, dict) else e.body
            return _error(f"http_{e.status_code}", str(detail or e), e.status_code >= 500)
    for e in chain:
        if isinstance(e, _TIMEOUTS):
            return _error("timeout", str(e) or "call timed out", True)
    for e in chain:
        if isinstance(e, _TRANSPORT):
            return _error("connect_error", str(e) or type(e).__name__, True)
    if any(isinstance(e, UnexpectedModelBehavior) for e in chain):
        return _error("bad_response", str(exc) or type(exc).__name__, False)
    return _error("model_error", str(exc) or type(exc).__name__, False)


def _error(code: str, message: str, retryable: bool) -> dict[str, Any]:
    return event("error", code=code, message=message[:500], retryable=retryable)
