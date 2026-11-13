"""A fake of the LangSmith OTLP trace and feedback routes, as one Starlette app (CH-10, CH-11).

LangSmith takes OTLP over HTTP at `POST /otel/v1/traces`, with the key in the `x-api-key` header
(the LangSmith OpenTelemetry guide). The Collector's `otlphttp/langsmith` exporter sends there.

- `POST /otel/v1/traces`: an `ExportTraceServiceRequest`, protobuf or JSON. The spans are decoded
  and kept by trace id; the answer is an empty 200. A body that is not OTLP is 400.
- `GET /fake/traces/{trace_id}`: the recorded spans of one trace, or 404. Not a LangSmith route:
  a stack test reads the fake back through it.
- `POST /api/v1/feedback`: one feedback record, upserted by its `id` (130 CH-11). suggested: the
  path and fields follow `LangSmithFeedback`; the answer echoes the record. A body that is not a
  JSON object with an `id` and a UUID `run_id` is 422. `FakeLangSmith.unavailable` makes it 503.
- `GET /fake/feedback/{id}`: one feedback record, or 404. `GET /fake/runs/{run_id}/feedback`: the
  records on one run, the run id as a UUID or 32 hex. Not LangSmith routes: read-backs for tests.
- `GET /health`: no auth.

A missing or wrong `x-api-key` on a POST is 401 and stores nothing.
"""

from __future__ import annotations

import base64
import json
import uuid
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
class FakeLangSmith:
    """The fake's state: the one accepted API key, the spans in arrival order, and the feedback
    records by id. `unavailable` makes the feedback route answer 503."""

    api_key: str
    recorded: list[RecordedSpan] = field(default_factory=list)
    feedback: dict[str, dict[str, Any]] = field(default_factory=dict)
    unavailable: bool = False

    def feedback_on(self, run_id: str) -> list[dict[str, Any]]:
        """The feedback records on one run; `run_id` is a UUID in any form uuid.UUID reads."""
        run = _uuid(run_id)
        return [r for r in self.feedback.values() if run is not None and _uuid(r["run_id"]) == run]

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


def _uuid(value: object) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def _feedback_record(body: bytes) -> dict[str, Any] | None:
    """The feedback record in `body`, or None when it has no `id` or no UUID `run_id`."""
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if not isinstance(data, dict) or not data.get("id") or _uuid(data.get("run_id")) is None:
        return None
    return data


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

    async def post_feedback(request: Request) -> Response:
        if request.headers.get("x-api-key") != state.api_key:
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        if state.unavailable:
            return JSONResponse({"detail": "Service unavailable"}, status_code=503)
        record = _feedback_record(await request.body())
        if record is None:
            return JSONResponse({"detail": "a feedback needs an id and a run_id"}, status_code=422)
        state.feedback[str(record["id"])] = record
        return JSONResponse(record)

    async def read_feedback(request: Request) -> Response:
        record = state.feedback.get(request.path_params["feedback_id"])
        if record is None:
            return JSONResponse({"detail": "Feedback not found"}, status_code=404)
        return JSONResponse(record)

    async def read_run_feedback(request: Request) -> Response:
        run_id = request.path_params["run_id"]
        if _uuid(run_id) is None:
            return JSONResponse({"detail": "Run not found"}, status_code=404)
        return JSONResponse({"feedback": state.feedback_on(run_id)})

    async def health(request: Request) -> Response:
        return JSONResponse({"status": "ok"})

    return Starlette(
        routes=[
            Route("/otel/v1/traces", otel_traces, methods=["POST"]),
            Route("/fake/traces/{trace_id}", read_trace, methods=["GET"]),
            Route("/api/v1/feedback", post_feedback, methods=["POST"]),
            Route("/fake/feedback/{feedback_id}", read_feedback, methods=["GET"]),
            Route("/fake/runs/{run_id}/feedback", read_run_feedback, methods=["GET"]),
            Route("/health", health, methods=["GET"]),
        ]
    )
