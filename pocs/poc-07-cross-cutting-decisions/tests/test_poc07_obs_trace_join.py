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
import asyncio
import re
import shutil
import tempfile
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from chassis.adapters.a2a import SidecarConnector
from chassis.adapters.otel import OtelTelemetry
from chassis.fakes import InMemoryConfig
from chassis.fakes.tool import default_tools
from chassis.ports.bundle import PortBundle
from chassis.server import create_app
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from poc_harness.apps import chassis_config, client_for, running
from poc_harness.outbound import route_outbound
from poc_harness.scenario import AFTER_TOOL, TIMEOUT_S, TOOL_PROMPT
from poc_harness.scripted_model import ToolLoopModel
from workload_a2a import telemetry as workload_telemetry

ROOT = Path(__file__).resolve().parents[3]
CHASSIS_SRC = ROOT / "packages/chassis/src/chassis"
CALLER_TRACE = "0af7651916cd43dd8448eb211c80319c"  # pragma: allowlist secret
CALLER_SPAN = "b7ad6b7169203331"  # pragma: allowlist secret
SIDECAR_URL = "http://127.0.0.1:9100"
SESSION = "sess-poc07"
ENGINES = {
    "echo-langgraph": "echo_langgraph.handle:handle",
    "echo-openai-agents": "echo_openai_agents.handle:handle",
}
"""The two engines run end to end. Each one's instrumentor is in its own `telemetry.py`."""
ENGINE_NAMES = re.compile(
    r"langgraph|langchain|openai_agents|openai-agents|pydantic_ai|pydanticai|smolagents|"
    r"claude_agent|crewai|openinference|echo_python|echo-python|echo_typescript|kagent",
    re.IGNORECASE,
)


@pytest.fixture
def exporter() -> Iterator[InMemorySpanExporter]:
    """One exporter for both halves; the workload's tracing is configured for the test only."""
    exporter = InMemorySpanExporter()
    workload_telemetry.configure(span_exporters=[exporter], service_name="workload")
    try:
        yield exporter
    finally:
        workload_telemetry.shutdown()


@asynccontextmanager
async def workload_on_uds(handle_path: str) -> AsyncIterator[str]:
    """The workload's template A2A server on a Unix socket, on this event loop."""
    from chassis.adapters.a2a.inprocess import load_handle
    from workload_a2a.server import build_agent_card, build_app

    card = build_agent_card(name=handle_path, version="0.0.1", url=SIDECAR_URL)
    folder = Path(tempfile.mkdtemp(prefix="p7-"))
    uds = str(folder / "a2a.sock")
    app = build_app(load_handle(handle_path), card)
    server = uvicorn.Server(uvicorn.Config(app, uds=uds, log_level="warning", lifespan="off"))
    task = asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(10):
            while not server.started:
                if task.done():
                    task.result()
                await asyncio.sleep(0.01)
        yield uds
    finally:
        server.should_exit = True
        server.force_exit = True
        await task
        shutil.rmtree(folder, ignore_errors=True)


def _hex(span: ReadableSpan) -> tuple[str, str]:
    assert span.context is not None
    return f"{span.context.trace_id:032x}", f"{span.context.span_id:016x}"


def _parent(span: ReadableSpan) -> str | None:
    return f"{span.parent.span_id:016x}" if span.parent is not None else None


def _named(spans: list[ReadableSpan], name: str) -> list[ReadableSpan]:
    return [s for s in spans if s.name == name]


def _scope(span: ReadableSpan) -> str:
    return span.instrumentation_scope.name if span.instrumentation_scope else ""


async def run_once(
    engine: str, monkeypatch: pytest.MonkeyPatch, exporter: InMemorySpanExporter
) -> tuple[dict[str, Any], list[ReadableSpan]]:
    """One tool-using `/v1/run` through the chassis to `engine` in the sidecar lane."""
    async with workload_on_uds(ENGINES[engine]) as uds:
        ports = PortBundle(
            model=ToolLoopModel(),
            engine=SidecarConnector(),
            config=InMemoryConfig(),
            telemetry=OtelTelemetry(span_exporters=[exporter], service_name="chassis"),
            tools=default_tools(),
        )
        app = create_app(
            chassis_config({"connector": "sidecar", "url": SIDECAR_URL, "uds": uds}), ports
        )
        route_outbound(monkeypatch, app)
        headers = {
            "traceparent": f"00-{CALLER_TRACE}-{CALLER_SPAN}-01",
            "x-session-id": SESSION,
        }
        body = {"input": {"text": TOOL_PROMPT}}
        async with asyncio.timeout(TIMEOUT_S), running(app), client_for(app) as client:
            response = await client.post("/v1/run", json=body, headers=headers)
    assert response.status_code == 200, response.text
    return response.json(), list(exporter.get_finished_spans())


@pytest.mark.parametrize("engine", list(ENGINES))
async def test_one_trace_joins_chassis_workload_framework_model_and_tool(
    engine: str, monkeypatch: pytest.MonkeyPatch, exporter: InMemorySpanExporter
) -> None:
    """Criterion 4: "One trace joins the chassis and workload containers", on two engines (5).

    One trace id, the caller's, on every span of the run, with parent links: the caller's span ->
    `chassis.run` -> `chassis.engine.run` (the A2A connector) -> `workload.handle` (the workload,
    from the A2A `traceparent` header) -> the engine's OpenInference spans; each
    `chassis.model.call` (the model proxy) and `chassis.tool.call` (`/mcp`) is a child of a
    workload span, from the `traceparent` the workload sent. The session id reaches the workload's
    spans through W3C baggage.
    """
    out, spans = await run_once(engine, monkeypatch, exporter)
    assert out["status"] == "ok", out
    assert out["output"]["text"] == AFTER_TOOL

    assert {_hex(s)[0] for s in spans} == {CALLER_TRACE}, "every span is in the caller's trace"
    by_id = {_hex(s)[1]: s for s in spans}

    (run,) = _named(spans, "chassis.run")
    (connector,) = _named(spans, "chassis.engine.run")
    (handle,) = _named(spans, "workload.handle")
    assert _parent(run) == CALLER_SPAN, "the caller's traceparent is continued"
    assert _parent(connector) == _hex(run)[1]
    assert _parent(handle) == _hex(connector)[1], "the A2A traceparent names the connector span"

    workload_ids = {
        _hex(s)[1] for s in spans if s.resource.attributes["service.name"] == "workload"
    }
    framework = [s for s in spans if _scope(s).startswith("openinference")]
    assert framework, f"{engine}: no OpenInference span"
    for span in framework:
        hops, cursor = 0, span
        while _parent(cursor) != _hex(handle)[1]:
            parent_id = _parent(cursor)
            assert parent_id in workload_ids, f"{span.name} is not under workload.handle"
            cursor, hops = by_id[parent_id], hops + 1
            assert hops < 50

    model_calls = _named(spans, "chassis.model.call")
    tool_calls = _named(spans, "chassis.tool.call")
    assert len(model_calls) >= 2, "the tool scenario makes two model calls"
    assert tool_calls, "the tool call reached the chassis /mcp"
    for call in model_calls + tool_calls:
        assert _parent(call) in workload_ids, f"{call.name} is not a child of a workload span"
        assert (call.attributes or {}).get("correlated", True) is not False

    assert (run.attributes or {}).get("session.id") == SESSION
    assert (handle.attributes or {}).get("session.id") == SESSION, "baggage reached the workload"
    assert all((s.attributes or {}).get("session.id") == SESSION for s in framework)


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
