"""PoC-7 observability, exit criterion 1: the same spans reach Langfuse and a second OpenTelemetry
backend by exporter config only.

No Langfuse, Collector, or Tempo runs here (no image registry), so two in-process OTLP/HTTP
receivers stand in for them. Each is an ASGI app that decodes `ExportTraceServiceRequest`
(`opentelemetry-proto`):

- `LANGFUSE` serves Langfuse's OTLP path, `/api/public/otel/v1/traces`, and refuses a request
  without the project's Basic auth (`pk:sk`), as Langfuse does.
- `TEMPO` serves the plain OTLP path, `/v1/traces`, as Tempo and Jaeger do.

The exporters are the SDK's own OTLP/HTTP exporters. Their HTTP is routed into the receivers by
host (`*.invalid`), below the exporter, so the exporter code and its config are the real ones.
The spans are what a run emits on both sides: `chassis.run`, `chassis.engine.run`, then the
workload's `workload.handle` from the A2A `traceparent`, then `chassis.model.call` from the
workload's. The chassis exports with `OtelTelemetry.from_env()`, the workload with
`workload_a2a.telemetry.configure_from_env()`: the swap is only the `OTEL_EXPORTER_OTLP_*`
variables. The fan-out is the Collector's job (plan decision 7): `deploy/compose/poc07/
otel-collector.yaml` lists both exporters in the traces pipeline, checked statically here, and the
same fan-out with two exporters in one process shows both receivers get the same set.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
import requests
import yaml
from chassis.adapters.otel import OtelTelemetry
from chassis.core.trace import traceparent_for
from chassis_contracts.helpers import make_context, make_request
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route
from starlette.testclient import TestClient
from workload_a2a import telemetry as workload_telemetry

ROOT = Path(__file__).resolve().parents[3]
COLLECTOR = ROOT / "deploy/compose/poc07/otel-collector.yaml"
TRACE = "5b8aa5a2d2c872e8321cf37308d69df2"  # pragma: allowlist secret
LANGFUSE_HOST = "langfuse.invalid"
TEMPO_HOST = "tempo.invalid"
LANGFUSE_PATH = "/api/public/otel/v1/traces"
TEMPO_PATH = "/v1/traces"
PUBLIC_KEY = "pk-lf-poc07"
SECRET_KEY = "sk-lf-poc07"  # pragma: allowlist secret (a test value for a stub, not a key)
BASIC = base64.b64encode(f"{PUBLIC_KEY}:{SECRET_KEY}".encode()).decode()

SpanKey = tuple[str, str, str | None, str]
"""(name, trace id, parent name, service): what must be equal in both backends."""


@dataclass
class Receiver:
    """One OTLP/HTTP backend: the path it serves, the auth it wants, the spans it decoded."""

    path: str
    auth: str | None = None
    spans: list[dict[str, Any]] = field(default_factory=list)
    refused: int = 0

    def app(self) -> Starlette:
        async def traces(request: Request) -> Response:
            if self.auth is not None and request.headers.get("authorization") != self.auth:
                self.refused += 1
                return Response(status_code=401)
            if request.headers.get("content-type") != "application/x-protobuf":
                return Response(status_code=415)
            message = ExportTraceServiceRequest.FromString(await request.body())
            for resource in message.resource_spans:
                service = next(
                    a.value.string_value
                    for a in resource.resource.attributes
                    if a.key == "service.name"
                )
                for scope in resource.scope_spans:
                    for span in scope.spans:
                        self.spans.append(
                            {
                                "name": span.name,
                                "trace_id": span.trace_id.hex(),
                                "span_id": span.span_id.hex(),
                                "parent": span.parent_span_id.hex() or None,
                                "service": service,
                            }
                        )
            body = ExportTraceServiceResponse().SerializeToString()
            return Response(body, media_type="application/x-protobuf")

        return Starlette(routes=[Route(self.path, traces, methods=["POST"])])

    def keys(self) -> set[SpanKey]:
        names = {s["span_id"]: s["name"] for s in self.spans}
        return {
            (s["name"], s["trace_id"], names.get(s["parent"]), s["service"]) for s in self.spans
        }


@pytest.fixture
def backends(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Receiver]]:
    """Langfuse and Tempo stand-ins; every `requests` call to their hosts lands in them."""
    receivers = {
        LANGFUSE_HOST: Receiver(LANGFUSE_PATH, auth=f"Basic {BASIC}"),
        TEMPO_HOST: Receiver(TEMPO_PATH),
    }
    clients = {host: TestClient(r.app()) for host, r in receivers.items()}

    def send(self: Any, request: requests.PreparedRequest, **kwargs: Any) -> requests.Response:
        url = urlsplit(str(request.url))
        client = clients.get(url.hostname or "")
        if client is None:
            raise requests.ConnectionError(f"no stand-in for {url.hostname}")
        body = request.body if isinstance(request.body, bytes) else None
        headers = {k: str(v) for k, v in request.headers.items()}
        answer = client.post(url.path, content=body, headers=headers)
        response = requests.Response()
        response.status_code = answer.status_code
        response._content = answer.content
        response.headers.update(answer.headers)
        response.url = str(request.url)
        response.request = request
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    monkeypatch.setenv("OTEL_METRICS_EXPORTER", "none")
    for name in (
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "OTEL_EXPORTER_OTLP_TRACES_HEADERS",
        "OTEL_SERVICE_NAME",
    ):
        monkeypatch.delenv(name, raising=False)
    yield receivers
    for client in clients.values():
        client.close()


def _point_at(monkeypatch: pytest.MonkeyPatch, backend: str) -> None:
    """The whole swap: the standard OTLP variables, nothing else."""
    if backend == "langfuse":
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", f"http://{LANGFUSE_HOST}/api/public/otel")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", f"Authorization=Basic%20{BASIC}")
    else:
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", f"http://{TEMPO_HOST}")
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_HEADERS", raising=False)


def emit_one_run(chassis: OtelTelemetry) -> None:
    """The spans of one run, both containers, linked only through `traceparent` values."""
    request = make_request(text="hi").model_copy(update={"trace_id": TRACE})
    ctx = make_context(request)
    with (
        chassis.span("chassis.run", trace_id=TRACE, **{"session.id": "s1"}),
        chassis.span("chassis.engine.run", trace_id=TRACE) as connector,
    ):
        a2a = {"traceparent": traceparent_for(ctx, connector.span_id)}
        a2a.update(chassis.propagation_headers())
        with workload_telemetry.handle_span(a2a, {"request_id": request.request_id}):
            outbound = workload_telemetry.trace_headers()
        with chassis.span("chassis.model.call", traceparent=outbound["traceparent"]):
            pass


def _run_with_env(monkeypatch: pytest.MonkeyPatch, backend: str) -> None:
    _point_at(monkeypatch, backend)
    chassis = OtelTelemetry.from_env()
    workload_telemetry.configure_from_env()
    try:
        emit_one_run(chassis)
    finally:
        chassis.shutdown()
        workload_telemetry.shutdown()


EXPECTED: set[SpanKey] = {
    ("chassis.run", TRACE, None, "chassis"),
    ("chassis.engine.run", TRACE, "chassis.run", "chassis"),
    ("workload.handle", TRACE, "chassis.engine.run", "workload"),
    ("chassis.model.call", TRACE, "workload.handle", "chassis"),
}


def test_switching_the_backend_is_exporter_config_only(
    backends: dict[str, Receiver], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit criterion 1 (swap): the same code, run twice, once with the OTLP variables naming the
    Langfuse stand-in (its path and Basic auth), once naming the Tempo stand-in. Each backend gets
    the same spans, in one trace, with the same parent links."""
    _run_with_env(monkeypatch, "langfuse")
    langfuse, tempo = backends[LANGFUSE_HOST], backends[TEMPO_HOST]
    assert tempo.spans == []
    _run_with_env(monkeypatch, "tempo")
    assert langfuse.keys() == tempo.keys() == EXPECTED
    assert len(langfuse.spans) == len(tempo.spans) == 4
    assert langfuse.refused == 0


def test_the_langfuse_stand_in_refuses_a_call_without_its_auth(
    backends: dict[str, Receiver], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stand-in checks what Langfuse checks, so the swap test proves the header is sent."""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", f"http://{LANGFUSE_HOST}/api/public/otel")
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_HEADERS", raising=False)
    chassis = OtelTelemetry.from_env()
    with chassis.span("chassis.run", trace_id=TRACE):
        pass
    chassis.shutdown()
    assert backends[LANGFUSE_HOST].refused >= 1
    assert backends[LANGFUSE_HOST].spans == []


def test_fan_out_sends_the_same_spans_to_both(backends: dict[str, Receiver]) -> None:
    """Exit criterion 1 (fan-out): two OTLP/HTTP exporters, one per backend, get the same span
    set from one run. In the deployment the Collector does this (one OTLP input, two exporters);
    here two exporters in one process stand in for it."""
    langfuse_exporter = OTLPSpanExporter(
        endpoint=f"http://{LANGFUSE_HOST}{LANGFUSE_PATH}",
        headers={"Authorization": f"Basic {BASIC}"},
    )
    tempo_exporter = OTLPSpanExporter(endpoint=f"http://{TEMPO_HOST}{TEMPO_PATH}")
    processors = [BatchSpanProcessor(langfuse_exporter), BatchSpanProcessor(tempo_exporter)]
    chassis = OtelTelemetry(span_processors=processors, service_name="chassis")
    workload_telemetry.configure(
        span_processors=[
            BatchSpanProcessor(
                OTLPSpanExporter(
                    endpoint=f"http://{LANGFUSE_HOST}{LANGFUSE_PATH}",
                    headers={"Authorization": f"Basic {BASIC}"},
                )
            ),
            BatchSpanProcessor(OTLPSpanExporter(endpoint=f"http://{TEMPO_HOST}{TEMPO_PATH}")),
        ],
        service_name="workload",
    )
    try:
        emit_one_run(chassis)
    finally:
        chassis.shutdown()
        workload_telemetry.shutdown()
    langfuse, tempo = backends[LANGFUSE_HOST], backends[TEMPO_HOST]
    assert langfuse.keys() == tempo.keys() == EXPECTED
    assert sorted(s["span_id"] for s in langfuse.spans) == sorted(s["span_id"] for s in tempo.spans)


def test_the_collector_config_fans_traces_out_to_langfuse_and_jaeger() -> None:
    """Exit criterion 1 (config): the Collector config parses; its traces pipeline takes OTLP in
    and exports to both Langfuse (OTLP/HTTP at `/api/public/otel`, Basic auth from the
    environment, never a literal key) and Jaeger, the second backend; the logs pipeline has the
    redaction stage."""
    config = yaml.safe_load(COLLECTOR.read_text())
    assert "otlp" in config["receivers"]
    exporters = config["exporters"]
    langfuse = exporters["otlphttp/langfuse"]
    assert langfuse["endpoint"] == "http://langfuse-web:3000/api/public/otel"
    assert langfuse["headers"]["Authorization"] == "Basic ${env:LANGFUSE_AUTH}"
    assert "otlp/jaeger" in exporters
    pipelines = config["service"]["pipelines"]
    assert pipelines["traces"]["receivers"] == ["otlp"]
    assert {"otlphttp/langfuse", "otlp/jaeger"} <= set(pipelines["traces"]["exporters"])
    assert any(p.startswith("transform/redact") for p in pipelines["logs"]["processors"])
