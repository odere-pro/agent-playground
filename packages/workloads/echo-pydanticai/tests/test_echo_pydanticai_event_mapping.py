"""The PydanticAI mapping against the agent-testkit `EventMappingContract` (058 CH-8).

The fixture stream is written by hand: the PydanticAI events one tool-using run yields from
`Agent.run_stream_events`, built in memory. No model, no MCP server, no socket.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any, ClassVar

import pytest
from agent_testkit.event_mapping import EventMappingContract, MapStream, run_mapping
from echo_pydanticai.mapping import EventMapper, event
from pydantic_ai import (
    AgentRunResultEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai._agent_graph import GraphAgentState
from pydantic_ai.run import AgentRunResult
from pydantic_ai.usage import RunUsage

REQUEST_ID = "req-pydanticai-1"
ROUTE = "big-default"


def _run_result(input_tokens: int, output_tokens: int) -> AgentRunResultEvent[str]:
    usage = RunUsage(input_tokens=input_tokens, output_tokens=output_tokens)
    return AgentRunResultEvent(AgentRunResult("done", _state=GraphAgentState(usage=usage)))


def _stream(input_tokens: int = 12) -> list[Any]:
    call = ToolCallPart("glossary_lookup", {"term": "RAG"}, "call-1")
    answer = {"term": "RAG", "definition": "retrieval-augmented generation"}
    return [
        FunctionToolCallEvent(call),
        FunctionToolResultEvent(ToolReturnPart("glossary_lookup", answer, "call-1")),
        PartStartEvent(index=0, part=TextPart("RAG means ")),
        PartDeltaEvent(index=0, delta=TextPartDelta("retrieval-augmented generation.")),
        _run_result(input_tokens, 3),
    ]


def map_run(stream: Sequence[Any]) -> Iterator[dict[str, Any]]:
    """What `handle` yields around the mapper: `start`, then each event mapped in order."""
    yield event("start", request_id=REQUEST_ID)
    mapper = EventMapper(ROUTE)
    for ev in stream:
        yield from mapper.map(ev)


class TestPydanticAIMapping(EventMappingContract):
    request_id: ClassVar[str | None] = REQUEST_ID

    @pytest.fixture
    def fixture_stream(self) -> Sequence[Any]:
        return _stream()

    @pytest.fixture
    def map_stream(self) -> MapStream:
        return map_run


def test_the_stream_carries_deltas_a_tool_call_and_usage() -> None:
    types = [e["type"] for e in run_mapping(map_run, _stream())]
    assert types == ["start", "tool_call", "delta", "delta", "metrics", "end"]


def test_the_contract_fails_on_usage_that_maps_to_a_bad_metrics_event() -> None:
    mapped = run_mapping(map_run, _stream(input_tokens=-1))
    with pytest.raises(AssertionError, match=r"^event 4 does not match the event schema"):
        TestPydanticAIMapping().test_every_mapped_event_matches_the_event_schema(mapped)
