"""`A2AConnector`: the A2A client side every lane shares. Only the httpx transport and the URL
differ between lanes; the mapping, the validation, the cancel, and the span are here.

A subclass's `setup` builds an `httpx.AsyncClient` and calls `_open`, which reads the agent card
and opens an a2a-sdk client on it. `run` sends one message per `Request`, always streaming, and
yields the chassis events it reads back from `metadata["chassis.event"]` (a JSON string, or the
v0 `Struct`; one that does not parse is `error {code: "a2a.bad_event"}`).

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
"""

from __future__ import annotations

from collections.abc import AsyncIterator
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
from chassis.core.trace import traceparent_for
from chassis.ports.engine import Lane

if TYPE_CHECKING:
    from chassis.ports.bundle import PortBundle

SPAN_TASK_ID_CAP = 128
"""suggested: characters of the remote's task id kept in the span attribute."""
CANCEL_TIMEOUT_S = 5.0
"""suggested: the cancel has its own short timeout; the run's budget may already be spent."""


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
        task_id: str | None = None
        with telemetry.span(
            "chassis.engine.run", lane=self.kind, request_id=request.request_id
        ) as span:
            traceparent = traceparent_for(ctx, span.span_id)
            message = self._message_for(
                request, ctx.model_copy(update={"traceparent": traceparent})
            )
            translator = self._translator_for(request)
            headers = {"traceparent": traceparent}
            call = ClientCallContext(
                timeout=request.budget.timeout_ms / 1000, service_parameters=dict(headers)
            )
            stream = self._client.send_message(message, context=call)
            responses = aiter(stream)
            deadline = anyio.current_time() + request.budget.timeout_ms / 1000
            try:
                while True:
                    # One deadline for the whole run, applied to each read. The `yield` stays
                    # outside the scope: a cancel scope must not span a `yield` of a generator.
                    with anyio.CancelScope(deadline=deadline) as read:
                        response = await anext(responses, None)
                    if read.cancelled_caught:
                        yield Error(
                            code="a2a.timeout",
                            message=f"the run took longer than its budget of "
                            f"{request.budget.timeout_ms} ms",
                            retryable=True,
                        )
                        return
                    if response is None:
                        yield Error(
                            code="a2a.transport",
                            message="stream ended without end or error",
                            retryable=True,
                        )
                        return
                    bad: BadJson | None = None
                    try:
                        raws = translator.feed(response)
                    except BadJson as exc:
                        bad, raws = exc, []
                    if task_id is None and translator.task_id is not None:
                        task_id = translator.task_id
                        span.attributes["a2a.task_id"] = task_id[:SPAN_TASK_ID_CAP]
                    if bad is not None:
                        yield Error(code="a2a.bad_event", message=str(bad))
                        return
                    for raw in raws:
                        try:
                            event = parse_event(raw)
                        except (ValidationError, ValueError) as exc:
                            event = Error(code="a2a.bad_event", message=str(exc))
                        if isinstance(event, Start) and event.request_id != request.request_id:
                            event = Error(
                                code="a2a.request_mismatch",
                                message=f"start.request_id {event.request_id!r} is not "
                                f"{request.request_id!r}",
                            )
                        yield event
                        if isinstance(event, End | Error):
                            return
            except (httpx.TimeoutException, A2AClientTimeoutError) as exc:
                yield Error(
                    code="a2a.timeout",
                    message=self._failure_text("a2a.timeout", exc) or "timed out",
                    retryable=True,
                )
            except (httpx.TransportError, A2AClientError) as exc:
                span.attributes["a2a.transport_error"] = type(exc).__name__
                yield Error(
                    code="a2a.transport",
                    message=self._failure_text("a2a.transport", exc) or type(exc).__name__,
                    retryable=True,
                )
            except self._malformed_stream as exc:
                span.attributes["a2a.transport_error"] = type(exc).__name__
                yield Error(
                    code="a2a.transport",
                    message=self._failure_text("a2a.transport", exc),
                    retryable=True,
                )
            finally:
                span.attributes.update(translator.span_attributes)
                # Shielded: a client disconnect cancels this task, and an unshielded await here
                # would be skipped at its first checkpoint, leaving the A2A task running.
                with anyio.CancelScope(shield=True):
                    closer = getattr(stream, "aclose", None)
                    if closer is not None:
                        try:
                            await closer()
                        except Exception as exc:  # the cancel below must still go out
                            telemetry.log(
                                "warning",
                                "a2a stream close failed",
                                task_id=task_id,
                                reason=f"{type(exc).__name__}: {exc}",
                            )
                    if not translator.server_finished and task_id is not None:
                        await self._cancel(task_id, headers)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
        if self._http is not None:
            await self._http.aclose()
            self._http = None
