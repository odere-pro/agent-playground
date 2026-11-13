"""The LangGraph mapping against the agent-testkit `EventMappingContract` (058 CH-8).

The fixture stream is written by hand: the `(mode, data)` parts one tool-using run yields from
`graph.astream(..., stream_mode=["messages", "updates"])`, built in memory. No model, no socket.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any, ClassVar

import pytest
from agent_testkit.event_mapping import EventMappingContract, MapStream, run_mapping
from echo_langgraph.mapping import EventMapper, event
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

REQUEST_ID = "req-langgraph-1"
ROUTE = "big-default"
USAGE = {"input_tokens": 12, "output_tokens": 3, "total_tokens": 15}
ASKED = {"id": "call-1", "name": "glossary_lookup", "args": {"term": "RAG"}}
ANSWER = '{"term": "RAG", "definition": "retrieval-augmented generation"}'


def _stream(tool_name: str | None = "glossary_lookup", call_id: str = "call-1") -> list[Any]:
    asked = AIMessage(content="", tool_calls=[ASKED], usage_metadata=USAGE)
    result = ToolMessage(content=ANSWER, tool_call_id=call_id, name=tool_name)
    return [
        ("updates", {"model": {"messages": [asked]}}),
        ("updates", {"tools": {"messages": [result]}}),
        ("messages", (AIMessageChunk(content="RAG means "), {})),
        ("messages", (AIMessageChunk(content="retrieval-augmented generation."), {})),
    ]


def map_run(stream: Sequence[Any]) -> Iterator[dict[str, Any]]:
    """What `handle` yields around the mapper: `start`, each part mapped, then `finish`."""
    yield event("start", request_id=REQUEST_ID)
    mapper = EventMapper(ROUTE)
    for mode, data in stream:
        yield from mapper.on_part(mode, data)
    yield from mapper.finish()


class TestLangGraphMapping(EventMappingContract):
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
    assert mapped[4]["input_tokens"] == 12


def test_the_contract_fails_on_a_tool_result_that_maps_to_a_nameless_tool_call() -> None:
    """A `ToolMessage` for a call the model never asked for, with no name: `tool_call.name` is
    empty, which the schema refuses."""
    mapped = run_mapping(map_run, _stream(tool_name=None, call_id="call-unasked"))
    with pytest.raises(AssertionError, match=r"^event 1 does not match the event schema"):
        TestLangGraphMapping().test_every_mapped_event_matches_the_event_schema(mapped)
