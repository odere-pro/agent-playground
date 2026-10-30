"""A scripted `ModelPort` for the tool scenario: call the tool once, then answer."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

from chassis.ports.model import (
    ModelChunk,
    ModelMessage,
    ModelResult,
    ToolCallRequest,
    ToolSpec,
    Usage,
)

from poc_harness.scenario import AFTER_TOOL, NO_TOOL, ROUTE


class ToolLoopModel:
    """A `ModelPort` for the tool scenario: offered `glossary_lookup` and no tool result yet, it
    calls the tool; given a tool result, it answers `AFTER_TOOL`. Records what it saw.
    """

    name = "tool-loop"

    def __init__(self) -> None:
        self.calls: list[tuple[list[ModelMessage], list[str]]] = []

    def _answer(
        self, messages: Sequence[ModelMessage], tools: Sequence[ToolSpec] | None
    ) -> ModelResult:
        offered = [t.name for t in tools or []]
        self.calls.append((list(messages), offered))
        usage = Usage(input_tokens=10, output_tokens=5)
        if any(m.role == "tool" for m in messages):
            return ModelResult(text=AFTER_TOOL, usage=usage, model=ROUTE)
        if "glossary_lookup" in offered:
            call = ToolCallRequest(
                call_id="call_1", name="glossary_lookup", arguments={"term": "SLM"}
            )
            return ModelResult(text="", tool_calls=[call], usage=usage, model=ROUTE)
        return ModelResult(text=NO_TOOL, usage=usage, model=ROUTE)

    def tool_results(self) -> list[str]:
        return [m.content or "" for msgs, _ in self.calls for m in msgs if m.role == "tool"]

    async def complete(
        self,
        messages: Sequence[ModelMessage],
        *,
        route: str,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ModelResult:
        return self._answer(messages, tools)

    async def stream(
        self,
        messages: Sequence[ModelMessage],
        *,
        route: str,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AsyncIterator[ModelChunk]:
        result = self._answer(messages, tools)
        if result.text:
            yield ModelChunk(text=result.text)
        for call in result.tool_calls:
            yield ModelChunk(tool_call=call)
        yield ModelChunk(usage=result.usage, finish=True)
