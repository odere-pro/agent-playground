"""The fake LangSmith OTLP route, in process over an ASGI transport: no socket, no key."""

from __future__ import annotations

import httpx
from fake_langsmith import FakeLangSmith, create_app
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
from opentelemetry.proto.trace.v1.trace_pb2 import ResourceSpans, ScopeSpans, Span

KEY = "lsv2-test-key"  # pragma: allowlist secret
TRACE = bytes.fromhex("0af7651916cd43dd8448eb211c80319c")
RUN, STEP = bytes.fromhex("b7ad6b7169203331"), bytes.fromhex("00f067aa0ba902b7")
PROTOBUF = "application/x-protobuf"


def client(state: FakeLangSmith, key: str | None = KEY) -> httpx.AsyncClient:
    headers = {"x-api-key": key} if key is not None else {}
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(state)),
        base_url="http://langsmith",
        headers=headers,
    )


def export() -> ExportTraceServiceRequest:
    run = Span(trace_id=TRACE, span_id=RUN, name="chassis.run")
    run.attributes.append(KeyValue(key="request_id", value=AnyValue(string_value="req-1")))
    step = Span(trace_id=TRACE, span_id=STEP, parent_span_id=RUN, name="graph.step")
    request = ExportTraceServiceRequest()
    request.resource_spans.append(ResourceSpans(scope_spans=[ScopeSpans(spans=[run, step])]))
    return request


async def test_an_otlp_protobuf_trace_is_recorded_and_read_back_by_trace_id() -> None:
    state = FakeLangSmith(api_key=KEY)
    body = export().SerializeToString()
    async with client(state) as http:
        answer = await http.post(
            "/otel/v1/traces", content=body, headers={"content-type": PROTOBUF}
        )
        read = await http.get(f"/fake/traces/{TRACE.hex()}")
    assert answer.status_code == 200
    assert state.trace_ids() == [TRACE.hex()]
    by_name = {s.name: s for s in state.spans(TRACE.hex())}
    assert by_name["graph.step"].parent_span_id == RUN.hex()
    assert by_name["chassis.run"].parent_span_id == ""
    assert by_name["chassis.run"].attributes == {"request_id": "req-1"}
    assert read.status_code == 200
    names = {s["name"]: s["parent_span_id"] for s in read.json()["spans"]}
    assert names == {"chassis.run": "", "graph.step": RUN.hex()}


async def test_a_missing_or_wrong_key_is_401_and_stores_nothing() -> None:
    state = FakeLangSmith(api_key=KEY)
    for key in (None, "wrong"):
        async with client(state, key) as http:
            answer = await http.post(
                "/otel/v1/traces",
                content=export().SerializeToString(),
                headers={"content-type": PROTOBUF},
            )
        assert answer.status_code == 401
    assert state.trace_ids() == []


async def test_a_body_that_is_not_otlp_is_400() -> None:
    state = FakeLangSmith(api_key=KEY)
    async with client(state) as http:
        answer = await http.post(
            "/otel/v1/traces", content=b"{not json", headers={"content-type": "application/json"}
        )
    assert answer.status_code == 400
    assert state.trace_ids() == []


async def test_an_unknown_trace_reads_back_as_404() -> None:
    async with client(FakeLangSmith(api_key=KEY)) as http:
        answer = await http.get(f"/fake/traces/{TRACE.hex()}")
    assert answer.status_code == 404


def otlp_json_body(attributes: list[dict[str, object]]) -> dict[str, object]:
    """An OTLP/JSON body as the spec writes it: trace and span ids are lowercase hex."""
    span: dict[str, object] = {
        "traceId": TRACE.hex(),
        "spanId": STEP.hex(),
        "parentSpanId": RUN.hex(),
    }
    span |= {"name": "graph.step", "attributes": attributes}
    return {"resourceSpans": [{"scopeSpans": [{"spans": [span]}]}]}


async def test_otlp_json_hex_ids_are_recorded_as_the_same_hex() -> None:
    state = FakeLangSmith(api_key=KEY)
    async with client(state) as http:
        attributes: list[dict[str, object]] = [
            {"key": "request_id", "value": {"stringValue": "req-1"}}
        ]
        answer = await http.post("/otel/v1/traces", json=otlp_json_body(attributes))
    assert answer.status_code == 200
    assert state.trace_ids() == [TRACE.hex()]
    (span,) = state.spans(TRACE.hex())
    assert (span.span_id, span.parent_span_id) == (STEP.hex(), RUN.hex())
    assert span.attributes == {"request_id": "req-1"}


async def test_an_attribute_with_an_empty_value_is_recorded_as_none() -> None:
    state = FakeLangSmith(api_key=KEY)
    attributes: list[dict[str, object]] = [{"key": "empty", "value": {}}]
    async with client(state) as http:
        answer = await http.post("/otel/v1/traces", json=otlp_json_body(attributes))
    assert answer.status_code == 200
    (span,) = state.spans(TRACE.hex())
    assert span.attributes == {"empty": None}


async def test_scalar_attributes_keep_their_type() -> None:
    state = FakeLangSmith(api_key=KEY)
    attributes: list[dict[str, object]] = [
        {"key": "tokens", "value": {"intValue": "5"}},
        {"key": "ok", "value": {"boolValue": True}},
        {"key": "score", "value": {"doubleValue": 0.5}},
    ]
    async with client(state) as http:
        answer = await http.post("/otel/v1/traces", json=otlp_json_body(attributes))
    assert answer.status_code == 200
    (span,) = state.spans(TRACE.hex())
    assert span.attributes == {"tokens": 5, "ok": True, "score": 0.5}


async def test_an_uppercase_trace_id_reads_back_the_same_trace() -> None:
    state = FakeLangSmith(api_key=KEY)
    async with client(state) as http:
        await http.post(
            "/otel/v1/traces",
            content=export().SerializeToString(),
            headers={"content-type": PROTOBUF},
        )
        read = await http.get(f"/fake/traces/{TRACE.hex().upper()}")
    assert read.status_code == 200
    assert len(read.json()["spans"]) == 2
