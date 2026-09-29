"""Self-test: every suite passes against its fake. Real adapters bind the same classes."""

from __future__ import annotations

import pytest
from chassis.core.handle import echo
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel, ScriptRule
from chassis.ports.model import ModelMessage, ToolCallRequest, ToolSpec
from chassis_contracts import (
    ConfigPortContract,
    EngineConnectorContract,
    ModelPortContract,
    TelemetryPortContract,
)
from chassis_contracts.config import Bump
from chassis_contracts.model import ToolCallCase
from chassis_contracts.telemetry import ReadCounter, ReadSpans


class TestScriptedModel(ModelPortContract):
    @pytest.fixture
    def model_port(self) -> ScriptedModel:
        return ScriptedModel(
            [
                ScriptRule(
                    match="look up", tool_call=ToolCallRequest(call_id="c1", name="glossary_lookup")
                ),
                ScriptRule(match="boom", error="scripted_failure"),
                ScriptRule(reply="Hello, world."),
            ]
        )

    @pytest.fixture
    def tool_call_case(self) -> ToolCallCase:
        return ToolCallCase(
            [{"role": "user", "content": "look up SLM"}],
            [ToolSpec(name="glossary_lookup")],
            "glossary_lookup",
        )

    @pytest.fixture
    def error_messages(self) -> list[ModelMessage]:
        return [{"role": "user", "content": "boom"}]


class TestFakeEngineWithHandle(EngineConnectorContract):
    @pytest.fixture
    def engine(self) -> FakeEngine:
        return FakeEngine(handle=echo)


class TestInMemoryConfig(ConfigPortContract):
    @pytest.fixture
    def config_port(self) -> InMemoryConfig:
        return InMemoryConfig({"echo": {"spec": {"kind": "transformer"}}})

    @pytest.fixture
    def bump_config(self, config_port: InMemoryConfig, config_name: str) -> Bump:
        async def bump() -> None:
            await config_port.put(config_name, {"spec": {"kind": "transformer", "changed": True}})

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
