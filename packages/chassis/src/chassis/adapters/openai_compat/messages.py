"""The OpenAI chat message mapping, shared by the model proxy (`POST /v1/chat/completions` on the
proxy port, `chassis.server.model_proxy`) and the OpenAI interface (the same path on the public
port). Pure: no SDK, no I/O.

Each OpenAI message maps to one `ModelMessage`, tool loop included: a list of text parts is joined
with `"\n"` (suggested), an assistant's `tool_calls` become `ToolCallRequest`s with
`function.arguments` parsed to an object, a tool message keeps `tool_call_id`, and `name` is
carried on any role. A key the port does not carry is ignored when its value is null or empty
(`is_empty`), as SDKs echo `refusal`, `audio`, `function_call`, and `annotations` back. Anything
else raises `UnsupportedMessage`, whose `param` names it: `messages[<i>].<field>`.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from chassis.ports.model import ModelMessage, ToolCallRequest

__all__ = [
    "MESSAGE_KEYS",
    "ROLES",
    "UnsupportedMessage",
    "is_empty",
    "refuse_extra",
    "text_of",
    "to_port_message",
    "tool_calls_of",
]

ROLES = frozenset({"system", "user", "assistant", "tool"})
MESSAGE_KEYS = frozenset({"role", "content", "name", "tool_calls", "tool_call_id"})


class UnsupportedMessage(ValueError):
    """A message the port cannot carry unchanged. `param` names it: `messages[<i>].<field>`."""

    def __init__(self, param: str, message: str) -> None:
        super().__init__(f"{param}: {message}")
        self.param = param
        self.message = message

    def body(self) -> dict[str, Any]:
        return {
            "error": {
                "code": "unsupported_message",
                "type": "invalid_request_error",
                "param": self.param,
                "message": self.message,
            }
        }


def is_empty(value: Any) -> bool:
    """Null or empty: what SDKs echo back for a field they did not use."""
    return value is None or (isinstance(value, str | list | dict) and not value)


def refuse_extra(
    value: dict[str, Any], allowed: frozenset[str], param: str, *, by: str = "the model port"
) -> None:
    """Raise `UnsupportedMessage` for the first key outside `allowed` whose value is not empty:
    `"<key> is not supported by <by>"`. The default is the model proxy's wording; the OpenAI
    interface passes its own, since its client has never heard of a model port.
    """
    for key, item in value.items():
        if key not in allowed and not is_empty(item):
            raise UnsupportedMessage(f"{param}.{key}", f"{key} is not supported by {by}")


def text_of(value: Any, param: str) -> str | None:
    """The content as text: a string or null as is, text parts joined with `"\n"`. Any other
    part raises `UnsupportedMessage` at `param`.
    """
    if value is None or isinstance(value, str):
        return value
    if not isinstance(value, list):
        raise UnsupportedMessage(param, "content must be a string, a list of text parts, or null")
    texts: list[str] = []
    for j, part in enumerate(value):
        if not (
            isinstance(part, dict)
            and part.get("type") == "text"
            and isinstance(part.get("text"), str)
        ):
            kind = part.get("type") if isinstance(part, dict) else type(part).__name__
            raise UnsupportedMessage(
                param, f"content part {j} is {kind!r}; only text parts are supported"
            )
        texts.append(part["text"])
    return "\n".join(texts)


def tool_calls_of(value: Any, param: str) -> list[ToolCallRequest]:
    """An assistant's `tool_calls` as `ToolCallRequest`s, arguments parsed to an object."""
    if not isinstance(value, list):
        raise UnsupportedMessage(param, "tool_calls must be a list")
    out: list[ToolCallRequest] = []
    for j, call in enumerate(value):
        at = f"{param}[{j}]"
        if not isinstance(call, dict):
            raise UnsupportedMessage(at, "a tool call must be an object")
        refuse_extra(call, frozenset({"id", "type", "function"}), at)
        if call.get("type", "function") != "function":
            raise UnsupportedMessage(f"{at}.type", "only function tool calls are supported")
        call_id = call.get("id")
        if not isinstance(call_id, str) or not call_id:
            raise UnsupportedMessage(f"{at}.id", "a tool call needs a string id")
        function = call.get("function")
        if not isinstance(function, dict):
            raise UnsupportedMessage(f"{at}.function", "a tool call needs a function object")
        refuse_extra(function, frozenset({"name", "arguments"}), f"{at}.function")
        name = function.get("name")
        if not isinstance(name, str) or not name:
            raise UnsupportedMessage(f"{at}.function.name", "a tool call needs a function name")
        raw = function.get("arguments")
        try:
            arguments = json.loads(raw) if isinstance(raw, str) else None
        except ValueError:
            arguments = None
        if not isinstance(arguments, dict):
            raise UnsupportedMessage(
                f"{at}.function.arguments", "arguments must be a JSON object, as a string"
            )
        out.append(ToolCallRequest(call_id=call_id, name=name, arguments=arguments))
    return out


def to_port_message(index: int, raw: dict[str, Any]) -> ModelMessage:
    """One OpenAI message as a `ModelMessage`, or `UnsupportedMessage` naming what is refused."""
    at = f"messages[{index}]"
    refuse_extra(raw, MESSAGE_KEYS, at)
    role = raw.get("role")
    if role not in ROLES:
        raise UnsupportedMessage(
            f"{at}.role", f"role {role!r} is not supported; use system, user, assistant, or tool"
        )
    content = text_of(raw.get("content"), f"{at}.content")
    name = raw.get("name")
    if name is not None and not isinstance(name, str):
        raise UnsupportedMessage(f"{at}.name", "name must be a string")
    tool_calls: list[ToolCallRequest] | None = None
    if not is_empty(raw.get("tool_calls")):
        if role != "assistant":
            raise UnsupportedMessage(f"{at}.tool_calls", "tool_calls are allowed on assistant only")
        tool_calls = tool_calls_of(raw["tool_calls"], f"{at}.tool_calls")
    tool_call_id = raw.get("tool_call_id")
    if role == "tool":
        if not isinstance(tool_call_id, str) or not tool_call_id:
            raise UnsupportedMessage(f"{at}.tool_call_id", "a tool message needs tool_call_id")
    elif not is_empty(tool_call_id):
        raise UnsupportedMessage(f"{at}.tool_call_id", "tool_call_id is allowed on tool only")
    else:
        tool_call_id = None
    if content is None and not tool_calls:
        raise UnsupportedMessage(
            f"{at}.content",
            "content may be null only on an assistant message with tool_calls",
        )
    try:
        return ModelMessage(
            role=role,
            content=content,
            name=name or None,
            tool_calls=tool_calls,
            tool_call_id=tool_call_id,
        )
    except ValidationError as exc:
        raise UnsupportedMessage(at, str(exc)) from exc
