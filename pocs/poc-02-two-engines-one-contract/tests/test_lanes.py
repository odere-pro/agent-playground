"""PoC-2 lanes: the same `handle` over A2A in memory (`inprocess`) and over A2A on localhost
(`sidecar`) gives the same envelope and event stream.

Each test names the exit criterion in docs/planning/poc/002-PoC-2-two-engines-one-contract.md it
covers. The sidecar runs on a Unix socket (no TCP; `make test` allows Unix sockets). So does the
TypeScript echo, and the fake model server it calls; its twin test skips only when its
`node_modules` is absent (`npm ci`). One `network` variant runs the same twin over TCP loopback.
`echo_python` lists its tools from the chassis's `/mcp`, served on a Unix socket too, so no lane
runs it without tools.
"""

from __future__ import annotations

import asyncio
import importlib
import subprocess
import sys
import textwrap
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
from chassis.adapters.a2a import InProcessConnector
from chassis.core.collector import collect
from chassis.core.envelope import Versions
from chassis.core.events import Event, parse_event
from chassis.ports.engine import EngineConnector
from fake_model_server import Script
from fake_model_server import create_app as create_fake_model_app
from poc02_harness import (
    EXAMPLE_SCRIPT,
    ROOT,
    SIMPLIFIED,
    SIMPLIFY,
    TIMEOUT_S,
    a2a_on_unix_socket,
    bundle_for,
    chassis_tools_on_unix_socket,
    free_port,
    point_tools_at,
    request_and_context,
    require_tcp,
    serve_tcp,
    tool_list_failures,
    typescript_echo,
    typescript_echo_on_unix_sockets,
    uds_transport,
)

CASES = [
    pytest.param("chassis.core.handle:echo_wire", "hello big world", id="echo"),
    pytest.param("echo_python:handle", SIMPLIFY, id="echo_python"),
    pytest.param("echo_python:handle", "fail on purpose", id="echo_python-error"),
]


@pytest.fixture(scope="module")
def chassis_tools_uds() -> Iterator[str]:
    """The chassis proxy app, `/mcp` over the fake `ToolPort`, on a Unix socket for this module."""
    with chassis_tools_on_unix_socket() as uds:
        yield uds


@pytest.fixture(autouse=True)
def echo_python_on_fake_model_server(
    chassis_tools_uds: str, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """echo_python's model call goes to the fake model server over ASGI, and its MCP calls to the
    chassis's `/mcp` on a Unix socket, in both lanes (the sidecar's thread shares the module), so
    the only difference between runs is the lane.
    """
    workload: Any = importlib.import_module("echo_python.handle")
    app = create_fake_model_app(Script.from_yaml(EXAMPLE_SCRIPT))
    monkeypatch.setattr(workload, "transport", httpx.ASGITransport(app=app))
    point_tools_at(monkeypatch, uds_transport(chassis_tools_uds))
    yield


async def _run(connector: EngineConnector, text: str) -> tuple[list[dict[str, Any]], Any]:
    """The event stream as dicts, and the envelope `collect` builds from it."""
    request, ctx = request_and_context(text)
    events: list[Event] = [e async for e in connector.run(request, ctx)]

    async def replay() -> AsyncIterator[Event]:
        for event in events:
            yield event

    envelope = await collect(replay(), request, Versions(chassis="0.1.0", model_route="r"))
    return [e.model_dump(mode="json") for e in events], envelope.model_dump(mode="json")


async def _inprocess(handle: str, text: str) -> tuple[list[dict[str, Any]], Any]:
    connector = InProcessConnector()
    await connector.setup({"connector": "inprocess", "handle": handle}, bundle_for(connector))
    try:
        return await _run(connector, text)
    finally:
        await connector.close()


async def _sidecar(spec: dict[str, Any], text: str) -> tuple[list[dict[str, Any]], Any]:
    from chassis.adapters.a2a.sidecar import SidecarConnector

    connector = SidecarConnector()
    assert connector.kind == "sidecar"
    await connector.setup({"connector": "sidecar", **spec}, bundle_for(connector))
    try:
        return await _run(connector, text)
    finally:
        await connector.close()


@pytest.mark.parametrize(("handle", "text"), CASES)
async def test_sidecar_and_inprocess_give_the_same_stream(handle: str, text: str) -> None:
    """Exit criterion: the lane contract suite passes: every case gives the same envelope and
    event stream over A2A on localhost and in memory. The same `handle` behind `inprocess` and
    behind `sidecar` (the workload's own template server on a Unix socket) yields equal events,
    in order, and equal envelopes, for a plain echo, a model call, and a model error. Neither
    lane ran echo_python without its tools.
    """
    failures = tool_list_failures()
    async with asyncio.timeout(TIMEOUT_S):
        local_events, local_envelope = await _inprocess(handle, text)
        with a2a_on_unix_socket(handle) as (url, uds):
            sidecar_events, sidecar_envelope = await _sidecar({"url": url, "uds": uds}, text)
    assert local_events[0]["type"] == "start"
    assert local_events[-1]["type"] in ("end", "error")
    assert sidecar_events == local_events
    assert sidecar_envelope == local_envelope
    assert tool_list_failures() == failures, "the MCP listing failed and was swallowed"


_ONLY_LANE_SUITE = textwrap.dedent(
    """
    import sys

    import pytest
    from chassis_contracts.lane import LaneContract


    class OnlyLaneSuite:
        def pytest_collection_modifyitems(self, config, items):
            keep = [i for i in items if i.cls is not None and issubclass(i.cls, LaneContract)]
            config.hook.pytest_deselected(items=[i for i in items if i not in keep])
            items[:] = keep


    sys.exit(pytest.main(sys.argv[1:], plugins=[OnlyLaneSuite()]))
    """
)


@pytest.mark.slow
def test_lane_contract_suite_passes_over_both_lanes() -> None:
    """Exit criterion: the lane contract suite passes: every case gives the same envelope and
    event stream over A2A on localhost and in memory. Runs every binding of
    `chassis_contracts.lane.LaneContract` under `packages/`, offline, and needs passing cases
    for both the `inprocess` and the `sidecar` lane.
    """
    from chassis_contracts.lane import LaneContract

    assert LaneContract.__name__.endswith("Contract"), "a suite is not collected on its own"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            _ONLY_LANE_SUITE,
            "packages",
            "-q",
            "-rA",
            "-p",
            "no:cacheprovider",
            "--disable-socket",
            "--allow-unix-socket",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
    passed = [line for line in result.stdout.splitlines() if line.startswith("PASSED")]
    assert any("inprocess" in line for line in passed), result.stdout[-4000:]
    assert any("sidecar" in line for line in passed), result.stdout[-4000:]


# --- The TypeScript echo against its Python twin ---


def _assert_twins(
    ts: tuple[list[dict[str, Any]], Any], py: tuple[list[dict[str, Any]], Any], fake_model: Any
) -> None:
    """The same event stream (number types included) and envelope, and the same model request."""
    from chassis_contracts.lane import assert_same

    (ts_events, ts_envelope), (py_events, py_envelope) = ts, py
    assert py_events[0]["type"] == "start" and py_events[-1]["type"] == "end"
    assert py_envelope["output"]["text"] == SIMPLIFIED, py_envelope
    for event in ts_events:
        parse_event(event)
    assert_same(ts_events, py_events, "events, echo-typescript vs echo_python")
    assert_same(ts_envelope, py_envelope, "envelope, echo-typescript vs echo_python")
    ts_call, py_call = fake_model.state.calls
    assert ts_call["messages"] == py_call["messages"], (ts_call, py_call)
    assert ts_call["model"] == py_call["model"]


@pytest.mark.slow
async def test_typescript_echo_gives_the_same_stream_as_echo_python(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exit criterion: the TypeScript echo passes the lane contract suite with no chassis change.
    The TypeScript echo (`node dist/src/main.js` on a Unix socket) and the Python `echo_python`
    under `workload_a2a` (on a Unix socket), both calling one fake model server (same app, same
    script; the TypeScript echo over its own Unix socket), get the same request from the same
    `SidecarConnector`; they give the same event stream (number types included) and the same
    envelope, and send the model the same messages. No TCP.

    The TypeScript workload serves one fixed `handle`, the simplifier, so it cannot run the
    `LaneContract` cases that need a custom handle (multi-delta, JSON values, tool call, error,
    `end.output`, bad event, and the traceparent echo); this case compares it with its Python
    twin instead.
    """
    fake_model = create_fake_model_app(Script.from_yaml(EXAMPLE_SCRIPT))
    workload: Any = importlib.import_module("echo_python.handle")
    monkeypatch.setattr(workload, "transport", httpx.ASGITransport(app=fake_model))
    failures = tool_list_failures()
    with (
        typescript_echo_on_unix_sockets(fake_model) as (ts_url, ts_uds),
        a2a_on_unix_socket("echo_python:handle") as (py_url, py_uds),
    ):
        async with asyncio.timeout(TIMEOUT_S):
            ts = await _sidecar({"url": ts_url, "uds": ts_uds}, SIMPLIFY)
            py = await _sidecar({"url": py_url, "uds": py_uds}, SIMPLIFY)
    _assert_twins(ts, py, fake_model)
    assert tool_list_failures() == failures, "echo_python: the MCP listing failed and was swallowed"


@pytest.mark.network
@pytest.mark.slow
async def test_typescript_echo_gives_the_same_stream_as_echo_python_over_tcp(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit criterion: the TypeScript echo passes the lane contract suite with no chassis change.
    The same twin comparison as the gated test, over TCP on loopback the way the sidecar runs in
    Compose: both workloads on a free loopback port, both calling one fake model server over TCP.
    Needs TCP: run with `uv run pytest -m network -p no:socket`.
    """
    require_tcp(request)
    from chassis.adapters.a2a.inprocess import load_handle
    from workload_a2a.server import build_agent_card as card
    from workload_a2a.server import build_app

    fake_model = create_fake_model_app(Script.from_yaml(EXAMPLE_SCRIPT))
    workload: Any = importlib.import_module("echo_python.handle")
    monkeypatch.setattr(workload, "transport", None)  # a real socket, like the TypeScript echo

    def python_server(url: str) -> Any:
        return build_app(
            load_handle("echo_python:handle"), card(name="echo-python", version="0.1.0", url=url)
        )

    with serve_tcp(lambda _: fake_model) as model_host:
        model_url = f"{model_host}/v1"
        monkeypatch.setenv("CHASSIS_MODEL_URL", model_url)
        env = {"CHASSIS_MODEL_URL": model_url}
        with (
            typescript_echo(port=free_port(), env=env) as ts_url,
            serve_tcp(python_server) as py_url,
        ):
            async with asyncio.timeout(TIMEOUT_S):
                ts = await _sidecar({"url": ts_url}, SIMPLIFY)
                py = await _sidecar({"url": py_url}, SIMPLIFY)
    _assert_twins(ts, py, fake_model)
