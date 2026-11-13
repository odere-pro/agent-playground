"""The smolagents mapping against the agent-testkit `EventMappingContract` (058 CH-8).

The fixture stream is written by hand and built in memory: an MCP call the generated code made (a
`ToolCall` record, which the tool hands to `EventMapper.tool_call`), the steps `CodeAgent.run(
stream=True)` yields, and the monitor's token counts, which `EventMapper.finish` reads. No model,
no MCP server, no sandbox, no socket.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

import pytest
from agent_testkit.event_mapping import EventMappingContract, MapStream, run_mapping
from echo_smolagents.mapping import EventMapper
from smolagents.memory import FinalAnswerStep  # type: ignore[import-untyped]
from workload_a2a.events import event

REQUEST_ID = "req-smolagents-1"
ROUTE = "big-default"


@dataclass(frozen=True)
class ToolCall:
    """One MCP call the generated code made, as the tool reports it to the mapper."""

    call_id: str
    name: str
    arguments: dict[str, Any]
    result: dict[str, Any]


@dataclass(frozen=True)
class Monitor:
    """The agent monitor's token counts at the end of the run."""

    input_tokens: int
    output_tokens: int


def _stream(input_tokens: int = 12) -> list[Any]:
    answer = {"term": "RAG", "definition": "retrieval-augmented generation"}
    return [
        ToolCall("call-1", "glossary_lookup", {"term": "RAG"}, answer),
        FinalAnswerStep(output="RAG means retrieval-augmented generation."),
        Monitor(input_tokens, 3),
    ]


def map_run(stream: Sequence[Any]) -> Iterator[dict[str, Any]]:
    """What `handle` yields around the mapper: `start`, each item mapped, then `finish`."""
    monitor = next(item for item in stream if isinstance(item, Monitor))
    mapper = EventMapper(ROUTE, lambda: (monitor.input_tokens, monitor.output_tokens))
    yield event("start", request_id=REQUEST_ID)
    for item in stream:
        if isinstance(item, ToolCall):
            yield mapper.tool_call(item.call_id, item.name, item.arguments, item.result)
        elif not isinstance(item, Monitor):
            yield from mapper.map(item)
    yield from mapper.finish()


class TestSmolagentsMapping(EventMappingContract):
    request_id: ClassVar[str | None] = REQUEST_ID

    @pytest.fixture
    def fixture_stream(self) -> Sequence[Any]:
        return _stream()

    @pytest.fixture
    def map_stream(self) -> MapStream:
        return map_run


def test_the_stream_carries_one_delta_a_tool_call_and_usage() -> None:
    """One delta only: the streaming gap the PoC-6 scorecard lists for smolagents."""
    types = [e["type"] for e in run_mapping(map_run, _stream())]
    assert types == ["start", "tool_call", "delta", "metrics", "end"]


def test_the_contract_fails_on_usage_that_maps_to_a_bad_metrics_event() -> None:
    mapped = run_mapping(map_run, _stream(input_tokens=-1))
    with pytest.raises(AssertionError, match=r"^event 3 does not match the event schema"):
        TestSmolagentsMapping().test_every_mapped_event_matches_the_event_schema(mapped)
