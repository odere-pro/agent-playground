"""`chassis.server.interfaces`: the seam every inbound interface builds on (PoC-3 open note,
sections 1, 3, 6, 11, and "Requirements on the pipeline seam").

The id rule and the re-mint, the hold rule, `serve` over a stub adapter (a toy wire format, so the
flow is checked without the OpenAI or Anthropic mapping), the validation-error seam, the native
route's new headers and telemetry label, `spec.interfaces`, and the OpenAPI of the routes that
exist now.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
from collections.abc import AsyncGenerator, AsyncIterator, Mapping, Sequence
from typing import Any

import httpx
import pytest
from chassis.core.envelope import Context, Request, Response, TaskInput
from chassis.core.events import Delta, End, Error, Event, Metrics, Start
from chassis.core.handle import echo
from chassis.core.inbound import (
    Ids,
    Interface,
    Refused,
    Reply,
    ReplyMeta,
    Served,
    StreamReply,
    status_for,
)
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.ports.bundle import PortBundle
from chassis.server import ChassisConfig, create_app
from chassis.server.correlation import TraceIdInUse
from chassis.server.interfaces import mount_interfaces
from chassis.server.interfaces.errors import INTERFACE_KEY, register_validation_format
from chassis.server.interfaces.hold import hold
from chassis.server.interfaces.ids import ResolvedIds, may_remint, open_run, resolve_ids
from chassis.server.interfaces.serve import serve
from chassis.server.pipeline import RunPipeline
from fastapi import APIRouter, FastAPI
from fastapi import Request as HTTPRequest
from pydantic import BaseModel, ValidationError

CONFIG: dict[str, Any] = {
    "version": "cfg-1",
    "profile": "fake",
    "agent": {"name": "echo", "version": "0.0.1"},
    "spec": {
        "engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"},
        "model": {"route": "fake-route"},
        "prompt": {"version": "p1"},
    },
}
TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
TRACEPARENT = f"00-{TRACE}-00f067aa0ba902b7-01"
HEX = frozenset("0123456789abcdef")


def _app(engine: FakeEngine | None = None, config: dict[str, Any] | None = None) -> FastAPI:
    ports = PortBundle(
        model=ScriptedModel(),
        engine=engine or FakeEngine(handle=echo),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
    )
    return create_app(ChassisConfig.model_validate(config or CONFIG), ports)


def _telemetry(app: FastAPI) -> InMemoryTelemetry:
    telemetry = app.state.ports.telemetry
    assert isinstance(telemetry, InMemoryTelemetry)
    return telemetry


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://chassis")


def _minted(value: str) -> bool:
    return len(value) == 32 and set(value) <= HEX


# --- ids (section 6) ---------------------------------------------------------------------------


def test_ids_from_the_body_win_for_trace_and_request() -> None:
    headers = {"traceparent": TRACEPARENT, "idempotency-key": "hdr-key"}
    got = resolve_ids(headers, request_id="r", trace_id="t", idempotency_key="body-key")
    assert got == ResolvedIds(Ids("r", "t", "hdr-key"), trace_from="body", key_from="header")


def test_ids_from_headers_when_the_body_leaves_them_unset() -> None:
    got = resolve_ids({"traceparent": TRACEPARENT, "idempotency-key": "hdr-key"})
    assert got.trace_from == "traceparent"
    assert (got.ids.trace_id, got.ids.idempotency_key) == (TRACE, "hdr-key")
    assert _minted(got.ids.request_id), "request_id is never taken from a header"


@pytest.mark.parametrize("traceparent", [None, "", "garbage", f"ff-{TRACE}-00f067aa0ba902b7-01"])
def test_ids_are_minted_when_nothing_valid_is_given(traceparent: str | None) -> None:
    headers = {} if traceparent is None else {"traceparent": traceparent}
    got = resolve_ids(headers, request_id="", trace_id="", idempotency_key="")
    assert got.trace_from == "minted"
    assert all(_minted(v) for v in (got.ids.request_id, got.ids.trace_id, got.ids.idempotency_key))
    assert len({got.ids.request_id, got.ids.trace_id, got.ids.idempotency_key}) == 3


def test_body_idempotency_key_is_used_without_the_header() -> None:
    assert resolve_ids({}, idempotency_key="body-key").ids.idempotency_key == "body-key"


@pytest.mark.parametrize(
    ("trace_from", "interface", "expected"),
    [
        ("body", "native", False),
        ("traceparent", "native", True),
        ("minted", "native", True),
        ("body", "mcp", True),
        ("body", "openai", True),
        ("traceparent", "anthropic", True),
    ],
)
def test_only_a_native_body_trace_id_keeps_its_409(
    trace_from: Any, interface: Interface, expected: bool
) -> None:
    ids = ResolvedIds(Ids("r", "t", "i"), trace_from=trace_from)
    assert may_remint(ids, interface) is expected


def _request(pipeline: RunPipeline, request_id: str, trace_id: str = TRACE) -> Request:
    return pipeline.to_request(input=TaskInput(text="x"), request_id=request_id, trace_id=trace_id)


async def test_open_run_remints_once_and_counts_it() -> None:
    app = _app()
    async with app.router.lifespan_context(app):
        pipeline: RunPipeline = app.state.pipeline
        first = open_run(pipeline, _request(pipeline, "first"), interface="openai", remint=True)
        second = open_run(pipeline, _request(pipeline, "second"), interface="openai", remint=True)
        assert first.request.trace_id == TRACE
        assert second.request.trace_id != TRACE and _minted(second.request.trace_id)
        assert second.ctx.trace_id == second.request.trace_id
        assert second.request.request_id == "second", "only the trace id is re-minted"
        assert app.state.runs.lookup(second.request.trace_id) is second.record
        with pytest.raises(TraceIdInUse):
            open_run(pipeline, _request(pipeline, "third"), interface="native", remint=False)
        first.close()
        second.close()
    telemetry = _telemetry(app)
    assert telemetry.counter_value("chassis.trace_id_reminted", interface="openai") == 1
    assert telemetry.counter_value("chassis.requests_refused", reason="trace_id_in_use") == 1, (
        "a re-minted run is not a refusal; only the native one counts"
    )


# --- the hold rule (section 3) -----------------------------------------------------------------


class _Source:
    """An async generator of fixed events that records how far it was read and if it closed."""

    def __init__(self, events: Sequence[Event]) -> None:
        self.events = list(events)
        self.read = 0
        self.closed = False

    async def gen(self) -> AsyncGenerator[Event]:
        try:
            for event in self.events:
                self.read += 1
                yield event
        finally:
            self.closed = True


async def test_hold_peeks_to_the_first_delta_and_continues_the_same_iterator() -> None:
    events: list[Event] = [
        Start(request_id="r"),
        Metrics(input_tokens=1),
        Delta(text="a"),
        Delta(text="b"),
        End(),
    ]
    source = _Source(events)
    held = await hold(source.gen())
    assert source.read == 3, "nothing past the first delta is read"
    assert held.head == tuple(events[:3]) and held.decider == events[2] and held.error is None
    assert [e async for e in held.events()] == events
    assert source.closed
    with pytest.raises(RuntimeError, match="once"):
        held.events()


@pytest.mark.parametrize(
    "events",
    [
        [Error(code="not_ready", message="m")],
        [
            Start(request_id="r"),
            Metrics(),
            Error(code="engine_error", message="m"),
            Delta(text="x"),
        ],
    ],
)
async def test_hold_stops_at_an_error_before_any_text(events: list[Event]) -> None:
    source = _Source(events)
    held = await hold(source.gen())
    assert isinstance(held.error, Error) and held.decider is held.error
    assert source.read == len(events[:-1]) if len(events) > 1 else source.read == 1
    await held.aclose()
    await held.aclose()
    assert source.closed


async def test_hold_on_end_and_on_a_stream_without_a_terminal_event() -> None:
    ended = await hold(_Source([Start(request_id="r"), End()]).gen())
    assert isinstance(ended.decider, End) and ended.error is None
    source = _Source([Start(request_id="r")])
    bare = await hold(source.gen())
    assert bare.decider is None and bare.error is None and bare.head == (Start(request_id="r"),)
    assert [e async for e in bare.events()] == [Start(request_id="r")]


async def test_held_events_enter_and_leave_the_run_in_one_context() -> None:
    """The peek runs in the handler's task and the rest in the response's task. The run's span
    (a context variable) must still be entered and left in one context.
    """
    var: contextvars.ContextVar[str | None] = contextvars.ContextVar("span", default=None)
    left: list[str] = []

    async def run() -> AsyncGenerator[Event]:
        token = var.set("chassis.run")
        try:
            yield Start(request_id="r")
            yield Delta(text="a")
            yield End()
        finally:
            var.reset(token)  # ValueError when left from another context
            left.append("ok")

    held = await hold(run())

    async def consume() -> list[Event]:
        return [e async for e in held.events()]

    events = await asyncio.create_task(consume())
    assert [e.type for e in events] == ["start", "delta", "end"] and left == ["ok"]


async def test_aclose_ends_a_held_run_that_was_never_streamed() -> None:
    source = _Source([Start(request_id="r"), Delta(text="a"), End()])
    held = await hold(source.gen())
    await held.aclose()
    assert source.closed and source.read == 2


# --- serve over a stub adapter -----------------------------------------------------------------


class PlainBody(BaseModel):
    text: str
    stream: bool = False
    refuse: bool = False


class PlainInbound:
    """A toy wire format: text in; `{"answer"}` out; frames `t:<text>`; errors as
    `{"err": code}` with `x-should-retry`. Stands in for OpenAI and Anthropic.
    """

    interface: Interface = "openai"

    def to_request(
        self, body: PlainBody, headers: Mapping[str, str], *, ids: Ids, served: Served
    ) -> Request:
        if body.refuse:
            raise Refused(400, {"x-should-retry": "false"}, {"err": "refused"})
        return Request(
            request_id=ids.request_id,
            trace_id=ids.trace_id,
            idempotency_key=ids.idempotency_key,
            agent=served.agent,
            agent_version=served.agent_version,
            input=TaskInput(text=body.text),
            stream=body.stream,
        )

    def complete(self, response: Response, meta: ReplyMeta) -> Reply:
        headers = {"x-request-id": meta.ids.request_id, "x-trace-id": meta.ids.trace_id}
        return Reply(200, headers, {"answer": response.output.get("text"), "at": meta.created})

    def stream(self, events: AsyncIterator[Event], meta: ReplyMeta) -> StreamReply:
        async def frames() -> AsyncIterator[str]:
            seen: list[Event] = []
            async for event in events:
                seen.append(event)
                if isinstance(event, Delta):
                    yield f"t:{event.text}\n"
                elif isinstance(event, Error):
                    yield f"e:{event.code}\n"
                    return
            response = await meta.collect(seen)
            yield f"u:{response.metrics['input_tokens']}\n"

        return StreamReply(frames(), {"x-request-id": meta.ids.request_id})

    def error(self, code: str, message: str, retryable: bool, meta: ReplyMeta) -> Reply:
        headers = {"x-should-retry": "true" if retryable else "false"}
        body = {"err": code, "message": message, "request_id": meta.ids.request_id}
        return Reply(status_for(code, retryable), headers, body)


def _plain_app(engine: FakeEngine | None = None) -> FastAPI:
    app = _app(engine)
    pipeline: RunPipeline = app.state.pipeline
    router = APIRouter()
    adapter = PlainInbound()

    @router.post("/plain", openapi_extra={INTERFACE_KEY: "openai"})
    async def plain(body: PlainBody, http: HTTPRequest) -> Any:
        return await serve(
            adapter,
            body,
            http.headers,
            pipeline=pipeline,
            ids=resolve_ids(http.headers),
            errors_as_http=True,
            ignored=["temperature"],
        )

    app.include_router(router)
    register_validation_format(app, adapter)
    return app


async def _say(input: TaskInput, ctx: Context) -> AsyncIterator[Event]:
    """Scripted by the input text: `fail-first`, `fail-late`, `retryable`, or an echo."""
    yield Start(request_id=ctx.request_id)
    yield Metrics(input_tokens=5, output_tokens=1)
    text = input.text or ""
    if text == "fail-first":
        yield Error(code="engine_error", message="boom")
        return
    if text == "retryable":
        yield Error(code="a2a.transport", message="gone", retryable=True)
        return
    yield Delta(text="one")
    if text == "fail-late":
        yield Error(code="a2a.timeout", message="slow", retryable=True)
        return
    yield End(status="ok")


async def test_serve_complete_and_stream_through_the_adapter() -> None:
    app = _plain_app(FakeEngine(handle=_say))
    async with _client(app) as client, app.router.lifespan_context(app):
        complete = await client.post("/plain", json={"text": "hi"})
        streamed = await client.post("/plain", json={"text": "hi", "stream": True})
        assert len(app.state.runs) == 0
    assert complete.status_code == 200 and complete.json()["answer"] == "one"
    assert complete.json()["at"] > 0
    assert complete.headers["x-trace-id"] == complete.headers["x-trace-id"]
    assert _minted(complete.headers["x-request-id"])
    assert streamed.status_code == 200 and streamed.headers["content-type"].startswith(
        "text/event-stream"
    )
    assert _minted(streamed.headers["x-request-id"])
    assert streamed.text == "t:one\nu:5\n"
    telemetry = _telemetry(app)
    assert telemetry.counter_value("chassis.requests", agent="echo", interface="openai") == 2
    runs = [s for s in telemetry.spans if s.name == "chassis.run"]
    assert [s.attributes["interface"] for s in runs] == ["openai", "openai"]
    assert all(s.ended for s in runs)
    assert (
        telemetry.counter_value("chassis.inbound_ignored", interface="openai", param="temperature")
        == 2
    )


@pytest.mark.parametrize(
    ("text", "status", "should_retry"),
    [("fail-first", 500, "false"), ("retryable", 503, "true")],
)
@pytest.mark.parametrize("stream", [False, True])
async def test_serve_answers_an_error_before_text_as_http(
    text: str, status: int, should_retry: str, stream: bool
) -> None:
    app = _plain_app(FakeEngine(handle=_say))
    async with _client(app) as client, app.router.lifespan_context(app):
        res = await client.post("/plain", json={"text": text, "stream": stream})
        assert len(app.state.runs) == 0
    assert res.status_code == status, res.text
    assert res.headers["content-type"].startswith("application/json")
    assert res.headers["x-should-retry"] == should_retry
    assert res.json()["request_id"] == res.json()["request_id"] and res.json()["err"]
    spans = [s for s in _telemetry(app).spans if s.name == "chassis.run"]
    assert len(spans) == 1 and spans[0].ended


async def test_serve_error_after_text_is_a_frame_in_stream_and_http_in_complete() -> None:
    app = _plain_app(FakeEngine(handle=_say))
    async with _client(app) as client, app.router.lifespan_context(app):
        streamed = await client.post("/plain", json={"text": "fail-late", "stream": True})
        complete = await client.post("/plain", json={"text": "fail-late"})
    assert streamed.status_code == 200 and streamed.text == "t:one\ne:a2a.timeout\n"
    assert complete.status_code == 504 and complete.json()["err"] == "a2a.timeout"


async def test_serve_refused_not_ready_and_validation_use_the_adapter() -> None:
    engine = FakeEngine(handle=_say)
    app = _plain_app(engine)
    async with _client(app) as client:
        not_ready = await client.post("/plain", json={"text": "hi"})
        async with app.router.lifespan_context(app):
            refused = await client.post("/plain", json={"text": "hi", "refuse": True})
            invalid = await client.post("/plain", json={"stream": "maybe"})
            native = await client.post("/v1/run", json={"bogus": 1})
    assert not_ready.status_code == 503 and not_ready.json()["err"] == "not_ready"
    assert not_ready.headers["x-should-retry"] == "true"
    assert refused.status_code == 400 and refused.json() == {"err": "refused"}
    assert invalid.status_code == 400 and invalid.json()["err"] == "invalid_body"
    assert "text" in invalid.json()["message"] and "stream" in invalid.json()["message"]
    assert native.status_code == 422, "native keeps FastAPI's 422"
    assert engine.runs == 0
    assert (
        _telemetry(app).counter_value(
            "chassis.inbound_ignored", interface="openai", param="temperature"
        )
        == 0
    )


async def test_serve_remints_a_header_trace_id_in_use_for_a_non_native_interface() -> None:
    in_flight = asyncio.Event()
    release = asyncio.Event()

    async def wait(input: TaskInput, ctx: Context) -> AsyncIterator[Event]:
        if input.text == "first":
            in_flight.set()
            await asyncio.wait_for(release.wait(), timeout=5)
        yield Delta(text=ctx.trace_id)
        yield End()

    app = _plain_app(FakeEngine(handle=wait))
    headers = {"traceparent": TRACEPARENT}
    async with _client(app) as client, app.router.lifespan_context(app):
        first = asyncio.create_task(client.post("/plain", json={"text": "first"}, headers=headers))
        await asyncio.wait_for(in_flight.wait(), timeout=5)
        second = await client.post("/plain", json={"text": "second"}, headers=headers)
        release.set()
        done = await first
    assert done.json()["answer"] == TRACE == done.headers["x-trace-id"]
    assert second.status_code == 200
    assert second.json()["answer"] == second.headers["x-trace-id"] != TRACE
    assert _telemetry(app).counter_value("chassis.trace_id_reminted", interface="openai") == 1


# --- the native route: headers and telemetry ---------------------------------------------------


async def test_native_run_reads_traceparent_and_idempotency_key_when_the_body_has_none() -> None:
    app = _app()
    headers = {"traceparent": TRACEPARENT, "Idempotency-Key": "key-1"}
    body = {"input": {"text": "x"}}
    async with _client(app) as client, app.router.lifespan_context(app):
        from_headers = (await client.post("/v1/run", json=body, headers=headers)).json()
        from_body = (
            await client.post(
                "/v1/run",
                json={**body, "trace_id": "t-body", "idempotency_key": "k-body"},
                # a key of its own: the same key and input would replay the first run (PoC-4)
                headers={**headers, "Idempotency-Key": "key-2"},
            )
        ).json()
        bad = (await client.post("/v1/run", json=body, headers={"traceparent": "nope"})).json()
    assert (from_headers["trace_id"], from_headers["idempotency_key"]) == (TRACE, "key-1")
    assert (from_body["trace_id"], from_body["idempotency_key"]) == ("t-body", "key-2"), (
        "the body's trace_id wins; the Idempotency-Key header wins (section 6)"
    )
    assert _minted(bad["trace_id"])


async def test_native_run_remints_a_traceparent_in_use_but_keeps_409_for_a_body_trace_id() -> None:
    in_flight = asyncio.Event()
    release = asyncio.Event()

    async def wait(input: TaskInput, ctx: Context) -> AsyncIterator[Event]:
        if input.text == "first":
            in_flight.set()
            await asyncio.wait_for(release.wait(), timeout=5)
        yield End(output={"trace": ctx.trace_id})

    app = _app(FakeEngine(handle=wait))
    async with _client(app) as client, app.router.lifespan_context(app):
        first = asyncio.create_task(
            client.post("/v1/run", json={"trace_id": TRACE, "input": {"text": "first"}})
        )
        await asyncio.wait_for(in_flight.wait(), timeout=5)
        header = await client.post(
            "/v1/run", json={"input": {"text": "h"}}, headers={"traceparent": TRACEPARENT}
        )
        body = await client.post("/v1/run", json={"trace_id": TRACE, "input": {"text": "b"}})
        release.set()
        await first
    assert header.status_code == 200 and header.json()["trace_id"] != TRACE
    assert header.json()["output"]["trace"] == header.json()["trace_id"]
    assert body.status_code == 409 and body.json()["detail"]["code"] == "trace_id_in_use"
    telemetry = _telemetry(app)
    assert telemetry.counter_value("chassis.trace_id_reminted", interface="native") == 1


async def test_native_run_labels_telemetry_native() -> None:
    app = _app()
    async with _client(app) as client, app.router.lifespan_context(app):
        await client.post("/v1/run", json={"input": {"text": "x"}})
        await client.post("/v1/run", json={"input": {"text": "x"}, "stream": True})
    telemetry = _telemetry(app)
    assert telemetry.counter_value("chassis.requests", agent="echo", interface="native") == 2
    runs = [s for s in telemetry.spans if s.name == "chassis.run"]
    assert [s.attributes["interface"] for s in runs] == ["native", "native"]


# --- spec.interfaces and mount_interfaces ------------------------------------------------------


def test_spec_interfaces_default_on_and_refuse_unknown_keys() -> None:
    config = ChassisConfig.model_validate(CONFIG)
    assert config.spec.interfaces.model_dump() == {"openai": True, "anthropic": True, "mcp": True}
    off = {**CONFIG, "spec": {**CONFIG["spec"], "interfaces": {"openai": False}}}
    assert ChassisConfig.model_validate(off).spec.interfaces.openai is False
    bad = {**CONFIG, "spec": {**CONFIG["spec"], "interfaces": {"native": False}}}
    with pytest.raises(ValidationError, match="native"):
        ChassisConfig.model_validate(bad)


def _paths(app: FastAPI) -> set[str]:
    """FastAPI 0.141 keeps an included router as one lazy entry, so read the spec's paths."""
    return set(app.openapi()["paths"])


def test_mount_interfaces_mounts_native_whatever_the_flags() -> None:
    for flags in ({}, {"openai": False, "anthropic": False, "mcp": False}):
        config = ChassisConfig.model_validate(
            {**CONFIG, "spec": {**CONFIG["spec"], "interfaces": flags}}
        )
        app = FastAPI()
        app.state.config = config
        app.state.pipeline = RunPipeline(app.state)
        mount_interfaces(app, config)
        assert "/v1/run" in _paths(app)
    assert {"/v1/run", "/health", "/ready"} <= _paths(_app())


# --- OpenAPI (section 11) ----------------------------------------------------------------------


def test_openapi_names_every_operation_and_declares_run_responses() -> None:
    spec = _app().openapi()
    assert spec["openapi"].startswith("3.1")
    ops = {
        (path, method): op["operationId"]
        for path, item in spec["paths"].items()
        for method, op in item.items()
    }
    assert {
        ("/v1/run", "post"): "run",
        ("/health", "get"): "health",
        ("/ready", "get"): "ready",
    }.items() <= ops.items()
    # Every operation has an explicit id: FastAPI's generated ones end in the method.
    assert not [o for (_, method), o in ops.items() if o.endswith(f"_{method}")], ops
    run = spec["paths"]["/v1/run"]["post"]
    assert run[INTERFACE_KEY] == "native"
    ok = run["responses"]["200"]["content"]
    assert ok["application/json"]["schema"] == {"$ref": "#/components/schemas/Response"}
    assert "text/event-stream" in ok
    schemas = spec["components"]["schemas"]

    def schema(status: str) -> dict[str, Any]:
        ref = run["responses"][status]["content"]["application/json"]["schema"]["$ref"]
        found: dict[str, Any] = schemas[ref.rsplit("/", 1)[-1]]
        return found

    assert schema("400")["properties"]["detail"]["type"] == "string"
    assert schema("503")["properties"]["detail"]["type"] == "string"
    detail = schema("409")["properties"]["detail"]["$ref"].rsplit("/", 1)[-1]
    assert set(schemas[detail]["properties"]) == {"code", "message"}
    assert "422" in run["responses"], "422 stays FastAPI's"
    assert spec["paths"]["/health"]["get"]["responses"]["200"]["content"]
    ready = spec["paths"]["/ready"]["get"]["responses"]
    assert {"200", "503"} <= set(ready)
    assert json.dumps(spec)  # serializable


async def test_health_and_ready_bodies_are_unchanged() -> None:
    app = _app()
    async with _client(app) as client:
        assert (await client.get("/health")).json() == {"status": "ok"}
        not_ready = await client.get("/ready")
        async with app.router.lifespan_context(app):
            ready = await client.get("/ready")
    # PoC-4 (contract v3, additive): a 503 body names its `reason`.
    assert (not_ready.status_code, not_ready.json()) == (
        503,
        {"status": "not ready", "reason": "starting"},
    )
    assert (ready.status_code, ready.json()) == (200, {"status": "ready"})
