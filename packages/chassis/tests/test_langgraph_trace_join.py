"""LangGraph's graph steps sit under the chassis run span, over A2A (ADR-006 decision 1).

ADR-006 made LangGraph the default engine with one open doubt: its OpenInference instrumentor is
published under the LangChain name (`openinference-instrumentation-langchain`), so that it covers
LangGraph's graph steps was a doc claim. This is the trace-join test the ADR asks for.

One tool-using `/v1/run` through the chassis with `OtelTelemetry`, in the `sidecar` lane, to
`echo-langgraph` on its template A2A server (`poc_harness.trace_join`, the PoC-7 shape). Both
halves export to one in-memory exporter; the only link between them is the A2A request's
`traceparent`. A graph step is a span the instrumentor opens for a graph node: its name is the
node's name and its `metadata` carries `langgraph_node` and `langgraph_step`. No TCP, no key.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
import pytest_asyncio
from openinference.semconv.trace import SpanAttributes
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from poc_harness.trace_join import (
    CALLER_TRACE,
    ancestor_ids,
    parent_span_id,
    run_once,
    trace_and_span_id,
)
from workload_a2a import telemetry as workload_telemetry

ENGINE = "echo-langgraph"
GRAPH_SPAN = "LangGraph"
"""LangGraph's default name for a compiled graph, which the instrumentor gives the graph's span."""
TOOL_LOOP = [(1, "model"), (2, "tools"), (3, "model")]
"""The tool scenario's graph steps: the model asks for the tool, the tool node runs it, the model
answers. `tools_condition` is an edge, not a node, so it is not a step."""


@dataclass(frozen=True)
class TracedRun:
    """One run of the LangGraph workload and every span both halves exported."""

    out: dict[str, Any]
    spans: list[ReadableSpan]

    def named(self, name: str) -> ReadableSpan:
        (span,) = [s for s in self.spans if s.name == name]
        return span

    @property
    def graph(self) -> ReadableSpan:
        """The graph's span: the one CHAIN span whose parent is `workload.handle`."""
        handle = self.span_id(self.named("workload.handle"))
        (span,) = [s for s in self.spans if _kind(s) == "CHAIN" and parent_span_id(s) == handle]
        return span

    def span_id(self, span: ReadableSpan) -> str:
        return trace_and_span_id(span)[1]

    @property
    def steps(self) -> list[ReadableSpan]:
        """The graph-step spans, in step order."""
        found = [s for s in self.spans if _metadata(s).get("langgraph_node") == s.name]
        return sorted(found, key=lambda s: int(_metadata(s)["langgraph_step"]))

    def under(self, span: ReadableSpan, ancestor: ReadableSpan) -> bool:
        return self.span_id(ancestor) in ancestor_ids(span, self.spans)


def _metadata(span: ReadableSpan) -> dict[str, Any]:
    """The span's OpenInference metadata. Absent is `{}`; any other shape than a JSON string is an
    instrumentor change this test must hear about, so it raises."""
    raw = (span.attributes or {}).get(SpanAttributes.METADATA)
    if raw is None:
        return {}
    if not isinstance(raw, str):
        raise TypeError(f"{span.name}: metadata is {type(raw).__name__}, not a JSON string")
    found = json.loads(raw)
    if not isinstance(found, dict):
        raise TypeError(f"{span.name}: metadata is not a JSON object")
    return found


def _kind(span: ReadableSpan) -> object:
    return (span.attributes or {}).get(SpanAttributes.OPENINFERENCE_SPAN_KIND)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def traced() -> AsyncIterator[TracedRun]:
    """One run, shared by the tests below; the workload's tracing is set for it only."""
    exporter = InMemorySpanExporter()
    workload_telemetry.configure(span_exporters=[exporter], service_name="workload")
    try:
        with pytest.MonkeyPatch.context() as monkeypatch:
            out, spans = await run_once(ENGINE, monkeypatch, exporter)
        yield TracedRun(out, spans)
    finally:
        workload_telemetry.shutdown()


def test_the_run_is_ok_and_in_the_callers_trace(traced: TracedRun) -> None:
    assert traced.out["status"] == "ok", traced.out
    assert {trace_and_span_id(s)[0] for s in traced.spans} == {CALLER_TRACE}


def test_the_graph_span_is_under_the_run_span_through_the_workload(traced: TracedRun) -> None:
    graph = traced.graph

    assert graph.name == GRAPH_SPAN, "LangGraph renamed its default graph name"
    assert traced.under(graph, traced.named("chassis.engine.run"))
    assert traced.under(graph, traced.named("chassis.run"))


def test_each_graph_step_is_a_span_in_step_order(traced: TracedRun) -> None:
    steps = [(int(_metadata(s)["langgraph_step"]), s.name) for s in traced.steps]

    assert steps == TOOL_LOOP


def test_each_graph_step_is_a_child_of_the_graph_span(traced: TracedRun) -> None:
    graph = traced.span_id(traced.graph)

    assert [parent_span_id(s) for s in traced.steps] == [graph] * len(TOOL_LOOP)


def test_each_graph_step_is_under_the_run_span(traced: TracedRun) -> None:
    run = traced.named("chassis.run")

    assert [s.name for s in traced.steps] == [name for _, name in TOOL_LOOP]
    assert [s.name for s in traced.steps if not traced.under(s, run)] == []


def test_the_model_calls_and_the_tool_call_sit_in_their_steps(traced: TracedRun) -> None:
    by_id = {traced.span_id(s): s for s in traced.spans}
    llm = [s for s in traced.spans if _kind(s) == "LLM"]
    tool = [s for s in traced.spans if _kind(s) == "TOOL"]

    assert [by_id[parent_span_id(s) or ""].name for s in llm] == ["model", "model"]
    assert [by_id[parent_span_id(s) or ""].name for s in tool] == ["tools"]
