"""The workload exports every span under a sampled parent (014 H-7, ADR-007 item 8).

Sampling is the Collector's. The workload's tracer provider pins `ParentBased(ALWAYS_ON)`, so
`OTEL_TRACES_SAMPLER` cannot drop a span the chassis marked sampled. Offline: in-memory exporter.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from workload_a2a import telemetry

TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"  # pragma: allowlist secret
PARENT = "00f067aa0ba902b7"


@pytest.fixture
def exporter(monkeypatch: pytest.MonkeyPatch) -> Iterator[InMemorySpanExporter]:
    monkeypatch.setenv("OTEL_TRACES_SAMPLER", "always_off")
    spans = InMemorySpanExporter()
    telemetry.configure(span_exporters=[spans])
    yield spans
    telemetry.shutdown()


def test_an_always_off_env_does_not_drop_a_span_under_a_sampled_parent(
    exporter: InMemorySpanExporter,
) -> None:
    headers = {"traceparent": f"00-{TRACE}-{PARENT}-01"}

    with telemetry.handle_span(headers, {}):
        pass

    spans = exporter.get_finished_spans()
    assert [s.name for s in spans] == ["workload.handle"]
    context = spans[0].get_span_context()
    assert context is not None and f"{context.trace_id:032x}" == TRACE
