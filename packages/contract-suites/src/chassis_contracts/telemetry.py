"""TelemetryPortContract: one span per call with its attributes, nesting, and counters."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from chassis.ports.telemetry import Span, TelemetryPort

ReadSpans = Callable[[], list[Span]]
ReadCounter = Callable[[str], int]


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
