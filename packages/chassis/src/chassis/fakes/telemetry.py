"""InMemoryTelemetry: records spans, counters, and logs so tests can assert on them."""

from __future__ import annotations

import contextvars
import itertools
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from chassis.ports.telemetry import Span

_current: contextvars.ContextVar[Span | None] = contextvars.ContextVar("span", default=None)


class InMemoryTelemetry:
    def __init__(self) -> None:
        self.spans: list[Span] = []
        self.counters: dict[tuple[str, tuple[tuple[str, Any], ...]], int] = {}
        self.logs: list[dict[str, Any]] = []
        self._ids = itertools.count(1)

    @contextmanager
    def span(self, name: str, **attributes: Any) -> Iterator[Span]:
        parent = _current.get()
        span = Span(
            name=name,
            span_id=f"span-{next(self._ids)}",
            parent_id=parent.span_id if parent else None,
            attributes=dict(attributes),
        )
        self.spans.append(span)
        token = _current.set(span)
        try:
            yield span
        finally:
            span.ended = True
            _current.reset(token)

    def counter(self, name: str, value: int = 1, **labels: Any) -> None:
        key = (name, tuple(sorted(labels.items())))
        self.counters[key] = self.counters.get(key, 0) + value

    def log(self, level: str, message: str, **fields: Any) -> None:
        self.logs.append({"level": level, "message": message, **fields})

    def counter_value(self, name: str, **labels: Any) -> int:
        return self.counters.get((name, tuple(sorted(labels.items()))), 0)
