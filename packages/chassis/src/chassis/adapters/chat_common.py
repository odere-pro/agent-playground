"""The format-neutral chat helpers both chat wire formats share: the OpenAI ones
(`openai_compat`, the OpenAI interface and the model proxy) and the Anthropic ones
(`anthropic_compat`, the Anthropic interface and the Anthropic proxy route). Pure: no SDK, no I/O.

- `UnsupportedMessage`: a message, block, or param the chassis will not carry; `param` names it.
- `is_empty`: null or empty, what SDKs echo back for a field they did not use.
- `text_of`: content as text, a string or text parts joined with `"\n"` (suggested).

`openai_compat.messages` re-exports all three under the same names, so its importers (the model
proxy among them) are unchanged.
"""

from __future__ import annotations

from typing import Any

__all__ = ["UnsupportedMessage", "is_empty", "text_of"]


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
