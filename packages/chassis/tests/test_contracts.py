"""Every fake in this package passes its port's contract suite. Real adapters bind the same
classes.
"""

from __future__ import annotations

import importlib
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from chassis.adapters.a2a import InProcessConnector, SidecarConnector
from chassis.adapters.a2a.server import build_agent_card, build_app
from chassis.adapters.litellm import LiteLLMModel
from chassis.core.envelope import Request
from chassis.core.events import Delta, End, Start
from chassis.core.handle import echo, echo_wire
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel, ScriptRule
from chassis.fakes.tool import InMemoryTools, default_tools
from chassis.ports.bundle import PortBundle
from chassis.ports.model import ModelMessage, ToolCallRequest, ToolSpec
from chassis_contracts import (
    ConfigPortContract,
    EngineConnectorContract,
    ModelPortContract,
    TelemetryPortContract,
    ToolPortContract,
)
from chassis_contracts.config import Bump
from chassis_contracts.engine import JSON_VALUES_HANDLE, json_values_handle
from chassis_contracts.helpers import make_request
from chassis_contracts.model import ReceivedMessages, ToolCallCase
from chassis_contracts.telemetry import ReadCounter, ReadSpans
from chassis_contracts.tool import KnownCall
from fake_model_server import Script, create_app
from fastapi import FastAPI

ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_SCRIPT = ROOT / "packages/fake-model-server/scripts/example.yaml"


async def _inprocess(handle: str) -> InProcessConnector:
    """An `InProcessConnector` set up the way the server's lifespan does it, from `spec.engine`."""
    connector = InProcessConnector()
    bundle = PortBundle(
        model=ScriptedModel(),
        engine=connector,
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
    )
    await connector.setup({"connector": "inprocess", "handle": handle}, bundle)
    return connector


class TestScriptedModel(ModelPortContract):
    @pytest.fixture
    def model_port(self) -> ScriptedModel:
        return ScriptedModel(
            [
                ScriptRule(
                    match="lookup", tool_call=ToolCallRequest(call_id="c1", name="glossary_lookup")
                ),
                ScriptRule(match="fail", error="upstream_down", retryable=True),
                ScriptRule(reply="Plain words. Short sentences."),
            ]
        )

    @pytest.fixture
    def received_messages(self, model_port: ScriptedModel) -> ReceivedMessages:
        return lambda: model_port.calls[-1]

    @pytest.fixture
    def tool_call_case(self) -> ToolCallCase:
        return ToolCallCase(
            [ModelMessage(role="user", content="lookup SLM")],
            [ToolSpec(name="glossary_lookup")],
            "glossary_lookup",
        )

    @pytest.fixture
    def error_messages(self) -> list[ModelMessage]:
        return [ModelMessage(role="user", content="fail")]


def _from_openai(message: dict[str, Any]) -> ModelMessage:
    """Read back what `LiteLLMModel` put on the wire. `function.arguments` must be a JSON string."""
    calls = message.get("tool_calls")
    tool_calls = None
    if calls is not None:
        tool_calls = []
        for call in calls:
            assert call["type"] == "function"
            assert isinstance(call["function"]["arguments"], str)
            tool_calls.append(
                ToolCallRequest(
                    call_id=call["id"],
                    name=call["function"]["name"],
                    arguments=json.loads(call["function"]["arguments"]),
                )
            )
    return ModelMessage(
        role=message["role"],
        content=message["content"],
        name=message.get("name"),
        tool_calls=tool_calls,
        tool_call_id=message.get("tool_call_id"),
    )


class TestLiteLLMModel(ModelPortContract):
    """The real adapter, bound to the same suite, over an ASGI transport to the fake model server.
    No socket.
    """

    @pytest.fixture
    def fake_server(self) -> FastAPI:
        return create_app(Script.from_yaml(EXAMPLE_SCRIPT))

    @pytest.fixture
    def model_port(self, fake_server: FastAPI) -> LiteLLMModel:
        return LiteLLMModel("http://fake/v1", transport=httpx.ASGITransport(app=fake_server))

    @pytest.fixture
    def received_messages(self, fake_server: FastAPI) -> ReceivedMessages:
        return lambda: [_from_openai(m) for m in fake_server.state.calls[-1]["messages"]]

    @pytest.fixture
    def tool_call_case(self) -> ToolCallCase:
        return ToolCallCase(
            [ModelMessage(role="user", content="glossary SLM")],
            [ToolSpec(name="glossary_lookup")],
            "glossary_lookup",
        )

    @pytest.fixture
    def error_messages(self) -> list[ModelMessage]:
        return [ModelMessage(role="user", content="fail")]


class TestFakeEngineHandle(EngineConnectorContract):
    @pytest.fixture
    def engine(self) -> FakeEngine:
        return FakeEngine(handle=echo)


class TestFakeEngineScripted(EngineConnectorContract):
    @pytest.fixture
    def engine(self) -> FakeEngine:
        return FakeEngine(events=[Start(request_id="req-1"), Delta(text="scripted"), End()])


class TestInProcessConnectorEcho(EngineConnectorContract):
    """The `inprocess` lane over A2A in memory, serving the chassis's own `echo` in wire form."""

    @pytest.fixture
    async def engine(self) -> AsyncIterator[InProcessConnector]:
        connector = await _inprocess("chassis.core.handle:echo_wire")
        yield connector
        await connector.close()

    @pytest.fixture
    async def json_values_engine(self) -> AsyncIterator[InProcessConnector]:
        connector = await _inprocess(JSON_VALUES_HANDLE)
        yield connector
        await connector.close()


class TestInProcessConnectorEchoPython(EngineConnectorContract):
    """The same lane serving the plain-Python plug-in, whose model call goes to the fake model
    server over an ASGI transport. No socket.
    """

    @pytest.fixture
    async def engine(self) -> AsyncIterator[InProcessConnector]:
        workload: Any = importlib.import_module("echo_python.handle")
        workload.transport = httpx.ASGITransport(app=create_app(Script.from_yaml(EXAMPLE_SCRIPT)))
        connector = await _inprocess("echo_python:handle")
        try:
            yield connector
        finally:
            workload.transport = None
            await connector.close()

    @pytest.fixture
    def run_request(self) -> Request:
        return make_request(text="simplify: the quick brown fox")


class TestInMemoryConfig(ConfigPortContract):
    @pytest.fixture
    def config_port(self) -> InMemoryConfig:
        return InMemoryConfig({"echo": {"spec": {"kind": "transformer"}}})

    @pytest.fixture
    def bump_config(self, config_port: InMemoryConfig, config_name: str) -> Bump:
        async def bump() -> None:
            await config_port.put(config_name, {"spec": {"kind": "transformer", "v": 2}})

        return bump


class TestInMemoryTelemetry(TelemetryPortContract):
    @pytest.fixture
    def telemetry(self) -> InMemoryTelemetry:
        return InMemoryTelemetry()

    @pytest.fixture
    def read_spans(self, telemetry: InMemoryTelemetry) -> ReadSpans:
        return lambda: list(telemetry.spans)

    @pytest.fixture
    def read_counter(self, telemetry: InMemoryTelemetry) -> ReadCounter:
        return lambda name: telemetry.counter_value(name)


class TestInMemoryTools(ToolPortContract):
    @pytest.fixture
    def tool_port(self) -> InMemoryTools:
        return default_tools()

    @pytest.fixture
    def known_call(self) -> KnownCall:
        return KnownCall("glossary_lookup", {"term": "SLM"})


async def _sidecar_over(app: Any) -> AsyncIterator[SidecarConnector]:
    """A `SidecarConnector` set up against `app`, served on uvicorn over a Unix socket in a
    background task. The offline gate refuses TCP and allows Unix sockets.
    """
    from a2a_uds import SIDECAR_URL, serve_uds

    async with serve_uds(app) as path:
        connector = SidecarConnector()
        bundle = PortBundle(
            model=ScriptedModel(),
            engine=connector,
            config=InMemoryConfig(),
            telemetry=InMemoryTelemetry(),
        )
        await connector.setup({"connector": "sidecar", "url": SIDECAR_URL, "uds": path}, bundle)
        try:
            yield connector
        finally:
            await connector.close()


def _chassis_server(handle: Any, name: str) -> Any:
    from a2a_uds import SIDECAR_URL

    return build_app(handle, build_agent_card(name=name, version="1", url=SIDECAR_URL))


def _workload_server(handle: Any, name: str) -> Any:
    from a2a_uds import SIDECAR_URL
    from workload_a2a.server import build_agent_card as card
    from workload_a2a.server import build_app as app

    return app(handle, card(name=name, version="1", url=SIDECAR_URL))


class TestSidecarConnectorEcho(EngineConnectorContract):
    """The `sidecar` lane against the chassis's own template server (`chassis.adapters.a2a.server`)
    around `echo_wire`.
    """

    @pytest.fixture
    async def engine(self) -> AsyncIterator[SidecarConnector]:
        async for connector in _sidecar_over(_chassis_server(echo_wire, "echo_wire")):
            yield connector

    @pytest.fixture
    async def json_values_engine(self) -> AsyncIterator[SidecarConnector]:
        async for connector in _sidecar_over(_chassis_server(json_values_handle, "json")):
            yield connector


class TestSidecarConnectorWorkloadServer(EngineConnectorContract):
    """The `sidecar` lane against the template server a workload ships (`workload_a2a.server`), so
    the suite covers both Python servers.
    """

    @pytest.fixture
    async def engine(self) -> AsyncIterator[SidecarConnector]:
        async for connector in _sidecar_over(_workload_server(echo_wire, "echo_wire")):
            yield connector

    @pytest.fixture
    async def json_values_engine(self) -> AsyncIterator[SidecarConnector]:
        async for connector in _sidecar_over(_workload_server(json_values_handle, "json")):
            yield connector
