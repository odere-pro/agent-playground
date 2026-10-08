"""ScriptedModel: a ModelPort that answers from a script. Streaming, tool calls, scripted errors,
and a tool loop that ends. It records the messages of every call in `.calls`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

from pydantic import BaseModel, Field

from chassis.ports.model import (
    ModelChunk,
    ModelError,
    ModelMessage,
    ModelResult,
    ToolCallRequest,
    ToolSpec,
    Usage,
)


class ScriptRule(BaseModel):
    """The first matching rule wins; when none matches, the model answers `default_reply`.

    A rule without `after_tool` matches only when the last message is not a `tool` message, and
    `match` is a substring of the last user message. A rule with `after_tool=True` (suggested)
    matches only when the last message is a `tool` message, and `match` is tested against that
    tool content. `match=None` matches any text. So a rule that calls a tool is never picked again
    on the tool's result, and a scripted tool loop ends.
    """

    match: str | None = None
    after_tool: bool = False
    reply: str = ""
    tool_call: ToolCallRequest | None = None
    usage: Usage = Field(default_factory=lambda: Usage(input_tokens=10, output_tokens=5))
    error: str | None = None
    """When set, the call raises ModelError(error, ...) instead of answering."""
    retryable: bool = False


def _last_user(messages: Sequence[ModelMessage]) -> str:
    for m in reversed(messages):
        if m.role == "user":
            return m.content or ""
    return ""


def _pick_rule(rules: Sequence[ScriptRule], messages: Sequence[ModelMessage]) -> ScriptRule | None:
    """The first rule that matches `messages`, or `None`. See `ScriptRule`."""
    after_tool = bool(messages) and messages[-1].role == "tool"
    text = (messages[-1].content or "") if after_tool else _last_user(messages)
    for rule in rules:
        if rule.after_tool == after_tool and (rule.match is None or rule.match in text):
            return rule
    return None


def _tokens(text: str) -> list[str]:
    """Split so that the pieces join back to the exact text."""
    out: list[str] = []
    word = ""
    for ch in text:
        word += ch
        if ch == " ":
            out.append(word)
            word = ""
    if word:
        out.append(word)
    return out


class ScriptedModel:
    name = "fake"

    def __init__(
        self, rules: Sequence[ScriptRule] | None = None, default_reply: str = "ok"
    ) -> None:
        self.rules = list(rules or [])
        self.default_reply = default_reply
        self.calls: list[list[ModelMessage]] = []

    def _pick(self, messages: Sequence[ModelMessage]) -> ScriptRule:
        return _pick_rule(self.rules, messages) or ScriptRule(reply=self.default_reply)

    async def complete(
        self,
        messages: Sequence[ModelMessage],
        *,
        route: str,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ModelResult:
        self.calls.append(list(messages))
        rule = self._pick(messages)
        if rule.error:
            raise ModelError(
                rule.error, f"scripted error for route {route}", retryable=rule.retryable
            )
        return ModelResult(
            text=rule.reply,
            tool_calls=[rule.tool_call] if rule.tool_call else [],
            usage=rule.usage,
            model=route,
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
        self.calls.append(list(messages))
        rule = self._pick(messages)
        if rule.error:
            raise ModelError(
                rule.error, f"scripted error for route {route}", retryable=rule.retryable
            )
        for piece in _tokens(rule.reply):
            yield ModelChunk(text=piece)
        if rule.tool_call:
            yield ModelChunk(tool_call=rule.tool_call)
        yield ModelChunk(usage=rule.usage, finish=True)
