"""One run traced through the chassis and a workload, joined by `traceparent` over A2A.

The workload's template A2A server runs on a Unix socket, on the test's event loop. The chassis
runs `OtelTelemetry`. Both export to the one in-memory exporter the caller passes.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from chassis.adapters.a2a import SidecarConnector
from chassis.adapters.a2a.inprocess import load_handle
from chassis.adapters.otel import OtelTelemetry
from chassis.fakes import InMemoryConfig
from chassis.fakes.tool import default_tools
from chassis.ports.bundle import PortBundle
from chassis.server import create_app
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from workload_a2a.server import build_agent_card, build_app

from poc_harness.apps import chassis_config, client_for, running
from poc_harness.outbound import route_outbound
from poc_harness.scenario import TIMEOUT_S, TOOL_PROMPT
from poc_harness.scripted_model import ToolLoopModel

CALLER_TRACE = "0af7651916cd43dd8448eb211c80319c"  # pragma: allowlist secret
CALLER_SPAN = "b7ad6b7169203331"  # pragma: allowlist secret
SIDECAR_URL = "http://127.0.0.1:9100"
SESSION = "sess-poc07"
ENGINES = {
    "echo-langgraph": "echo_langgraph.handle:handle",
    "echo-openai-agents": "echo_openai_agents.handle:handle",
}
"""The two engines run end to end. Each one's instrumentor is in its own `telemetry.py`."""


def caller_headers(session: str = SESSION) -> dict[str, str]:
    """A caller's trace context and session id."""
    return {"traceparent": f"00-{CALLER_TRACE}-{CALLER_SPAN}-01", "x-session-id": session}


@asynccontextmanager
async def workload_on_uds(handle_path: str) -> AsyncIterator[str]:
    """The workload's template A2A server on a Unix socket, on this event loop."""
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


def traced_ports(exporter: InMemorySpanExporter, **extra: Any) -> PortBundle:
    """The tool-loop model, a sidecar connector, and `OtelTelemetry` into `exporter`."""
    return PortBundle(
        model=ToolLoopModel(),
        engine=SidecarConnector(),
        config=InMemoryConfig(),
        telemetry=OtelTelemetry(span_exporters=[exporter], service_name="chassis"),
        tools=default_tools(),
        **extra,
    )


async def run_once(
    engine: str, monkeypatch: pytest.MonkeyPatch, exporter: InMemorySpanExporter
) -> tuple[dict[str, Any], list[ReadableSpan]]:
    """One tool-using `/v1/run` through the chassis to `engine` in the sidecar lane."""
    async with workload_on_uds(ENGINES[engine]) as uds:
        app = create_app(
            chassis_config({"connector": "sidecar", "url": SIDECAR_URL, "uds": uds}),
            traced_ports(exporter),
        )
        route_outbound(monkeypatch, app)
        body = {"input": {"text": TOOL_PROMPT}}
        async with asyncio.timeout(TIMEOUT_S), running(app), client_for(app) as client:
            response = await client.post("/v1/run", json=body, headers=caller_headers())
    assert response.status_code == 200, response.text
    return response.json(), list(exporter.get_finished_spans())


MAX_DEPTH = 50
"""suggested: a span chain deeper than this is a cycle, not a real trace."""


def trace_and_span_id(span: ReadableSpan) -> tuple[str, str]:
    """The span's trace id and span id, as W3C hex."""
    if span.context is None:
        raise ValueError(f"{span.name} has no span context")
    return f"{span.context.trace_id:032x}", f"{span.context.span_id:016x}"


def parent_span_id(span: ReadableSpan) -> str | None:
    """The parent's span id, as W3C hex, or `None` for a root span."""
    return f"{span.parent.span_id:016x}" if span.parent is not None else None


def ancestor_ids(span: ReadableSpan, spans: list[ReadableSpan]) -> list[str]:
    """The span ids from `span`'s parent up to the root, nearest first.

    A parent outside `spans` (the caller's span) ends the chain. A chain longer than `MAX_DEPTH`
    raises, because it can only be a cycle.
    """
    by_id = {trace_and_span_id(s)[1]: s for s in spans}
    chain: list[str] = []
    parent = parent_span_id(span)
    while parent is not None:
        chain.append(parent)
        if len(chain) > MAX_DEPTH:
            raise ValueError(f"{span.name}: span chain deeper than {MAX_DEPTH}")
        if parent not in by_id:
            break
        parent = parent_span_id(by_id[parent])
    return chain


def is_under_through(
    span: ReadableSpan, ancestor: str, via: set[str], spans: list[ReadableSpan]
) -> bool:
    """True when `ancestor` is in `span`'s chain and every span between them is in `via`."""
    chain = ancestor_ids(span, spans)
    if ancestor not in chain:
        return False
    return set(chain[: chain.index(ancestor)]) <= via
