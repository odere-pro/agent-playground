"""The `sidecar` lane: the chassis's A2A client talks to the template server in another process,
here uvicorn on a Unix socket in a background task (the offline gate refuses TCP). It gives the same
events as `inprocess`, streams one delta at a time, times out mid-run and cancels, sends a
`traceparent` on every request of a run, and reaches loopback only (ADR-001).
"""

from __future__ import annotations

import asyncio
import importlib
import re
import socket
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from a2a_uds import SIDECAR_URL, Recorder, kill_connections, serve_uds, serve_uds_server
from chassis.adapters.a2a import InProcessConnector, SidecarConnector
from chassis.adapters.a2a.server import WireHandle, build_agent_card, build_app
from chassis.core.envelope import Budget, Request
from chassis.core.events import Delta, End, Error, Event, Start
from chassis.core.handle import echo_wire
from chassis.core.trace import trace_id_hex
from chassis.fakes import InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.ports.bundle import PortBundle
from chassis.profiles import REGISTRY, resolve
from chassis.server.config import EngineSpec, load_config
from chassis_contracts.helpers import make_context, make_request
from fake_model_server import Script, create_app

ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_SCRIPT = ROOT / "packages/fake-model-server/scripts/example.yaml"
SIDECAR_YAML = ROOT / "packages/chassis/configs/sidecar.yaml"
TRACEPARENT = re.compile(r"00-([0-9a-f]{32})-([0-9a-f]{16})-01")


def _bundle(engine: Any) -> PortBundle:
    return PortBundle(
        model=ScriptedModel(), engine=engine, config=InMemoryConfig(), telemetry=InMemoryTelemetry()
    )


def _app(handle: WireHandle, *, url: str = SIDECAR_URL) -> Any:
    return build_app(handle, build_agent_card(name="sidecar-test", version="1", url=url))


async def _sidecar(path: str, url: str = SIDECAR_URL) -> SidecarConnector:
    connector = SidecarConnector()
    await connector.setup({"connector": "sidecar", "url": url, "uds": path}, _bundle(connector))
    return connector


async def _inprocess(handle: str) -> InProcessConnector:
    connector = InProcessConnector()
    await connector.setup({"connector": "inprocess", "handle": handle}, _bundle(connector))
    return connector


async def _run(connector: Any, request: Request) -> list[Event]:
    try:
        async with asyncio.timeout(10):
            return [e async for e in connector.run(request, make_context(request))]
    finally:
        await connector.close()


@pytest.fixture
def echo_python() -> Iterator[Any]:
    """`echo_python:handle` with its model call routed to the fake model server. No socket."""
    workload: Any = importlib.import_module("echo_python.handle")
    workload.transport = httpx.ASGITransport(app=create_app(Script.from_yaml(EXAMPLE_SCRIPT)))
    try:
        yield workload
    finally:
        workload.transport = None


# --- same events as inprocess ---


async def test_echo_gives_the_same_events_as_inprocess() -> None:
    request = make_request(text="the same over both lanes")
    async with serve_uds(_app(echo_wire)) as path:
        over_socket = await _run(await _sidecar(path), request)
    in_memory = await _run(await _inprocess("chassis.core.handle:echo_wire"), request)
    assert over_socket == in_memory
    assert [e.type for e in over_socket][:2] == ["start", "delta"] and over_socket[-1] == End()


async def test_echo_python_gives_the_same_events_as_inprocess(echo_python: Any) -> None:
    request = make_request(text="simplify: the quick brown fox")
    async with serve_uds(_app(echo_python.handle)) as path:
        over_socket = await _run(await _sidecar(path), request)
    in_memory = await _run(await _inprocess("echo_python:handle"), request)
    assert over_socket == in_memory
    assert isinstance(over_socket[-1], End)


# --- streaming, timeout, cancel: live in this lane ---


async def test_deltas_arrive_one_at_a_time() -> None:
    """The first delta is yielded while the handle is still parked before the second."""
    gate = asyncio.Event()
    finished = asyncio.Event()

    async def gated(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
        yield {"type": "start", "request_id": ctx["request_id"]}
        yield {"type": "delta", "text": "first "}
        await gate.wait()
        yield {"type": "delta", "text": "second"}
        yield {"type": "end"}
        finished.set()

    request = make_request()
    async with serve_uds(_app(gated)) as path:
        connector = await _sidecar(path)
        seen: list[Event] = []
        try:
            async with asyncio.timeout(5):
                async for event in connector.run(request, make_context(request)):
                    seen.append(event)
                    if isinstance(event, Delta) and event.text == "first ":
                        assert not gate.is_set() and not finished.is_set()
                        gate.set()
        finally:
            await connector.close()
    assert seen == [
        Start(request_id=request.request_id),
        Delta(text="first "),
        Delta(text="second"),
        End(),
    ]


async def test_timeout_mid_run_yields_a2a_timeout_and_cancels() -> None:
    closed = asyncio.Event()

    async def stuck(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
        try:
            yield {"type": "start", "request_id": ctx["request_id"]}
            yield {"type": "delta", "text": "partial"}
            await asyncio.Event().wait()
            yield {"type": "end"}
        finally:
            closed.set()

    request = make_request().model_copy(update={"budget": Budget(timeout_ms=300)})
    recorder = Recorder(_app(stuck))
    async with serve_uds(recorder) as path:
        events = await _run(await _sidecar(path), request)
        async with asyncio.timeout(5):
            await closed.wait()
    assert [e.type for e in events] == ["start", "delta", "error"]
    error = events[-1]
    assert isinstance(error, Error) and error.code == "a2a.timeout" and error.retryable
    methods = [s.method for s in recorder.rpc()]
    assert methods == ["SendStreamingMessage", "CancelTask"], methods


async def test_closing_the_stream_early_cancels_the_task() -> None:
    closed = asyncio.Event()

    async def parked(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
        try:
            yield {"type": "start", "request_id": ctx["request_id"]}
            await asyncio.Event().wait()
            yield {"type": "end"}
        finally:
            closed.set()

    request = make_request()
    recorder = Recorder(_app(parked))
    async with serve_uds(recorder) as path:
        connector = await _sidecar(path)
        stream = connector.run(request, make_context(request))
        try:
            assert isinstance(await anext(stream), Start)
            await stream.aclose()  # type: ignore[attr-defined]
            async with asyncio.timeout(5):
                await closed.wait()
        finally:
            await connector.close()
    assert [s.method for s in recorder.rpc()] == ["SendStreamingMessage", "CancelTask"]


async def test_a_sidecar_that_dies_mid_stream_is_a2a_transport() -> None:
    """The socket drops after `start`: the run ends with one retryable `error`, not an exception."""
    started = asyncio.Event()

    async def parked(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
        yield {"type": "start", "request_id": ctx["request_id"]}
        started.set()
        await asyncio.Event().wait()
        yield {"type": "end"}

    request = make_request()
    async with serve_uds_server(_app(parked)) as (path, server):
        connector = await _sidecar(path)
        seen: list[Event] = []
        try:
            async with asyncio.timeout(5):
                async for event in connector.run(request, make_context(request)):
                    seen.append(event)
                    if isinstance(event, Start):
                        await started.wait()
                        assert kill_connections(server) >= 1
        finally:
            await connector.close()
    assert [e.type for e in seen] == ["start", "error"]
    error = seen[-1]
    assert isinstance(error, Error) and error.code == "a2a.transport" and error.retryable


# --- traceparent on every request of a run ---


async def test_every_request_of_a_run_carries_the_traceparent() -> None:
    async def stuck(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
        yield {"type": "start", "request_id": ctx["request_id"]}
        await asyncio.Event().wait()
        yield {"type": "end"}

    request = make_request().model_copy(update={"budget": Budget(timeout_ms=300)})
    recorder = Recorder(_app(stuck))
    async with serve_uds(recorder) as path:
        await _run(await _sidecar(path), request)
    rpc = recorder.rpc()
    assert len(rpc) == 2
    for seen in rpc:
        match = TRACEPARENT.fullmatch(seen.headers.get("traceparent", ""))
        assert match is not None, seen.headers
        assert match.group(1) == trace_id_hex(request.trace_id)


async def test_the_traceparent_keeps_a_w3c_trace_id() -> None:
    w3c = "4bf92f3577b34da6a3ce929d0e0e4736"
    request = make_request().model_copy(update={"trace_id": w3c})
    recorder = Recorder(_app(echo_wire))
    async with serve_uds(recorder) as path:
        await _run(await _sidecar(path), request)
    [seen] = recorder.rpc()
    assert seen.headers["traceparent"].split("-")[1] == w3c


async def test_inprocess_sets_the_same_traceparent() -> None:
    connector = await _inprocess("chassis.core.handle:echo_wire")
    transport: Any = connector.transport
    recorder = Recorder(transport.app)
    transport.app = recorder
    request = make_request()
    await _run(connector, request)
    [seen] = recorder.rpc()
    match = TRACEPARENT.fullmatch(seen.headers.get("traceparent", ""))
    assert match is not None and match.group(1) == trace_id_hex(request.trace_id)


async def test_each_run_gets_its_own_span_id() -> None:
    recorder = Recorder(_app(echo_wire))
    async with serve_uds(recorder) as path:
        connector = await _sidecar(path)
        try:
            for _ in range(2):
                request = make_request()
                _ = [e async for e in connector.run(request, make_context(request))]
        finally:
            await connector.close()
    spans = {s.headers["traceparent"].split("-")[2] for s in recorder.rpc()}
    assert len(spans) == 2


async def test_the_card_fetch_is_outside_any_run() -> None:
    recorder = Recorder(_app(echo_wire))
    async with serve_uds(recorder) as path:
        connector = await _sidecar(path)
        await connector.close()
    [card] = recorder.seen
    assert card.path.endswith("agent-card.json") and "traceparent" not in card.headers


# --- one span per run ---


async def test_one_span_per_run_with_the_task_id() -> None:
    async with serve_uds(_app(echo_wire)) as path:
        connector = SidecarConnector()
        bundle = _bundle(connector)
        await connector.setup({"url": SIDECAR_URL, "uds": path}, bundle)
        request = make_request()
        await _run(connector, request)
    telemetry: Any = bundle.telemetry
    [span] = telemetry.spans
    assert span.name == "chassis.engine.run" and span.attributes["lane"] == "sidecar"
    assert span.attributes["a2a.task_id"]


# --- loopback only, and a clear failure when the sidecar is not up ---


@pytest.mark.parametrize(
    "url",
    [
        "http://10.0.0.5:9000",
        "http://example.com:9000",
        "http://0.0.0.0:9000",
        "https://127.0.0.1:9000",
        "http://127.0.0.1:9000/a2a",
        "http://127.0.0.1",
        "http://user@127.0.0.1:9000",
        "http://127.0.0.1:9000?x=1",
        "http://127.0.0.1.example.com:9000",
    ],
)
async def test_a_non_loopback_url_is_refused(url: str) -> None:
    connector = SidecarConnector()
    with pytest.raises(ValueError, match="ADR-001"):
        await connector.setup({"url": url}, _bundle(connector))


@pytest.mark.parametrize("url", ["http://127.0.0.1:9000", "http://localhost:9000/"])
async def test_loopback_urls_are_accepted(url: str) -> None:
    async with serve_uds(_app(echo_wire, url=url)) as path:
        connector = await _sidecar(path, url)
        await connector.close()


async def test_url_is_required() -> None:
    connector = SidecarConnector()
    with pytest.raises(ValueError, match=r"engine\.url"):
        await connector.setup({"connector": "sidecar"}, _bundle(connector))


async def test_a_card_that_points_off_host_is_refused() -> None:
    async with serve_uds(_app(echo_wire, url="http://10.0.0.5:9000")) as path:
        connector = SidecarConnector()
        with pytest.raises(ValueError, match="ADR-001"):
            await connector.setup({"url": SIDECAR_URL, "uds": path}, _bundle(connector))


async def test_setup_against_a_dead_socket_fails_clearly(tmp_path: Path) -> None:
    connector = SidecarConnector()
    dead = str(tmp_path / "nobody-home.sock")
    with pytest.raises(RuntimeError, match=re.escape(SIDECAR_URL)):
        await connector.setup({"url": SIDECAR_URL, "uds": dead}, _bundle(connector))


async def test_run_before_setup_is_an_error() -> None:
    connector = SidecarConnector()
    request = make_request()
    with pytest.raises(RuntimeError, match="sidecar connector is not set up"):
        await anext(connector.run(request, make_context(request)))


# --- config picks the lane ---


def test_registry_builds_the_sidecar_connector() -> None:
    assert REGISTRY["engine"]["sidecar"] is SidecarConnector
    assert isinstance(resolve("engine", "sidecar"), SidecarConnector)


def test_engine_spec_names_url_and_uds() -> None:
    spec = EngineSpec(connector="sidecar", url=SIDECAR_URL, uds="/tmp/a.sock")
    assert spec.as_mapping() == {"connector": "sidecar", "url": SIDECAR_URL, "uds": "/tmp/a.sock"}


def test_sidecar_yaml_loads() -> None:
    config = load_config(SIDECAR_YAML)
    assert config.profile == "local"
    assert config.spec.engine.connector == "sidecar"
    assert config.spec.engine.url == SIDECAR_URL
    assert config.spec.adapters is not None
    assert config.spec.adapters.model == "litellm"  # the lane is spec.engine.connector only


# --- over TCP: only when sockets are allowed ---


def _tcp_allowed() -> bool:
    try:
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).close()
    except Exception:  # pytest-socket raises its own error type when TCP is disabled
        return False
    return True


@pytest.mark.network
async def test_over_tcp_on_loopback() -> None:
    if not _tcp_allowed():
        pytest.skip("TCP is disabled (the offline gate); run with `uv run pytest -m network`")
    import uvicorn

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(
        uvicorn.Config(_app(echo_wire, url=url), host="127.0.0.1", port=port, log_level="warning")
    )
    task = asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if task.done():
                    task.result()
                    raise RuntimeError("uvicorn exited before it started")
                await asyncio.sleep(0.01)
        connector = SidecarConnector()
        await connector.setup({"url": url}, _bundle(connector))
        events = await _run(connector, make_request())
    finally:
        server.should_exit = True
        await task
    assert isinstance(events[0], Start) and isinstance(events[-1], End)
