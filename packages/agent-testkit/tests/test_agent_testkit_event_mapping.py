"""Self-test of `EventMappingContract`: it passes a sound mapping of a toy framework's stream, and
it fails when the fixture stream holds an event that does not match the event schema.

The toy framework is not a real one. Its chunks are `{"kind": ..., ...}` dicts, and `toy_mapping`
turns them into chassis events the way a 058 CH-8 mapping does. No process starts; no `chassis`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any, ClassVar

import pytest
from agent_testkit.event_mapping import (
    EventMappingContract,
    MapStream,
    check_schema,
    run_mapping,
)

REQUEST_ID = "req-toy-1"

SOUND: tuple[dict[str, Any], ...] = (
    {"kind": "begin", "run": REQUEST_ID},
    {"kind": "token", "text": "hello "},
    {"kind": "token", "text": "world"},
    {"kind": "tool", "id": "c1", "name": "glossary_lookup", "args": {"term": "RAG"}},
    {"kind": "usage", "in": 12, "out": 3},
    {"kind": "done"},
)


def toy_mapping(chunks: Sequence[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    """The toy framework's chunks as chassis events; an unknown kind passes through as is."""
    for chunk in chunks:
        kind = chunk["kind"]
        if kind == "begin":
            yield {"type": "start", "request_id": chunk["run"]}
        elif kind == "token":
            yield {"type": "delta", "text": chunk["text"]}
        elif kind == "tool":
            call = {"call_id": chunk["id"], "name": chunk["name"], "arguments": chunk["args"]}
            yield {"type": "tool_call", **call}
        elif kind == "usage":
            yield {"type": "metrics", "input_tokens": chunk["in"], "output_tokens": chunk["out"]}
        elif kind == "done":
            yield {"type": "end", "status": "ok"}
        else:
            yield {"type": kind, **{k: v for k, v in chunk.items() if k != "kind"}}


async def toy_mapping_async(chunks: Sequence[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
    for event in toy_mapping(chunks):
        yield event


class TestToyMapping(EventMappingContract):
    request_id: ClassVar[str | None] = REQUEST_ID

    @pytest.fixture
    def fixture_stream(self) -> Sequence[dict[str, Any]]:
        return SOUND

    @pytest.fixture
    def map_stream(self) -> MapStream:
        return toy_mapping


class TestToyMappingAsync(TestToyMapping):
    @pytest.fixture
    def map_stream(self) -> MapStream:
        return toy_mapping_async


def _with(index: int, chunk: dict[str, Any]) -> list[dict[str, Any]]:
    stream = list(SOUND)
    stream[index] = chunk
    return stream


BAD_FIXTURES = {
    "a delta whose text is a number": _with(1, {"kind": "token", "text": 7}),
    "a type the schema does not know": _with(2, {"kind": "progress", "percent": 50}),
    "an end with a status the schema does not know": _with(5, {"kind": "done_bad"}),
    "metrics with negative tokens": _with(4, {"kind": "usage", "in": -1, "out": 3}),
}


def _bad_end(chunks: Sequence[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    for event in toy_mapping(chunks):
        yield {"type": "end", "status": "finished"} if event["type"] == "done_bad" else event


@pytest.mark.parametrize("case", list(BAD_FIXTURES))
def test_the_schema_case_fails_on_a_fixture_event_that_does_not_match(case: str) -> None:
    mapped = run_mapping(_bad_end, BAD_FIXTURES[case])
    with pytest.raises(AssertionError, match=r"event \d does not match the event schema"):
        TestToyMapping().test_every_mapped_event_matches_the_event_schema(mapped)


def test_the_failure_names_the_event_by_index() -> None:
    mapped = run_mapping(toy_mapping, BAD_FIXTURES["a delta whose text is a number"])
    with pytest.raises(AssertionError, match=r"^event 1 does not match"):
        check_schema(mapped)


@pytest.mark.parametrize(
    ("chunks", "message"),
    [
        (SOUND[1:], "the first event is not start"),
        (SOUND[:-1], "the last event is not one of"),
        ((*SOUND[:2], {"kind": "done"}, *SOUND[2:]), "a terminal mid-stream"),
        (_with(0, {"kind": "begin", "run": "another"}), "start names another request"),
        ((), "the stream holds no event"),
    ],
)
def test_the_sequence_case_fails_on_a_broken_stream(
    chunks: Sequence[dict[str, Any]], message: str
) -> None:
    mapped = run_mapping(toy_mapping, chunks)
    with pytest.raises(AssertionError, match=message):
        TestToyMapping().test_the_mapped_stream_starts_first_and_ends_once(mapped)


def test_a_mapped_event_that_is_neither_a_dict_nor_a_model_fails() -> None:
    with pytest.raises(AssertionError, match="neither a dict nor a model"):
        run_mapping(lambda chunks: ["start"], SOUND)
