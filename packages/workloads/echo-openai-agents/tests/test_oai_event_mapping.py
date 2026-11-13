"""The OpenAI Agents SDK mapping against the agent-testkit `EventMappingContract` (058 CH-8).

The fixture stream is written by hand: the SDK events one tool-using run yields from
`Runner.run_streamed(...).stream_events()`, built in memory, then the run's `Usage`, which
`handle` hands to `EventMapper.finish`. No model, no MCP server, no socket.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any, ClassVar

import pytest
from agent_testkit.event_mapping import EventMappingContract, MapStream, run_mapping
from agents import Agent, RunItemStreamEvent, ToolCallItem, ToolCallOutputItem, Usage
from agents.stream_events import RawResponsesStreamEvent
from echo_openai_agents.mapping import EventMapper
from openai.types.responses import ResponseFunctionToolCall, ResponseTextDeltaEvent
from workload_a2a.events import event

REQUEST_ID = "req-openai-agents-1"
ROUTE = "big-default"
AGENT: Agent[Any] = Agent(name="echo")
ANSWER = '{"term": "RAG", "definition": "retrieval-augmented generation"}'


def _delta(text: str, sequence: int) -> RawResponsesStreamEvent:
    data = ResponseTextDeltaEvent(
        type="response.output_text.delta",
        content_index=0,
        delta=text,
        item_id="msg-1",
        logprobs=[],
        output_index=0,
        sequence_number=sequence,
    )
    return RawResponsesStreamEvent(data=data)


def _called(call_id: str) -> RunItemStreamEvent:
    raw = ResponseFunctionToolCall(
        type="function_call", call_id=call_id, name="glossary_lookup", arguments='{"term": "RAG"}'
    )
    return RunItemStreamEvent(name="tool_called", item=ToolCallItem(AGENT, raw))


def _output(call_id: str) -> RunItemStreamEvent:
    raw = {"type": "function_call_output", "call_id": call_id, "output": ANSWER}
    item = ToolCallOutputItem(AGENT, raw, ANSWER)
    return RunItemStreamEvent(name="tool_output", item=item)


def _stream(called_id: str = "call-1") -> list[Any]:
    return [
        _called(called_id),
        _output("call-1"),
        _delta("RAG means ", 1),
        _delta("retrieval-augmented generation.", 2),
        Usage(requests=2, input_tokens=12, output_tokens=3, total_tokens=15),
    ]


def map_run(stream: Sequence[Any]) -> Iterator[dict[str, Any]]:
    """What `handle` yields around the mapper: `start`, each event mapped, then `finish`."""
    yield event("start", request_id=REQUEST_ID)
    mapper = EventMapper(ROUTE)
    for ev in stream:
        yield from mapper.finish(ev) if isinstance(ev, Usage) else mapper.map(ev)


class TestOpenAIAgentsMapping(EventMappingContract):
    request_id: ClassVar[str | None] = REQUEST_ID

    @pytest.fixture
    def fixture_stream(self) -> Sequence[Any]:
        return _stream()

    @pytest.fixture
    def map_stream(self) -> MapStream:
        return map_run


def test_the_stream_carries_deltas_a_tool_call_and_usage() -> None:
    mapped = run_mapping(map_run, _stream())
    assert [e["type"] for e in mapped] == ["start", "tool_call", "delta", "delta", "metrics", "end"]
    assert mapped[1]["name"] == "glossary_lookup"


def test_the_contract_fails_on_an_output_that_maps_to_a_nameless_tool_call() -> None:
    """A `tool_output` with no `tool_called` before it: `tool_call.name` is empty, which the
    schema refuses."""
    mapped = run_mapping(map_run, _stream(called_id="call-other"))
    with pytest.raises(AssertionError, match=r"^event 1 does not match the event schema"):
        TestOpenAIAgentsMapping().test_every_mapped_event_matches_the_event_schema(mapped)
