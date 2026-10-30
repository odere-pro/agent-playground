"""`A2AConnector`: the A2A client side every lane shares. Only the httpx transport and the URL
differ between lanes; the mapping, the validation, the cancel, and the span are here.

A subclass's `setup` builds an `httpx.AsyncClient` and calls `_open`, which reads the agent card
and opens an a2a-sdk client on it. `run` sends one message per `Request`, always streaming, and
yields the chassis events it reads back from `metadata["chassis.event"]` (a JSON string, or the
v0 `Struct`; one that does not parse is `error {code: "a2a.bad_event"}` with fixed text, and the
parse error, which can quote the remote's payload, goes to the log through `chassis.redaction`).

`budget.timeout_ms` is two limits: the a2a-sdk call timeout, so each HTTP connect, write, pool
wait, and read gets that long, and a deadline for the whole run, `timeout_ms` after `run` sends,
checked on every read in every lane (`inprocess` included, where no HTTP timeout fires). Either is
`error {code: "a2a.timeout", retryable: true}`. A transport failure (the sidecar dies mid-stream,
the connection drops) is `error {code: "a2a.transport", retryable: true}` (suggested), never an
exception from `run`; so is a stream that ends with no `end` or `error`. Closing the stream early,
a timeout, a transport failure, an early end, or a local refusal of the stream sends
`CancelTaskRequest` for a task the server has not finished (best effort), even when closing the
stream raised (that is logged). One span per run; the task id is the span attribute
`a2a.task_id`.

Two hooks let a lane read another dialect (the plain-A2A mode of `remote`): `_message_for` builds
the request, and `_translator_for` makes the run's `EventTranslator`, which turns each A2A stream
item into raw events and says whether the remote's task is finished and what its id is. The base
class reads `chassis.event` (`ChassisTranslator`).

Each run gets one W3C `traceparent` from `chassis.core.trace.traceparent_for(ctx, span.span_id)`:
the run's trace id and the run span as parent. It travels twice with the same value: in
`ctx.traceparent` inside `chassis.ctx`, which is how `handle` sees it in every lane and language,
and as the `traceparent` HTTP header on every A2A request of the run (the message and the cancel),
through a2a-sdk's `ClientCallContext.service_parameters`. The caller's `ctx` is not changed. The
card fetch in `setup` belongs to no run and carries none.

The run span carries `trace_id` (`trace_id_hex(ctx.trace_id)`), so a telemetry adapter keeps it in
the run's trace even with no `chassis.run` span around it. PoC-7: when the telemetry adapter has
`propagation_headers()` (the OTel one does; the port does not require it), its W3C `baggage`
header (`user.id`, `session.id`) goes on every A2A request of the run next to `traceparent`. It is
read through `getattr`, so this module imports no telemetry SDK; it never replaces `traceparent`.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator, Iterator
from contextlib import aclosing
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

import anyio
import httpx
from a2a.client import (
    A2ACardResolver,
    A2AClientError,
    A2AClientTimeoutError,
    Client,
    ClientCallContext,
    ClientConfig,
    ClientFactory,
)
from a2a.types import AgentCard, CancelTaskRequest, SendMessageRequest, StreamResponse
from a2a.utils.errors import A2AError
from pydantic import ValidationError

from chassis.adapters.a2a.mapping import BadJson, request_to_message, update_to_event
from chassis.core.envelope import Context, Request
from chassis.core.events import SCHEMA_VERSION, End, Error, Event, Start, parse_event
from chassis.core.trace import trace_id_hex, traceparent_for
from chassis.ports.engine import Lane
from chassis.ports.telemetry import Span, TelemetryPort
from chassis.redaction import redact

if TYPE_CHECKING:
    from chassis.ports.bundle import PortBundle

SPAN_TASK_ID_CAP = 128
"""suggested: characters of the remote's task id kept in the span attribute."""
CANCEL_TIMEOUT_S = 5.0
"""suggested: the cancel has its own short timeout; the run's budget may already be spent."""
BAD_EVENT_TEXT = "the agent sent an event that is not valid"
"""suggested: the fixed `a2a.bad_event` message. The parse error's own text goes to the log."""


PROPAGATED = frozenset({"baggage"})
"""The headers a telemetry adapter's `propagation_headers()` may add to an A2A request."""


def propagation_headers(telemetry: object) -> dict[str, str]:
    """The telemetry adapter's extra W3C headers (`baggage`), or `{}` when it has none."""
    extra = getattr(telemetry, "propagation_headers", None)
    if not callable(extra):
        return {}
    headers = extra()
    if not isinstance(headers, dict):
        return {}
    return {k: v for k, v in headers.items() if k in PROPAGATED and isinstance(v, str) and v}


class EventTranslator(Protocol):
    """Reads one run's A2A stream. Made per run by `A2AConnector._translator_for`, fed every
    `StreamResponse` in order. `feed` returns the raw events (dicts) to emit, possibly none.
    """

    def feed(self, response: StreamResponse) -> list[dict[str, Any]]: ...

    @property
    def server_finished(self) -> bool:
        """The remote's task is terminal, so no cancel is needed."""
        ...

    @property
    def task_id(self) -> str | None:
        """The remote's task id once seen, for the cancel and the span."""
        ...

    @property
    def span_attributes(self) -> dict[str, Any]:
        """Attributes to set on the run's span when the run ends."""
        ...


class ChassisTranslator:
    """The chassis mode: one A2A stream event is zero or one chassis event, read back from
    `metadata["chassis.event"]` (`update_to_event`). `BadJson` propagates from `feed`.
    """

    def __init__(self) -> None:
        self._task_id: str | None = None
        self._server_finished = False

    @property
    def server_finished(self) -> bool:
        return self._server_finished

    @property
    def task_id(self) -> str | None:
        return self._task_id

    @property
    def span_attributes(self) -> dict[str, Any]:
        return {}

    def feed(self, response: StreamResponse) -> list[dict[str, Any]]:
        if response.HasField("task") and self._task_id is None:
            self._task_id = response.task.id
        raw = update_to_event(response)
        if raw is None:
            return []
        self._server_finished = raw.get("type") in ("end", "error")
        return [raw]


class _DeadlinePassed(Exception):
    """The run passed its deadline, `budget.timeout_ms` after the send (`a2a.timeout`)."""


@dataclass
class _Run:
    """What one run's read loop and its cleanup share. `task_id` is the remote's task id, kept
    from the first stream item that had one."""

    request: Request
    translator: EventTranslator
    span: Span
    headers: dict[str, str]
    telemetry: TelemetryPort
    task_id: str | None = None

    def note_task_id(self) -> None:
        """Keep the translator's task id once it has one, and put it on the span, capped."""
        if self.task_id is None and self.translator.task_id is not None:
            self.task_id = self.translator.task_id
            self.span.attributes["a2a.task_id"] = self.task_id[:SPAN_TASK_ID_CAP]


class A2AConnector:
    """The shared client. Subclasses set `kind` and implement `setup` with `_open`."""

    kind: Lane
    capabilities: frozenset[str] = frozenset({"streaming"})
    _malformed_stream: tuple[type[BaseException], ...] = ()
    """Exceptions that a stream the SDK could not parse raises (a `JSONDecodeError`). Empty in the
    base: they propagate, as before. Plain mode of `remote` sets them, so they become
    `a2a.transport` with fixed text instead of leaking the remote's payload."""

    def __init__(self) -> None:
        self._http: httpx.AsyncClient | None = None
        self._client: Client | None = None
        self._ports: PortBundle | None = None

    def _check_card(self, card: AgentCard) -> None:
        """A lane's rule for the card it reads. The base accepts any card."""

    async def _open(self, http: httpx.AsyncClient, url: str, ports: PortBundle) -> None:
        """Read the agent card at `url` over `http` and open the A2A client on it."""
        self._http = http
        card = await A2ACardResolver(http, url).get_agent_card()
        self._check_card(card)
        self._client = ClientFactory(ClientConfig(httpx_client=http, streaming=True)).create(card)
        self._ports = ports

    async def _cancel(self, task_id: str, headers: dict[str, str]) -> None:
        """Best effort: the task may already be terminal, which is fine."""
        if self._client is None:
            return
        call = ClientCallContext(timeout=CANCEL_TIMEOUT_S, service_parameters=dict(headers))
        try:
            await self._client.cancel_task(CancelTaskRequest(id=task_id), context=call)
        except (A2AError, httpx.HTTPError) as exc:
            if self._ports is not None:
                self._ports.telemetry.log(
                    "debug", "a2a cancel did not apply", task_id=task_id, reason=str(exc)
                )

    def _message_for(self, request: Request, ctx: Context) -> SendMessageRequest:
        """The A2A request for one run. `ctx` already holds the run's `traceparent`."""
        return request_to_message(
            request.input.model_dump(mode="json"),
            ctx.model_dump(mode="json"),
            schema_version=SCHEMA_VERSION,
        )

    def _failure_text(self, code: str, exc: BaseException) -> str:
        """The text of a timeout or transport `Error`: the exception text, in the base."""
        return str(exc)

    def _translator_for(self, request: Request) -> EventTranslator:
        """The reader of one run's stream. The base reads `chassis.event`."""
        return ChassisTranslator()

    async def run(self, request: Request, ctx: Context) -> AsyncIterator[Event]:
        if self._client is None or self._ports is None:
            raise RuntimeError(f"{self.kind} connector is not set up")
        telemetry = self._ports.telemetry
        with telemetry.span(
            "chassis.engine.run",
            lane=self.kind,
            request_id=request.request_id,
            trace_id=trace_id_hex(ctx.trace_id),
        ) as span:
            traceparent = traceparent_for(ctx, span.span_id)
            message = self._message_for(
                request, ctx.model_copy(update={"traceparent": traceparent})
            )
            headers = {**propagation_headers(telemetry), "traceparent": traceparent}
            call = ClientCallContext(
                timeout=request.budget.timeout_ms / 1000, service_parameters=dict(headers)
            )
            run = _Run(request, self._translator_for(request), span, headers, telemetry)
            stream = self._client.send_message(message, context=call)
            deadline = anyio.current_time() + request.budget.timeout_ms / 1000
            try:
                async with aclosing(self._read(aiter(stream), run, deadline)) as events:
                    async for event in events:
                        yield event
            except _DeadlinePassed:
                yield Error(
                    code="a2a.timeout",
                    message=f"the run took longer than its budget of "
                    f"{request.budget.timeout_ms} ms",
                    retryable=True,
                )
            except (httpx.TimeoutException, A2AClientTimeoutError) as exc:
                text = self._failure_text("a2a.timeout", exc) or "timed out"
                yield Error(code="a2a.timeout", message=text, retryable=True)
            except (httpx.TransportError, A2AClientError) as exc:
                span.attributes["a2a.transport_error"] = type(exc).__name__
                text = self._failure_text("a2a.transport", exc) or type(exc).__name__
                yield Error(code="a2a.transport", message=text, retryable=True)
            except self._malformed_stream as exc:
                span.attributes["a2a.transport_error"] = type(exc).__name__
                text = self._failure_text("a2a.transport", exc)
                yield Error(code="a2a.transport", message=text, retryable=True)
            finally:
                await self._finish(stream, run)

    async def _read_one(
        self, responses: AsyncIterator[StreamResponse], deadline: float
    ) -> StreamResponse | None:
        """The next stream item, or None at the end of the stream. `_DeadlinePassed` when the
        run's deadline comes first. One deadline for the whole run, applied to each read; the
        caller's `yield` stays outside the cancel scope, which must not span a generator's
        `yield`."""
        with anyio.CancelScope(deadline=deadline) as read:
            response = await anext(responses, None)
        if read.cancelled_caught:
            raise _DeadlinePassed
        return response

    async def _read(
        self, responses: AsyncIterator[StreamResponse], run: _Run, deadline: float
    ) -> AsyncGenerator[Event]:
        """The run's events up to and including its first `end` or `error`. A stream that ends
        before one is `a2a.transport`; a `chassis.event` that is not a JSON object is
        `a2a.bad_event`."""
        while True:
            response = await self._read_one(responses, deadline)
            if response is None:
                yield Error(
                    code="a2a.transport",
                    message="stream ended without end or error",
                    retryable=True,
                )
                return
            try:
                raws = run.translator.feed(response)
            except BadJson as exc:
                run.note_task_id()
                yield self._bad_event(run.request, exc)
                return
            run.note_task_id()
            for event in self._events_from(raws, run.request):
                yield event
                if isinstance(event, End | Error):
                    return

    def _events_from(self, raws: list[dict[str, Any]], request: Request) -> Iterator[Event]:
        """Each raw event parsed, lazily. One that fails `parse_event` is `a2a.bad_event`; a
        `start` for another request is `a2a.request_mismatch`."""
        for raw in raws:
            try:
                event = parse_event(raw)
            except (ValidationError, ValueError) as exc:
                event = self._bad_event(request, exc)
            if isinstance(event, Start) and event.request_id != request.request_id:
                event = Error(
                    code="a2a.request_mismatch",
                    message=f"start.request_id {event.request_id!r} is not {request.request_id!r}",
                )
            yield event

    def _bad_event(self, request: Request, exc: Exception) -> Error:
        """`a2a.bad_event` with fixed text. The parse error quotes the remote's payload (pydantic's
        `input_value=...`), so it goes to the log only, through `chassis.redaction.redact`."""
        if self._ports is not None:
            self._ports.telemetry.log(
                "warning",
                "a2a bad event",
                request_id=request.request_id,
                error_type=type(exc).__name__,
                detail=redact(str(exc)),
            )
        return Error(code="a2a.bad_event", message=BAD_EVENT_TEXT)

    async def _finish(self, stream: object, run: _Run) -> None:
        """The end of every run: the translator's span attributes, then the stream closed and,
        when the server has not finished the task, the cancel. Shielded: a client disconnect
        cancels this task, and an unshielded await here would be skipped at its first
        checkpoint, leaving the A2A task running."""
        run.span.attributes.update(run.translator.span_attributes)
        with anyio.CancelScope(shield=True):
            closer = getattr(stream, "aclose", None)
            if closer is not None:
                try:
                    await closer()
                except Exception as exc:  # the cancel below must still go out
                    run.telemetry.log(
                        "warning",
                        "a2a stream close failed",
                        task_id=run.task_id,
                        reason=f"{type(exc).__name__}: {exc}",
                    )
            if not run.translator.server_finished and run.task_id is not None:
                await self._cancel(run.task_id, run.headers)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
        if self._http is not None:
            await self._http.aclose()
            self._http = None
