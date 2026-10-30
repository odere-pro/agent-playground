"""The run pipeline every inbound adapter shares: from a canonical `Request` to events and a
`Response`, the same for the native `/v1/run` and for every other wire format.

`RunPipeline` is built once per public app from its `state` and stored at `app.state.pipeline`.
It reads `state.config`, `state.ports`, `state.runs`, and `state.ready` when it needs them, so it
sees the ports the lifespan builds. An adapter maps its body to a `Request` (`to_request` fills
the ids and the agent the body leaves out), then `open` holds a `RunRecord` for the run's trace
id. A trace id another in-flight run holds raises `TraceIdInUse` before the engine runs; `open`
counts the refusal (`chassis.requests_refused`, `reason=trace_id_in_use`) and logs a warning, and
the adapter answers in its own format.

PoC-7 guard stages (`chassis.server.guard`): `input` runs before the engine, and a block is the
run's one `error` event (the engine never runs); `output` runs on a complete call's events after
the engine ends and may replace `Run.seen` (masked, or blocked with a closing `error`). PoC-8: a
streamed run's events pass `stream_guard.guard_stream` (a secret closes the stream, URLs to hosts
not on `spec.guardrails.output_url_allow` are masked), and a complete answer's URLs are masked
after the `output` stage (`output_filter.mask_answer`), so the cache and result events hold the
masked answer.

`Run.events()` runs the engine once, inside one `chassis.run` span with one `chassis.requests`
count, both labeled with the `interface` the run was opened for (`open(request, interface=...)`,
default `native`); an engine exception becomes one `engine_error` event, never a 500. Every
`error` event's code and message go on the span (`error.code`, `error.message`) and in an `error`
log. The record
is closed when the engine stream ends (a trailing frame is not part of the run) and again in
`finally`. `Run.response()` collects the seen events afterwards; `Run.complete()` does both for a
non-streaming call. A streaming adapter returns a `RunStream`, which closes the run when the
response ends, even when the body was never iterated (a client that left before the first frame).

`Run.seen` (the events so far) and `Run.done` (the engine stream ended) are what the idempotency
layer caches and what a result publisher reads. `RunPipeline.on_finished` is a list of async
callbacks; `serve` awaits `RunPipeline.finished(run)` once after each run ends, whatever happened,
which calls each in order. A callback that raises is counted
(`chassis.on_finished_failed`, suggested) and logged, never sent to the client.

**Trace context (PoC-7, 014 H-7).** The `chassis.run` span carries `trace_id` (the 32-hex W3C id
of the request's trace id), so a telemetry adapter roots the run in that trace. `open(...,
observed=...)` adds what the caller's HTTP request says about the run (`Observed`, built by
`observed_for` from the headers): `session.id` from the header `spec.observability.session_header`
names, `user.id` when the caller gives one (the caller's principal; never a header), and the
caller's `traceparent` when it names the run's own trace (a re-minted trace id drops it), so the
run span continues the caller's span. They are span attributes only: no field of `Context` or
`Request` changes (contract v5, C.2). The OTel adapter turns `user.id` and `session.id` into
baggage, which the A2A connector sends to the workload.

`RunPipeline.idempotency` is built lazily from `state.ports.state` and
`state.config.spec.idempotency` (PoC-4 plan, section 4), so `app.py` does not change for it. It is
rebuilt when the ports change, and it follows a reloaded `spec.idempotency`.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import aclosing
from dataclasses import dataclass
from typing import Any

from starlette.datastructures import State
from starlette.responses import ContentStream, StreamingResponse
from starlette.types import Receive, Scope, Send

from chassis import CHASSIS_VERSION
from chassis.core.collector import collect
from chassis.core.envelope import Budget, Context, Request, Response, TaskInput, Versions
from chassis.core.events import Error, Event
from chassis.core.inbound import Interface, Served
from chassis.core.trace import parse_traceparent, trace_id_hex
from chassis.ports.bundle import PortBundle
from chassis.redaction import redact
from chassis.server import guard
from chassis.server.config import ChassisConfig, GuardrailsSpec
from chassis.server.correlation import RunRecord, RunRegistry, TraceIdInUse
from chassis.server.idempotency import Idempotency
from chassis.server.output_filter import URL_MASKED, mask_answer
from chassis.server.stream_guard import guard_stream, stream_guard_on

__all__ = [
    "Observed",
    "OnFinished",
    "Run",
    "RunPipeline",
    "RunStream",
    "TraceIdInUse",
    "context_for",
    "versions_for",
]

ON_FINISHED_FAILED = "chassis.on_finished_failed"
"""suggested: the counter of an `on_finished` callback that raised."""
SESSION_ID_MAX = 128
"""suggested: the longest session id kept; a longer or non-printable one is dropped."""


def _printable(value: str | None, limit: int = SESSION_ID_MAX) -> str | None:
    """`value` stripped when it is printable ASCII of at most `limit` characters, else None."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > limit or not all(" " <= c <= "~" for c in value):
        return None
    return value


@dataclass(frozen=True)
class Observed:
    """What the caller's request adds to the run's span: the session id, the user id (the
    caller's principal subject, set by the auth layer; never read from a header), and the caller's
    `traceparent`. All optional; none of them changes the run."""

    session_id: str | None = None
    user_id: str | None = None
    traceparent: str | None = None

    def span_attributes(self, trace_id: str) -> dict[str, str]:
        """The `chassis.run` attributes for a run in `trace_id` (32 hex). The `traceparent` is
        kept only when it is valid and names that trace."""
        out: dict[str, str] = {}
        if self.user_id:
            out["user.id"] = self.user_id
        if self.session_id:
            out["session.id"] = self.session_id
        if self.traceparent and parse_traceparent(self.traceparent) == trace_id:
            out["traceparent"] = self.traceparent.strip().lower()[:55]
        return out


def versions_for(config: ChassisConfig) -> Versions:
    return Versions(
        chassis=CHASSIS_VERSION,
        config=config.version,
        prompt=config.spec.prompt.version,
        model_route=config.spec.model.route,
    )


def context_for(request: Request, config: ChassisConfig) -> Context:
    return Context(
        request_id=request.request_id,
        trace_id=request.trace_id,
        idempotency_key=request.idempotency_key,
        agent=request.agent,
        agent_version=request.agent_version,
        budget=request.budget,
        versions=versions_for(config),
        model_route=config.spec.model.route,
    )


async def _guarded(events: AsyncIterator[Event]) -> AsyncGenerator[Event]:
    """Forward the engine's events; an exception becomes one `Error` event, not a 500."""
    try:
        async for event in events:
            yield event
    except Exception as exc:  # the pipeline owns errors; never a 500 on the stream
        yield Error(code="engine_error", message=f"{type(exc).__name__}: {exc}")


async def _replay(events: Sequence[Event]) -> AsyncIterator[Event]:
    for event in events:
        yield event


class RunPipeline:
    """Request to events to response over the app's ports and run registry."""

    def __init__(self, state: State) -> None:
        self._state = state
        self.on_finished: list[OnFinished] = []
        """Async callbacks awaited once after each run ends (`finished`), in order."""
        self._idempotency: Idempotency | None = None

    @property
    def idempotency(self) -> Idempotency:
        """The idempotency layer over `ports.state`, built on first use (no I/O), rebuilt when the
        ports change, with the live `spec.idempotency` (only `ttl_s` reloads)."""
        ports, spec = self.ports, self.config.spec.idempotency
        idem = self._idempotency
        if idem is None or idem.state is not ports.state or idem.telemetry is not ports.telemetry:
            idem = self._idempotency = Idempotency(ports.state, spec, ports.telemetry)
        idem.reload(spec)
        return idem

    async def finished(self, run: Run) -> None:
        """Await each `on_finished` callback with `run`. A callback that raises is counted and
        logged; the next one still runs."""
        for callback in list(self.on_finished):
            try:
                await callback(run)
            except Exception as exc:  # a publisher must never change the answer
                telemetry = self.ports.telemetry
                telemetry.counter(ON_FINISHED_FAILED)
                telemetry.log(
                    "warning",
                    "on_finished callback failed",
                    request_id=run.request.request_id,
                    trace_id=run.request.trace_id,
                    detail=f"{type(exc).__name__}: {exc}",
                )

    @property
    def config(self) -> ChassisConfig:
        config: ChassisConfig = self._state.config
        return config

    @property
    def ports(self) -> PortBundle:
        ports: PortBundle = self._state.ports
        return ports

    @property
    def runs(self) -> RunRegistry:
        runs: RunRegistry = self._state.runs
        return runs

    @property
    def ready(self) -> bool:
        """The lifespan built the ports and set up the engine."""
        return bool(self._state.ready)

    def to_request(
        self,
        *,
        input: TaskInput,
        request_id: str | None = None,
        trace_id: str | None = None,
        idempotency_key: str | None = None,
        agent: str | None = None,
        agent_version: str | None = None,
        context_ref: str | None = None,
        stream: bool = False,
        budget: Budget | None = None,
    ) -> Request:
        """The canonical request: a missing or empty id is a fresh one, a missing agent is the
        served one.
        """
        return Request(
            request_id=request_id or uuid.uuid4().hex,
            trace_id=trace_id or uuid.uuid4().hex,
            idempotency_key=idempotency_key or uuid.uuid4().hex,
            agent=agent or self.config.agent.name,
            agent_version=agent_version or self.config.agent.version,
            input=input,
            context_ref=context_ref,
            stream=stream,
            budget=budget if budget is not None else Budget(),
        )

    @property
    def served(self) -> Served:
        """The agent and versions this chassis serves, for the inbound adapters."""
        config = self.config
        return Served(config.agent.name, config.agent.version, versions_for(config))

    def versions_for(self) -> Versions:
        return versions_for(self.config)

    def context_for(self, request: Request) -> Context:
        return context_for(request, self.config)

    def observed_for(self, headers: Mapping[str, str], *, user_id: str | None = None) -> Observed:
        """The `Observed` of one inbound call: the session id from the header
        `spec.observability.session_header` names, `user_id` as given, and the `traceparent`
        header as sent (`Observed.span_attributes` checks it)."""
        name = self.config.spec.observability.session_header.lower()
        session = headers.get(name)
        if session is None:
            session = next((v for k, v in headers.items() if k.lower() == name), None)
        traceparent = headers.get("traceparent")
        return Observed(
            session_id=_printable(session),
            user_id=_printable(user_id, 256),
            traceparent=traceparent if isinstance(traceparent, str) else None,
        )

    def open(
        self,
        request: Request,
        *,
        interface: Interface = "native",
        observed: Observed | None = None,
    ) -> Run:
        """Hold a record for the run until it ends. Raises `TraceIdInUse`, counted and logged,
        when another in-flight run holds the trace id; the engine never sees that request.
        `interface` labels the run's `chassis.requests` count and `chassis.run` span; `observed`
        adds the caller's session, user, and `traceparent` to the span.
        """
        ctx = self.context_for(request)
        telemetry = self.ports.telemetry
        try:
            record = self.runs.open(request, ctx)
        except TraceIdInUse as exc:
            telemetry.counter("chassis.requests_refused", reason="trace_id_in_use")
            telemetry.log(
                "warning",
                "run refused: its trace id is in use by an in-flight run",
                request_id=request.request_id,
                trace_id=exc.trace_id,
                holder=exc.request_id,
            )
            raise
        return Run(self, request, ctx, record, interface=interface, observed=observed)


class Run:
    """One opened run: its events once, then its response. Holds the record until `close`."""

    def __init__(
        self,
        pipeline: RunPipeline,
        request: Request,
        ctx: Context,
        record: RunRecord,
        *,
        interface: Interface = "native",
        observed: Observed | None = None,
    ) -> None:
        self.request = request
        self.ctx = ctx
        self.record = record
        self.interface: Interface = interface
        self.observed = observed or Observed()
        self._pipeline = pipeline
        self._seen: list[Event] = []
        self._started = False
        self._done = False

    @property
    def seen(self) -> tuple[Event, ...]:
        """The events the run gave so far."""
        return tuple(self._seen)

    @property
    def done(self) -> bool:
        """The engine stream ended: `seen` is the whole run."""
        return self._done

    def events(self) -> AsyncGenerator[Event]:
        """The guarded engine events, once per run. Iterate under `contextlib.aclosing` so a
        client that leaves ends the span and closes the record at once.
        """
        if self._started:
            raise RuntimeError("the run's events were already taken")
        self._started = True
        return self._stream()

    async def _stream(self) -> AsyncGenerator[Event]:
        request, ports, interface = self.request, self._pipeline.ports, self.interface
        telemetry = ports.telemetry
        trace_id = trace_id_hex(request.trace_id)
        try:
            with telemetry.span(
                "chassis.run",
                request_id=request.request_id,
                trace_id=trace_id,
                agent=request.agent,
                interface=interface,
                **self.observed.span_attributes(trace_id),
            ) as span:
                telemetry.counter("chassis.requests", agent=request.agent, interface=interface)
                guards = self._pipeline.config.spec.guardrails
                refused = await guard.check_input(
                    ports, guards, request.input, request_id=request.request_id
                )
                if refused is not None:  # the engine never runs
                    self._seen.append(refused)
                    self._record_error(span.attributes, refused)
                    yield refused
                    self._done = True
                    self.close()
                    return
                source = _guarded(ports.engine.run(request, self.ctx))
                if request.stream and stream_guard_on(guards):  # PoC-8: secrets, URLs
                    source = guard_stream(source, guards, telemetry, request_id=request.request_id)
                async with aclosing(source):
                    async for event in source:
                        self._seen.append(event)
                        if isinstance(event, Error):
                            self._record_error(span.attributes, event)
                        yield event
                if not request.stream:  # the `output` stage: complete calls only
                    checked = await guard.check_output(
                        ports, guards, self._seen, request_id=request.request_id
                    )
                    if checked is not None:
                        self._seen = checked
                        last = checked[-1]
                        if isinstance(last, Error):
                            self._record_error(span.attributes, last)
                            yield last
                    self._mask_urls(guards)
                self._done = True
                self.close()  # the run is over; a trailing frame is not part of it
        finally:
            self.close()

    def _mask_urls(self, guards: GuardrailsSpec) -> None:
        """PoC-8: image and link URLs to hosts not on `output_url_allow` leave a complete answer
        (`chassis.server.output_filter`); a stream is masked by `guard_stream`."""
        masked, count = mask_answer(self._seen, guards.output_url_allow)
        if masked is not None:
            self._seen = masked
            self._pipeline.ports.telemetry.counter(URL_MASKED, count)

    def _record_error(self, attributes: dict[str, Any], error: Error) -> None:
        """Keep a run error's detail where an operator sees it: the span and the log. The OpenAI
        and Anthropic interfaces send a fixed text per code instead (`public_message`). The detail
        is regex-redacted (`chassis.redaction`, 022 H-6) in both: a log and a span are stored."""
        detail = redact(error.message)
        attributes["error.code"] = error.code
        attributes["error.message"] = detail
        self._pipeline.ports.telemetry.log(
            "error",
            "run error",
            request_id=self.request.request_id,
            trace_id=self.request.trace_id,
            code=error.code,
            detail=detail,
        )

    async def response(self) -> Response:
        """The response collected over the events `events()` gave. Raises `RuntimeError` until
        the engine stream has ended.
        """
        if not self._done:
            raise RuntimeError("the run's events are not consumed yet")
        return await collect(_replay(self._seen), self.request, self._pipeline.versions_for())

    async def complete(self) -> Response:
        """Run to the end and collect: the non-streaming call."""
        try:
            async for _ in self.events():
                pass
        finally:
            self.close()
        return await self.response()

    def close(self) -> None:
        """Free the run's trace id. Idempotent; never frees another run's record."""
        self._pipeline.runs.close(self.record)


OnFinished = Callable[[Run], Awaitable[None]]
"""An `on_finished` callback: awaited with the run once it is over (`Run.done` tells how)."""


class RunStream(StreamingResponse):
    """A streamed run that closes the run when the response ends, even when the body was never
    iterated (a client that left before the first frame). Server-sent events by default; `headers`
    go out with the status line. `on_close` is awaited first when the response ends, for what the
    body would close had it been iterated (a held run's reader, `interfaces.hold`).
    """

    def __init__(
        self,
        run: Run,
        content: ContentStream,
        *,
        media_type: str = "text/event-stream",
        headers: Mapping[str, str] | None = None,
        on_close: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        super().__init__(content, media_type=media_type, headers=headers)
        self._run = run
        self._on_close = on_close

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            try:
                if self._on_close is not None:
                    await self._on_close()
            finally:
                self._run.close()
