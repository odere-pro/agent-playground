"""ModelPortContract: streaming and complete agree, usage is reported, tool calls and errors
surface.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from chassis.ports.model import ModelError, ModelMessage, ModelPort, ToolSpec


class ToolCallCase:
    def __init__(self, messages: Sequence[ModelMessage], tools: Sequence[ToolSpec], tool_name: str):
        self.messages = list(messages)
        self.tools = list(tools)
        self.tool_name = tool_name


@pytest.mark.contract
class ModelPortContract:
    """Subclass as `Test*`, provide `model_port`. Override the case fixtures for tool calls and
    errors.
    """

    route = "fake-route"

    @pytest.fixture
    def model_port(self) -> ModelPort:
        raise NotImplementedError("provide a model_port fixture")

    @pytest.fixture
    def plain_messages(self) -> list[ModelMessage]:
        return [{"role": "user", "content": "hello world"}]

    @pytest.fixture
    def tool_call_case(self) -> ToolCallCase:
        pytest.skip("this adapter has no tool-call case; override tool_call_case to add one")

    @pytest.fixture
    def error_messages(self) -> list[ModelMessage]:
        pytest.skip("this adapter has no error case; override error_messages to add one")

    async def test_complete_and_stream_agree(
        self, model_port: ModelPort, plain_messages: list[ModelMessage]
    ) -> None:
        complete = await model_port.complete(plain_messages, route=self.route)
        streamed = [c async for c in model_port.stream(plain_messages, route=self.route)]
        assert complete.text == "".join(c.text for c in streamed)
        assert complete.text, "an empty answer hides a broken adapter"

    async def test_stream_ends_once_with_usage(
        self, model_port: ModelPort, plain_messages: list[ModelMessage]
    ) -> None:
        streamed = [c async for c in model_port.stream(plain_messages, route=self.route)]
        finals = [c for c in streamed if c.finish]
        assert len(finals) == 1 and finals[0] is streamed[-1]
        assert finals[0].usage is not None and finals[0].usage.output_tokens >= 0

    async def test_complete_reports_usage_and_model(
        self, model_port: ModelPort, plain_messages: list[ModelMessage]
    ) -> None:
        result = await model_port.complete(plain_messages, route=self.route)
        assert result.usage.input_tokens >= 0 and result.usage.output_tokens >= 0
        assert result.model, "the adapter must report which model or route answered"

    async def test_tool_call_round_trip(
        self, model_port: ModelPort, tool_call_case: ToolCallCase
    ) -> None:
        result = await model_port.complete(
            tool_call_case.messages, route=self.route, tools=tool_call_case.tools
        )
        assert [t.name for t in result.tool_calls] == [tool_call_case.tool_name]
        streamed = [
            c
            async for c in model_port.stream(
                tool_call_case.messages, route=self.route, tools=tool_call_case.tools
            )
        ]
        assert [c.tool_call.name for c in streamed if c.tool_call] == [tool_call_case.tool_name]

    async def test_error_surfaces_as_model_error(
        self, model_port: ModelPort, error_messages: list[ModelMessage]
    ) -> None:
        with pytest.raises(ModelError) as exc:
            await model_port.complete(error_messages, route=self.route)
        assert exc.value.code
