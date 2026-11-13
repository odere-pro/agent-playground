"""The Claude Agent SDK mapping against the agent-testkit `EventMappingContract` (058 CH-8).

The fixture stream is written by hand: the SDK messages one tool-using run yields from `query()`
with partial messages on, built in memory. No CLI, no model, no socket.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any, ClassVar

import pytest
from agent_testkit.event_mapping import EventMappingContract, MapStream, run_mapping
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    StreamEvent,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from echo_claude_agent.mapping import EventMapper
from workload_a2a.events import event

REQUEST_ID = "req-claude-agent-1"
ROUTE = "big-default"
MODEL = "claude-test"
ANSWER = '{"term": "RAG", "definition": "retrieval-augmented generation"}'


def _partial(text: str) -> StreamEvent:
    delta = {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}}
    return StreamEvent(uuid=f"u-{text}", session_id="s-1", event=delta)


def _stream(result_id: str = "toolu-1") -> list[Any]:
    asked = ToolUseBlock("toolu-1", "mcp__chassis__glossary_lookup", {"term": "RAG"})
    usage = {"input_tokens": 10, "cache_read_input_tokens": 2, "output_tokens": 3}
    return [
        AssistantMessage(content=[asked], model=MODEL),
        UserMessage(content=[ToolResultBlock(result_id, ANSWER, is_error=False)]),
        _partial("RAG means "),
        _partial("retrieval-augmented generation."),
        AssistantMessage(
            content=[TextBlock("RAG means retrieval-augmented generation.")], model=MODEL
        ),
        ResultMessage(
            subtype="success",
            duration_ms=40,
            duration_api_ms=30,
            is_error=False,
            num_turns=2,
            session_id="s-1",
            usage=usage,
        ),
    ]


def map_run(stream: Sequence[Any]) -> Iterator[dict[str, Any]]:
    """What `handle` yields around the mapper: `start`, then each message mapped in order."""
    yield event("start", request_id=REQUEST_ID)
    mapper = EventMapper(ROUTE)
    for message in stream:
        yield from mapper.map(message)


class TestClaudeAgentMapping(EventMappingContract):
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
    assert mapped[4]["input_tokens"] == 12


def test_the_contract_fails_on_a_result_block_that_maps_to_a_tool_call_with_no_id() -> None:
    """A `ToolResultBlock` with an empty `tool_use_id`: `tool_call.call_id` is empty, which the
    schema refuses."""
    mapped = run_mapping(map_run, _stream(result_id=""))
    with pytest.raises(AssertionError, match=r"^event 1 does not match the event schema"):
        TestClaudeAgentMapping().test_every_mapped_event_matches_the_event_schema(mapped)
