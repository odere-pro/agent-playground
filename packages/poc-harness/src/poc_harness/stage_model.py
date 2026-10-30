"""The PoC-8 scripted model: one script for every engine, and the prompts that drive it."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator, Sequence
from typing import Any

from chassis.ports.model import (
    ModelChunk,
    ModelMessage,
    ModelResult,
    ToolCallRequest,
    ToolSpec,
    Usage,
)

ROUTE = "big-default"
FALLBACK_ROUTE = "fallback-route"
PLAIN = "Plain words. Short sentences."
LOOK_IT_UP = "look it up"
"""A task with this asks the model for `glossary_lookup`."""
FETCH = "fetch the page"
"""A task with this asks the model for `fetch_page` (the red-team tool)."""
AFTER_TOOL = "SLM means a small language model."
GATE_MARK = "gate-check"
"""A task with this gets `BAD` on the main route; the evaluator scores `BAD` below threshold."""
BAD = "The report was signed."
GOOD = "Dana Ruiz signed the report on 4 May 2025."
PORT_LOGGER = "chassis.poc08.ports"
"""The fake ports log one line per call here, as a real adapter would, so a test can read the
chassis's JSON log lines of a run in every lane (the workload's own logs never pass the chassis)."""
log = logging.getLogger(PORT_LOGGER)


def _code_mode(messages: Sequence[ModelMessage]) -> bool:
    """The prompt asks for Python in `<code>` blocks (a smolagents `CodeAgent`). A model that
    follows the format answers in it; ADR-006 names this as the engine's model requirement."""
    return any(m.role == "system" and "final_answer" in (m.content or "") for m in messages)


def _offered(tools: Sequence[ToolSpec] | None, bare: str, code: bool) -> str | None:
    """The offered tool whose name is `bare` or ends with `__bare` (`mcp__chassis__bare`). In
    code mode the tools are in the prompt, not in `tools`."""
    if code:
        return bare
    return next((t.name for t in tools or [] if t.name.split("__")[-1] == bare), None)


def _has_result(messages: Sequence[ModelMessage], code: bool) -> bool:
    if any(m.role == "tool" for m in messages):
        return True
    users = [m.content or "" for m in messages if m.role == "user"]
    return code and any("Execution logs" in u or "Observation" in u for u in users[1:])


def _code(text: str | None, call: ToolCallRequest | None) -> str:
    if call is not None:
        args = ", ".join(f"{k}={json.dumps(v)}" for k, v in call.arguments.items())
        return f"Thought: Call the tool.\n<code>\nprint({call.name}({args}))\n</code>"
    return f"Thought: Done.\n<code>\nfinal_answer({json.dumps(text)})\n</code>"


class StageModel:
    """A `ModelPort`. On `FALLBACK_ROUTE` it answers `GOOD`. Else, by the task text: `FETCH` or
    `LOOK_IT_UP` with the tool offered and no tool result yet is a tool call; after a tool result,
    `AFTER_TOOL`; `GATE_MARK` is `BAD`; anything else is `answer`. Records every message text and
    every route it was called on."""

    name = "poc08-stage"

    def __init__(self) -> None:
        self.answer = PLAIN
        self.fetch_args: dict[str, Any] = {"text": "page"}
        self.seen: list[str] = []
        self.routes: list[str] = []

    def reset(self) -> None:
        self.answer, self.fetch_args = PLAIN, {"text": "page"}
        self.seen.clear()
        self.routes.clear()

    def seen_text(self) -> str:
        return "\n".join(self.seen)

    def _decide(
        self, messages: Sequence[ModelMessage], tools: Sequence[ToolSpec] | None, route: str
    ) -> tuple[str | None, ToolCallRequest | None]:
        code = _code_mode(messages)
        task = next((m.content or "" for m in messages if m.role == "user"), "")
        done = _has_result(messages, code)
        if route == FALLBACK_ROUTE:
            return GOOD, None
        for marker, bare, args in (
            (FETCH, "fetch_page", self.fetch_args),
            (LOOK_IT_UP, "glossary_lookup", {"term": "SLM"}),
        ):
            name = _offered(tools, bare, code)
            if marker in task and name and not done:
                return None, ToolCallRequest(call_id="call_1", name=name, arguments=dict(args))
        if done:
            return AFTER_TOOL, None
        return (BAD if GATE_MARK in task else self.answer), None

    def _result(
        self, messages: Sequence[ModelMessage], tools: Sequence[ToolSpec] | None, route: str
    ) -> ModelResult:
        self.routes.append(route)
        log.info("model call", extra={"route": route})
        self.seen.extend(m.content or "" for m in messages)
        text, call = self._decide(messages, tools, route)
        usage = Usage(input_tokens=10, output_tokens=5)
        if _code_mode(messages):
            return ModelResult(text=_code(text, call), usage=usage, model=route)
        if call is not None:
            return ModelResult(text="", tool_calls=[call], usage=usage, model=route)
        return ModelResult(text=text or "", usage=usage, model=route)

    async def complete(
        self,
        messages: Sequence[ModelMessage],
        *,
        route: str,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ModelResult:
        return self._result(messages, tools, route)

    async def stream(
        self,
        messages: Sequence[ModelMessage],
        *,
        route: str,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AsyncIterator[ModelChunk]:
        result = self._result(messages, tools, route)
        for word in re.findall(r"\S+\s*", result.text or ""):  # word by word, as a model streams
            yield ModelChunk(text=word)
        for call in result.tool_calls:
            yield ModelChunk(tool_call=call)
        yield ModelChunk(usage=result.usage, finish=True)
