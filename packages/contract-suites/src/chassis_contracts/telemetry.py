"""TelemetryPortContract: one span per call with its attributes, nesting, and counters.

PoC-7 adds the trace-context rule every adapter keeps (014 H-7): a span opened with a valid W3C
`traceparent` attribute is a child of the span that header names, even inside another span (the
model proxy and the tool endpoint continue the workload's call). An invalid one is ignored: the
span nests as usual.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from chassis.ports.telemetry import Span, TelemetryPort

ReadSpans = Callable[[], list[Span]]
ReadCounter = Callable[[str], int]

REMOTE_PARENT = "00f067aa0ba902b7"
TRACEPARENT = f"00-4bf92f3577b34da6a3ce929d0e0e4736-{REMOTE_PARENT}-01"


@pytest.mark.contract
class TelemetryPortContract:
    """Subclass as `Test*`, provide `telemetry`, `read_spans` (spans recorded so far), and
    `read_counter`.
    """

    @pytest.fixture
    def telemetry(self) -> TelemetryPort:
        raise NotImplementedError("provide a telemetry fixture")

    @pytest.fixture
    def read_spans(self) -> ReadSpans:
        raise NotImplementedError("provide a read_spans fixture")

    @pytest.fixture
    def read_counter(self) -> ReadCounter:
        raise NotImplementedError("provide a read_counter fixture")

    def test_span_records_name_and_attributes(
        self, telemetry: TelemetryPort, read_spans: ReadSpans
    ) -> None:
        with telemetry.span("chassis.run", request_id="req-1"):
            pass
        spans = [s for s in read_spans() if s.name == "chassis.run"]
        assert len(spans) == 1
        assert spans[0].attributes.get("request_id") == "req-1"
        assert spans[0].ended

    def test_nested_span_has_parent(self, telemetry: TelemetryPort, read_spans: ReadSpans) -> None:
        with telemetry.span("outer") as outer, telemetry.span("inner") as inner:
            assert inner.parent_id == outer.span_id
        names = {s.name for s in read_spans()}
        assert {"outer", "inner"} <= names

    def test_counter_increments(self, telemetry: TelemetryPort, read_counter: ReadCounter) -> None:
        telemetry.counter("chassis.requests")
        telemetry.counter("chassis.requests", 2)
        assert read_counter("chassis.requests") == 3

    def test_traceparent_attribute_continues_the_remote_parent(
        self, telemetry: TelemetryPort, read_spans: ReadSpans
    ) -> None:
        with telemetry.span("chassis.model.call", traceparent=TRACEPARENT) as span:
            assert span.parent_id == REMOTE_PARENT
        (recorded,) = [s for s in read_spans() if s.name == "chassis.model.call"]
        assert recorded.parent_id == REMOTE_PARENT

    def test_traceparent_wins_over_the_current_span(
        self, telemetry: TelemetryPort, read_spans: ReadSpans
    ) -> None:
        with telemetry.span("outer"), telemetry.span("call", traceparent=TRACEPARENT) as call:
            assert call.parent_id == REMOTE_PARENT

    def test_an_invalid_traceparent_is_ignored(
        self, telemetry: TelemetryPort, read_spans: ReadSpans
    ) -> None:
        with telemetry.span("outer") as outer, telemetry.span("call", traceparent="garbage") as c:
            assert c.parent_id == outer.span_id
