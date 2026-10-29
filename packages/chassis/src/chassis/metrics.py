"""Prometheus metrics from what the chassis already counts (PoC-8, 014 H-7). Standard library only.

One source of truth: the telemetry adapters (`InMemoryTelemetry`, `OtelTelemetry`) each hold a
`MetricsRegistry` at `.metrics` and feed it from the port's own calls. `GET /metrics` renders it
(`chassis.server.observability`). Nothing new is counted for Prometheus alone. Per replica, in
memory, by design: Prometheus scrapes and sums the replicas.

- **Counters.** Every `TelemetryPort.counter(name, value, **labels)` is the counter
  `<name>_total`, `.` and any other character outside `[a-zA-Z0-9_]` made `_`:
  `chassis.requests{agent, interface}` is `chassis_requests_total`.
- **Spans.** `observe_span` times the spans in `TIMED_SPANS`. Each one is a histogram
  `<name>_duration_seconds{<its label>, status}`, `status` being `ok`, `error` (an exception, or an
  `error.code` attribute), or `cancelled` (the client left). A span that ends with `error.code` is
  also `<name>_errors_total{<its label>, code}`: `chassis_run_errors_total{interface, code}`.
- **Declared families.** `DECLARED` families show their `# HELP` and `# TYPE` before anything is
  counted, so a dashboard query names a metric that exists from the start.

`render(const_labels)` adds the constant labels (the chassis's `agent`) to every sample unless the
sample has its own value. suggested: the bucket bounds, the timed spans, and every name.
"""

from __future__ import annotations

import asyncio
import math
import re
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from chassis.logs import run_context

__all__ = [
    "CONTENT_TYPE",
    "DECLARED",
    "DURATION_BUCKETS",
    "TIMED_SPANS",
    "MetricsRegistry",
    "observe_span",
    "prom_name",
]

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
DURATION_BUCKETS: tuple[float, ...] = (
    0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0,
)  # fmt: skip
"""suggested: seconds. A run on a real model takes seconds; the upper bounds cover that."""
TIMED_SPANS: Mapping[str, str] = {
    "chassis.run": "interface",
    "chassis.model.call": "route",
    "chassis.tool.call": "tool",
    "chassis.evaluator.score": "mode",
}
"""The spans timed as histograms, and the one attribute each is labeled by."""
DECLARED: Mapping[str, tuple[str, str]] = {
    "chassis_requests_total": ("counter", "Runs started, by interface."),
    "chassis_run_duration_seconds": ("histogram", "Run latency, by interface and status."),
    "chassis_run_errors_total": ("counter", "Runs that ended with an error, by code."),
    "chassis_model_calls_total": ("counter", "Model proxy calls, by the route asked for."),
    "chassis_model_call_duration_seconds": ("histogram", "Model proxy call latency, by route."),
    "chassis_model_tokens_total": ("counter", "Model tokens, by route and direction."),
    "chassis_model_cost_microusd_total": ("counter", "Model cost in micro-USD, by route."),
    "chassis_tool_calls_total": ("counter", "Tool proxy calls, by tool."),
    "chassis_gate_runs_total": ("counter", "Runs the evaluator gate checked, by mode."),
    "chassis_gate_retries_total": ("counter", "Evaluator gate retries."),
    "chassis_gate_fallbacks_total": ("counter", "Runs the evaluator gate sent to the fallback."),
}
_BAD = re.compile(r"[^a-zA-Z0-9_]")
LabelKey = tuple[tuple[str, str], ...]


def prom_name(name: str) -> str:
    """`name` as a Prometheus metric or label name."""
    clean = _BAD.sub("_", name)
    return clean if not clean[:1].isdigit() else f"_{clean}"


def _counter_name(name: str) -> str:
    clean = prom_name(name)
    return clean if clean.endswith("_total") else f"{clean}_total"


def _key(labels: Mapping[str, Any]) -> LabelKey:
    return tuple(sorted((prom_name(k), str(v)) for k, v in labels.items() if v is not None))


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _number(value: float) -> str:
    if math.isinf(value):
        return "+Inf"
    return str(int(value)) if float(value).is_integer() else repr(float(value))


@dataclass
class _Histogram:
    bounds: tuple[float, ...]
    counts: list[int] = field(default_factory=list)
    total: float = 0.0
    count: int = 0

    def observe(self, value: float) -> None:
        if not self.counts:
            self.counts = [0] * len(self.bounds)
        for i, bound in enumerate(self.bounds):
            if value <= bound:
                self.counts[i] += 1
        self.total += value
        self.count += 1


class MetricsRegistry:
    """Counters and histograms in memory, rendered in the Prometheus text format 0.0.4."""

    def __init__(self, buckets: tuple[float, ...] = DURATION_BUCKETS) -> None:
        self._buckets = buckets
        self._lock = threading.Lock()
        self._counters: dict[str, dict[LabelKey, float]] = {}
        self._histograms: dict[str, dict[LabelKey, _Histogram]] = {}

    def count(self, name: str, value: float = 1, labels: Mapping[str, Any] | None = None) -> None:
        """Add `value` to the counter `<name>_total` with `labels`."""
        family, key = _counter_name(name), _key(labels or {})
        with self._lock:
            series = self._counters.setdefault(family, {})
            series[key] = series.get(key, 0) + value

    def observe(self, family: str, value: float, labels: Mapping[str, Any]) -> None:
        """One observation of the histogram `family` (already a Prometheus name)."""
        key = _key(labels)
        with self._lock:
            series = self._histograms.setdefault(family, {})
            series.setdefault(key, _Histogram(self._buckets)).observe(value)

    def span_ended(
        self, name: str, attributes: Mapping[str, Any], seconds: float, outcome: str
    ) -> None:
        """Record a timed span (`TIMED_SPANS`); any other span is ignored."""
        label = TIMED_SPANS.get(name)
        if label is None:
            return
        code = attributes.get("error.code")
        status = "error" if code is not None and outcome == "ok" else outcome
        own = {label: attributes.get(label, "")}
        self.observe(f"{prom_name(name)}_duration_seconds", seconds, {**own, "status": status})
        if code is not None:
            self.count(f"{name}_errors", 1, {**own, "code": code})

    def render(self, const_labels: Mapping[str, str] | None = None) -> str:
        """Every family in the text format, the declared ones first."""
        const = _key(const_labels or {})
        with self._lock:
            counters = {f: dict(s) for f, s in self._counters.items()}
            histograms = {f: dict(s) for f, s in self._histograms.items()}
        lines: list[str] = []
        names = [*DECLARED, *sorted((set(counters) | set(histograms)) - set(DECLARED))]
        for family in names:
            found = "histogram" if family in histograms else "counter"
            kind, text = DECLARED.get(family, (found, ""))
            lines.append(f"# HELP {family} {text or family}")
            lines.append(f"# TYPE {family} {kind}")
            if kind == "histogram":
                lines.extend(_histogram_lines(family, histograms.get(family, {}), const))
            else:
                for key, value in sorted(counters.get(family, {}).items()):
                    lines.append(f"{family}{_labels(key, const)} {_number(value)}")
        return "\n".join(lines) + "\n"


def _labels(key: LabelKey, const: LabelKey, extra: LabelKey = ()) -> str:
    merged = dict(const)
    merged.update(dict(key))
    merged.update(dict(extra))
    if not merged:
        return ""
    return "{" + ",".join(f'{k}="{_escape(v)}"' for k, v in sorted(merged.items())) + "}"


def _histogram_lines(
    family: str, series: Mapping[LabelKey, _Histogram], const: LabelKey
) -> Iterator[str]:
    for key, hist in sorted(series.items()):
        for bound, count in zip(hist.bounds, hist.counts, strict=True):
            le = (("le", _number(bound)),)
            yield f"{family}_bucket{_labels(key, const, le)} {count}"
        yield f"{family}_bucket{_labels(key, const, (('le', '+Inf'),))} {hist.count}"
        yield f"{family}_sum{_labels(key, const)} {_number(hist.total)}"
        yield f"{family}_count{_labels(key, const)} {hist.count}"


@contextmanager
def observe_span(
    metrics: MetricsRegistry | None, name: str, attributes: Mapping[str, Any]
) -> Iterator[None]:
    """Around a span's body: the run's ids for log lines (`chassis.logs.run_context`), and, when
    `metrics` is set, its time and outcome. `attributes` is read again at the end, so an
    `error.code` set inside counts."""
    with run_context(request_id=attributes.get("request_id"), trace_id=attributes.get("trace_id")):
        start = time.perf_counter()
        outcome = "ok"
        try:
            yield
        except GeneratorExit:
            outcome = "cancelled"
            raise
        except BaseException as exc:
            outcome = "cancelled" if isinstance(exc, asyncio.CancelledError) else "error"
            raise
        finally:
            if metrics is not None:
                metrics.span_ended(name, attributes, time.perf_counter() - start, outcome)
