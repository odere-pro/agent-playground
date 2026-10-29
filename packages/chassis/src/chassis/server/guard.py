"""The chassis guard stages over `GuardrailPort` (PoC-7, 022 H-6).

Three stages, each on when `spec.guardrails.<stage>` is true (the default) and the bundle has a
guardrail port other than `NoGuardrails` (the `PortBundle` default, so a chassis that names no
guardrails never calls one and behaves as before):

- `input` (`check_input`, called by `Run` before the engine runs): the caller's text, that is,
  `input.text` and every string inside `input.data` (history, a system text), one per line.
  Blocked: one `error` event `guardrail_blocked` (not retryable) and the engine is never called.
  A masked input is not passed on: the chassis cannot change a frozen `Request` per stage without
  a contract change, and LiteLLM's own `pii-mask` on the model call masks it there.
- `tool_output` (`GuardedTools`, the tools port the tool endpoint serves on `/mcp`): each tool
  result's text before the workload reads it. Blocked: the call is an MCP tool error
  `guardrail_blocked`, so the workload never sees the text. Masked: the masked text is the result.
- `output` (`check_output`, called by `Run` after the engine ends, complete calls only): the
  answer's text. Blocked: the run's deltas, tool calls, and `end` are dropped and one `error`
  event `guardrail_blocked` closes it, so nothing of the answer reaches the response, the
  idempotency cache, or a result event. Masked: the deltas become one delta with the masked text,
  and `end.output.text` is masked too. A stream is not held back; LiteLLM's guardrails on the
  model call cover it (`spec.guardrails.output`'s docstring).

A port that cannot answer (`GuardrailError`) is `guardrail_unavailable` (retryable) when
`spec.guardrails.on_unavailable` is `fail_closed` (the default), else the text passes and
`chassis.guardrail_unavailable{stage}` is counted. A block is counted as
`chassis.guardrail_blocked{stage, reason}` (suggested names) and logged with the ids and the
reasons, never the text.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

from chassis.core.envelope import TaskInput
from chassis.core.events import Delta, End, Error, Event, Metrics, Start
from chassis.core.inbound import public_message
from chassis.ports.bundle import PortBundle
from chassis.ports.guardrail import GuardrailError, NoGuardrails, Stage, Verdict
from chassis.ports.tool import ToolDefinition, ToolError, ToolPort, ToolResult
from chassis.server.config import GuardrailsSpec

__all__ = [
    "BLOCKED_CODE",
    "GUARDRAIL_BLOCKED",
    "GUARDRAIL_UNAVAILABLE",
    "UNAVAILABLE_CODE",
    "GuardedTools",
    "check_input",
    "check_output",
    "enabled",
    "input_text",
]

GUARDRAIL_BLOCKED = "chassis.guardrail_blocked"
GUARDRAIL_UNAVAILABLE = "chassis.guardrail_unavailable"
BLOCKED_CODE = "guardrail_blocked"
UNAVAILABLE_CODE = "guardrail_unavailable"


def enabled(ports: PortBundle, spec: GuardrailsSpec, stage: Stage) -> bool:
    return not isinstance(ports.guardrails, NoGuardrails) and bool(getattr(spec, stage))


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _strings(item)


def input_text(task: TaskInput) -> str:
    """The caller's text: `input.text`, then every string in `input.data`, one per line."""
    parts = [task.text] if task.text else []
    parts.extend(s for s in _strings(task.data) if s)
    return "\n".join(parts)


async def _check(
    ports: PortBundle, spec: GuardrailsSpec, text: str, stage: Stage, **ids: str
) -> Verdict | Error:
    """The port's verdict, or the `Error` event a block or a closed outage becomes."""
    telemetry = ports.telemetry
    try:
        verdict = await ports.guardrails.check(text, stage)
    except GuardrailError as exc:
        if spec.on_unavailable == "fail_open":
            telemetry.counter(GUARDRAIL_UNAVAILABLE, stage=stage)
            telemetry.log(
                "warning", "guardrail unavailable; passed (fail_open)", stage=stage, **ids
            )
            return Verdict(allowed=True, text=text)
        telemetry.counter(GUARDRAIL_UNAVAILABLE, stage=stage)
        telemetry.log(
            "warning", "guardrail unavailable; refused", stage=stage, detail=exc.message, **ids
        )
        return Error(
            code=UNAVAILABLE_CODE, message=public_message(UNAVAILABLE_CODE), retryable=True
        )
    if verdict.allowed:
        return verdict
    for reason in verdict.reasons or ("blocked",):
        telemetry.counter(GUARDRAIL_BLOCKED, stage=stage, reason=reason)
    telemetry.log("warning", "guardrail blocked", stage=stage, reasons=list(verdict.reasons), **ids)
    return Error(code=BLOCKED_CODE, message=public_message(BLOCKED_CODE), retryable=False)


async def check_input(
    ports: PortBundle, spec: GuardrailsSpec, task: TaskInput, *, request_id: str
) -> Error | None:
    """The `error` event that ends the run before the engine, or None to go on."""
    if not enabled(ports, spec, "input"):
        return None
    text = input_text(task)
    if not text:
        return None
    outcome = await _check(ports, spec, text, "input", request_id=request_id)
    return outcome if isinstance(outcome, Error) else None


def _answer_text(events: Sequence[Event]) -> tuple[str, str | None]:
    """The deltas' text, and `end.output.text` when the `end` event has one."""
    deltas = "".join(e.text for e in events if isinstance(e, Delta))
    end_text = None
    for event in events:
        if isinstance(event, End) and event.output and isinstance(event.output.get("text"), str):
            end_text = event.output["text"]
    return deltas, end_text


async def check_output(
    ports: PortBundle, spec: GuardrailsSpec, events: Sequence[Event], *, request_id: str
) -> list[Event] | None:
    """The run's events after the `output` stage, or None when they stay as they are. A run that
    has an `error` event or no `end` is left alone (it is not an answer)."""
    if not enabled(ports, spec, "output"):
        return None
    if any(isinstance(e, Error) for e in events) or not any(isinstance(e, End) for e in events):
        return None
    deltas, end_text = _answer_text(events)
    checked: dict[str, str] = {}
    for text in dict.fromkeys(t for t in (deltas, end_text) if t):
        outcome = await _check(ports, spec, text, "output", request_id=request_id)
        if isinstance(outcome, Error):
            kept: list[Event] = [e for e in events if isinstance(e, Start | Metrics)]
            return [*kept, outcome]
        checked[text] = outcome.text
    if all(checked[t] == t for t in checked):
        return None
    out: list[Event] = []
    delta_done = False
    for event in events:
        if isinstance(event, Delta):
            if not delta_done:
                out.append(Delta(text=checked.get(deltas, deltas)))
                delta_done = True
            continue
        if isinstance(event, End) and end_text is not None and event.output is not None:
            output = {**event.output, "text": checked.get(end_text, end_text)}
            out.append(event.model_copy(update={"output": output}))
            continue
        out.append(event)
    return out


def _result_text(content: Any) -> str:
    return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)


class GuardedTools:
    """A `ToolPort` that runs the `tool_output` stage on each result of `inner`. The tool endpoint
    serves it on `/mcp`; the lifecycle (refresh, start, aclose) stays on `inner`."""

    def __init__(self, inner: ToolPort, ports: PortBundle, spec: GuardrailsSpec) -> None:
        self.inner = inner
        self.name = inner.name
        self._ports = ports
        self._spec = spec

    def list_tools(self) -> Sequence[ToolDefinition]:
        return self.inner.list_tools()

    async def call(
        self, name: str, arguments: Mapping[str, Any], *, idempotency_key: str | None = None
    ) -> ToolResult:
        result = await self.inner.call(name, arguments, idempotency_key=idempotency_key)
        if result.content is None:
            return result
        text = _result_text(result.content)
        outcome = await _check(self._ports, self._spec, text, "tool_output", tool=name)
        if isinstance(outcome, Error):
            raise ToolError(
                outcome.code, f"tool {name}: {outcome.code}", retryable=outcome.retryable
            )
        if outcome.text == text:
            return result
        content: Any = outcome.text
        if not isinstance(result.content, str):
            try:
                content = json.loads(outcome.text)
            except ValueError:
                content = outcome.text
        return ToolResult(content=content, is_error=result.is_error)
