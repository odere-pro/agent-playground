"""PoC-2 engines: the three Python engines and the TypeScript echo behind the same contract.

Each test names the exit criterion in docs/planning/poc/002-PoC-2-two-engines-one-contract.md it
covers. No network for the Python engines: each workload's outbound HTTP is routed into the
chassis app over ASGI (`poc02_harness.route_outbound`), where the model proxy calls the fake model
server through the real LiteLLM adapter. No TCP for the TypeScript echo either: it listens on a
Unix socket (`UDS`) and calls the fake model server over another (`CHASSIS_MODEL_UDS`). It skips
only when its `node_modules` is absent (`npm ci`).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Any, ClassVar

import pytest
from chassis.core.envelope import Request, Response
from chassis.core.handle import echo
from chassis.fakes import FakeEngine
from chassis.ports.engine import EngineConnector
from chassis_contracts import EngineConnectorContract
from chassis_contracts.engine import JSON_VALUES_HANDLE
from chassis_contracts.helpers import make_request
from fake_model_server import Script
from fake_model_server import create_app as create_fake_model_app
from poc02_harness import (
    EXAMPLE_SCRIPT,
    SIMPLIFIED,
    SIMPLIFY,
    TIMEOUT_S,
    bundle_for,
    chassis_app,
    client_for,
    engine_params,
    route_outbound,
    run_handle,
    running,
    sse_frames,
    tool_list_failures,
    typescript_echo_on_unix_sockets,
)


@pytest.mark.parametrize("engine", engine_params())
async def test_python_engine_passes_offline_against_the_fake_model_server(
    engine: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit criterion: all three Python engines pass their tests offline, against the fake model
    server. The engine's `handle`, called in its wire form, streams the fake model's answer as
    deltas, reports the fake model's usage in `metrics`, and ends `ok`; its model call went to
    the chassis proxy, which the fake model server answered; its MCP listing did not fail.
    """
    app = chassis_app(engine)
    outbound = route_outbound(monkeypatch, app)
    failures = tool_list_failures()
    async with asyncio.timeout(TIMEOUT_S), running(app):
        events = await run_handle(engine, SIMPLIFY)
    assert tool_list_failures() == failures, f"{engine}: the MCP listing failed and was swallowed"
    kinds = [e["type"] for e in events]
    assert kinds[0] == "start" and kinds[-1] == "end", kinds
    assert "".join(e["text"] for e in events if e["type"] == "delta") == SIMPLIFIED
    metrics = [e for e in events if e["type"] == "metrics"]
    assert metrics and (metrics[-1]["input_tokens"], metrics[-1]["output_tokens"]) == (42, 9)
    assert events[-1]["status"] == "ok"
    assert outbound.to("/v1/chat/completions"), "the model call did not reach the chassis proxy"


# --- The same EnginePort suite over the fake engine and all four workloads ---


class TestFakeEngine(EngineConnectorContract):
    """Exit criterion: the fake engine and all four workloads pass the same `EnginePort` contract
    suite. The scripted fake engine, serving the chassis's own `echo`.

    The JSON-integer and `ctx.traceparent` cases skip here, on purpose: `FakeEngine` is a test
    double, not a lane. It hands `handle` the `Context` object, not wire JSON, and sets no
    `traceparent`, so there is no lane for those cases to check.
    """

    @pytest.fixture
    def engine(self) -> FakeEngine:
        return FakeEngine(handle=echo)

    @pytest.fixture
    def json_values_engine(self) -> EngineConnector:
        pytest.skip("FakeEngine is not a lane: no wire JSON and no traceparent to check")


class _WorkloadInProcess(EngineConnectorContract):
    """A workload behind the chassis's own `inprocess` connector (the one its lifespan sets up),
    its model call routed back into the chassis proxy. Not collected: no `Test` prefix.

    `json_values_engine` is the same chassis `inprocess` connector on the suite's own
    `JSON_VALUES_HANDLE`, so the JSON-integer and `ctx.traceparent` cases run for each Python
    engine's lane instead of skipping. It is the lane under test there, not the workload: a
    simplifier cannot echo `input.data` or `ctx` back.
    """

    handle_path: ClassVar[str]

    @pytest.fixture
    async def engine(self, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[EngineConnector]:
        app = chassis_app(self.handle_path)
        route_outbound(monkeypatch, app)
        async with running(app):
            yield app.state.ports.engine

    @pytest.fixture
    async def json_values_engine(self) -> AsyncIterator[EngineConnector]:
        app = chassis_app(JSON_VALUES_HANDLE)
        async with running(app):
            yield app.state.ports.engine

    @pytest.fixture
    def run_request(self) -> Request:
        return make_request(text=SIMPLIFY)


class TestEchoPythonEngine(_WorkloadInProcess):
    """Exit criterion: the fake engine and all four workloads pass the same `EnginePort` contract
    suite. Workload 1 of 4: plain Python.
    """

    handle_path = "echo_python:handle"


class TestEchoPydanticAIEngine(_WorkloadInProcess):
    """Exit criterion: the fake engine and all four workloads pass the same `EnginePort` contract
    suite. Workload 2 of 4: PydanticAI.
    """

    handle_path = "echo_pydanticai:handle"


class TestEchoLangGraphEngine(_WorkloadInProcess):
    """Exit criterion: the fake engine and all four workloads pass the same `EnginePort` contract
    suite. Workload 3 of 4: LangGraph.
    """

    handle_path = "echo_langgraph:handle"


@dataclass(frozen=True)
class TypeScriptEcho:
    url: str
    """What the echo's agent card names; over `uds` it only fills the `Host` header."""
    uds: str
    """The Unix socket the echo listens on (`UDS`)."""
    fake_model: Any
    """The fake model server app the echo calls over its own Unix socket."""


@pytest.fixture(scope="module")
def typescript() -> Iterator[TypeScriptEcho]:
    """The TypeScript echo (`node dist/src/main.js`) on a Unix socket, its model calls going to
    the fake model server (example script) over another Unix socket. No TCP. Skips only when the
    workload's `node_modules` is absent (`npm ci`).
    """
    fake_model = create_fake_model_app(Script.from_yaml(EXAMPLE_SCRIPT))
    with typescript_echo_on_unix_sockets(fake_model) as (url, uds):
        yield TypeScriptEcho(url, uds, fake_model)


def _sidecar_spec(typescript: TypeScriptEcho) -> dict[str, Any]:
    return {"connector": "sidecar", "url": typescript.url, "uds": typescript.uds}


@pytest.mark.slow
class TestEchoTypeScriptEngine(EngineConnectorContract):
    """Exit criterion: the fake engine and all four workloads pass the same `EnginePort` contract
    suite. Workload 4 of 4: the TypeScript echo, over the `sidecar` connector on a Unix socket.

    The JSON-integer and `ctx.traceparent` cases stay skipped here, on purpose: the TypeScript
    workload serves one fixed `handle`, the simplifier, so it cannot run the suite's custom
    `json_values_handle`. Its integers both ways are checked in `npm test`
    (`test/wire.test.ts`), and its `traceparent` on the model call in `test/handle.test.ts`.
    """

    @pytest.fixture
    async def engine(self, typescript: TypeScriptEcho) -> AsyncIterator[EngineConnector]:
        from chassis.adapters.a2a.sidecar import SidecarConnector

        connector = SidecarConnector()
        await connector.setup(_sidecar_spec(typescript), bundle_for(connector))
        try:
            yield connector
        finally:
            await connector.close()

    @pytest.fixture
    def json_values_engine(self) -> EngineConnector:
        pytest.skip(
            "echo-typescript serves the fixed simplifier handle; it cannot run json_values_handle"
        )

    @pytest.fixture
    def run_request(self) -> Request:
        return make_request(text=SIMPLIFY)


# --- The same response-shape checks, streaming and complete ---


async def _complete_and_streamed(
    app: Any, text: str
) -> tuple[Response, list[tuple[str, dict[str, Any]]]]:
    body = {"input": {"text": text}}
    async with (
        asyncio.timeout(TIMEOUT_S),
        running(app),
        client_for(app) as client,
    ):
        complete = Response.model_validate((await client.post("/v1/run", json=body)).json())
        streamed = await client.post("/v1/run", json={**body, "stream": True})
    return complete, sse_frames(streamed.text)


def _assert_same_shape(complete: Response, frames: list[tuple[str, dict[str, Any]]]) -> str:
    """The response-shape checks every engine must pass. Returns the text."""
    names = [name for name, _ in frames]
    assert names[0] == "start" and names[-2:] == ["end", "response"], names
    assert "delta" in names and "metrics" in names, names
    final = Response.model_validate(frames[-1][1])
    streamed_text = "".join(data["text"] for name, data in frames if name == "delta")
    assert complete.status == final.status == "ok"
    assert streamed_text == complete.output["text"] == final.output["text"] != ""
    assert complete.metrics == final.metrics
    assert type(complete.metrics["input_tokens"]) is int
    assert type(complete.metrics["output_tokens"]) is int
    assert complete.versions == final.versions
    assert complete.versions.prompt == "simplifier-v1"
    return streamed_text


@pytest.mark.parametrize("engine", engine_params())
async def test_python_engine_response_shape_streaming_and_complete(
    engine: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit criterion: all four engines pass the same response-shape tests, streaming and
    complete. `POST /v1/run` complete and streamed: `start` first, `end` then `response` last,
    the streamed deltas equal the complete output, same metrics, same versions.
    """
    app = chassis_app(engine)
    route_outbound(monkeypatch, app)
    complete, frames = await _complete_and_streamed(app, SIMPLIFY)
    assert _assert_same_shape(complete, frames) == SIMPLIFIED
    assert (complete.metrics["input_tokens"], complete.metrics["output_tokens"]) == (42, 9)


@pytest.mark.slow
async def test_typescript_echo_response_shape_streaming_and_complete(
    typescript: TypeScriptEcho, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit criterion: all four engines pass the same response-shape tests, streaming and
    complete. The TypeScript echo behind `spec.engine.connector: sidecar` (on a Unix socket), same
    checks.

    The TypeScript echo runs the simplifier, not the chassis's `echo`, so it is compared with its
    Python twin: `echo_python` behind `inprocess`, its model call through the chassis proxy to the
    fake model server (same script), gives the same frame sequence, output, and metrics.
    """
    from chassis.adapters.a2a.sidecar import SidecarConnector

    ts_app = chassis_app(connector=SidecarConnector(), engine=_sidecar_spec(typescript))
    ts_complete, ts_frames = await _complete_and_streamed(ts_app, SIMPLIFY)
    py_app = chassis_app("echo_python:handle")
    route_outbound(monkeypatch, py_app)
    failures = tool_list_failures()
    py_complete, py_frames = await _complete_and_streamed(py_app, SIMPLIFY)
    assert tool_list_failures() == failures, "echo_python: the MCP listing failed and was swallowed"
    assert _assert_same_shape(ts_complete, ts_frames) == SIMPLIFIED
    assert _assert_same_shape(py_complete, py_frames) == SIMPLIFIED
    assert [name for name, _ in ts_frames] == [name for name, _ in py_frames]
    assert ts_complete.output == py_complete.output
    assert (ts_complete.metrics["input_tokens"], ts_complete.metrics["output_tokens"]) == (42, 9)
    assert ts_complete.metrics.keys() == py_complete.metrics.keys()
    assert typescript.fake_model.state.calls, "the TypeScript echo did not call the fake model"
