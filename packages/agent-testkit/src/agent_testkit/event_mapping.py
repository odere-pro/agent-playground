"""`EventMappingContract`: a framework's events, mapped, match the chassis JSON event schema.

A mapping (058 CH-8) turns one framework's stream (token chunks, tool calls, usage) into chassis
events. Bind the contract once per mapping, as `Test*`, with two fixtures:

- `fixture_stream`: that framework's events for one run, recorded or written by hand;
- `map_stream`: the mapping, `map_stream(fixture_stream)`, a sync or async iterable of events.

```python
from agent_testkit.event_mapping import EventMappingContract

class TestMyMapping(EventMappingContract):
    @pytest.fixture
    def fixture_stream(self): return load_fixture("run.json")

    @pytest.fixture
    def map_stream(self): return my_agent.mapping.to_chassis_events
```

Each mapped event is checked with `workload_a2a.server.validate_event`, the vendored event schema
the workload's own A2A server enforces, so the check is the one the lane makes. The stream must
start with `start`, end with exactly one terminal event, and hold none in between. An event may be
a dict or a model with `model_dump`; nothing here imports `chassis`.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterable, Callable, Iterable, Sequence
from typing import Any, ClassVar

import pytest
from workload_a2a.server import validate_event

TERMINAL = ("end", "error")
MapStream = Callable[[Sequence[Any]], Iterable[Any] | AsyncIterable[Any]]
"""A mapping over one whole fixture stream; a mapping may keep state between events."""


def as_dict(event: Any) -> dict[str, Any]:
    """`event` as the wire dict: a dict as it is, a model through `model_dump(mode="json")`."""
    if isinstance(event, dict):
        return event
    dump = getattr(event, "model_dump", None)
    if callable(dump):
        dumped: dict[str, Any] = dump(mode="json")
        return dumped
    raise AssertionError(f"a mapped event is neither a dict nor a model: {event!r}")


async def _collect(events: AsyncIterable[Any]) -> list[Any]:
    return [event async for event in events]


def run_mapping(map_stream: MapStream, fixture_stream: Sequence[Any]) -> list[dict[str, Any]]:
    """Apply `map_stream` to `fixture_stream`; the mapped events as dicts, in order."""
    mapped = map_stream(fixture_stream)
    if isinstance(mapped, AsyncIterable):
        return [as_dict(e) for e in asyncio.run(_collect(mapped))]
    return [as_dict(e) for e in mapped]


def check_schema(events: Sequence[dict[str, Any]]) -> None:
    """Every event matches the chassis event schema; the first that does not fails, by index."""
    for index, event in enumerate(events):
        try:
            validate_event(event)
        except ValueError as exc:
            shown = json.dumps(event, default=repr)[:300]
            raise AssertionError(
                f"event {index} does not match the event schema: {exc}\n  event: {shown}"
            ) from None


def check_sequence(
    events: Sequence[dict[str, Any]],
    request_id: str | None = None,
    terminal: tuple[str, ...] = TERMINAL,
) -> None:
    """`start` first (naming `request_id` when given), one terminal event last, none between."""
    assert events, "the stream holds no event"
    first, last = events[0], events[-1]
    assert first.get("type") == "start", f"the first event is not start: {first!r}"
    if request_id is not None:
        assert first.get("request_id") == request_id, f"start names another request: {first!r}"
    assert last.get("type") in terminal, f"the last event is not one of {terminal}: {last!r}"
    middle = [e.get("type") for e in events[1:-1]]
    assert not set(middle) & {"start", *TERMINAL}, f"start or a terminal mid-stream: {middle}"


class EventMappingContract:
    """The mapping cases; see the module docstring. Set `request_id` when the fixture's `start`
    names one, and `terminal_types` to narrow the terminal events the fixture may end with."""

    request_id: ClassVar[str | None] = None
    terminal_types: ClassVar[tuple[str, ...]] = TERMINAL

    @pytest.fixture
    def fixture_stream(self) -> Sequence[Any]:
        raise NotImplementedError("provide fixture_stream: the framework's events for one run")

    @pytest.fixture
    def map_stream(self) -> MapStream:
        raise NotImplementedError("provide map_stream: the mapping over one fixture stream")

    @pytest.fixture
    def mapped_events(
        self, map_stream: MapStream, fixture_stream: Sequence[Any]
    ) -> list[dict[str, Any]]:
        return run_mapping(map_stream, fixture_stream)

    def test_every_mapped_event_matches_the_event_schema(
        self, mapped_events: list[dict[str, Any]]
    ) -> None:
        check_schema(mapped_events)

    def test_the_mapped_stream_starts_first_and_ends_once(
        self, mapped_events: list[dict[str, Any]]
    ) -> None:
        check_sequence(mapped_events, self.request_id, self.terminal_types)
