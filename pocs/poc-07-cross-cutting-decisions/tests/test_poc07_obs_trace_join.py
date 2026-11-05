"""PoC-7 observability: one trace joins the chassis and the workload through `traceparent` over A2A.

Exit criteria (docs/planning/poc/007-PoC-7-cross-cutting-decisions.md):

- 4: one trace joins the chassis and workload containers through `traceparent` over A2A.
- 5 (observability part): it works on two engines with no engine-specific code outside the
  workload's own wiring.

One `/v1/run` through the chassis with `OtelTelemetry`, in the `sidecar` lane: the workload's own
template A2A server (`workload_a2a.server`) on a Unix socket, its tracing configured by
`workload_a2a.telemetry` with the engine's OpenInference instrumentor. Both export to one in-memory
exporter (one process stands in for the two containers; the only link between the halves is the
A2A request's `traceparent` and `baggage` headers, and the workload's outbound headers). The model
is the chassis's `ModelPort` behind the model proxy; the tool is the chassis's `/mcp`. No TCP, no
key.
"""

from __future__ import annotations

import ast
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from poc_harness.scenario import AFTER_TOOL
from poc_harness.trace_join import (
    CALLER_SPAN,
    CALLER_TRACE,
    ENGINES,
    SESSION,
    is_under_through,
    parent_span_id,
    run_once,
    trace_and_span_id,
)
from workload_a2a import telemetry as workload_telemetry

ROOT = Path(__file__).resolve().parents[3]
CHASSIS_SRC = ROOT / "packages/chassis/src/chassis"
ENGINE_NAMES = re.compile(
    r"langgraph|langchain|openai_agents|openai-agents|pydantic_ai|pydanticai|smolagents|"
    r"claude_agent|crewai|openinference|echo_python|echo-python|echo_typescript|kagent",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TracedRun:
    """One tool-using `/v1/run` and the spans both halves exported."""

    engine: str
    out: dict[str, Any]
    spans: list[ReadableSpan]

    def named(self, name: str) -> ReadableSpan:
        """The run's one span called `name`."""
        (span,) = [s for s in self.spans if s.name == name]
        return span

    def span_id(self, name: str) -> str:
        """The span id of the run's one span called `name`."""
        return trace_and_span_id(self.named(name))[1]

    @property
    def workload_ids(self) -> set[str]:
        """The span ids the workload half exported."""
        return {
            trace_and_span_id(s)[1]
            for s in self.spans
            if s.resource.attributes["service.name"] == "workload"
        }

    @property
    def framework(self) -> list[ReadableSpan]:
        """The engine's OpenInference spans."""
        return [s for s in self.spans if _scope(s).startswith("openinference")]

    @property
    def calls(self) -> list[ReadableSpan]:
        """The chassis spans the workload's outbound calls open: model proxy and `/mcp`."""
        return [s for s in self.spans if s.name in {"chassis.model.call", "chassis.tool.call"}]


def _scope(span: ReadableSpan) -> str:
    return span.instrumentation_scope.name if span.instrumentation_scope else ""


@pytest_asyncio.fixture(scope="module", loop_scope="module", params=list(ENGINES))
async def traced(request: pytest.FixtureRequest) -> AsyncIterator[TracedRun]:
    """One run per engine, shared by the tests below; the workload's tracing is set for it only."""
    exporter = InMemorySpanExporter()
    workload_telemetry.configure(span_exporters=[exporter], service_name="workload")
    try:
        with pytest.MonkeyPatch.context() as monkeypatch:
            out, spans = await run_once(request.param, monkeypatch, exporter)
        yield TracedRun(request.param, out, spans)
    finally:
        workload_telemetry.shutdown()


def test_one_trace_joins_chassis_workload_framework_model_and_tool(traced: TracedRun) -> None:
    """Criterion 4: "One trace joins the chassis and workload containers", on two engines (5).

    One trace id, the caller's, on every span of the run, with parent links: the caller's span ->
    `chassis.run` -> `chassis.engine.run` (the A2A connector) -> `workload.handle` (the workload,
    from the A2A `traceparent` header) -> the engine's OpenInference spans; each
    `chassis.model.call` (the model proxy) and `chassis.tool.call` (`/mcp`) is a child of a
    workload span, from the `traceparent` the workload sent. The session id reaches the workload's
    spans through W3C baggage. The single-behavior tests below repeat each link on its own.
    """
    assert traced.out["status"] == "ok", traced.out
    assert traced.out["output"]["text"] == AFTER_TOOL

    assert {trace_and_span_id(s)[0] for s in traced.spans} == {CALLER_TRACE}

    run = traced.named("chassis.run")
    handle = traced.named("workload.handle")
    assert parent_span_id(run) == CALLER_SPAN, "the caller's traceparent is continued"
    assert parent_span_id(traced.named("chassis.engine.run")) == traced.span_id("chassis.run")
    assert parent_span_id(handle) == traced.span_id("chassis.engine.run"), (
        "the A2A traceparent names the connector span"
    )

    assert traced.framework, f"{traced.engine}: no OpenInference span"
    handle_id = traced.span_id("workload.handle")
    stray = [
        s.name
        for s in traced.framework
        if not is_under_through(s, handle_id, traced.workload_ids, traced.spans)
    ]
    assert stray == [], "every framework span is under workload.handle"

    model_calls = [s for s in traced.calls if s.name == "chassis.model.call"]
    tool_calls = [s for s in traced.calls if s.name == "chassis.tool.call"]
    assert len(model_calls) >= 2, "the tool scenario makes two model calls"
    assert tool_calls, "the tool call reached the chassis /mcp"
    for call in traced.calls:
        assert parent_span_id(call) in traced.workload_ids, f"{call.name} is not under the workload"
        assert (call.attributes or {}).get("correlated", True) is not False

    assert (run.attributes or {}).get("session.id") == SESSION
    assert (handle.attributes or {}).get("session.id") == SESSION, "baggage reached the workload"
    assert all((s.attributes or {}).get("session.id") == SESSION for s in traced.framework)


def test_every_span_is_in_the_callers_trace(traced: TracedRun) -> None:
    """Criterion 4: "One trace joins the chassis and workload containers through `traceparent`
    over A2A": every span of the run is in the caller's trace."""
    trace_ids = {trace_and_span_id(s)[0] for s in traced.spans}

    assert trace_ids == {CALLER_TRACE}


def test_the_traced_run_answers_after_the_tool_call(traced: TracedRun) -> None:
    """Criterion 4: "One trace joins the chassis and workload containers"; the run is ok."""
    assert traced.out["status"] == "ok", traced.out
    assert traced.out["output"]["text"] == AFTER_TOOL


def test_the_run_span_continues_the_callers_traceparent(traced: TracedRun) -> None:
    """Criterion 4: "One trace joins the chassis and workload containers"; the caller's span is
    the parent of `chassis.run`."""
    assert parent_span_id(traced.named("chassis.run")) == CALLER_SPAN


def test_the_connector_span_is_a_child_of_the_run_span(traced: TracedRun) -> None:
    """Criterion 4: "One trace joins the chassis and workload containers"; `chassis.engine.run`
    is a child of `chassis.run`."""
    connector = traced.named("chassis.engine.run")

    assert parent_span_id(connector) == traced.span_id("chassis.run")


def test_the_workload_handle_span_is_a_child_of_the_connector_span(traced: TracedRun) -> None:
    """Criterion 4: "through `traceparent` over A2A"; the A2A `traceparent` names the connector
    span as the parent of `workload.handle`."""
    handle = traced.named("workload.handle")

    assert parent_span_id(handle) == traced.span_id("chassis.engine.run")


def test_the_engine_emits_openinference_spans(traced: TracedRun) -> None:
    """Criterion 4: "One trace joins the chassis and workload containers"; the engine's own
    instrumentor emits spans."""
    assert traced.framework, f"{traced.engine}: no OpenInference span"


def test_every_framework_span_is_under_workload_handle(traced: TracedRun) -> None:
    """Criterion 4: "One trace joins the chassis and workload containers"; every OpenInference
    span hangs under `workload.handle` through workload spans only."""
    handle = traced.span_id("workload.handle")

    stray = [
        s.name
        for s in traced.framework
        if not is_under_through(s, handle, traced.workload_ids, traced.spans)
    ]

    assert stray == []


def test_the_tool_scenario_makes_two_model_calls(traced: TracedRun) -> None:
    """Criterion 4: "One trace joins the chassis and workload containers"; the tool scenario's
    two model calls go through the chassis model proxy."""
    model_calls = [s for s in traced.calls if s.name == "chassis.model.call"]

    assert len(model_calls) >= 2


def test_the_tool_call_reaches_the_chassis_mcp(traced: TracedRun) -> None:
    """Criterion 4: "One trace joins the chassis and workload containers"; the tool call goes
    through the chassis `/mcp`."""
    tool_calls = [s for s in traced.calls if s.name == "chassis.tool.call"]

    assert tool_calls


def test_every_model_and_tool_call_is_a_child_of_a_workload_span(traced: TracedRun) -> None:
    """Criterion 4: "One trace joins the chassis and workload containers"; each model and tool
    call continues the `traceparent` the workload sent."""
    orphans = [s.name for s in traced.calls if parent_span_id(s) not in traced.workload_ids]

    assert orphans == []


def test_no_model_or_tool_call_is_marked_uncorrelated(traced: TracedRun) -> None:
    """Criterion 4: "One trace joins the chassis and workload containers"; no call span says
    `correlated=False`."""
    uncorrelated = [s.name for s in traced.calls if (s.attributes or {}).get("correlated") is False]

    assert uncorrelated == []


@pytest.mark.parametrize("name", ["chassis.run", "workload.handle"])
def test_the_session_id_is_on_the_run_and_handle_spans(traced: TracedRun, name: str) -> None:
    """Criterion 4: "One trace joins the chassis and workload containers"; the session id reaches
    the workload through W3C baggage."""
    attributes = traced.named(name).attributes or {}

    assert attributes.get("session.id") == SESSION


def test_the_session_id_is_on_every_framework_span(traced: TracedRun) -> None:
    """Criterion 4: "One trace joins the chassis and workload containers"; baggage puts the
    session id on every OpenInference span."""
    without = [
        s.name for s in traced.framework if (s.attributes or {}).get("session.id") != SESSION
    ]

    assert without == []


def _code_tokens(path: Path) -> list[str]:
    """Every identifier, import, and string in a module except bare string statements (docstrings,
    attribute docs) and comments, which are not code."""
    tree = ast.parse(path.read_text())
    docstrings = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            out.append(node.id)
        elif isinstance(node, ast.Attribute):
            out.append(node.attr)
        elif isinstance(node, ast.alias):
            out.append(node.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.append(node.module)
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            out.append(node.value)
    return out


def test_the_chassis_has_no_engine_specific_branch() -> None:
    """Criterion 5: "no engine-specific code outside the workload's event mapping" (observability).

    The chassis code names no engine, framework, or instrumentor. Each engine's instrumentor is in
    the workload's own `telemetry.py`; the template server and its tracing (`workload_a2a.server`,
    `workload_a2a.telemetry`) are engine-agnostic too."""
    offenders = [
        f"{path.relative_to(ROOT)}: {token}"
        for path in sorted(CHASSIS_SRC.rglob("*.py"))
        for token in _code_tokens(path)
        if ENGINE_NAMES.search(token)
    ]
    template = ROOT / "packages/workload-a2a/src/workload_a2a"
    offenders += [
        f"{path.relative_to(ROOT)}: {token}"
        for path in (template / "server.py", template / "telemetry.py")
        for token in _code_tokens(path)
        if ENGINE_NAMES.search(token)
    ]
    assert offenders == []
