"""Claude Agent SDK messages to chassis events (`packages/chassis/schemas/events.v0.json`).

The one framework-specific mapping in this workload, kept in its own file so its size can be
counted (PoC-2 exit criterion). Input: what the SDK's `query()` yields. Output: event dicts in the
wire form, `schema_version: "0"` on each.

- `StreamEvent` (partial messages) with a `text_delta`: one `delta` per chunk. The `TextBlock` of
  the `AssistantMessage` that follows is then skipped, so no text is sent twice. Without partial
  messages the `TextBlock` is the `delta`.
- `ToolUseBlock` is held until its `ToolResultBlock` (in a `UserMessage`), then one `tool_call`
  with `call_id`, `name`, `arguments`, and `result`. The name loses its `mcp__<server>__` prefix
  (`glossary_lookup`); a built-in keeps its name (`Bash`).
- `ResultMessage`: `metrics` from its usage, then `end {status: ok}`. With `is_error` it is one
  `error` instead.
- Error codes: `http_<status>` (the status the CLI saw from the model route), `tool_loop_exceeded`
  (`error_max_turns`), `model_error`, `timeout`, and `cli_error` (the CLI did not start or died).
  `env_not_clean` comes from the guard in `handle.py`.
"""

from __future__ import annotations

import json
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeSDKError,
    ResultMessage,
    StreamEvent,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from claude_agent_sdk._errors import ResultError

SCHEMA_VERSION = "0"
MAX_MESSAGE = 500


def event(kind: str, **fields: Any) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "type": kind, **fields}


def tool_name(name: str) -> str:
    """`mcp__chassis__glossary_lookup` is `glossary_lookup`; `Bash` stays `Bash`."""
    parts = name.split("__", 2)
    return parts[2] if len(parts) == 3 and parts[0] == "mcp" and parts[2] else name


class EventMapper:
    """Turns one run's SDK messages into chassis events. One instance per run."""

    def __init__(self, route: str) -> None:
        self.route = route
        self.done = False
        self._pending: dict[str, ToolUseBlock] = {}
        self._streamed = False

    def map(self, message: Any) -> list[dict[str, Any]]:
        if self.done:
            return []
        if isinstance(message, StreamEvent):
            return self._partial(message)
        if isinstance(message, AssistantMessage):
            return self._assistant(message)
        if isinstance(message, UserMessage):
            return self._user(message)
        if isinstance(message, ResultMessage):
            self.done = True
            return self._result(message)
        return []

    def _partial(self, message: StreamEvent) -> list[dict[str, Any]]:
        if message.parent_tool_use_id is not None or message.event.get("type") != (
            "content_block_delta"
        ):
            return []
        delta = message.event.get("delta") or {}
        text = delta.get("text") if delta.get("type") == "text_delta" else None
        if not isinstance(text, str) or not text:
            return []
        self._streamed = True
        return [event("delta", text=text)]

    def _assistant(self, message: AssistantMessage) -> list[dict[str, Any]]:
        if message.parent_tool_use_id is not None:
            return []
        out: list[dict[str, Any]] = []
        for block in message.content:
            if isinstance(block, TextBlock) and block.text and not self._streamed:
                out.append(event("delta", text=block.text))
            elif isinstance(block, ToolUseBlock):
                self._pending[block.id] = block
        self._streamed = False
        return out

    def _user(self, message: UserMessage) -> list[dict[str, Any]]:
        if not isinstance(message.content, list):
            return []
        out: list[dict[str, Any]] = []
        for block in message.content:
            if isinstance(block, ToolResultBlock):
                call = self._pending.pop(block.tool_use_id, None)
                out.append(self._tool_call(block.tool_use_id, call, _result(block)))
        return out

    @staticmethod
    def _tool_call(
        call_id: str, call: ToolUseBlock | None, result: dict[str, Any]
    ) -> dict[str, Any]:
        return event(
            "tool_call",
            call_id=call_id,
            name=tool_name(call.name) if call else "unknown",
            arguments=dict(call.input) if call else {},
            result=result,
        )

    def _result(self, message: ResultMessage) -> list[dict[str, Any]]:
        if message.is_error:
            return [result_error(message)]
        # A call the CLI never returned a result for (a run that ended mid-tool).
        out = [
            self._tool_call(call_id, call, {"error": "no result"})
            for call_id, call in self._pending.items()
        ]
        self._pending.clear()
        usage = message.usage or {}
        prompt = sum(
            int(usage.get(key) or 0)
            for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
        )
        out.append(
            event(
                "metrics",
                input_tokens=prompt,
                output_tokens=int(usage.get("output_tokens") or 0),
                latency_ms=int(message.duration_ms),
                model_route=self.route,
                attempt=1,
            )
        )
        out.append(event("end", status="ok"))
        return out


def _result(block: ToolResultBlock) -> dict[str, Any]:
    """A tool result as a JSON object: the text parsed as JSON when it is an object (the chassis
    tools answer with structured content), else `{"text": ...}`; `{"error": ...}` on failure.
    """
    content = block.content
    if isinstance(content, list):
        text = "".join(str(b.get("text", "")) for b in content if b.get("type") == "text")
    else:
        text = content or ""
    if block.is_error:
        return {"error": text}
    try:
        parsed = json.loads(text)
    except ValueError:
        return {"text": text}
    return parsed if isinstance(parsed, dict) else {"text": text}


def _error(code: str, message: str, retryable: bool) -> dict[str, Any]:
    return event("error", code=code, message=message[:MAX_MESSAGE], retryable=retryable)


def _classify(
    subtype: str | None, terminal_reason: str | None, status: int | None, text: str
) -> dict[str, Any]:
    if subtype == "error_max_turns" or terminal_reason == "max_turns":
        return _error("tool_loop_exceeded", text or "the turn limit was reached", False)
    if status is not None:
        return _error(f"http_{status}", text or f"HTTP {status}", status >= 500 or status == 429)
    return _error("model_error", text or "the run failed", False)


def result_error(message: ResultMessage) -> dict[str, Any]:
    """The `error` for a `ResultMessage` with `is_error`."""
    text = "; ".join(message.errors or []) or (message.result or "") or str(message.subtype)
    return _classify(message.subtype, message.terminal_reason, message.api_error_status, text)


def error_event(exc: BaseException) -> dict[str, Any]:
    """The `error` for an exception out of `query()`. Never raises."""
    text = str(exc) or type(exc).__name__
    if isinstance(exc, ResultError):
        return _classify(exc.subtype, exc.terminal_reason, exc.api_error_status, exc.result or text)
    if isinstance(exc, TimeoutError):
        return _error("timeout", text, True)
    if isinstance(exc, ClaudeSDKError):  # not found, no connection, died, bad JSON from the CLI
        return _error("cli_error", text, False)
    return _error("model_error", text, False)
