"""OpenAI Agents SDK stream events to chassis events (`packages/chassis/schemas/events.v0.json`).

The one framework-specific mapping in this workload, kept in its own file so its size can be
counted (PoC-6 exit criterion). Input: what `Runner.run_streamed(...).stream_events()` yields.
Output: event dicts in the wire form, `schema_version: "0"` on each.

- `RawResponsesStreamEvent` whose data is a `response.output_text.delta`: one `delta`. (The chat
  completions model turns each chunk into Responses-style events; every other raw event is
  bookkeeping and maps to nothing.)
- `RunItemStreamEvent` `tool_called` is held until its `tool_output`, then one `tool_call` with
  `call_id`, `name`, `arguments`, and `result`.
- The end of the stream: `metrics` from the run's usage (summed over every model call), then
  `end {status: ok}` (`EventMapper.finish`).
- A failure: one `error` (`error_event`), with the same codes as echo-python and echo-pydanticai.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx
import httpx2
import openai
from agents import ModelBehaviorError, RunItemStreamEvent, Usage
from agents.exceptions import MaxTurnsExceeded
from agents.stream_events import RawResponsesStreamEvent

from echo_openai_agents.tools import ToolFailed

SCHEMA_VERSION = "0"
_TIMEOUTS = (httpx2.TimeoutException, httpx.TimeoutException, openai.APITimeoutError, TimeoutError)
_TRANSPORT = (httpx2.TransportError, httpx.TransportError, openai.APIConnectionError)
_BAD_RESPONSE = (ModelBehaviorError, openai.APIResponseValidationError, json.JSONDecodeError)


class ToolLoopExceeded(Exception):
    """The model asked for a tool round beyond the allowed number."""


def event(kind: str, **fields: Any) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "type": kind, **fields}


class EventMapper:
    """Turns one run's SDK events into chassis events. One instance per run."""

    def __init__(self, route: str) -> None:
        self.route = route
        self._pending: dict[str, tuple[str, dict[str, Any]]] = {}

    def map(self, ev: Any) -> list[dict[str, Any]]:
        if isinstance(ev, RawResponsesStreamEvent):
            if ev.data.type == "response.output_text.delta" and ev.data.delta:
                return [event("delta", text=ev.data.delta)]
            return []
        if not isinstance(ev, RunItemStreamEvent):
            return []
        if ev.name == "tool_called":
            raw = ev.item.raw_item
            call_id = str(_field(raw, "call_id"))
            self._pending[call_id] = (
                str(_field(raw, "name")),
                _arguments(_field(raw, "arguments")),
            )
            return []
        if ev.name == "tool_output":
            call_id = str(_field(ev.item.raw_item, "call_id"))
            name, arguments = self._pending.pop(call_id, ("", {}))
            return [
                event(
                    "tool_call",
                    call_id=call_id,
                    name=name,
                    arguments=arguments,
                    result=_result(getattr(ev.item, "output", None)),
                )
            ]
        return []

    def finish(self, usage: Usage) -> list[dict[str, Any]]:
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


def _field(raw: Any, key: str) -> Any:
    """A field of a raw item, which is a pydantic model or a typed dict."""
    return raw.get(key) if isinstance(raw, dict) else getattr(raw, key, None)


def _arguments(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(value) if isinstance(value, str) and value else value
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _result(output: Any) -> dict[str, Any]:
    """A tool result as a JSON object. The MCP server hands over the structured content as a JSON
    string; anything else becomes `{"text": ...}`, because an event's `result` is an object."""
    if isinstance(output, dict) and output.get("type") == "text":
        output = output.get("text")
    if isinstance(output, str):
        try:
            parsed = json.loads(output)
        except ValueError:
            return {"text": output}
        return parsed if isinstance(parsed, dict) else {"text": output}
    return output if isinstance(output, dict) else {"text": str(output)}


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
    """One `error`: `tool_loop_exceeded`, `tool_error`, `http_<status>` (retryable for 5xx),
    `timeout` and `connect_error` (retryable), `bad_response`, else `model_error`. Never raises.
    """
    chain = list(_chain(exc))
    for e in chain:
        if isinstance(e, ToolLoopExceeded | MaxTurnsExceeded):
            return _error("tool_loop_exceeded", "the model asked for too many tool rounds", False)
    for e in chain:
        if isinstance(e, ToolFailed):
            return _error("tool_error", str(e), False)
    for e in chain:
        if isinstance(e, openai.APIStatusError):
            body = e.body
            detail = body.get("detail") or body.get("message") if isinstance(body, dict) else body
            return _error(f"http_{e.status_code}", str(detail or e.message), e.status_code >= 500)
    for e in chain:
        if isinstance(e, _TIMEOUTS):
            return _error("timeout", str(e) or "call timed out", True)
    for e in chain:
        if isinstance(e, _TRANSPORT):
            return _error("connect_error", str(e) or type(e).__name__, True)
    if any(isinstance(e, _BAD_RESPONSE) for e in chain):
        return _error("bad_response", str(exc) or type(exc).__name__, False)
    return _error("model_error", str(exc) or type(exc).__name__, False)


def _error(code: str, message: str, retryable: bool) -> dict[str, Any]:
    return event("error", code=code, message=message[:500], retryable=retryable)
