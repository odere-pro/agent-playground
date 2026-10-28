"""Workload-side tracing (PoC-7, 014 H-7): one `workload.handle` span per A2A task, in the trace
the chassis sent, and the OpenInference instrumentors each engine turns on in its own module.

Never imports the chassis (ADR-002). The OpenTelemetry SDK is the optional extra
`workload-a2a[otel]`; without it every function here is a no-op and the server runs as before.

- **Setup.** `configure(...)` builds a `TracerProvider` (never the global one) with the given
  span exporters or processors; tests pass an in-memory exporter. `configure_from_env()` does it
  with the OTLP/HTTP exporter when `OTEL_EXPORTER_OTLP_ENDPOINT` or
  `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` is set (the standard variables pick the endpoint and the
  headers), and does nothing otherwise. `build_app` calls it. `shutdown()` undoes all of it.
- **Engines.** An engine calls `register_instrumentor(fn)` at import. `fn(tracer_provider)` turns
  its OpenInference instrumentor on and returns how to turn it off. It runs when telemetry is
  configured (at once when it already is). Nothing engine-specific lives here.
- **The handle span.** `handle_span(headers, ctx)` opens `workload.handle` as a child of the A2A
  request's `traceparent` header (else `ctx["traceparent"]`) with the W3C `baggage` header in its
  context, and makes it current while `handle` runs. `BaggageAttributes`, a span processor, copies
  `user.id` and `session.id` from the baggage onto every workload span, framework spans included.
- **Outbound calls.** `inject_trace_headers` is an `httpx`/`httpx2` request hook: it sets
  `traceparent` (and `baggage`) to the current span, so the chassis model proxy and tool endpoint
  spans are children of the workload span that made the call. With no current span it leaves the
  request as it is (the workload's own `ctx["traceparent"]` header stays).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

try:
    from opentelemetry import baggage, trace
    from opentelemetry import context as otel_context
    from opentelemetry.baggage.propagation import W3CBaggagePropagator
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider
    from opentelemetry.sdk.trace import Span as SdkSpan
    from opentelemetry.sdk.trace.export import (
        BatchSpanProcessor,
        SimpleSpanProcessor,
        SpanExporter,
    )
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

    AVAILABLE = True
except ImportError:  # pragma: no cover - the `otel` extra is not installed
    AVAILABLE = False

__all__ = [
    "AVAILABLE",
    "BAGGAGE_KEYS",
    "HANDLE_SPAN",
    "Instrumentor",
    "configure",
    "configure_from_env",
    "current",
    "handle_span",
    "inject_trace_headers",
    "register_instrumentor",
    "shutdown",
    "trace_headers",
]

HANDLE_SPAN = "workload.handle"
BAGGAGE_KEYS = ("user.id", "session.id")
DEFAULT_SERVICE_NAME = "workload"
SCOPE = "workload_a2a"

Undo = Callable[[], None]
Instrumentor = Callable[[Any], Undo | None]
"""`fn(tracer_provider)`: turn an engine's instrumentor on; return how to turn it off."""

_instrumentors: list[Instrumentor] = []
_undo: list[Undo] = []
_provider: Any = None


if AVAILABLE:

    class BaggageAttributes(SpanProcessor):
        """Copies `user.id` and `session.id` from the context's baggage onto each new span."""

        def on_start(
            self, span: SdkSpan, parent_context: otel_context.Context | None = None
        ) -> None:
            for key in BAGGAGE_KEYS:
                value = baggage.get_baggage(key, parent_context)
                if value is not None:
                    span.set_attribute(key, str(value))

        def on_end(self, span: ReadableSpan) -> None:
            return None

    _TRACE_CONTEXT = TraceContextTextMapPropagator()
    _BAGGAGE = W3CBaggagePropagator()


def current() -> Any:
    """The configured `TracerProvider`, or None."""
    return _provider


def configure(
    *,
    span_exporters: Sequence[Any] = (),
    span_processors: Sequence[Any] = (),
    service_name: str | None = None,
) -> Any:
    """Build the workload's tracer provider and turn on every registered instrumentor. Replaces
    an earlier configuration. Returns the provider (None without the `otel` extra)."""
    global _provider
    if not AVAILABLE:
        return None
    shutdown()
    name = service_name or os.environ.get("OTEL_SERVICE_NAME") or DEFAULT_SERVICE_NAME
    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: name}))
    provider.add_span_processor(BaggageAttributes())
    for exporter in span_exporters:
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    for processor in span_processors:
        provider.add_span_processor(processor)
    _provider = provider
    for instrumentor in _instrumentors:
        _turn_on(instrumentor)
    return provider


def configure_from_env() -> Any:
    """`configure` with the OTLP/HTTP exporter when an OTLP endpoint is set; else nothing."""
    if not AVAILABLE or _provider is not None:
        return _provider
    if not (
        os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        or os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
    ):
        return None
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    exporter: SpanExporter = OTLPSpanExporter()
    return configure(span_processors=[BatchSpanProcessor(exporter)])


def shutdown() -> None:
    """Turn every instrumentor off and flush and drop the provider."""
    global _provider
    while _undo:
        _undo.pop()()
    if _provider is not None:
        _provider.shutdown()
    _provider = None


def register_instrumentor(instrumentor: Instrumentor) -> None:
    """Called by an engine at import. Runs `instrumentor` now if telemetry is configured."""
    if instrumentor in _instrumentors:
        return
    _instrumentors.append(instrumentor)
    if _provider is not None:
        _turn_on(instrumentor)


def _turn_on(instrumentor: Instrumentor) -> None:
    undo = instrumentor(_provider)
    if undo is not None:
        _undo.append(undo)


@contextmanager
def handle_span(headers: Mapping[str, str], ctx: Mapping[str, Any]) -> Iterator[Any]:
    """`workload.handle`, current for the block, in the trace the request names. Yields the span,
    or None when telemetry is not configured."""
    if _provider is None:
        yield None
        return
    lower = {k.lower(): v for k, v in headers.items()}
    carrier: dict[str, str] = {}
    traceparent = lower.get("traceparent") or ctx.get("traceparent")
    if isinstance(traceparent, str) and traceparent:
        carrier["traceparent"] = traceparent
    if lower.get("baggage"):
        carrier["baggage"] = lower["baggage"]
    parent = _BAGGAGE.extract(carrier, context=_TRACE_CONTEXT.extract(carrier))
    tracer = _provider.get_tracer(SCOPE)
    attributes = {"request_id": str(ctx.get("request_id") or "")}
    span = tracer.start_span(HANDLE_SPAN, context=parent, attributes=attributes)
    token = otel_context.attach(trace.set_span_in_context(span, parent))
    try:
        yield span
    except Exception as exc:
        span.record_exception(exc)
        span.set_status(trace.Status(trace.StatusCode.ERROR, type(exc).__name__))
        raise
    finally:
        span.end()
        otel_context.detach(token)


def trace_headers() -> dict[str, str]:
    """`traceparent` and `baggage` for the current span, or `{}` when there is none."""
    if _provider is None or not trace.get_current_span().get_span_context().is_valid:
        return {}
    carrier: dict[str, str] = {}
    _TRACE_CONTEXT.inject(carrier)
    _BAGGAGE.inject(carrier)
    return carrier


async def inject_trace_headers(request: Any) -> None:
    """An `httpx`/`httpx2` request event hook: the current span's trace headers on the request."""
    for key, value in trace_headers().items():
        request.headers[key] = value
