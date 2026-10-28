"""PoC-6a, the benchmark kit (exit criterion 3) on every sidecar-lane engine and lane: smoke,
simplifier, and lookup run through the chassis connector, in memory (`inprocess`) and over A2A on a
Unix socket (`sidecar`), against the fake model server on `scripts/bakeoff.yaml` and the two-tool
fake MCP server. Each run is judged by the pass check of `poc06_harness`. The engines come from the
registry (`poc06_harness.ENGINES`), so a new one is one registry line.

The TypeScript agent is a Node process, so its in-memory cell is skipped on purpose. Its cells skip
as a whole only when its `node_modules` is absent (`npm ci`).

Also: each engine sends the run's `traceparent` on every model call and every MCP request. The
backends record the headers they receive; the trace id must be the run's own.
"""

from __future__ import annotations

import pytest
from chassis.core.trace import trace_id_hex
from poc06_harness import ENGINES, LOOKUP, TASKS, Task
from poc06a_harness import (
    Backends,
    Seen,
    SidecarAddress,
    cell_params,
    run_task,
    wire_python_engine,
)


@pytest.fixture
def address(request: pytest.FixtureRequest) -> SidecarAddress | None:
    """The TypeScript agent for a TypeScript cell, else None."""
    if ENGINES[request.getfixturevalue("engine_name")].language != "typescript":
        return None
    found: SidecarAddress = request.getfixturevalue("typescript")
    return found


@pytest.fixture
def wired(engine_name: str, backends: Backends, monkeypatch: pytest.MonkeyPatch) -> Backends:
    """The backends, with a Python engine's hooks pointed at them."""
    if ENGINES[engine_name].language == "python":
        wire_python_engine(monkeypatch, ENGINES[engine_name], backends)
    return backends


@pytest.mark.parametrize("task", TASKS, ids=[t.name for t in TASKS])
@pytest.mark.parametrize(("engine_name", "lane"), cell_params())
async def test_engine_passes_each_task_in_each_lane(
    engine_name: str,
    lane: str,
    task: Task,
    wired: Backends,
    address: SidecarAddress | None,
) -> None:
    """The task passes its check, the model server was called, and exactly the task's tool calls
    reached the tool server, in order."""
    _, verdict, _ = await run_task(ENGINES[engine_name], lane, task, address)
    assert verdict.passed, verdict.problems
    assert wired.model_calls(), "the model server was never called"
    assert wired.tool_calls == [(s.name, dict(s.arguments)) for s in task.tool_calls]


def _trace_ids(requests: list[Seen]) -> set[str | None]:
    return {None if r.traceparent is None else r.traceparent.split("-")[1] for r in requests}


@pytest.mark.parametrize(("engine_name", "lane"), cell_params())
async def test_engine_sends_the_runs_traceparent_on_model_and_tool_calls(
    engine_name: str, lane: str, wired: Backends, address: SidecarAddress | None
) -> None:
    """The lookup task makes model calls and MCP requests; every one carries a `traceparent`
    whose trace id is the run's (`ctx.trace_id`). Nothing without it, nothing with another."""
    _, verdict, ctx = await run_task(ENGINES[engine_name], lane, LOOKUP, address)
    assert verdict.passed, verdict.problems
    want = {trace_id_hex(ctx.trace_id)}
    model, tools = wired.model_calls(), wired.tool_requests()
    assert len(model) >= 3, f"model calls: {[(c.method, c.path) for c in model]}"
    assert tools, "no MCP request reached the tool server"
    assert _trace_ids(model) == want, [c.traceparent for c in model]
    assert _trace_ids(tools) == want, [(c.method, c.traceparent) for c in tools]
