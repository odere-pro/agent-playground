"""smolagents run events to chassis events (`packages/chassis/schemas/events.v0.json`).

The one framework-specific mapping in this workload, kept in its own file so its size can be
counted (PoC-2 exit criterion). Input: what `CodeAgent.run(stream=True)` yields, plus the MCP calls
the generated code made. Output: event dicts in the wire form, `schema_version: "0"` on each.

- `FinalAnswerStep`: one `delta` with the final answer. The code the model writes, its logs, and
  its observations are NOT streamed (streaming fidelity: the user sees one delta at the end).
- Each MCP call the generated code makes: one `tool_call` (`tool_call`, called from the tool).
- `finish`: `metrics` from the agent's monitor (tokens summed over every model call), then
  `end {status: ok}`.
- A failure: one `error` (`error_event`), with the same codes as echo-python and echo-pydanticai.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import httpx
import httpx2
import openai
from smolagents.memory import FinalAnswerStep  # type: ignore[import-untyped]

SCHEMA_VERSION = "0"
_TIMEOUTS = (httpx2.TimeoutException, httpx.TimeoutException, openai.APITimeoutError, TimeoutError)
_TRANSPORT = (httpx2.TransportError, httpx.TransportError, openai.APIConnectionError)


class ToolLoopExceeded(Exception):
    """The agent used all its steps without calling `final_answer`."""


class ToolFailure(Exception):
    """An MCP `tools/call` failed (transport error or an `isError` result)."""


class BadResponse(Exception):
    """The model server answered 200 with a body that is not a usable chat completion."""


def event(kind: str, **fields: Any) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "type": kind, **fields}


class EventMapper:
    """Turns one run's smolagents events into chassis events. One instance per run."""

    def __init__(self, route: str, usage: Callable[[], tuple[int, int]]) -> None:
        self.route = route
        self._usage = usage

    def tool_call(
        self, call_id: str, name: str, arguments: dict[str, Any], result: dict[str, Any]
    ) -> dict[str, Any]:
        return event("tool_call", call_id=call_id, name=name, arguments=arguments, result=result)

    def map(self, step: Any) -> list[dict[str, Any]]:
        if isinstance(step, FinalAnswerStep):
            text = str(step.output) if step.output is not None else ""
            return [event("delta", text=text)] if text else []
        return []

    def finish(self) -> list[dict[str, Any]]:
        input_tokens, output_tokens = self._usage()
        return [
            event(
                "metrics",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                model_route=self.route,
                attempt=1,
            ),
            event("end", status="ok"),
        ]


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
    """One `error`: `tool_loop_exceeded`, `tool_error`, `bad_response`, `http_<status>`
    (retryable for 5xx), `timeout` and `connect_error` (retryable), else `model_error`. The
    smolagents wrappers (`AgentGenerationError`) are looked through to their cause. Never raises.
    """
    chain = list(_chain(exc))
    for e in chain:
        if isinstance(e, ToolLoopExceeded):
            return _error("tool_loop_exceeded", str(e), False)
    for e in chain:
        if isinstance(e, ToolFailure):
            return _error("tool_error", str(e), False)
    for e in chain:
        if isinstance(e, BadResponse):
            return _error("bad_response", str(e), False)
    for e in chain:
        if isinstance(e, openai.APIStatusError):
            return _error(f"http_{e.status_code}", _detail(e), e.status_code >= 500)
    for e in chain:
        if isinstance(e, _TIMEOUTS):
            return _error("timeout", str(e) or "call timed out", True)
    for e in chain:
        if isinstance(e, _TRANSPORT):
            return _error("connect_error", str(e) or type(e).__name__, True)
    return _error("model_error", str(exc) or type(exc).__name__, False)


def _detail(exc: openai.APIStatusError) -> str:
    body = exc.body
    detail = body.get("detail") if isinstance(body, dict) else body
    return str(detail or exc.message)


def _error(code: str, message: str, retryable: bool) -> dict[str, Any]:
    return event("error", code=code, message=message[:500], retryable=retryable)
