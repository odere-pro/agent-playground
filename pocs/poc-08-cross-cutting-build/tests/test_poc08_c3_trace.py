"""PoC-8 exit criterion 3: "Every call on every engine has a complete trace, logs with
`trace_id`, and metrics." (docs/planning/poc/008-PoC-8-build-cross-cutting.md)

This file holds the trace part. Each cell (`poc08_harness.CELLS`: every supported Python engine in
each lane it can run in offline) runs one call through the chassis with every stage on. The trace
is complete when it has one `chassis.run` span, a `chassis.model.call` span (the model proxy) for
each model call the workload made, and a `chassis.tool.call` span (the tool proxy, `/mcp`) when
the tool is called, all with the run's one trace id. A call with no tool has no tool span.

The metrics and JSON-log parts wait on the observability branch: see the skipped placeholder.
"""

from __future__ import annotations

import pytest
from chassis.core.trace import trace_id_hex
from poc08_harness import LOOK_IT_UP, PLAIN, cell_params, full_stack

RUN, MODEL, TOOL = "chassis.run", "chassis.model.call", "chassis.tool.call"


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


@pytest.mark.skip(reason="waits on the observability branch merge")
@pytest.mark.parametrize(("engine", "lane"), cell_params())
async def test_every_call_has_metrics_and_json_logs_with_the_trace_id(
    engine: str, lane: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Criterion 3 (metrics and logs): placeholder until the observability branch lands.

    TODO(observability): after one call per cell, `GET /metrics` holds the request, latency,
    error, token, cost, and fallback-rate series for the agent, labeled by agent.
    TODO(observability): every chassis log line of the call is JSON with `request_id` and
    `trace_id`, and `trace_id` equals the run's trace id.
    """
    with full_stack(engine, lane, monkeypatch) as stack:
        response = await stack.run("simplify: Hello.")
    assert response.status_code == 200
    raise AssertionError("fill in the metrics and log asserts")
