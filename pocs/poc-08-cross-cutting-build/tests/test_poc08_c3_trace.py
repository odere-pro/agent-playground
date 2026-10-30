"""PoC-8 exit criterion 3: "Every call on every engine has a complete trace, logs with
`trace_id`, and metrics." (docs/planning/poc/008-PoC-8-build-cross-cutting.md)

This file holds the trace part. Each cell (`poc_harness.full_stack.CELLS`: every supported Python
engine in each lane it can run in offline) runs one call through the chassis with every stage on.
The trace is complete when it has one `chassis.run` span, a `chassis.model.call` span (the model
proxy) for each model call the workload made, and a `chassis.tool.call` span (the tool proxy,
`/mcp`) when the tool is called, all with the run's one trace id. A call with no tool has no
tool span.

Metrics and logs: after a tool-calling run, `GET /metrics` (Prometheus text, no token by default)
counts the request, the model calls, their tokens, and the tool call for the agent. The chassis log
lines, captured with the chassis's own `JsonFormatter` on the root logger, are JSON, and the lines
logged during the run carry the run's `request_id` and 32-hex `trace_id`. The router span
(`chassis.model.route`) is in the run's trace, one per model call. The lines come from the fake
ports (`PORT_LOGGER`), which log inside the run, proxy, and tool spans the way a real adapter does.
"""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from chassis.core.trace import trace_id_hex
from chassis.logs import JsonFormatter
from poc_harness.full_stack import cell_params, full_stack
from poc_harness.promtext import value
from poc_harness.stage_model import LOOK_IT_UP, PLAIN, PORT_LOGGER, ROUTE

RUN, MODEL, TOOL = "chassis.run", "chassis.model.call", "chassis.tool.call"
AGENT = "simplifier"
ROUTER = "chassis.model.route"
"""The router span's name, pinned here: a rename in the chassis must fail this test."""


@pytest.mark.parametrize(("engine", "lane"), cell_params())
async def test_a_tool_call_has_one_trace_across_run_model_and_tool_spans(
    engine: str, lane: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Criterion 3 (trace): a tool-using call has `chassis.run`, at least two model proxy spans
    (the tool call, then the answer), and a tool proxy span for `glossary_lookup`, every one on
    the run's trace id, and every model span correlated to the run."""
    with full_stack(engine, lane, monkeypatch) as stack:
        response = await stack.run(f"simplify: what does SLM mean? {LOOK_IT_UP}")
    body = response.json()
    assert response.status_code == 200, response.text
    assert body["status"] == "ok", body
    trace = trace_id_hex(body["trace_id"])

    (run,) = stack.spans(RUN)
    models, tools = stack.spans(MODEL), stack.spans(TOOL)
    assert run.attributes["trace_id"] == trace
    assert len(models) >= 2, f"{engine}/{lane}: {len(models)} model proxy spans"
    assert [t.attributes["tool"] for t in tools] == ["glossary_lookup"]
    for span in [run, *models, *tools]:
        assert span.attributes.get("trace_id") == trace, f"{span.name} is on another trace"
    assert all(m.attributes.get("correlated") is True for m in models)
    assert all(m.attributes.get("request_id") == body["request_id"] for m in models)


@pytest.mark.parametrize(("engine", "lane"), cell_params())
async def test_a_call_without_a_tool_has_no_tool_span(
    engine: str, lane: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Criterion 3 (trace): the tool proxy span is there only when the tool is called; the run
    and model spans are on one trace."""
    with full_stack(engine, lane, monkeypatch) as stack:
        response = await stack.run("simplify: Hello.")
    body = response.json()
    assert body["status"] == "ok", body
    assert body["output"]["text"] == PLAIN
    trace = trace_id_hex(body["trace_id"])
    assert stack.spans(TOOL) == []
    spans = [*stack.spans(RUN), *stack.spans(MODEL)]
    assert len(spans) >= 2
    assert {s.attributes.get("trace_id") for s in spans} == {trace}


@pytest.fixture
def log_lines() -> Iterator[Callable[[], list[str]]]:
    """The raw lines written so far by a root handler with the chassis's `JsonFormatter`."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter(agent=AGENT))
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    try:
        yield lambda: stream.getvalue().splitlines()
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)


@pytest.mark.parametrize(("engine", "lane"), cell_params())
async def test_every_call_has_metrics_and_json_logs_with_the_trace_id(
    engine: str,
    lane: str,
    monkeypatch: pytest.MonkeyPatch,
    log_lines: Callable[[], list[str]],
) -> None:
    """Criterion 3 (metrics and logs): `/metrics` counts the run's request, model calls, tokens,
    and tool call; every captured log line is JSON; the run's lines carry its ids; the router
    span is on the run's trace."""
    with full_stack(engine, lane, monkeypatch) as stack:
        response = await stack.run(f"simplify: what does SLM mean? {LOOK_IT_UP}")
        async with stack.client() as client:
            metrics = await client.get("/metrics")
    body = response.json()
    assert body["status"] == "ok", body
    trace, request_id = trace_id_hex(body["trace_id"]), body["request_id"]
    calls = len(stack.spans(MODEL))
    assert calls >= 2

    assert metrics.status_code == 200, metrics.text
    text = metrics.text
    assert value(text, "chassis_requests_total", agent=AGENT, interface="native") == 1
    assert value(text, "chassis_model_calls_total", agent=AGENT, route=ROUTE) == calls
    for direction, per_call in (("input", 10), ("output", 5)):
        tokens = value(
            text, "chassis_model_tokens_total", agent=AGENT, route=ROUTE, direction=direction
        )
        assert tokens == per_call * calls, direction
    assert value(text, "chassis_tool_calls_total", agent=AGENT, tool="glossary_lookup") == 1

    lines: list[dict[str, Any]] = [json.loads(line) for line in log_lines()]
    mine = [x for x in lines if x["logger"] == PORT_LOGGER]
    assert {x.get("stage") for x in mine} >= {"input", "tool_output", "output"}
    assert len([x for x in mine if x["message"] == "model call"]) == calls
    for line in mine:
        assert line["agent"] == AGENT
        assert (line.get("trace_id"), line.get("request_id")) == (trace, request_id), line
    assert all(len(x["trace_id"]) == 32 for x in lines if "trace_id" in x)

    routes = stack.spans(ROUTER)
    assert len(routes) == calls
    assert {s.attributes.get("trace_id") for s in routes} == {trace}
    assert {s.attributes.get("route") for s in routes} == {ROUTE}
