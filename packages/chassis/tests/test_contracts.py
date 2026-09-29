"""Every fake in this package passes its port's contract suite. Real adapters bind the same
classes.
"""

from __future__ import annotations

import importlib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from chassis.adapters.a2a import InProcessConnector
from chassis.adapters.litellm import LiteLLMModel
from chassis.core.envelope import Request
from chassis.core.events import Delta, End, Start
from chassis.core.handle import echo
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel, ScriptRule
from chassis.ports.bundle import PortBundle
from chassis.ports.model import ModelMessage, ToolCallRequest, ToolSpec
from chassis_contracts import (
    ConfigPortContract,
    EngineConnectorContract,
    ModelPortContract,
    TelemetryPortContract,
)
from chassis_contracts.config import Bump
from chassis_contracts.helpers import make_request
from chassis_contracts.model import ToolCallCase
from chassis_contracts.telemetry import ReadCounter, ReadSpans
from fake_model_server import Script, create_app

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
    def tool_call_case(self) -> ToolCallCase:
        return ToolCallCase(
            [{"role": "user", "content": "lookup SLM"}],
            [ToolSpec(name="glossary_lookup")],
            "glossary_lookup",
        )

    @pytest.fixture
    def error_messages(self) -> list[ModelMessage]:
        return [{"role": "user", "content": "fail"}]


class TestLiteLLMModel(ModelPortContract):
    """The real adapter, bound to the same suite, over an ASGI transport to the fake model server.
    No socket.
    """

    @pytest.fixture
    def model_port(self) -> LiteLLMModel:
        app = create_app(Script.from_yaml(EXAMPLE_SCRIPT))
        return LiteLLMModel("http://fake/v1", transport=httpx.ASGITransport(app=app))

    @pytest.fixture
    def tool_call_case(self) -> ToolCallCase:
        return ToolCallCase(
            [{"role": "user", "content": "glossary SLM"}],
            [ToolSpec(name="glossary_lookup")],
            "glossary_lookup",
        )

    @pytest.fixture
    def error_messages(self) -> list[ModelMessage]:
        return [{"role": "user", "content": "fail"}]


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
