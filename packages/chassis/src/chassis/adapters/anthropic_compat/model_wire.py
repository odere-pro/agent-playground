"""The Anthropic Messages wire format for the model proxy route (`POST /v1/messages` on the proxy
port; contract v5, part A). Pure: no I/O, no web framework, no SDK call.

This is the other side of the PoC-3 mapping in `inbound.py`. That one maps the request onto the
canonical `Request` and calls the agent. This one maps it onto `ModelMessage`, `ToolSpec`, and
`max_tokens`, to call the model. It reads the body by hand, because the Claude Agent SDK's CLI
sends things strict Anthropic refuses and the SDK types cannot hold: `role: "system"` inside
`messages`, a `system` list with a billing marker, unknown top-level keys.

- `parse_messages_request(raw)` returns `ParsedMessages` or raises `RefusedField` (a 400).
- `message_json(result, model, msg_id)` is the complete answer.
- `StreamEncoder` is the SSE answer: it owns the block indexes.
- `WireError`, `model_error_to_wire`, and `error_body` are the Anthropic error shape (A.8).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from chassis.adapters.openai_compat.messages import is_empty
from chassis.ports.model import (
    ModelChunk,
    ModelError,
    ModelMessage,
    ModelResult,
    ToolCallRequest,
    ToolSpec,
    Usage,
)

__all__ = [
    "BILLING_PREFIX",
    "BODY_CAP_BYTES",
    "DENIED_TEXT",
    "MODEL_MAX_CHARS",
    "TOO_LARGE",
    "ParsedMessages",
    "RefusedField",
    "StreamEncoder",
    "WireError",
    "error_body",
    "message_json",
    "model_error_to_wire",
    "new_ids",
    "parse_messages_request",
    "refusal_error",
]

MODEL_MAX_CHARS = 256  # suggested: a route name, which reaches labels, logs, and spans
BILLING_PREFIX = "x-anthropic-billing-header:"
DENIED_TEXT = "this model route is not allowed for this service"

# Top-level keys that are accepted and counted by their own name (A.5). Any other key is `other`.
_DROPPED = (
    "top_p",
    "top_k",
    "stop_sequences",
    "thinking",
    "context_management",
    "safeguards",
    "service_tier",
    "inference_geo",
    "diagnostics",
    "workspace_id",
)
_SILENT = ("metadata",)  # may identify a person: not read, not counted, not logged
_MAPPED = (
    "model",
    "messages",
    "system",
    "tools",
    "tool_choice",
    "max_tokens",
    "stream",
    "temperature",
    "output_config",
)
_REFUSED_KEYS = ("mcp_servers", "container")
_TOOL_DROPPED = (
    "defer_loading",
    "strict",
    "eager_input_streaming",
    "allowed_callers",
    "input_examples",
)
_TOOL_KEYS = ("type", "name", "description", "input_schema")


class RefusedField(ValueError):
    """A request the route refuses with 400 before the model is called. `code` is one of
    `invalid_body`, `unsupported_parameter`, `unsupported_message`; `param` names the field.
    """

    def __init__(self, code: str, param: str, message: str) -> None:
        super().__init__(f"{code}: {param}: {message}")
        self.code = code
        self.param = param
        self.message = message

    def text(self) -> str:
        """The error text: the field, then why."""
        return f"{self.param}: {self.message}"


@dataclass
class ParsedMessages:
    """What the route needs from the body. `ignored` lists each dropped `param` once."""

    route: str
    messages: list[ModelMessage]
    tools: list[ToolSpec] | None
    max_tokens: int
    temperature: float
    stream: bool
    ignored: list[str] = field(default_factory=list)


class _Parse:
    def __init__(self) -> None:
        self.ignored: dict[str, None] = {}
        self.tool_ids: dict[str, int] = {}  # id -> the latest assistant turn that used it

    def drop(self, param: str) -> None:
        self.ignored.setdefault(param, None)

    def cache_control(self, block: dict[str, Any]) -> None:
        if "cache_control" in block:
            self.drop("cache_control")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _text_block(p: _Parse, block: Any, param: str) -> str:
    """The text of a `text` block, or a refusal for anything else (used for `system` and for
    `tool_result` content)."""
    if not isinstance(block, dict) or block.get("type") != "text":
        raise RefusedField("unsupported_message", param, "this block type is not supported")
    p.cache_control(block)
    text = block.get("text")
    if not isinstance(text, str):
        raise RefusedField("invalid_body", f"{param}.text", "a string is required")
    return text


def _system(p: _Parse, system: Any) -> ModelMessage | None:
    if system is None:
        return None
    if isinstance(system, str):
        parts = [system]
    elif isinstance(system, list):
        parts = []
        for j, block in enumerate(system):
            text = _text_block(p, block, f"system[{j}]")
            if text.startswith(BILLING_PREFIX):
                p.drop("system.billing_header")
                continue
            parts.append(text)
    else:
        raise RefusedField("invalid_body", "system", "a string or a list of text blocks")
    joined = "\n".join(parts)
    return ModelMessage(role="system", content=joined) if joined else None


def _tool_result_text(p: _Parse, block: dict[str, Any], param: str) -> str:
    content = block.get("content")
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(_text_block(p, b, f"{param}.content[{k}]") for k, b in enumerate(content))
    raise RefusedField("invalid_body", f"{param}.content", "a string or a list of text blocks")


def _empty_user(i: int) -> RefusedField:
    return RefusedField("invalid_body", f"messages[{i}].content", "a user turn needs content")


def _user_turn(p: _Parse, i: int, content: Any) -> list[ModelMessage]:
    if isinstance(content, str):
        if not content:
            raise _empty_user(i)
        return [ModelMessage(role="user", content=content)]
    out: list[ModelMessage] = []
    texts: list[str] = []
    for j, block in enumerate(content):
        param = f"messages[{i}].content[{j}]"
        if not isinstance(block, dict):
            raise RefusedField("unsupported_message", param, "a block must be an object")
        kind = block.get("type")
        p.cache_control(block)
        if kind == "text":
            text = block.get("text")
            if not isinstance(text, str):
                raise RefusedField("invalid_body", f"{param}.text", "a string is required")
            texts.append(text)
        elif kind == "tool_result":
            if texts:
                raise RefusedField(
                    "unsupported_message", param, "tool_result must come before any text"
                )
            tool_id = block.get("tool_use_id")
            if not isinstance(tool_id, str) or tool_id not in p.tool_ids:
                raise RefusedField(
                    "unsupported_message", param, "tool_result: no earlier tool_use has this id"
                )
            if "is_error" in block:
                p.drop("tool_result.is_error")
            out.append(
                ModelMessage(
                    role="tool", tool_call_id=tool_id, content=_tool_result_text(p, block, param)
                )
            )
        elif kind in ("thinking", "redacted_thinking"):
            p.drop("content.thinking")
        elif kind == "tool_use":
            raise RefusedField("unsupported_message", param, "tool_use is for assistant turns")
        else:
            raise RefusedField("unsupported_message", param, "this block type is not supported")
    joined = "\n".join(texts)
    if joined:
        out.append(ModelMessage(role="user", content=joined))
    if not out:
        raise _empty_user(i)
    return out


def _assistant_turn(p: _Parse, i: int, content: Any) -> ModelMessage | None:
    """None when the turn is empty (after thinking blocks are dropped): it is skipped."""
    if isinstance(content, str):
        return ModelMessage(role="assistant", content=content) if content else None
    texts: list[str] = []
    calls: list[ToolCallRequest] = []
    for j, block in enumerate(content):
        param = f"messages[{i}].content[{j}]"
        if not isinstance(block, dict):
            raise RefusedField("unsupported_message", param, "a block must be an object")
        kind = block.get("type")
        p.cache_control(block)
        if kind == "text":
            text = block.get("text")
            if not isinstance(text, str):
                raise RefusedField("invalid_body", f"{param}.text", "a string is required")
            texts.append(text)
        elif kind == "tool_use":
            call_id, name, tool_input = block.get("id"), block.get("name"), block.get("input")
            if not isinstance(call_id, str) or not call_id:
                raise RefusedField("invalid_body", f"{param}.id", "a non-empty string")
            if not isinstance(name, str) or not name:
                raise RefusedField("invalid_body", f"{param}.name", "a non-empty string")
            if not isinstance(tool_input, dict):
                raise RefusedField(
                    "unsupported_message", f"{param}.input", "tool_use input must be an object"
                )
            p.tool_ids[call_id] = i
            calls.append(ToolCallRequest(call_id=call_id, name=name, arguments=tool_input))
        elif kind in ("thinking", "redacted_thinking"):
            p.drop("content.thinking")
        else:
            raise RefusedField("unsupported_message", param, "this block type is not supported")
    joined = "\n".join(texts)
    if calls:
        return ModelMessage(role="assistant", content=joined or None, tool_calls=calls)
    return ModelMessage(role="assistant", content=joined) if joined else None


def _system_turn(p: _Parse, i: int, content: Any) -> ModelMessage | None:
    """None when the entry is empty: it is skipped."""
    if isinstance(content, str):
        texts = [content]
    else:
        texts = [_text_block(p, b, f"messages[{i}].content[{j}]") for j, b in enumerate(content)]
    joined = "\n".join(texts)
    return ModelMessage(role="system", content=joined) if joined else None


def _messages(p: _Parse, raw: Any) -> list[ModelMessage]:
    if not isinstance(raw, list) or not raw:
        raise RefusedField("invalid_body", "messages", "a non-empty list is required")
    out: list[ModelMessage] = []
    last_role = ""
    for i, turn in enumerate(raw):
        if not isinstance(turn, dict):
            raise RefusedField("invalid_body", f"messages[{i}]", "an object is required")
        role, content = turn.get("role"), turn.get("content")
        if role not in ("user", "assistant", "system"):
            raise RefusedField(
                "unsupported_message", f"messages[{i}].role", "role must be user, assistant, system"
            )
        if not isinstance(content, str | list):
            raise RefusedField(
                "invalid_body", f"messages[{i}].content", "a string or a list of blocks"
            )
        last_role = role
        if role == "user":
            out.extend(_user_turn(p, i, content))
        else:
            turn_message = (
                _assistant_turn(p, i, content)
                if role == "assistant"
                else _system_turn(p, i, content)
            )
            if turn_message is not None:
                out.append(turn_message)
    if last_role == "assistant":
        raise RefusedField(
            "unsupported_message",
            f"messages[{len(raw) - 1}].role",
            "assistant prefill is not supported",
        )
    if not out:
        raise RefusedField("invalid_body", "messages", "no turn has any content")
    return out


def _tools(p: _Parse, raw: Any) -> list[ToolSpec]:
    if is_empty(raw):
        return []
    if not isinstance(raw, list):
        raise RefusedField("invalid_body", "tools", "a list is required")
    out: list[ToolSpec] = []
    for i, tool in enumerate(raw):
        param = f"tools[{i}]"
        if not isinstance(tool, dict):
            raise RefusedField("invalid_body", param, "an object is required")
        if tool.get("type", "custom") != "custom":
            raise RefusedField(
                "unsupported_parameter", f"{param}.type", "only custom tools are supported"
            )
        name = tool.get("name")
        if not isinstance(name, str) or not name:
            raise RefusedField("invalid_body", f"{param}.name", "a non-empty string is required")
        description = tool.get("description")
        if description is not None and not isinstance(description, str):
            raise RefusedField("invalid_body", f"{param}.description", "a string is required")
        schema = tool.get("input_schema", {"type": "object"})
        if not isinstance(schema, dict):
            raise RefusedField("invalid_body", f"{param}.input_schema", "an object is required")
        p.cache_control(tool)
        for key in _TOOL_DROPPED:
            if key in tool:
                p.drop(f"tools.{key}")
        if any(k not in _TOOL_KEYS + _TOOL_DROPPED + ("cache_control",) for k in tool):
            p.drop("other")
        # Top level only: a nested `$schema` stays.
        parameters = {k: v for k, v in schema.items() if k != "$schema"}
        out.append(ToolSpec(name=name, description=description or "", parameters=parameters))
    return out


def _tool_choice(p: _Parse, raw: Any) -> bool:
    """True when tools are sent to the model; False for `tool_choice: none`."""
    if raw is None:
        return True
    if not isinstance(raw, dict):
        raise RefusedField("invalid_body", "tool_choice", "an object is required")
    kind = raw.get("type")
    if kind not in ("auto", "none"):
        raise RefusedField(
            "unsupported_parameter", "tool_choice", "only auto and none are supported"
        )
    if "disable_parallel_tool_use" in raw:
        p.drop("tool_choice.disable_parallel_tool_use")
    return bool(kind == "auto")


def _output_config(p: _Parse, raw: Any) -> None:
    if raw is None:
        return
    if not isinstance(raw, dict):
        raise RefusedField("invalid_body", "output_config", "an object is required")
    if raw.get("format") is not None:
        raise RefusedField(
            "unsupported_parameter", "output_config.format", "structured output is not supported"
        )
    if "effort" in raw:
        p.drop("output_config.effort")
    if any(k not in ("effort", "format") for k in raw):
        p.drop("other")


def parse_messages_request(raw: Any) -> ParsedMessages:
    """Read a `/v1/messages` body by hand (contract v5, A.5). Raises `RefusedField`, and nothing
    else: a message the port rejects is a 400, never a 500."""
    try:
        return _parse(raw)
    except ValidationError as exc:
        raise RefusedField("invalid_body", "messages", "a message could not be built") from exc


def _parse(raw: Any) -> ParsedMessages:
    if not isinstance(raw, dict):
        raise RefusedField("invalid_body", "body", "a JSON object is required")
    p = _Parse()
    model = raw.get("model")
    if not isinstance(model, str) or not model:
        raise RefusedField("invalid_body", "model", "a non-empty string is required")
    if len(model) > MODEL_MAX_CHARS:
        raise RefusedField("invalid_body", "model", f"at most {MODEL_MAX_CHARS} characters")
    max_tokens = raw.get("max_tokens")
    if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens < 1:
        raise RefusedField("invalid_body", "max_tokens", "a positive integer is required")
    stream = raw.get("stream", False)
    if not isinstance(stream, bool):
        raise RefusedField("invalid_body", "stream", "a boolean is required")
    temperature = raw.get("temperature")
    if temperature is None:
        temperature = 0.0  # suggested: the chat route's default
    elif (
        isinstance(temperature, bool)
        or not isinstance(temperature, int | float)
        or not 0 <= temperature <= 1
    ):
        raise RefusedField("invalid_body", "temperature", "a number from 0 to 1 is required")
    for key in _REFUSED_KEYS:
        if key in raw:
            raise RefusedField("unsupported_parameter", key, f"{key} is not supported")
    _output_config(p, raw.get("output_config"))
    send_tools = _tool_choice(p, raw.get("tool_choice"))
    system = _system(p, raw.get("system"))
    tools = _tools(p, raw.get("tools"))
    messages = _messages(p, raw.get("messages"))
    if system is not None:
        messages.insert(0, system)
    for key in raw:
        if key in _DROPPED:
            p.drop(key)
        elif key not in _MAPPED and key not in _SILENT and key not in _REFUSED_KEYS:
            p.drop("other")
    return ParsedMessages(
        route=model,
        messages=messages,
        tools=tools if send_tools and tools else None,
        max_tokens=max_tokens,
        temperature=float(temperature),
        stream=stream,
        ignored=list(p.ignored),
    )


# ---- errors (A.8) -----------------------------------------------------------------------------


@dataclass(frozen=True)
class WireError:
    """One row of the A.8 table. `message` is the text after `<code>: `."""

    status: int
    type: str
    code: str
    message: str
    retryable: bool


def error_body(err: WireError, request_id: str) -> dict[str, Any]:
    """`{"type":"error","error":{"type":T,"message":"<code>: <text>"},"request_id":...}`."""
    return {
        "type": "error",
        "error": {"type": err.type, "message": f"{err.code}: {err.message}"},
        "request_id": request_id,
    }


TOO_LARGE = WireError(
    413, "request_too_large", "body_too_large", "the request body is over the size limit", False
)
BODY_CAP_BYTES = 4 * 1024 * 1024  # suggested


def refusal_error(refused: RefusedField) -> WireError:
    return WireError(400, "invalid_request_error", refused.code, refused.text(), False)


def model_error_to_wire(exc: ModelError) -> WireError:
    """Map a `ModelError` onto the A.8 table. Every row sends a fixed text: no upstream text
    reaches the remote. The route logs the upstream text at the chassis instead.
    """
    code = exc.code
    status = int(code[5:]) if code.startswith("http_") and code[5:].isdigit() else 0
    if status in (400, 413, 422):
        text = "the model route rejected the request"
        return WireError(400, "invalid_request_error", "model_rejected_request", text, False)
    if status in (401, 403):
        return WireError(403, "permission_error", "model_route_denied", DENIED_TEXT, False)
    if status == 429:
        text = "the model route is rate limited; retry later"
        return WireError(429, "rate_limit_error", "model_rate_limited", text, True)
    if status in (503, 529):
        text = "the model route is overloaded; retry later"
        return WireError(503, "overloaded_error", "model_overloaded", text, True)
    if code == "timeout":
        text = "the model route did not answer in time"
        return WireError(504, "timeout_error", "model_timeout", text, True)
    if exc.retryable:
        text = "the model route could not be reached; retry later"
        return WireError(502, "api_error", "model_unavailable", text, True)
    return WireError(500, "api_error", "model_error", "the model call failed", False)


# ---- answers (A.7) ----------------------------------------------------------------------------


def new_ids(hex24: str) -> tuple[str, str]:
    """`(msg_<24 hex>, req_<24 hex>)` from one 24-hex string."""
    return f"msg_{hex24}", f"req_{hex24}"


def _usage(usage: Usage | None) -> dict[str, int]:
    u = usage or Usage()
    return {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens}


def message_json(result: ModelResult, model: str, msg_id: str) -> dict[str, Any]:
    """The complete answer (A.7.2): the text block first (omitted when empty), then one
    `tool_use` per call."""
    content: list[dict[str, Any]] = []
    if result.text:
        content.append({"type": "text", "text": result.text})
    content.extend(
        {"type": "tool_use", "id": c.call_id, "name": c.name, "input": c.arguments}
        for c in result.tool_calls
    )
    return {
        "id": msg_id,
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": "tool_use" if result.tool_calls else "end_turn",
        "stop_sequence": None,
        "usage": _usage(result.usage),
    }


def _frame(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


class StreamEncoder:
    """The SSE answer (A.7.1). It owns the block indexes: 0, 1, 2, never repeated.

    Call `start()` then `ping()`, then `chunk()` for each `ModelChunk`, then `finish()`. After an
    error, call `error()` and stop: there is no `message_stop`.
    """

    def __init__(self, msg_id: str, model: str) -> None:
        self._id = msg_id
        self._model = model
        self._next = 0
        self._text_index: int | None = None
        self._tools = 0
        self._usage: Usage | None = None

    def start(self) -> str:
        message: dict[str, Any] = {
            "id": self._id,
            "type": "message",
            "role": "assistant",
            "model": self._model,
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 1},
        }
        return _frame("message_start", {"type": "message_start", "message": message})

    def ping(self) -> str:
        return _frame("ping", {"type": "ping"})

    def _close_text(self) -> list[str]:
        if self._text_index is None:
            return []
        frame = _frame(
            "content_block_stop", {"type": "content_block_stop", "index": self._text_index}
        )
        self._text_index = None
        return [frame]

    def chunk(self, chunk: ModelChunk) -> list[str]:
        frames: list[str] = []
        if chunk.text:
            if self._text_index is None:
                self._text_index = self._next
                self._next += 1
                block = {"type": "text", "text": ""}
                frames.append(
                    _frame(
                        "content_block_start",
                        {
                            "type": "content_block_start",
                            "index": self._text_index,
                            "content_block": block,
                        },
                    )
                )
            delta = {"type": "text_delta", "text": chunk.text}
            frames.append(
                _frame(
                    "content_block_delta",
                    {"type": "content_block_delta", "index": self._text_index, "delta": delta},
                )
            )
        if chunk.tool_call is not None:
            frames.extend(self._close_text())
            call = chunk.tool_call
            index = self._next
            self._next += 1
            self._tools += 1
            start = {"type": "tool_use", "id": call.call_id, "name": call.name, "input": {}}
            partial = json.dumps(call.arguments, separators=(",", ":"))
            frames.append(
                _frame(
                    "content_block_start",
                    {"type": "content_block_start", "index": index, "content_block": start},
                )
            )
            delta = {"type": "input_json_delta", "partial_json": partial}
            frames.append(
                _frame(
                    "content_block_delta",
                    {"type": "content_block_delta", "index": index, "delta": delta},
                )
            )
            frames.append(
                _frame("content_block_stop", {"type": "content_block_stop", "index": index})
            )
        if chunk.usage is not None:
            self._usage = chunk.usage
        return frames

    def finish(self) -> list[str]:
        frames = self._close_text()
        delta = {"stop_reason": "tool_use" if self._tools else "end_turn", "stop_sequence": None}
        frames.append(
            _frame(
                "message_delta",
                {"type": "message_delta", "delta": delta, "usage": _usage(self._usage)},
            )
        )
        frames.append(_frame("message_stop", {"type": "message_stop"}))
        return frames

    def error(self, err: WireError) -> str:
        """One `event: error` frame, with the same `type` and `message` as the HTTP body."""
        body = {"type": err.type, "message": f"{err.code}: {err.message}"}
        return _frame("error", {"type": "error", "error": body})
