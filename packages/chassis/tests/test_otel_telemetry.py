"""`OtelTelemetry` (PoC-7, 014 H-7): the OpenTelemetry SDK behind `TelemetryPort`.

The adapter passes the same `TelemetryPortContract` as `InMemoryTelemetry` (exit criterion 2),
with the SDK's `InMemorySpanExporter` and `InMemoryMetricReader`. The rest pins what the suite
cannot see through `Span`: real W3C ids, one trace per run from the request's `trace_id`, baggage
for the A2A hop, and redacted JSON logs. No network: nothing is exported over OTLP here.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import unquote_plus

import pytest
from chassis.adapters.otel import OtelTelemetry
from chassis.core.trace import trace_id_hex, traceparent_for
from chassis.ports.telemetry import Span
from chassis_contracts import TelemetryPortContract
from chassis_contracts.telemetry import ReadCounter, ReadSpans
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"  # pragma: allowlist secret
PARENT = "00f067aa0ba902b7"


def as_port_span(span: ReadableSpan) -> Span:
    """An exported SDK span as the port's `Span`, so the suite reads both adapters alike."""
    context = span.get_span_context()
    assert context is not None
    parent = span.parent
    return Span(
        name=span.name,
        span_id=f"{context.span_id:016x}",
        parent_id=f"{parent.span_id:016x}" if parent is not None else None,
        attributes=dict(span.attributes or {}),
        ended=span.end_time is not None,
    )


def counter_total(reader: InMemoryMetricReader, name: str) -> int:
    data = reader.get_metrics_data()
    total = 0
    for resource in data.resource_metrics if data is not None else []:
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                if metric.name == name:
                    points: Any = metric.data.data_points
                    total += sum(int(p.value) for p in points)
    return total


@pytest.fixture
def exporter() -> InMemorySpanExporter:
    return InMemorySpanExporter()


@pytest.fixture
def reader() -> InMemoryMetricReader:
    return InMemoryMetricReader()


@pytest.fixture
def logs() -> list[dict[str, Any]]:
    return []


@pytest.fixture
def otel(
    exporter: InMemorySpanExporter, reader: InMemoryMetricReader, logs: list[dict[str, Any]]
) -> OtelTelemetry:
    return OtelTelemetry(span_exporters=[exporter], metric_readers=[reader], log_sink=logs.append)


class TestOtelTelemetry(TelemetryPortContract):
    """Exit criterion 2 (TelemetryPort): the real adapter passes the fake's suite."""

    @pytest.fixture
    def telemetry(self, otel: OtelTelemetry) -> OtelTelemetry:
        return otel

    @pytest.fixture
    def read_spans(self, exporter: InMemorySpanExporter) -> ReadSpans:
        return lambda: [as_port_span(s) for s in exporter.get_finished_spans()]

    @pytest.fixture
    def read_counter(self, reader: InMemoryMetricReader) -> ReadCounter:
        return lambda name: counter_total(reader, name)


def _finished(exporter: InMemorySpanExporter, name: str) -> ReadableSpan:
    (span,) = [s for s in exporter.get_finished_spans() if s.name == name]
    return span


def test_span_id_is_the_real_w3c_span_id(
    otel: OtelTelemetry, exporter: InMemorySpanExporter
) -> None:
    """The connector builds its `traceparent` from `span.span_id`, so it must be the SDK's id."""
    with otel.span("chassis.engine.run") as span:
        assert re.fullmatch(r"[0-9a-f]{16}", span.span_id)
    context = _finished(exporter, "chassis.engine.run").get_span_context()
    assert context is not None
    assert span.span_id == f"{context.span_id:016x}"


def test_a_trace_id_attribute_roots_the_span_in_that_trace(
    otel: OtelTelemetry, exporter: InMemorySpanExporter
) -> None:
    """`chassis.run` opened with the request's trace id is a root span in that trace, and every
    span under it shares it (the connector's span, so the `traceparent` it sends names it)."""
    with (
        otel.span("chassis.run", trace_id=TRACE) as run,
        otel.span("chassis.engine.run", trace_id=TRACE) as engine,
    ):
        assert engine.parent_id == run.span_id
    root = _finished(exporter, "chassis.run")
    child = _finished(exporter, "chassis.engine.run")
    assert root.parent is None
    assert root.context is not None and child.context is not None
    assert f"{root.context.trace_id:032x}" == TRACE
    assert child.context.trace_id == root.context.trace_id


def test_a_non_hex_trace_id_maps_like_the_connector_does(
    otel: OtelTelemetry, exporter: InMemorySpanExporter
) -> None:
    with otel.span("chassis.run", trace_id="trace-from-a-client"):
        pass
    root = _finished(exporter, "chassis.run")
    assert root.context is not None
    assert f"{root.context.trace_id:032x}" == trace_id_hex("trace-from-a-client")


def test_a_trace_id_other_than_the_current_span_starts_its_own_root(
    otel: OtelTelemetry, exporter: InMemorySpanExporter
) -> None:
    with otel.span("outer"), otel.span("chassis.run", trace_id=TRACE) as run:
        assert run.parent_id is None
    root = _finished(exporter, "chassis.run")
    assert root.context is not None and f"{root.context.trace_id:032x}" == TRACE


def test_traceparent_continues_the_callers_trace(
    otel: OtelTelemetry, exporter: InMemorySpanExporter
) -> None:
    """A model proxy call made with the workload's `traceparent` is a child of that span."""
    with otel.span("chassis.model.call", traceparent=f"00-{TRACE}-{PARENT}-01"):
        pass
    span = _finished(exporter, "chassis.model.call")
    assert span.parent is not None and span.parent.is_remote
    assert f"{span.parent.span_id:016x}" == PARENT
    assert span.context is not None and f"{span.context.trace_id:032x}" == TRACE
    assert "traceparent" not in (span.attributes or {}), "the header is control, not data"


def test_the_connector_traceparent_names_the_engine_span(
    otel: OtelTelemetry, exporter: InMemorySpanExporter
) -> None:
    """`traceparent_for(ctx, span.span_id)` names the span the SDK exported."""
    from chassis_contracts.helpers import make_context, make_request

    request = make_request(text="x")
    ctx = make_context(request)
    with (
        otel.span("chassis.run", trace_id=trace_id_hex(ctx.trace_id)),
        otel.span("chassis.engine.run") as span,
    ):
        header = traceparent_for(ctx, span.span_id)
    exported = _finished(exporter, "chassis.engine.run").get_span_context()
    assert exported is not None
    assert header == f"00-{exported.trace_id:032x}-{exported.span_id:016x}-01"


def test_user_and_session_go_on_spans_and_into_baggage(
    otel: OtelTelemetry, exporter: InMemorySpanExporter
) -> None:
    """`user.id` and `session.id` on the run span become W3C baggage for the A2A hop and land on
    every chassis span under it."""
    with (
        otel.span("chassis.run", trace_id=TRACE, **{"user.id": "u-1", "session.id": "s 1"}),
        otel.span("chassis.engine.run"),
    ):
        headers = otel.propagation_headers()
    assert set(headers) == {"baggage"}
    entries = dict(item.split("=", 1) for item in headers["baggage"].split(","))
    assert {k: unquote_plus(v) for k, v in entries.items()} == {
        "user.id": "u-1",
        "session.id": "s 1",
    }
    engine = _finished(exporter, "chassis.engine.run")
    assert (engine.attributes or {}).get("session.id") == "s 1"
    assert (engine.attributes or {}).get("user.id") == "u-1"


def test_no_baggage_outside_a_run(otel: OtelTelemetry) -> None:
    assert otel.propagation_headers() == {}
    with otel.span("chassis.run", trace_id=TRACE):
        assert otel.propagation_headers() == {}


def test_attributes_set_while_the_span_is_open_are_exported(
    otel: OtelTelemetry, exporter: InMemorySpanExporter
) -> None:
    """The connector and the pipeline write `span.attributes[...]` after the span opened."""
    with otel.span("chassis.run", request_id="r1", absent=None) as span:
        span.attributes["a2a.task_id"] = "task-9"
        span.attributes["error.code"] = "a2a.timeout"
        span.attributes["error.message"] = "mail ops@example.com"
    exported = _finished(exporter, "chassis.run")
    attributes = dict(exported.attributes or {})
    assert attributes["a2a.task_id"] == "task-9"
    assert attributes["error.message"] == "mail <EMAIL_ADDRESS>", "free text is redacted"
    assert "absent" not in attributes
    assert exported.status.status_code is StatusCode.ERROR


def test_an_exception_marks_the_span_and_propagates(
    otel: OtelTelemetry, exporter: InMemorySpanExporter
) -> None:
    with pytest.raises(RuntimeError), otel.span("chassis.run"):
        raise RuntimeError("boom")
    exported = _finished(exporter, "chassis.run")
    assert exported.status.status_code is StatusCode.ERROR
    assert exported.end_time is not None


def test_span_exception_text_is_redacted_when_the_body_raises(
    otel: OtelTelemetry, exporter: InMemorySpanExporter
) -> None:
    """An exception's message reaches the exported span only after redaction (review R7-02)."""
    with pytest.raises(ValueError), otel.span("chassis.run"):
        raise ValueError("mail jane@example.com")

    exported = _finished(exporter, "chassis.run")
    event_values = [str(v) for e in exported.events for v in (e.attributes or {}).values()]
    span_values = [str(v) for v in (exported.attributes or {}).values()]

    assert not [v for v in event_values + span_values if "jane@example.com" in v]
    assert [e.name for e in exported.events] == ["exception"]
    assert dict(exported.events[0].attributes or {})["exception.type"] == "ValueError"
    assert exported.status.status_code is StatusCode.ERROR
    assert exported.status.description == "ValueError"


def test_counter_labels_reach_the_metric(otel: OtelTelemetry, reader: InMemoryMetricReader) -> None:
    otel.counter("chassis.requests", agent="echo", interface="native")
    data = reader.get_metrics_data()
    assert data is not None
    points = [
        p
        for r in data.resource_metrics
        for s in r.scope_metrics
        for m in s.metrics
        if m.name == "chassis.requests"
        for p in m.data.data_points
    ]
    assert [dict(p.attributes or {}) for p in points] == [{"agent": "echo", "interface": "native"}]


def test_log_is_json_ready_redacted_and_names_the_span(
    otel: OtelTelemetry, logs: list[dict[str, Any]]
) -> None:
    with otel.span("chassis.run", trace_id=TRACE) as span:
        otel.log("error", "run error from ops@example.com", detail=["call +1 415 555 0100"], n=3)
    (record,) = logs
    json.dumps(record)
    assert record["level"] == "error"
    assert record["message"] == "run error from <EMAIL_ADDRESS>"
    assert record["detail"] == ["call <PHONE_NUMBER>"]
    assert record["n"] == 3
    assert record["trace_id"] == TRACE
    assert record["span_id"] == span.span_id


def test_log_outside_a_span_maps_a_given_trace_id(
    otel: OtelTelemetry, logs: list[dict[str, Any]]
) -> None:
    otel.log("warning", "refused", trace_id="trace-from-a-client")
    assert logs[0]["trace_id"] == trace_id_hex("trace-from-a-client")
    assert "span_id" not in logs[0]


def test_default_log_sink_writes_one_json_line(
    caplog: pytest.LogCaptureFixture, exporter: InMemorySpanExporter
) -> None:
    otel = OtelTelemetry(span_exporters=[exporter])
    with caplog.at_level("INFO", logger="chassis"):
        otel.log("info", "hello", request_id="r1")
    (line,) = [r for r in caplog.records if r.name == "chassis"]
    assert json.loads(line.getMessage())["request_id"] == "r1"


def test_from_env_needs_an_otlp_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not configured is `LookupError`, which `profiles.lazy` reports as such."""
    for name in ("OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(LookupError):
        OtelTelemetry.from_env()


async def test_from_env_builds_an_otlp_http_exporter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exporter config only: the endpoint and headers come from the standard variables. Nothing
    is sent here (the batch processor is shut down before its first export)."""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector.invalid:4318")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "chassis-test")
    monkeypatch.setenv("OTEL_METRICS_EXPORTER", "none")
    otel = OtelTelemetry.from_env()
    try:
        assert otel.service_name == "chassis-test"
        assert [type(e).__name__ for e in otel.exporters] == ["OTLPSpanExporter"]
        assert otel.exporters[0]._endpoint == "http://collector.invalid:4318/v1/traces"  # type: ignore[attr-defined]
    finally:
        otel.shutdown()


def test_registry_names_the_adapter() -> None:
    from chassis.profiles import REGISTRY

    factory = REGISTRY["telemetry"]["otel"]
    assert callable(factory)
