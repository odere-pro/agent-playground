"""A fake of the LangSmith OTLP trace route, as one Starlette app (CH-10).

LangSmith takes OTLP over HTTP at `POST /otel/v1/traces`, with the key in the `x-api-key` header
(the LangSmith OpenTelemetry guide). The Collector's `otlphttp/langsmith` exporter sends there.

- `POST /otel/v1/traces`: an `ExportTraceServiceRequest`, protobuf or JSON. The spans are decoded
  and kept by trace id; the answer is an empty 200. A body that is not OTLP is 400.
- `GET /fake/traces/{trace_id}`: the recorded spans of one trace, or 404. Not a LangSmith route:
  a stack test reads the fake back through it.
- `GET /health`: no auth.
- The dataset, example, and experiment routes of CH-12: `fake_langsmith.datasets`.

A missing or wrong `x-api-key` is 401 and stores nothing.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any

from google.protobuf.json_format import MessageToDict, ParseDict, ParseError
from google.protobuf.message import DecodeError
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.common.v1.common_pb2 import AnyValue
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from fake_langsmith.datasets import DatasetStore, dataset_routes

__all__ = ["FakeLangSmith", "RecordedSpan", "create_app"]


@dataclass(frozen=True)
class RecordedSpan:
    """One decoded span; ids are lowercase hex, a root span has an empty `parent_span_id`."""

    trace_id: str
    span_id: str
    parent_span_id: str
    name: str
    attributes: dict[str, Any]


@dataclass
class FakeLangSmith(DatasetStore):
    """The fake's state: the one accepted API key, the spans in arrival order, and the datasets."""

    api_key: str
    recorded: list[RecordedSpan] = field(default_factory=list)

    def trace_ids(self) -> list[str]:
        """Each trace id once, in the order it first arrived."""
        return list(dict.fromkeys(s.trace_id for s in self.recorded))

    def spans(self, trace_id: str) -> list[RecordedSpan]:
        return [s for s in self.recorded if s.trace_id == trace_id]

    def record(self, request: ExportTraceServiceRequest) -> None:
        self.recorded.extend(
            RecordedSpan(
                trace_id=span.trace_id.hex(),
                span_id=span.span_id.hex(),
                parent_span_id=span.parent_span_id.hex(),
                name=span.name,
                attributes={kv.key: _any_value(kv.value) for kv in span.attributes},
            )
            for resource in request.resource_spans
            for scope in resource.scope_spans
            for span in scope.spans
        )


def _any_value(value: AnyValue) -> Any:
    """The one set field of an `AnyValue`, or None when it is empty (`{"value": {}}`).

    Scalars keep their Python type (an `intValue` is an int). An `arrayValue` or `kvlistValue`
    comes back as its OTLP/JSON dict.
    """
    kind = value.WhichOneof("value")
    if kind is None:
        return None
    if kind in _NESTED_KINDS:
        return MessageToDict(value).popitem()[1]
    return getattr(value, kind)


_NESTED_KINDS = frozenset({"array_value", "kvlist_value"})


_ID_KEYS = ("traceId", "spanId", "parentSpanId")


def _hex_ids_to_base64(item: dict[str, Any]) -> dict[str, Any]:
    """OTLP/JSON writes ids as hex; protobuf's JSON parser reads bytes as base64."""
    ids = {k: base64.b64encode(bytes.fromhex(item[k])).decode() for k in _ID_KEYS if k in item}
    links = [_hex_ids_to_base64(link) for link in item.get("links", [])]
    return item | ids | ({"links": links} if "links" in item else {})


def _otlp_json(body: bytes) -> dict[str, Any]:
    """The OTLP/JSON body with every span and link id turned to base64 for `ParseDict`."""
    data = json.loads(body)
    if not isinstance(data, dict):
        raise ValueError("an OTLP export is a JSON object")
    return data | {
        "resourceSpans": [
            resource
            | {
                "scopeSpans": [
                    scope | {"spans": [_hex_ids_to_base64(s) for s in scope.get("spans", [])]}
                    for scope in resource.get("scopeSpans", [])
                ]
            }
            for resource in data.get("resourceSpans", [])
        ]
    }


def _decode(content_type: str, body: bytes) -> ExportTraceServiceRequest | None:
    request = ExportTraceServiceRequest()
    try:
        if content_type.startswith("application/json"):
            ParseDict(_otlp_json(body), request)
        else:
            request.ParseFromString(body)
    except (ParseError, DecodeError, ValueError, TypeError, AttributeError):
        return None
    return request


def create_app(state: FakeLangSmith) -> Starlette:
    """The fake route over `state`."""

    async def otel_traces(request: Request) -> Response:
        if request.headers.get("x-api-key") != state.api_key:
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        decoded = _decode(request.headers.get("content-type", ""), await request.body())
        if decoded is None:
            return JSONResponse({"detail": "the body is not an OTLP trace export"}, status_code=400)
        state.record(decoded)
        return Response(b"", status_code=200, media_type="application/x-protobuf")

    async def read_trace(request: Request) -> Response:
        spans = state.spans(request.path_params["trace_id"].lower())
        if not spans:
            return JSONResponse({"detail": "Trace not found"}, status_code=404)
        return JSONResponse({"spans": [vars(s) for s in spans]})

    async def health(request: Request) -> Response:
        return JSONResponse({"status": "ok"})

    return Starlette(
        routes=[
            Route("/otel/v1/traces", otel_traces, methods=["POST"]),
            Route("/fake/traces/{trace_id}", read_trace, methods=["GET"]),
            Route("/health", health, methods=["GET"]),
            *dataset_routes(state, state.api_key),
        ]
    )
