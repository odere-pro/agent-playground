"""The admission step both model proxy routes share (contract v5, A.6).

The OpenAI route (`POST /v1/chat/completions` on the proxy port, `chassis.server.model_proxy`) and
the Anthropic proxy route (`POST /v1/messages` on the proxy port,
`chassis.server.model_proxy_messages`) both call `admit`: one run lookup, one budget reservation,
one uncorrelated cap. `admit` answers an `Admission`, whose `Hold` is the call's reservation, or a
`Refusal` (429 `budget_exhausted`). The rules for the budget and the uncorrelated cap, and the
bound they keep, are in the `chassis.server.model_proxy` docstring.

Both routes also read their body through `read_capped`, so neither parses more than
`BODY_CAP_BYTES`.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI
from fastapi import Request as HTTPRequest

from chassis.adapters.anthropic_compat.model_wire import BODY_CAP_BYTES
from chassis.ports.bundle import PortBundle
from chassis.ports.model import ModelError, Usage
from chassis.ports.telemetry import TelemetryPort
from chassis.server.correlation import RunRecord, RunRegistry, parse_traceparent
from chassis.server.observability import count_usage
from chassis.tracecontext import continued

log = logging.getLogger("chassis.model_proxy")
SPAN = "chassis.model.call"
ROUTE_SPAN = "chassis.model.route"  # suggested: the routing choice (PoC-8)
DENIED = "chassis.model_upstream_denied"  # suggested
FORCED = "chassis.model_route_forced"  # suggested: a call sent to the run's fallback route
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
    """A call refused at admission: 429 `budget_exhausted`, before the model is called."""

    code: str
    message: str
    retryable: bool
    status: int = 429

    def chat_body(self) -> dict[str, Any]:
        return {
            "error": {
                "message": self.message,
                "type": self.code,
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


@dataclass
class Admission:
    """A call let through: its reservation (`hold.max_tokens` is what to forward), and the span
    attributes. Settle or release the hold exactly as `Hold` says."""

    hold: Hold
    attributes: dict[str, Any]
    bundle: PortBundle
    route: str


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


def _uncorrelated_error(limit: int, worst: int) -> Refusal:
    message = f"uncorrelated model calls are capped at {limit} tokens per minute"
    if 0 < limit < worst:
        message += f"; this call asks for {worst}"
    # Retryable only when the call would fit an empty window.
    return Refusal("budget_exhausted", message, worst <= limit)


def _span_attributes(
    route: str, trace_id: str | None, record: RunRecord | None, fmt: str, traceparent: str | None
) -> dict[str, Any]:
    """The call's span attributes. `traceparent` (checked, `chassis.tracecontext.continued`) makes
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
    refuse a spent run or a full uncorrelated cap with 429, else reserve. `fmt` is `openai` or
    `anthropic`. The caller has checked `app.state.ready` and parsed the body.
    """
    bundle: PortBundle = app.state.ports
    telemetry = bundle.telemetry
    telemetry.counter("chassis.model_calls", route=route)
    requested = route
    trace_id = parse_traceparent(traceparent)
    runs: RunRegistry = app.state.runs
    record = runs.lookup(trace_id) if trace_id is not None else None
    if record is not None and record.model_route and record.model_route != route:
        # PoC-7: the evaluator gate's fallback attempt; the run's route wins.
        route = record.model_route
        telemetry.counter(FORCED, route=route)
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
        if not cap.reserve(worst, limit):
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
    return Admission(hold, attributes, bundle, route)


__all__ = [
    "DEFAULT_UNCORRELATED_MAX_TOKENS",
    "DENIED",
    "FORCED",
    "ROUTE_SPAN",
    "SPAN",
    "WINDOW_S",
    "Admission",
    "Hold",
    "Refusal",
    "UncorrelatedCap",
    "admit",
    "is_ready",
    "note_denied",
    "note_failed",
    "note_rejected",
    "read_capped",
]
