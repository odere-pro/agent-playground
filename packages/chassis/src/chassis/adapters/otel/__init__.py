"""`OtelTelemetry`: `TelemetryPort` on the OpenTelemetry SDK (PoC-7, 014 H-7).

`spec.adapters.telemetry: otel` builds it with `OtelTelemetry.from_env()`. The OpenTelemetry SDK
is imported only here (`make lint`). The SDK is never installed as the global provider, so two
instances (a test's, a workload's in the same process) never mix.

**Export.** Exporter config only. `from_env` sends spans (and counters, unless
`OTEL_METRICS_EXPORTER=none`) over OTLP/HTTP with the SDK's own exporters, which read the
standard `OTEL_EXPORTER_OTLP_*` variables (endpoint, headers, timeout). With neither
`OTEL_EXPORTER_OTLP_ENDPOINT` nor `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` set it raises
`LookupError` ("not configured"). Which backend receives the spans (Langfuse, Tempo, both) is the
OTel Collector's exporter config, never this code (plan decision 7). Tests pass span exporters,
span processors, and metric readers to the constructor.

**One trace per run.** `Span.span_id` is the SDK's 16-hex span id, so the connector's
`traceparent_for(ctx, span.span_id)` names a span that exists. The parent of a new span is, in
order:

1. the span a valid W3C `traceparent` attribute names (a remote parent): the model proxy and the
   tool endpoint continue the workload's call, the run continues the caller's. The header is
   control, not data: it is not copied onto the span.
2. the current span, when the span has no `trace_id` attribute or the same one;
3. none, in the trace `trace_id_hex(trace_id)` when `trace_id` is given (`chassis.run`), so the
   chassis, the A2A hop, the workload, and the model proxy share the request's trace id;
4. none, in a fresh trace.

**Baggage.** `user.id` and `session.id` attributes go into the span's OTel baggage, and every
span under it gets them as attributes. `propagation_headers()` is the current W3C `baggage`
header (or nothing); the A2A connector sends it next to `traceparent`, so the workload can put the
ids on its own spans. The connector reaches it through `getattr`, so neither it nor
`chassis.server` imports the SDK.

**Attributes.** `None` is dropped; numbers and booleans stay; any other value is a string. Every
string passes through `chassis.redaction.redact` except the ids in `ID_KEYS`. An `error.code`
attribute, or an exception out of the block, sets the span status to error.

**Logs.** `log` builds one JSON-ready record: `time`, `level`, `message`, the fields, and the
current `trace_id` and `span_id` (outside a span, `trace_id_hex` of a `trace_id` field). Every
string except the ids is redacted (regex only; named-entity redaction runs in the Collector's log
pipeline, never here). The record goes to `log_sink`, by default one JSON line on the `chassis`
logger.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import os
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from opentelemetry import baggage, trace
from opentelemetry import context as otel_context
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.metrics import Counter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.id_generator import RandomIdGenerator
from opentelemetry.sdk.trace.sampling import Sampler
from opentelemetry.trace import Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from chassis.core.trace import parse_traceparent, trace_id_hex
from chassis.ports.telemetry import Span
from chassis.redaction import redact, redact_value

__all__ = ["BAGGAGE_KEYS", "ID_KEYS", "LogSink", "OtelTelemetry"]

BAGGAGE_KEYS = ("user.id", "session.id")
"""The attributes that travel as W3C baggage (OpenInference and Langfuse read these names)."""
ID_KEYS = frozenset(
    {
        "request_id",
        "trace_id",
        "span_id",
        "agent",
        "interface",
        "lane",
        "route",
        "format",
        "tool",
        "a2a.task_id",
        *BAGGAGE_KEYS,
    }
)
"""Attribute and log keys that are ids, never redacted. suggested: the list."""
DEFAULT_SERVICE_NAME = "chassis"
SCOPE = "chassis"
LogSink = Callable[[dict[str, Any]], None]

_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "warn": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}
_logger = logging.getLogger("chassis")
_FORCED_TRACE_ID: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "chassis_otel_trace_id", default=None
)
_TRACE_CONTEXT = TraceContextTextMapPropagator()
_BAGGAGE = W3CBaggagePropagator()


class _RunTraceIds(RandomIdGenerator):
    """Random ids, except a root span's trace id while `_FORCED_TRACE_ID` is set: the request's."""

    def generate_trace_id(self) -> int:
        forced = _FORCED_TRACE_ID.get()
        return forced if forced is not None else super().generate_trace_id()


def _write_log(record: dict[str, Any]) -> None:
    level = _LEVELS.get(str(record.get("level", "info")).lower(), logging.INFO)
    _logger.log(level, json.dumps(record, default=str))


def _attribute(key: str, value: Any) -> Any:
    """`value` as an OTel attribute value, redacted unless `key` is an id; None to drop."""
    if value is None:
        return None
    if isinstance(value, bool | int | float):
        return value
    if isinstance(value, list | tuple) and all(isinstance(v, str) for v in value):
        return [v if key in ID_KEYS else redact(v) for v in value]
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if key in ID_KEYS else redact(text)


def _hex_span_id(context: trace.SpanContext | None) -> str | None:
    if context is None or not context.is_valid:
        return None
    return f"{context.span_id:016x}"


class OtelTelemetry:
    """`TelemetryPort` on the OpenTelemetry SDK. See the module docstring."""

    def __init__(
        self,
        *,
        span_exporters: Sequence[SpanExporter] = (),
        span_processors: Sequence[SpanProcessor] = (),
        metric_readers: Sequence[MetricReader] = (),
        service_name: str | None = None,
        sampler: Sampler | None = None,
        log_sink: LogSink | None = None,
    ) -> None:
        self.service_name = (
            service_name or os.environ.get("OTEL_SERVICE_NAME") or (DEFAULT_SERVICE_NAME)
        )
        resource = Resource.create({SERVICE_NAME: self.service_name})
        self.tracer_provider = TracerProvider(
            resource=resource, sampler=sampler, id_generator=_RunTraceIds()
        )
        self.exporters: list[SpanExporter] = list(span_exporters)
        for exporter in span_exporters:
            self.tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
        for processor in span_processors:
            self.tracer_provider.add_span_processor(processor)
        self._tracer = self.tracer_provider.get_tracer(SCOPE)
        self.meter_provider = MeterProvider(metric_readers=list(metric_readers), resource=resource)
        self._meter = self.meter_provider.get_meter(SCOPE)
        self._counters: dict[str, Counter] = {}
        self._log_sink: LogSink = log_sink or _write_log

    @classmethod
    def from_env(cls) -> OtelTelemetry:
        """OTLP/HTTP from the standard variables. `LookupError` when no endpoint is set."""
        if not (
            os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
            or os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
        ):
            raise LookupError(
                "set OTEL_EXPORTER_OTLP_ENDPOINT (or OTEL_EXPORTER_OTLP_TRACES_ENDPOINT)"
            )
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        exporter = OTLPSpanExporter()
        readers: list[MetricReader] = []
        if os.environ.get("OTEL_METRICS_EXPORTER", "otlp").strip().lower() != "none":
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
            from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

            readers.append(PeriodicExportingMetricReader(OTLPMetricExporter()))
        telemetry = cls(span_processors=[BatchSpanProcessor(exporter)], metric_readers=readers)
        telemetry.exporters.append(exporter)
        return telemetry

    # --- TelemetryPort ---

    @contextmanager
    def span(self, name: str, **attributes: Any) -> Iterator[Span]:
        traceparent = attributes.pop("traceparent", None)
        parent = self._parent_context(traceparent, attributes.get("trace_id"))
        context = parent.context
        for key in BAGGAGE_KEYS:
            value = attributes.get(key)
            if value is not None and str(value):
                context = baggage.set_baggage(key, str(value), context)
        for key in BAGGAGE_KEYS:
            carried = baggage.get_baggage(key, context)
            if attributes.get(key) is None and carried is not None:
                attributes[key] = str(carried)
        forced = _FORCED_TRACE_ID.set(parent.forced_trace_id)
        try:
            otel_span = self._tracer.start_span(name, context=context)
        finally:
            _FORCED_TRACE_ID.reset(forced)
        port_span = Span(
            name=name,
            span_id=_hex_span_id(otel_span.get_span_context()) or "",
            parent_id=parent.parent_id,
            attributes=attributes,
        )
        token = otel_context.attach(trace.set_span_in_context(otel_span, context))
        try:
            yield port_span
        except Exception as exc:
            otel_span.record_exception(exc)
            otel_span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            raise
        finally:
            self._finish(otel_span, port_span)
            otel_context.detach(token)

    def counter(self, name: str, value: int = 1, **labels: Any) -> None:
        counter = self._counters.get(name)
        if counter is None:
            counter = self._counters[name] = self._meter.create_counter(name)
        clean = {k: _attribute(k, v) for k, v in labels.items()}
        counter.add(value, {k: v for k, v in clean.items() if v is not None})

    def log(self, level: str, message: str, **fields: Any) -> None:
        record: dict[str, Any] = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
            "level": level,
            "message": redact(message),
        }
        for key, value in fields.items():
            record[key] = value if key in ID_KEYS else redact_value(value)
        current = trace.get_current_span().get_span_context()
        if current.is_valid:
            record["trace_id"] = f"{current.trace_id:032x}"
            record["span_id"] = f"{current.span_id:016x}"
        elif isinstance(fields.get("trace_id"), str) and fields["trace_id"]:
            record["trace_id"] = trace_id_hex(fields["trace_id"])
        self._log_sink(record)

    # --- Beyond the port ---

    def propagation_headers(self) -> dict[str, str]:
        """The W3C `baggage` header of the current context, or `{}`. The A2A connector sends it
        next to its own `traceparent`."""
        carrier: dict[str, str] = {}
        _BAGGAGE.inject(carrier)
        return {k: v for k, v in carrier.items() if k == "baggage" and v}

    def shutdown(self) -> None:
        """Flush and stop the span processors and metric readers."""
        self.tracer_provider.shutdown()
        self.meter_provider.shutdown()

    async def aclose(self) -> None:
        """`shutdown` off the event loop: a flush may wait on the exporter's HTTP call."""
        await asyncio.to_thread(self.shutdown)

    # --- Internals ---

    def _parent_context(self, traceparent: Any, trace_id: Any) -> _Parent:
        current = otel_context.get_current()
        if isinstance(traceparent, str) and parse_traceparent(traceparent) is not None:
            header = traceparent.strip().lower()
            remote = _TRACE_CONTEXT.extract({"traceparent": header}, context=current)
            remote_context = trace.get_current_span(remote).get_span_context()
            if remote_context.is_valid:
                return _Parent(remote, _hex_span_id(remote_context), None)
        wanted = trace_id_hex(trace_id) if isinstance(trace_id, str) and trace_id else None
        current_span = trace.get_current_span(current).get_span_context()
        if current_span.is_valid and (wanted is None or f"{current_span.trace_id:032x}" == wanted):
            return _Parent(current, _hex_span_id(current_span), None)
        root = trace.set_span_in_context(trace.INVALID_SPAN, current)
        return _Parent(root, None, int(wanted, 16) if wanted is not None else None)

    def _finish(self, otel_span: trace.Span, port_span: Span) -> None:
        port_span.ended = True
        attributes = {
            key: _attribute(key, value)
            for key, value in port_span.attributes.items()
            if key != "traceparent"
        }
        otel_span.set_attributes({k: v for k, v in attributes.items() if v is not None})
        code = port_span.attributes.get("error.code")
        if code is not None:
            otel_span.set_status(Status(StatusCode.ERROR, str(code)))
        otel_span.end()


class _Parent:
    """Where a new span hangs: the context to start it in, the parent's hex id, and the trace id
    to force when it is a root in the request's trace."""

    __slots__ = ("context", "forced_trace_id", "parent_id")

    def __init__(
        self, context: otel_context.Context, parent_id: str | None, forced_trace_id: int | None
    ) -> None:
        self.context = context
        self.parent_id = parent_id
        self.forced_trace_id = forced_trace_id
