"""InMemoryTelemetry: records spans, counters, and logs so tests can assert on them.

A span opened with a valid W3C `traceparent` attribute takes that header's parent id as its
`parent_id`, even inside another span (`TelemetryPortContract`, PoC-7). The attribute is kept, as
every attribute is.

`metrics` is the chassis's `MetricsRegistry` (PoC-8): every counter and every timed span feeds it,
and `GET /metrics` renders it. A span with `request_id` or `trace_id` sets the run's ids for log
lines inside it (`chassis.metrics.observe_span`).
"""

from __future__ import annotations

import contextvars
import itertools
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from chassis.metrics import MetricsRegistry, observe_span
from chassis.ports.telemetry import Span
from chassis.tracecontext import parent_span_id

_current: contextvars.ContextVar[Span | None] = contextvars.ContextVar("span", default=None)


class InMemoryTelemetry:
    def __init__(self) -> None:
        self.spans: list[Span] = []
        self.counters: dict[tuple[str, tuple[tuple[str, Any], ...]], int] = {}
        self.logs: list[dict[str, Any]] = []
        self._ids = itertools.count(1)
        self.metrics = MetricsRegistry()

    @contextmanager
    def span(self, name: str, **attributes: Any) -> Iterator[Span]:
        parent = _current.get()
        parent_id = parent.span_id if parent else None
        traceparent = attributes.get("traceparent")
        remote = parent_span_id(traceparent) if isinstance(traceparent, str) else None
        if remote is not None:
            parent_id = remote
        span = Span(
            name=name,
            span_id=f"span-{next(self._ids)}",
            parent_id=parent_id,
            attributes=dict(attributes),
        )
        self.spans.append(span)
        token = _current.set(span)
        try:
            with observe_span(self.metrics, name, span.attributes):
                yield span
        finally:
            span.ended = True
            _current.reset(token)

    def counter(self, name: str, value: int = 1, **labels: Any) -> None:
        key = (name, tuple(sorted(labels.items())))
        self.counters[key] = self.counters.get(key, 0) + value
        self.metrics.count(name, value, labels)

    def log(self, level: str, message: str, **fields: Any) -> None:
        self.logs.append({"level": level, "message": message, **fields})

    def counter_value(self, name: str, **labels: Any) -> int:
        return self.counters.get((name, tuple(sorted(labels.items()))), 0)
