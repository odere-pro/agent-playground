"""ScriptedModel: a ModelPort that answers from a script. Streaming, tool calls, and scripted
errors.
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
    """`match` is a substring of the last user message; the first matching rule wins. `None`
    matches all.
    """

    match: str | None = None
    reply: str = ""
    tool_call: ToolCallRequest | None = None
    usage: Usage = Field(default_factory=lambda: Usage(input_tokens=10, output_tokens=5))
    error: str | None = None
    """When set, the call raises ModelError(error, ...) instead of answering."""
    retryable: bool = False


def _last_user(messages: Sequence[ModelMessage]) -> str:
    for m in reversed(messages):
        if m["role"] == "user":
            return m["content"]
    return ""


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
        prompt = _last_user(messages)
        for rule in self.rules:
            if rule.match is None or rule.match in prompt:
                return rule
        return ScriptRule(reply=self.default_reply)

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
