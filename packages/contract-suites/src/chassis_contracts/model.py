"""ModelPortContract: streaming and complete agree, usage is reported, tool calls and errors
surface, and a tool conversation reaches the model intact.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import pytest
from chassis.ports.model import ModelError, ModelMessage, ModelPort, ToolCallRequest, ToolSpec

ReceivedMessages = Callable[[], list[ModelMessage]]
"""The messages the model behind the adapter received on its last call, as `ModelMessage`."""


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
    def received_messages(self) -> ReceivedMessages:
        raise NotImplementedError(
            "provide a received_messages fixture: what the model behind model_port last received"
        )

    @pytest.fixture
    def plain_messages(self) -> list[ModelMessage]:
        return [ModelMessage(role="user", content="hello world")]

    @pytest.fixture
    def tool_conversation(self) -> list[ModelMessage]:
        """Two turns: the model asked for a tool, and the tool answered."""
        return [
            ModelMessage(role="user", content="look up SLM"),
            ModelMessage(
                role="assistant",
                content=None,
                tool_calls=[
                    ToolCallRequest(call_id="c1", name="glossary_lookup", arguments={"n": 3})
                ],
            ),
            ModelMessage(
                role="tool",
                tool_call_id="c1",
                name="glossary_lookup",
                content="SLM: small language model",
            ),
        ]

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

    async def test_two_turn_tool_conversation_reaches_the_model_intact(
        self,
        model_port: ModelPort,
        received_messages: ReceivedMessages,
        tool_conversation: list[ModelMessage],
    ) -> None:
        tools = [ToolSpec(name="glossary_lookup")]
        result = await model_port.complete(tool_conversation, route=self.route, tools=tools)
        self._assert_intact(received_messages(), tool_conversation)
        assert not result.tool_calls, "after a tool result the scripted loop must end"
        assert result.text, "after a tool result the model answers"

        streamed = [
            c async for c in model_port.stream(tool_conversation, route=self.route, tools=tools)
        ]
        self._assert_intact(received_messages(), tool_conversation)
        assert not [c for c in streamed if c.tool_call], "after a tool result the loop must end"
        assert "".join(c.text for c in streamed) == result.text

    @staticmethod
    def _assert_intact(received: list[ModelMessage], sent: list[ModelMessage]) -> None:
        assert received == sent
        assistant, tool = received[1], received[2]
        assert assistant.content is None
        assert assistant.tool_calls is not None
        call = assistant.tool_calls[0]
        assert (call.call_id, call.name) == ("c1", "glossary_lookup")
        assert type(call.arguments["n"]) is int and call.arguments["n"] == 3
        assert tool.tool_call_id == "c1"
