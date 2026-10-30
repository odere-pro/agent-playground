"""LiteLLMModel forwards the current span's W3C `traceparent` to LiteLLM (PoC-8), so LiteLLM's
own OpenTelemetry spans join the run's trace under `chassis.model.call` (ADR-007 item 4).

No span current (the `memory` telemetry, or a call outside the proxy): no header. Every test uses
`httpx.MockTransport`; no socket.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from chassis.adapters.litellm import LiteLLMModel
from chassis.adapters.otel import OtelTelemetry
from chassis.ports.model import ModelMessage
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

MESSAGES = [ModelMessage(role="user", content="hello")]
TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"  # pragma: allowlist secret
COMPLETION: dict[str, Any] = {
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"}}],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
}
STREAM = (
    'data: {"choices": [{"index": 0, "delta": {"content": "hi"}, "finish_reason": "stop"}]}\n\n'
    "data: [DONE]\n\n"
)


def _capture(stream: bool = False) -> tuple[list[httpx.Request], httpx.MockTransport]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if stream:
            return httpx.Response(200, text=STREAM, headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json=COMPLETION)

    return seen, httpx.MockTransport(handler)


def _ids(request: httpx.Request) -> tuple[str, str, str]:
    """version, trace id, and parent span id of the request's `traceparent` (the flags byte may
    carry the SDK's random-trace-id bit, so it is not compared)."""
    version, trace_id, parent, _flags = request.headers["traceparent"].split("-")
    return version, trace_id, parent


def _telemetry() -> OtelTelemetry:
    return OtelTelemetry(span_exporters=[InMemorySpanExporter()])


async def test_complete_sends_the_current_spans_traceparent() -> None:
    seen, transport = _capture()
    model = LiteLLMModel("http://router/v1", transport=transport)
    telemetry = _telemetry()
    with telemetry.span("chassis.model.call", trace_id=TRACE_ID) as span:
        await model.complete(MESSAGES, route="big-default")
    assert _ids(seen[0]) == ("00", TRACE_ID, span.span_id)
    assert json.loads(seen[0].content)["model"] == "big-default"


async def test_stream_sends_the_current_spans_traceparent() -> None:
    seen, transport = _capture(stream=True)
    model = LiteLLMModel("http://router/v1", transport=transport)
    telemetry = _telemetry()
    with telemetry.span("chassis.model.call", trace_id=TRACE_ID) as span:
        chunks = [c async for c in model.stream(MESSAGES, route="big-default")]
    assert chunks[-1].finish
    assert _ids(seen[0]) == ("00", TRACE_ID, span.span_id)


async def test_no_current_span_sends_no_traceparent() -> None:
    seen, transport = _capture()
    model = LiteLLMModel("http://router/v1", transport=transport)
    await model.complete(MESSAGES, route="big-default")
    assert "traceparent" not in seen[0].headers
