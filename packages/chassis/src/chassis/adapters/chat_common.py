"""The format-neutral chat helpers both chat wire formats share: the OpenAI ones
(`openai_compat`, the OpenAI interface and the model proxy) and the Anthropic ones
(`anthropic_compat`, the Anthropic interface and the Anthropic proxy route). Pure: no SDK, no I/O.

- `UnsupportedMessage`: a message, block, or param the chassis will not carry; `param` names it.
- `is_empty`: null or empty, what SDKs echo back for a field they did not use.
- `text_of`: content as text, a string or text parts joined with `"\n"` (suggested).
- `materialize`: a body read into plain values, every lazy iterator read once. FastAPI hands
  over lists inside an SDK body as `ValidatorIterator`s that validate each item when it is read;
  their `ValidationError` is raised as `InvalidBody`, and a body nested past Python's stack as
  `InvalidBody(TOO_DEEP_MESSAGE)`, so both chat interfaces answer 400 `invalid_body`, never a 500
  (contract v8, item 2). Locations use the bracket form of `param`: `messages[1].content`.

`openai_compat.messages` re-exports the first three under the same names, so its importers (the
model proxy among them) are unchanged. `anthropic_compat.inbound` re-exports `materialize`,
`InvalidBody`, and `TOO_DEEP_MESSAGE`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import ValidationError

__all__ = [
    "INVALID_PARTS_MAX",
    "TOO_DEEP_MESSAGE",
    "InvalidBody",
    "UnsupportedMessage",
    "is_empty",
    "materialize",
    "text_of",
    "validation_text",
]

TOO_DEEP_MESSAGE = "the body is nested too deeply to read"

INVALID_PARTS_MAX = 3
"""At most this many distinct `<loc>: <msg>` parts in an `InvalidBody` text (suggested)."""


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


def _location(path: str, loc: Iterable[Any]) -> str:
    """`path` and a pydantic `loc` in the bracket form: `messages[1].content`."""
    out = path or "body"
    for part in loc:
        out += f"[{part}]" if isinstance(part, int) else f".{part}"
    return out


def validation_text(path: str, exc: ValidationError) -> str:
    """At most `INVALID_PARTS_MAX` distinct `<loc>: <msg>` parts joined with `"; "`, then
    `"; ..."` when there are more. The input is never echoed."""
    parts: list[str] = []
    for error in exc.errors():
        part = f"{_location(path, error.get('loc', ()))}: {error.get('msg', 'invalid')}"
        if part not in parts:
            parts.append(part)
    if not parts:
        return f"{path or 'body'}: invalid"
    more = "; ..." if len(parts) > INVALID_PARTS_MAX else ""
    return "; ".join(parts[:INVALID_PARTS_MAX]) + more


class InvalidBody(ValueError):
    """A body that cannot be read: a lazy iterator raised `ValidationError`, or the body nests
    past Python's stack. `str()` names where and why, never the input."""


def _read(value: Any, path: str) -> Any:
    try:
        if isinstance(value, Mapping):
            return {
                key: _read(item, f"{path}.{key}" if path else str(key))
                for key, item in value.items()
            }
        if isinstance(value, str | bytes | bytearray) or not isinstance(value, Iterable):
            return value
        return [_read(item, f"{path}[{i}]") for i, item in enumerate(value)]
    except ValidationError as exc:
        raise InvalidBody(validation_text(path, exc)) from None


def materialize(value: Any, path: str = "") -> Any:
    """`value` with every mapping as a new dict and every other non-string iterable as a list,
    read once, all the way down; `value` itself is not changed. `path` is where `value` sits in
    the body (`""` for the body itself). Raises `InvalidBody`.
    """
    try:
        return _read(value, path)
    except RecursionError:  # nested past Python's stack: a 400, never a plain 500
        raise InvalidBody(TOO_DEEP_MESSAGE) from None
