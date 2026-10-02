"""`chassis.core.trace`: the W3C `traceparent` every A2A request of a run carries, and the trace id
the model proxy looks the run up by. The connector also puts it in `ctx.traceparent` (decision 4
in contract-v0.md, "Changes decided for v1"): the value `handle` sees equals the header the server
saw, in both lanes.
"""

from __future__ import annotations

import ast
import hashlib
import re
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from a2a_uds import SIDECAR_URL, Recorder, serve_uds
from chassis.adapters.a2a import InProcessConnector, SidecarConnector
from chassis.adapters.a2a.server import build_agent_card, build_app
from chassis.core import trace
from chassis.core.envelope import Context
from chassis.core.trace import trace_id_hex, traceparent_for
from chassis.fakes import InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.ports.bundle import PortBundle
from chassis_contracts.helpers import make_context, make_request

TRACEPARENT = re.compile(r"00-([0-9a-f]{32})-([0-9a-f]{16})-01")


def test_a_w3c_trace_id_passes_through() -> None:
    tid = "4bf92f3577b34da6a3ce929d0e0e4736"
    assert trace_id_hex(tid) == tid


@pytest.mark.parametrize(
    "tid", ["trace-1", "4BF92F3577B34DA6A3CE929D0E0E4736", "abc", "4bf92f35" * 5, ""]
)
def test_any_other_id_is_hashed(tid: str) -> None:
    assert trace_id_hex(tid) == hashlib.sha256(tid.encode()).hexdigest()[:32]


def test_traceparent_carries_the_run_trace_id() -> None:
    ctx = make_context(make_request())
    header = traceparent_for(ctx)
    match = TRACEPARENT.fullmatch(header)
    assert match is not None, header
    assert match.group(1) == trace_id_hex(ctx.trace_id)
    assert match.group(2) != "0" * 16


def test_span_id_is_new_per_call() -> None:
    ctx = make_context(make_request())
    assert len({traceparent_for(ctx).split("-")[2] for _ in range(20)}) == 20


def test_trace_imports_only_the_standard_library() -> None:
    tree = ast.parse(Path(trace.__file__).read_text())
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    assert roots - {"__future__", "chassis"} <= set(sys.stdlib_module_names), roots


def test_traceparent_uses_the_run_span_id_when_it_is_w3c() -> None:
    ctx = make_context(make_request())
    span = "00f067aa0ba902b7"
    assert traceparent_for(ctx, span_id=span) == f"00-{trace_id_hex(ctx.trace_id)}-{span}-01"


@pytest.mark.parametrize("span", ["span-1", "0" * 16, "00F067AA0BA902B7", "abc", None])
def test_traceparent_draws_a_span_id_when_the_adapter_has_none(span: str | None) -> None:
    """suggested: a fresh random 16-hex id when the telemetry adapter has no W3C span id."""
    ctx = make_context(make_request())
    match = TRACEPARENT.fullmatch(traceparent_for(ctx, span_id=span))
    assert match is not None and match.group(2) not in {span, "0" * 16}


def test_context_traceparent_is_optional() -> None:
    ctx = make_context(make_request())
    assert ctx.traceparent is None
    dumped = ctx.model_dump(mode="json")
    assert dumped["traceparent"] is None
    assert Context.model_validate({k: v for k, v in dumped.items() if k != "traceparent"}) == ctx


# --- ctx.traceparent reaches handle and equals the header ---


def _catching(seen: list[dict[str, Any]]) -> Any:
    async def handle(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
        seen.append(ctx)
        yield {"type": "start", "request_id": ctx["request_id"]}
        yield {"type": "end"}

    return handle


def _bundle(engine: Any) -> PortBundle:
    return PortBundle(
        model=ScriptedModel(), engine=engine, config=InMemoryConfig(), telemetry=InMemoryTelemetry()
    )


def _check(ctx_seen: dict[str, Any], header: str | None, ctx: Context) -> None:
    value = ctx_seen.get("traceparent")
    assert isinstance(value, str), ctx_seen
    match = TRACEPARENT.fullmatch(value)
    assert match is not None and match.group(1) == trace_id_hex(ctx.trace_id)
    assert value == header, "ctx.traceparent must equal the traceparent header"
    assert ctx.traceparent is None, "the caller's ctx is not changed"


async def test_sidecar_ctx_traceparent_equals_the_header() -> None:
    seen: list[dict[str, Any]] = []
    card = build_agent_card(name="trace", version="1", url=SIDECAR_URL)
    recorder = Recorder(build_app(_catching(seen), card))
    request = make_request()
    ctx = make_context(request)
    async with serve_uds(recorder) as path:
        connector = SidecarConnector()
        await connector.setup({"url": SIDECAR_URL, "uds": path}, _bundle(connector))
        try:
            _ = [e async for e in connector.run(request, ctx)]
        finally:
            await connector.close()
    [rpc] = recorder.rpc()
    [ctx_seen] = seen
    _check(ctx_seen, rpc.headers.get("traceparent"), ctx)


async def test_inprocess_ctx_traceparent_equals_the_header() -> None:
    seen: list[dict[str, Any]] = []
    connector = InProcessConnector()
    await connector.setup(
        {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"}, _bundle(connector)
    )
    transport: Any = connector.transport
    recorder = Recorder(build_app(_catching(seen), build_agent_card(name="trace", version="1")))
    transport.app = recorder
    request = make_request()
    ctx = make_context(request)
    try:
        _ = [e async for e in connector.run(request, ctx)]
    finally:
        await connector.close()
    [rpc] = recorder.rpc()
    [ctx_seen] = seen
    _check(ctx_seen, rpc.headers.get("traceparent"), ctx)


async def test_each_run_sets_its_own_ctx_traceparent() -> None:
    seen: list[dict[str, Any]] = []
    connector = InProcessConnector()
    await connector.setup(
        {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"}, _bundle(connector)
    )
    transport: Any = connector.transport
    transport.app = build_app(_catching(seen), build_agent_card(name="trace", version="1"))
    request = make_request()
    ctx = make_context(request).model_copy(
        update={"traceparent": "00-" + "1" * 32 + "-" + "2" * 16 + "-01"}
    )
    try:
        for _ in range(2):
            _ = [e async for e in connector.run(request, ctx)]
    finally:
        await connector.close()
    values = [c["traceparent"] for c in seen]
    assert len(set(values)) == 2 and ctx.traceparent not in values
    assert all(v.split("-")[1] == trace_id_hex(ctx.trace_id) for v in values)
