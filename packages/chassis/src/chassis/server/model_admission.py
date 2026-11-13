"""The admission step both model proxy routes share (contract v5, A.6).

The OpenAI route (`POST /v1/chat/completions` on the proxy port, `chassis.server.model_proxy`) and
the Anthropic proxy route (`POST /v1/messages` on the proxy port,
`chassis.server.model_proxy_messages`) both call `admit`: one run lookup, one budget reservation,
one uncorrelated cap. `admit` answers an `Admission`, whose `Hold` is the call's reservation, or a
`Refusal` (429 `budget_exhausted`, or 403 `uncorrelated_refused` when the uncorrelated cap is
`0`, the `cloud` default; CH-14). The rules for the budget and the uncorrelated cap, and the
bound they keep, are in the `chassis.server.model_proxy` docstring.

Both routes also read their body through `read_capped`, so neither parses more than
`BODY_CAP_BYTES`.

Under failure (CH-16). `Admission.model` is the port bound to the call: it passes the run's
remaining time (`RunRecord.remaining_s`) as the port's `timeout_s`, and it cuts the call with
`run_ended` when the run closes first (`RunRecord.ended`), cancelling the adapter's call so the
upstream connection closes. A `service_budget_exhausted` error is counted once per call as
`chassis.model_budget_exhausted{route}` (`note_service_budget`), the metric the alert rule in
`deploy/prometheus/rules/chassis.yaml` reads.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from fastapi import Request as HTTPRequest

from chassis.adapters.anthropic_compat.model_wire import BODY_CAP_BYTES, DENIED_TEXT
from chassis.core.trace import continued
from chassis.ports.bundle import PortBundle
from chassis.ports.model import (
    RUN_ENDED,
    SERVICE_BUDGET_EXHAUSTED,
    ModelChunk,
    ModelError,
    ModelMessage,
    ModelPort,
    ModelResult,
    ToolSpec,
    Usage,
    call_ids,
)
from chassis.ports.telemetry import TelemetryPort
from chassis.server.correlation import RunRecord, RunRegistry, parse_traceparent
from chassis.server.observability import count_usage

if TYPE_CHECKING:
    from chassis.server.config import ModelSpec

log = logging.getLogger("chassis.model_proxy")
SPAN = "chassis.model.call"
ROUTE_SPAN = "chassis.model.route"  # suggested: the routing choice (PoC-8)
DENIED = "chassis.model_upstream_denied"  # suggested
PINNED = "chassis.model_route_pinned"  # suggested: an uncorrelated call sent to `spec.model.route`
ROUTE_REFUSED = "chassis.model_route_refused"
"""suggested (012 H-3): a call of a run refused at admission for a route that is not the run's,
counted by the run's route (never the one asked for, which the workload picks)."""
UNRESOLVED = "unresolved"
"""suggested: the `route` label of `ROUTE_REFUSED` for a run whose record holds no route."""
OTHER_ROUTE = "other"
"""suggested (012 H-3): the `route` label of `chassis.model_calls` for a refused call that names a
route the config does not declare (`ModelSpec.declared_routes`). The workload picks `model`, so its
own string never becomes a label: refused calls add no metric series."""
REQUESTED_LOG_CHARS = 64  # suggested: how much of an unlisted route name the warning keeps
BUDGET_EXHAUSTED = "chassis.model_budget_exhausted"
"""suggested (CH-16): the router refused a call for the service's budget, by route. Exported as
`chassis_model_budget_exhausted_total`."""
WINDOW_S = 60.0
# suggested: the worst case reserved, and forwarded, for an uncorrelated call that sets no
# `max_tokens`; never more than the cap itself. The epic gives no value.
DEFAULT_UNCORRELATED_MAX_TOKENS = 1024


class UncorrelatedCap:
    """Uncorrelated tokens spent in the current fixed window of `WINDOW_S` seconds, and reserved by
    the calls in flight (the `chassis.server.model_proxy` docstring has the rule and the bound).

    `clock` returns seconds (monotonic); a test passes its own, so nothing sleeps. Each method holds
    a lock, so a check and its reservation are one step even across threads.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._start = clock()
        self._spent = 0
        self._reserved = 0

    def _roll(self) -> None:
        now = self._clock()
        if now - self._start >= WINDOW_S:
            self._start = now
            self._spent = 0  # reservations in flight carry over

    @property
    def spent(self) -> int:
        with self._lock:
            self._roll()
            return self._spent

    @property
    def reserved(self) -> int:
        with self._lock:
            return self._reserved

    @staticmethod
    def worst_case(wanted: int | None, limit: int) -> int:
        """The tokens a call reserves and is forwarded as `max_tokens`."""
        if wanted is None:
            return max(1, min(DEFAULT_UNCORRELATED_MAX_TOKENS, limit))
        return max(1, wanted)

    def reserve(self, worst: int, limit: int) -> bool:
        """Hold `worst` tokens when they fit in `limit` with what is spent and held; else False."""
        with self._lock:
            self._roll()
            if self._spent + self._reserved + worst > limit:
                return False
            self._reserved += worst
            return True

    def release(self, held: int) -> None:
        """Give back a reservation without charging: the call never reached the model."""
        with self._lock:
            self._reserved = max(0, self._reserved - held)

    def settle(self, held: int, usage: Usage | None) -> None:
        """Replace a reservation by the call's usage, or charge all of it when there is none."""
        with self._lock:
            self._roll()
            self._reserved = max(0, self._reserved - held)
            self._spent += held if usage is None else usage.input_tokens + usage.output_tokens


@dataclass(frozen=True)
class Refusal:
    """A call refused at admission, before the model is called: 429 `budget_exhausted`, 403
    `uncorrelated_refused` under an uncorrelated cap of `0`, or 403 `model_route_denied` for a
    route the service does not list. `status` is the HTTP status. `kind` is the chat body's
    `type`, else the code."""

    code: str
    message: str
    retryable: bool
    status: int = 429
    kind: str | None = None

    def chat_body(self) -> dict[str, Any]:
        return {
            "error": {
                "message": self.message,
                "type": self.kind or self.code,
                "code": self.code,
                "retryable": self.retryable,
            }
        }


class Hold:
    """One call's reservation, on its run or on the replica's `UncorrelatedCap`, given back exactly
    once. `settle` charges the usage. `release` runs when a stream response ends: if the body never
    started, the model was never called and nothing is charged; an uncorrelated stream that started
    is charged its last usage seen, or its whole reservation (the `chassis.server.model_proxy`
    docstring says why). A complete call runs inside `settled`.
    """

    def __init__(
        self,
        record: RunRecord | None,
        wanted: int | None,
        cap: UncorrelatedCap | None = None,
        held: int = 0,
    ) -> None:
        self.record = record
        self.cap = cap if record is None else None
        if record is not None:
            self.tokens = record.reserve(wanted)
        else:
            self.tokens = held  # the router reserved it on the cap
        self.max_tokens = self.tokens if record is not None or cap is not None else wanted
        self.started = False
        # The Anthropic route sets this: a correlated stream whose client left after the model
        # call started is charged too (contract v5 review, item 1). The chat route does not
        # (Known gap 004 G-2).
        self.charge_abandoned = False
        self.seen: list[Usage] = []
        self.on_usage: Callable[[Usage], None] | None = None
        """Called once with the usage a call reported (PoC-8 metrics)."""
        self._done = False

    def settle(self, usage: Usage | None, *, reported: bool = True) -> None:
        if self._done:
            return
        self._done = True
        if usage is not None and reported and self.on_usage is not None:
            self.on_usage(usage)
        if self.record is not None:
            self.record.settle(self.tokens, usage)
        elif self.cap is not None:
            self.cap.settle(self.tokens, usage)

    @contextlib.contextmanager
    def settled(self) -> Iterator[None]:
        """The block of one complete call. The block settles the usage on success; whatever else
        ends it (a `ModelError` answered, a cancel, a bug) settles with no usage, once."""
        try:
            yield
        finally:
            self.settle(None)

    def release(self) -> None:
        if self._done:
            return
        if self.cap is not None and self.started:
            self.settle(self.seen[-1] if self.seen else None)
            return
        if self.record is not None and self.charge_abandoned and self.started:
            # Usage unknown: charge the whole reservation, the most the upstream can produce.
            if self.seen:
                self.settle(self.seen[-1])
            else:
                self.settle(Usage(output_tokens=self.tokens), reported=False)
            return
        self._done = True
        if self.record is not None:
            self.record.release(self.tokens)
        elif self.cap is not None:
            self.cap.release(self.tokens)


class BoundModel:
    """`ports.model` for one admitted call (CH-16). `complete` and `stream` take the proxy's
    arguments and add `timeout_s`: what the run has left, or None for an uncorrelated call (the
    adapter's default then bounds it). A correlated call is also raced against the run's end:
    when `record.ended` is set first, the port call is cancelled (the adapter closes the upstream
    connection on cancel) and the call raises `run_ended`.
    """

    def __init__(self, model: ModelPort, record: RunRecord | None) -> None:
        self._model = model
        self._record = record

    @property
    def timeout_s(self) -> float | None:
        return None if self._record is None else self._record.remaining_s()

    async def complete(
        self,
        messages: Sequence[ModelMessage],
        *,
        route: str,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ModelResult:
        call = self._model.complete(
            messages,
            route=route,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_s=self.timeout_s,
        )
        if self._record is None:
            return await call
        with call_ids(self._record.trace_id, self._record.request_id):
            return await until_run_ends(self._record, call)

    async def stream(
        self,
        messages: Sequence[ModelMessage],
        *,
        route: str,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AsyncIterator[ModelChunk]:
        chunks = self._model.stream(
            messages,
            route=route,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_s=self.timeout_s,
        )
        try:
            if self._record is None:
                async for chunk in chunks:
                    yield chunk
                return
            while True:
                with call_ids(self._record.trace_id, self._record.request_id):
                    nxt = await until_run_ends(self._record, _next_chunk(chunks))
                if nxt is None:
                    return
                yield nxt
        finally:
            # Closing this generator must close the adapter's: `async for` does not do it on an
            # exit by exception, and the route counts on one close freeing the upstream.
            aclose = getattr(chunks, "aclose", None)
            if aclose is not None:
                with contextlib.suppress(Exception):
                    await aclose()


async def _next_chunk(chunks: AsyncIterator[ModelChunk]) -> ModelChunk | None:
    try:
        return await chunks.__anext__()
    except StopAsyncIteration:
        return None


async def until_run_ends[T](record: RunRecord, awaitable: Awaitable[T]) -> T:
    """`awaitable`, unless `record.ended` is set first: then the awaitable's task is cancelled and
    the call raises `run_ended`. A cancel of the caller cancels the task too."""
    task = asyncio.ensure_future(awaitable)
    ended = asyncio.ensure_future(record.ended.wait())
    try:
        done, _ = await asyncio.wait({task, ended}, return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            return task.result()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        raise ModelError(RUN_ENDED, f"request {record.request_id} ended before the model answered")
    finally:
        ended.cancel()
        if not task.done():
            task.cancel()


@dataclass
class Admission:
    """A call let through: its reservation (`hold.max_tokens` is what to forward), the span
    attributes, and `model`, the port bound to this call (`BoundModel`). Settle or release the
    hold exactly as `Hold` says."""

    hold: Hold
    attributes: dict[str, Any]
    bundle: PortBundle
    route: str
    model: BoundModel


def note_rejected(telemetry: TelemetryPort, route: str, exc: ModelError) -> None:
    """Log the upstream's own text for a 400-class rejection at the chassis. The adapter has
    already redacted and capped it. It never reaches the response."""
    log.warning(
        "model route rejected the request: route=%s code=%s upstream=%r",
        route,
        exc.code,
        exc.message,
    )


def note_failed(route: str, exc: ModelError) -> None:
    """Log the upstream's own text for a model call that failed on the OpenAI route. The adapter
    has already redacted and capped it. It never reaches the response."""
    log.warning("model call failed: route=%s code=%s upstream=%r", route, exc.code, exc.message)


def note_denied(telemetry: TelemetryPort, route: str, exc: ModelError) -> None:
    """Count and log a refused model route once. The caller cannot tell a route outside its key
    from a chassis key that is itself invalid; the operator can, from this."""
    telemetry.counter(DENIED, route=route)
    log.warning("model route refused by the upstream: route=%s code=%s", route, exc.code)


def note_service_budget(telemetry: TelemetryPort, route: str, exc: ModelError) -> None:
    """Count and log a call the router refused for the service's budget (CH-16). The upstream
    text (redacted and capped by the adapter) names which budget; it never reaches the response."""
    telemetry.counter(BUDGET_EXHAUSTED, route=route)
    log.warning(
        "model call refused for the service's budget: route=%s upstream=%r", route, exc.message
    )


def note_model_error(telemetry: TelemetryPort, route: str, exc: ModelError) -> None:
    """Count and log one failed model call by its kind: a refused route, the service's budget, a
    run that ended, or any other upstream failure."""
    if exc.code in ("http_401", "http_403"):
        note_denied(telemetry, route, exc)
    elif exc.code == SERVICE_BUDGET_EXHAUSTED:
        note_service_budget(telemetry, route, exc)
    elif exc.code == RUN_ENDED:
        log.info("model call cut: its run ended: route=%s", route)
    else:
        note_failed(route, exc)


def is_ready(app: FastAPI) -> bool:
    return bool(getattr(app.state, "ready", False))


async def read_capped(http: HTTPRequest) -> bytes | None:
    """The body, or `None` when it is over `BODY_CAP_BYTES`. The cap is enforced while reading,
    before anything is parsed."""
    declared = http.headers.get("content-length")
    # `isascii` too: a Latin-1 superscript digit passes `isdigit` but not `int`.
    if (
        declared is not None
        and declared.isascii()
        and declared.isdigit()
        and int(declared) > BODY_CAP_BYTES
    ):
        return None
    size = 0
    parts: list[bytes] = []
    async for part in http.stream():
        size += len(part)
        if size > BODY_CAP_BYTES:
            return None
        parts.append(part)
    return b"".join(parts)


def _budget_error(record: RunRecord) -> Refusal:
    held = record.reserved_tokens
    message = (
        f"request {record.request_id} has spent {record.spent_tokens} of its "
        f"{record.budget.max_tokens} tokens"
    )
    if held:
        message += f"; {held} more are held by its in-flight model calls"
    # Retryable only when in-flight calls hold the rest: they may settle for less.
    return Refusal("budget_exhausted", message, not record.exhausted)


UNCORRELATED_REFUSED = "uncorrelated_refused"
"""suggested (CH-14): the code when `spec.limits.uncorrelated_tokens_per_minute` is `0`, the
`cloud` default. 403, not retryable: no wait makes room under a cap of 0."""


def _uncorrelated_error(limit: int, worst: int) -> Refusal:
    if limit == 0:
        message = "model calls that name no run in flight are refused"
        return Refusal(UNCORRELATED_REFUSED, message, False, status=403)
    message = f"uncorrelated model calls are capped at {limit} tokens per minute"
    if 0 < limit < worst:
        message += f"; this call asks for {worst}"
    # Retryable only when the call would fit an empty window.
    return Refusal("budget_exhausted", message, worst <= limit)


ROUTE_DENIED = Refusal(
    "model_route_denied", DENIED_TEXT, False, status=403, kind="permission_error"
)
"""012 H-3: the same code and fixed text as an upstream 401 or 403 (contract v5, A.9), so the
caller sees one refusal for one cause. The body names no route and no key."""


def _route_refused(telemetry: TelemetryPort, record: RunRecord, requested: str) -> Refusal | None:
    """`ROUTE_DENIED` when a call of a run names any route but the run's own, else None (012 H-3;
    user decision 2026-11-13: refuse, never redirect). The run's route is `RunRecord.model_route`:
    the task's route, else `spec.model.route`, or the gate's fallback route for its fallback
    attempt. It passes even after a reload drops it from the config: a run keeps the route it
    opened with. A record with no route fails closed: every route is refused."""
    if record.model_route is not None and requested == record.model_route:
        return None
    run_route = record.model_route or UNRESOLVED
    telemetry.counter(ROUTE_REFUSED, route=run_route)
    log.warning(
        "model route is not the run's route: run_route=%s requested=%r request_id=%s",
        run_route,
        requested[:REQUESTED_LOG_CHARS],
        record.request_id,
    )
    return ROUTE_DENIED


def _label(model_spec: ModelSpec, requested: str) -> str:
    """The `route` label of a refused call: the route when the config declares it, else
    `OTHER_ROUTE`. The set of labels stays as small as the config."""
    return requested if requested in model_spec.declared_routes() else OTHER_ROUTE


def _span_attributes(
    route: str, trace_id: str | None, record: RunRecord | None, fmt: str, traceparent: str | None
) -> dict[str, Any]:
    """The call's span attributes. `traceparent` (checked, `chassis.core.trace.continued`) makes
    the span a child of the workload's call that sent it (PoC-7)."""
    parent = {"traceparent": continued(traceparent)} if continued(traceparent) else {}
    if record is None:
        return {"route": route, "trace_id": trace_id, "correlated": False, "format": fmt, **parent}
    return {
        "format": fmt,
        "route": route,
        "trace_id": record.trace_id,
        "request_id": record.request_id,
        "agent": record.agent,
        "correlated": True,
        **parent,
    }


def admit(
    app: FastAPI, *, route: str, wanted: int | None, traceparent: str | None, fmt: str
) -> Admission | Refusal:
    """Steps 3 to 7 of the shared admission (contract v5, A.6): count the call, find its run,
    pick the route, refuse a spent run or a full uncorrelated cap with 429, else reserve. A call
    of a run that names any route but the run's own is refused with 403 `model_route_denied`
    (012 H-3), never redirected; an uncorrelated call always gets `spec.model.route` (R6).
    `chassis.model_calls{route}` counts the route the call is sent to; a refused route is counted
    by its name only when the config declares it, else as `OTHER_ROUTE`. `fmt` is `openai` or
    `anthropic`. The caller has checked `app.state.ready` and parsed the body.
    """
    bundle: PortBundle = app.state.ports
    telemetry = bundle.telemetry
    model_spec = app.state.config.spec.model
    requested = route
    trace_id = parse_traceparent(traceparent)
    runs: RunRegistry = app.state.runs
    record = runs.lookup(trace_id) if trace_id is not None else None
    if record is not None:
        refused = _route_refused(telemetry, record, requested)
        if refused is not None:
            telemetry.counter("chassis.model_calls", route=_label(model_spec, requested))
            return refused
    if record is None and route != model_spec.route:
        # R6: a call with no run cannot pick its route; it gets the agent's default route.
        route = model_spec.route
        telemetry.counter(PINNED, route=route)
    # The route the call is sent to: the run's own, or `spec.model.route`. Never the raw `model`.
    telemetry.counter("chassis.model_calls", route=route)
    cap: UncorrelatedCap = app.state.uncorrelated_cap
    worst = 0
    if record is None:
        telemetry.counter("chassis.model_calls_uncorrelated", route=route)
        telemetry.log(
            "warning",
            "model call names no in-flight run; served uncorrelated",
            route=route,
            trace_id=trace_id,
            reason="no traceparent" if trace_id is None else "no in-flight run",
        )
        limit = app.state.config.spec.limits.uncorrelated_tokens_per_minute
        worst = cap.worst_case(wanted, limit)
        if limit == 0 or not cap.reserve(worst, limit):
            telemetry.counter("chassis.model_calls_refused", route=route)
            return _uncorrelated_error(limit, worst)
    elif record.remaining_tokens == 0:
        telemetry.counter("chassis.model_calls_refused", route=route)
        return _budget_error(record)
    # No await between the check above and the reservation: one step on the event loop.
    # An uncorrelated call's reservation is already on the cap.
    hold = Hold(record, wanted, cap, worst)
    attributes = _span_attributes(route, trace_id, record, fmt, traceparent)
    price = app.state.config.spec.observability.prices.get(route)
    hold.on_usage = lambda usage: count_usage(telemetry, route, usage, price)
    choice = {"route.requested": requested, "route.forced": route != requested}
    with telemetry.span(ROUTE_SPAN, **attributes, **choice):
        pass  # the choice is made above; the span records it in the call's trace
    return Admission(hold, attributes, bundle, route, BoundModel(bundle.model, record))


__all__ = [
    "BUDGET_EXHAUSTED",
    "DEFAULT_UNCORRELATED_MAX_TOKENS",
    "DENIED",
    "OTHER_ROUTE",
    "PINNED",
    "REQUESTED_LOG_CHARS",
    "ROUTE_DENIED",
    "ROUTE_REFUSED",
    "ROUTE_SPAN",
    "SPAN",
    "UNCORRELATED_REFUSED",
    "UNRESOLVED",
    "WINDOW_S",
    "Admission",
    "BoundModel",
    "Hold",
    "Refusal",
    "UncorrelatedCap",
    "admit",
    "is_ready",
    "note_denied",
    "note_failed",
    "note_model_error",
    "note_rejected",
    "note_service_budget",
    "read_capped",
    "until_run_ends",
]
