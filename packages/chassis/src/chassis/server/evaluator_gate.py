"""The evaluator gate (PoC-7, backlog 017 H-4): `GatedEngine`, an `EngineConnector` around the
lane's connector. The lifespan builds it (`with_gate`) when `spec.harness.evaluator_gate` is true
and refuses to start with `NoEvaluator` (`GateNotConfigured`). It knows nothing about the engine:
it reruns `run(request, ctx)` through the connector, so it works the same in every lane.

**Which calls are gated.** Each call is gated with probability `spec.harness.evaluator_sample_rate`
(reloadable, read per call), decided by a hash of a salt and the `request_id` (`sampled`). The salt
is a per-process secret (`secrets.token_bytes(32)`), drawn once when `GatedEngine` is built, so a
caller who picks its own `request_id` cannot pick ids that are never gated (finding R7-17). Within
one process the same request id always decides the same. Across replicas, or after a restart, it
may decide differently: the sample is a rate, not a contract. A call that is not sampled passes
through untouched.

**Sync mode** (`evaluator_mode: sync`, a call that is not a stream). Each attempt's events are
buffered. The answer (the `delta` texts) is scored with `EvaluatorPort.score(EvalCase(input,
answer))`; it passes when `score >= threshold`. A failed answer is retried, up to `retries` more
attempts on the same route; the events of a failed attempt are never sent. After the last failed
retry, one fallback attempt runs with `ctx.model_route = spec.model.fallback_route`, and the run's
`RunRecord.model_route` is set, so the model proxy sends every model call of that attempt to the
fallback route even when the workload names another (`chassis.server.model_proxy.admit`). The
fallback answer is not scored (it is the last resort; scoring it would add a judge call to the
worst path); its `end` has `status: fallback`. With no fallback route, the run ends with one
`error {code: evaluator_gate_failed}` (suggested) and no answer.

What is sent for the attempt that goes out: its `start`, then one `metrics` per earlier attempt
(their tokens, with `attempt`), one `metrics` per scoring (the evaluator's own tokens, cost, and
latency, `model_route` = the evaluator's route, e.g. the judge route), then the rest of its events,
each `metrics` with its `attempt`. So the response's `metrics` count every attempt and the gate,
`attempts` is the number of attempts, and `model_route` is the route of the answer.

An attempt that ends with an `error` event is sent as it is, not scored and not retried. A model
error in particular is never retried here: the model router already retried it and took its
fallback route (ADR-013). An evaluator outage (`EvaluatorError`) follows
`spec.harness.evaluator_on_error` (reloadable, read per call). `fail_open` (the default,
suggested): the attempt goes out as it is, counted as `chassis.gate_failed_open` and logged.
`fail_closed`: the run ends with its `start`, the `metrics` so far, and one
`error {code: evaluator_gate_failed, retryable: false}`; the attempt's other events are never
sent (as for a failed gate), and it is counted as `chassis.gate_failed_closed`. The code and its
`retryable: false` match contract v6, so the public error mapping already knows it. A new,
retryable `evaluator_unavailable` code is left for the contract owner.

**Async mode** (`evaluator_mode: async`, and every stream in either mode). Events pass through
untouched. When the `end` event passes, the answer is scored in a background task (at most
`ASYNC_MAX_PENDING` at once, suggested; one more is dropped and counted as
`chassis.evaluator_dropped`). The score never changes the answer: it goes on a
`chassis.evaluator.score` span (`evaluator.score`, `evaluator.passed`) and the counter. Its cost is
not in the response's `metrics` (they are gone). `close()` waits for pending scores up to
`SHUTDOWN_WAIT_S` (suggested), cancels the rest (`chassis.evaluator_abandoned`), then closes the
inner connector.

**Counters** (suggested names): `chassis.evaluator_calls{evaluator, mode, outcome}` with outcome
`pass`, `fail`, or `error`; `chassis.gate_retries`; `chassis.gate_fallbacks`;
`chassis.gate_failed_open`; `chassis.gate_failed_closed`; `chassis.gate_stopped{reason}`;
`chassis.gate_runs{mode}` (PoC-8), each sampled call, so the fallback rate is
`chassis.gate_fallbacks` over `chassis.gate_runs{mode="sync"}`.

**One budget for every attempt** (017 H-4, `Allowance`). The run keeps one `budget.timeout_ms`
and one `budget.max_tokens` across its attempts and the fallback: each attempt runs with
`request.budget` and `ctx.budget` set to what is left (the A2A connector's run deadline reads the
first, a workload the second), and the judge's tokens count against it (charged to the run's
`RunRecord`, so the model proxy also gives the next attempt only what is left). A rerun (a retry
or the fallback) starts only with time and tokens left; else the run ends with the attempt's
`start`, the `metrics` so far, and one `error`: `a2a.timeout` (retryable) with no time left,
`budget_exhausted` (not retryable) with no tokens left, counted as
`chassis.gate_stopped{reason}` (`timeout` or `budget`, suggested).

A rerun reruns the agent, tool calls included; write tools are deduped by the run's idempotency
key (the record's `keyed_run_key` is the same for every attempt).
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import hashlib
import json
import secrets
import time
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Mapping
from typing import TYPE_CHECKING, Any

from chassis.core.envelope import Budget, Context, Request
from chassis.core.events import Delta, End, Error, Event, Metrics, Start
from chassis.core.trace import trace_id_hex
from chassis.ports.engine import EngineConnector, Lane
from chassis.ports.evaluator import EvalCase, EvaluatorError, EvaluatorPort, NoEvaluator, Score
from chassis.ports.model import Usage
from chassis.ports.telemetry import TelemetryPort

if TYPE_CHECKING:
    from chassis.ports.bundle import PortBundle
    from chassis.server.config import ChassisConfig
    from chassis.server.correlation import RunRecord, RunRegistry

__all__ = [
    "ASYNC_MAX_PENDING",
    "EVALUATOR_CALLS",
    "GATE_BUDGET",
    "GATE_FAILED",
    "GATE_FAILED_CLOSED",
    "GATE_FAILED_OPEN",
    "GATE_FALLBACKS",
    "GATE_RETRIES",
    "GATE_RUNS",
    "GATE_STOPPED",
    "GATE_TIMEOUT",
    "SALT_BYTES",
    "SCORE_SPAN",
    "SHUTDOWN_WAIT_S",
    "Allowance",
    "GateNotConfigured",
    "GatedEngine",
    "sampled",
    "with_gate",
]

EVALUATOR_CALLS = "chassis.evaluator_calls"
GATE_RETRIES = "chassis.gate_retries"
GATE_FALLBACKS = "chassis.gate_fallbacks"
GATE_RUNS = "chassis.gate_runs"
GATE_FAILED_OPEN = "chassis.gate_failed_open"
GATE_FAILED_CLOSED = "chassis.gate_failed_closed"
EVALUATOR_DROPPED = "chassis.evaluator_dropped"
EVALUATOR_ABANDONED = "chassis.evaluator_abandoned"
SCORE_SPAN = "chassis.evaluator.score"
GATE_FAILED = "evaluator_gate_failed"
"""The error code of a run whose every attempt failed the gate and that has no fallback route."""
GATE_STOPPED = "chassis.gate_stopped"
"""suggested: a rerun the gate did not start, by `reason` (`timeout` or `budget`)."""
GATE_TIMEOUT = "a2a.timeout"
"""The error code of a run with no time left for a rerun: the lane's own timeout code (504)."""
GATE_BUDGET = "budget_exhausted"
"""The error code of a run with no tokens left for a rerun: the model proxy's own code."""
ASYNC_MAX_PENDING = 64
"""suggested: background scores in flight per replica; one more is dropped and counted."""
SHUTDOWN_WAIT_S = 5.0
"""suggested: how long `close()` waits for pending background scores."""
SALT_BYTES = 32
"""The size of the per-process sampling salt."""


class GateNotConfigured(RuntimeError):
    """`spec.harness.evaluator_gate` is on but `spec.adapters.evaluator` is `none`."""


def sampled(request_id: str, rate: float, salt: bytes) -> bool:
    """Is this call gated? The first 64 bits of sha256(salt + request_id), as a fraction, below
    `rate`. The same salt and request id always decide the same."""
    if rate >= 1.0:
        return True
    if rate <= 0.0:
        return False
    digest = hashlib.sha256(salt + request_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / 2**64 < rate


def _input_text(request: Request) -> str:
    if request.input.text is not None:
        return request.input.text
    return json.dumps(request.input.data, sort_keys=True)


def _answer(events: list[Event]) -> str:
    text = "".join(e.text for e in events if isinstance(e, Delta))
    if text:
        return text
    end = next((e for e in events if isinstance(e, End)), None)
    if end is not None and end.output:
        value = end.output.get("text")
        return value if isinstance(value, str) else json.dumps(end.output, sort_keys=True)
    return ""


def _failed(events: list[Event]) -> bool:
    return any(isinstance(e, Error) for e in events) or not any(isinstance(e, End) for e in events)


class GatedEngine:
    """The gate around `inner`; see the module docstring."""

    def __init__(
        self,
        inner: EngineConnector,
        evaluator: EvaluatorPort,
        *,
        config: Callable[[], ChassisConfig],
        telemetry: TelemetryPort,
        runs: RunRegistry | None = None,
        max_pending: int = ASYNC_MAX_PENDING,
        salt: bytes | None = None,
    ) -> None:
        """`salt` is for tests; unset, a fresh secret salt is drawn for this process."""
        self.inner = inner
        self.kind: Lane = inner.kind
        self.capabilities: frozenset[str] = inner.capabilities
        self.evaluator = evaluator
        self._config = config
        self._telemetry = telemetry
        self._runs = runs
        self._max_pending = max_pending
        self._pending: set[asyncio.Task[None]] = set()
        self._salt = secrets.token_bytes(SALT_BYTES) if salt is None else salt

    # --- EngineConnector, delegated ---

    async def setup(self, config: Mapping[str, Any], ports: PortBundle) -> None:
        await self.inner.setup(config, ports)

    async def probe(self) -> bool:
        return await self.inner.probe()

    async def close(self) -> None:
        left = await self.wait(SHUTDOWN_WAIT_S)
        if left:
            tasks = [t for t in self._pending if not t.done()]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self._telemetry.counter(EVALUATOR_ABANDONED, len(tasks))
        await self.inner.close()

    async def wait(self, timeout_s: float) -> int:
        """Wait for pending background scores up to `timeout_s`; how many are still pending."""
        tasks = set(self._pending)
        if tasks:
            await asyncio.wait(tasks, timeout=timeout_s)
        return sum(1 for t in tasks if not t.done())

    @property
    def pending(self) -> int:
        return len(self._pending)

    # --- The gate ---

    @property
    def route(self) -> str:
        """The `model_route` of the evaluator's own `metrics`."""
        route = getattr(self.evaluator, "route", None)
        return str(route) if route else f"evaluator:{self.evaluator.name}"

    def run(self, request: Request, ctx: Context) -> AsyncIterator[Event]:
        config = self._config()
        harness = config.spec.harness
        if not sampled(request.request_id, harness.evaluator_sample_rate, self._salt):
            return self._passthrough(request, ctx, score=False)
        if request.stream or harness.evaluator_mode == "async":
            self._telemetry.counter(GATE_RUNS, mode="async")
            return self._passthrough(request, ctx, score=True)
        self._telemetry.counter(GATE_RUNS, mode="sync")
        return self._sync(request, ctx)

    async def _passthrough(
        self, request: Request, ctx: Context, *, score: bool
    ) -> AsyncGenerator[Event]:
        text: list[str] = []
        failed = False
        stream = self.inner.run(request, ctx)
        try:
            async for event in stream:
                if score:
                    if isinstance(event, Delta):
                        text.append(event.text)
                    elif isinstance(event, Error):
                        failed = True
                    elif isinstance(event, End) and not failed:
                        answer = "".join(text) or _answer([event])
                        self._score_later(request, ctx, answer)
                yield event
        finally:
            await _aclose(stream)

    def _score_later(self, request: Request, ctx: Context, answer: str) -> None:
        if len(self._pending) >= self._max_pending:
            self._telemetry.counter(EVALUATOR_DROPPED)
            return
        case = EvalCase(input=_input_text(request), output=answer)
        task = asyncio.create_task(
            self._score_async(request, ctx, case), name=f"evaluator:{request.request_id}"
        )
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _score_async(self, request: Request, ctx: Context, case: EvalCase) -> None:
        with contextlib.suppress(EvaluatorError):  # counted and logged in `_score`
            await self._score(request, ctx, case, mode="async", attempt=1)

    async def _score(
        self, request: Request, ctx: Context, case: EvalCase, *, mode: str, attempt: int
    ) -> Score:
        """One scoring in its span. Raises `EvaluatorError` after counting it."""
        threshold = self._config().spec.harness.threshold
        evaluator = self.evaluator.name
        with self._telemetry.span(
            SCORE_SPAN,
            request_id=request.request_id,
            trace_id=ctx.trace_id,
            evaluator=evaluator,
            mode=mode,
            attempt=attempt,
        ) as span:
            try:
                score = await self.evaluator.score(case)
            except EvaluatorError as exc:
                span.attributes["error.code"] = exc.code
                self._telemetry.counter(
                    EVALUATOR_CALLS, evaluator=evaluator, mode=mode, outcome="error"
                )
                self._telemetry.log(
                    "warning",
                    "evaluator failed",
                    request_id=request.request_id,
                    code=exc.code,
                    mode=mode,
                )
                raise
            passed = score.value >= threshold
            span.attributes.update(
                {
                    "evaluator.score": score.value,
                    "evaluator.passed": passed,
                    "evaluator.threshold": threshold,
                    "evaluator.latency_ms": score.latency_ms,
                }
            )
            self._telemetry.counter(
                EVALUATOR_CALLS,
                evaluator=evaluator,
                mode=mode,
                outcome="pass" if passed else "fail",
            )
        return score

    async def _attempt(self, request: Request, ctx: Context) -> list[Event]:
        stream = self.inner.run(request, ctx)
        try:
            return [event async for event in stream]
        finally:
            await _aclose(stream)

    def _cost_metrics(self, score: Score, attempt: int) -> Metrics:
        return Metrics(
            input_tokens=score.input_tokens,
            output_tokens=score.output_tokens,
            cost_usd=score.cost_usd,
            model_route=self.route,
            latency_ms=round(score.latency_ms),
            attempt=attempt,
        )

    async def _sync(self, request: Request, ctx: Context) -> AsyncGenerator[Event]:
        config = self._config()
        harness, fallback = config.spec.harness, config.spec.model.fallback_route
        record = self._runs.lookup(trace_id_hex(ctx.trace_id)) if self._runs else None
        allowance = Allowance(request.budget, record)
        before: list[Event] = []  # metrics of earlier attempts and of each scoring
        input_text = _input_text(request)
        events: list[Event] = []
        attempts = harness.retries + 1
        for attempt in range(1, attempts + 1):
            if attempt > 1:  # a retry starts only with time and tokens left
                if (stop := self._stopped(allowance, events, before)) is not None:
                    for event in stop:
                        yield event
                    return
                self._telemetry.counter(GATE_RETRIES)
            events = await self._attempt(*allowance.bounded(request, ctx))
            allowance.spend(_tokens(events))
            if _failed(events):
                for event in _emit(events, attempt, before):
                    yield event
                return
            case = EvalCase(input=input_text, output=_answer(events))
            try:
                score = await self._score(request, ctx, case, mode="sync", attempt=attempt)
            except EvaluatorError:
                if harness.evaluator_on_error == "fail_closed":
                    self._telemetry.counter(GATE_FAILED_CLOSED)
                    before.extend(_metrics_of(events, attempt))
                    message = f"the evaluator failed on attempt {attempt}; failing closed"
                    for event in _closing(events, before, message):
                        yield event
                    return
                self._telemetry.counter(GATE_FAILED_OPEN)
                for event in _emit(events, attempt, before):
                    yield event
                return
            allowance.charge_judge(score)
            before.append(self._cost_metrics(score, attempt))
            if score.value >= harness.threshold:
                for event in _emit(events, attempt, before):
                    yield event
                return
            before.extend(_metrics_of(events, attempt))
        if fallback is None:
            message = f"the answer scored below {harness.threshold} on {attempts} attempts"
            for event in _closing(events, before, message):
                yield event
            return
        if (stop := self._stopped(allowance, events, before)) is not None:
            for event in stop:
                yield event
            return
        self._telemetry.counter(GATE_FALLBACKS)
        if record is not None:
            record.model_route = fallback
        fallback_ctx = ctx.model_copy(update={"model_route": fallback})
        events = await self._attempt(*allowance.bounded(request, fallback_ctx))
        for event in _emit(events, attempts + 1, before, status="fallback"):
            yield event

    def _stopped(
        self, allowance: Allowance, events: list[Event], before: list[Event]
    ) -> list[Event] | None:
        """The closing events when the run has no time or no tokens left for a rerun, else
        None. Counted as `chassis.gate_stopped{reason}`."""
        reason = allowance.spent_reason()
        if reason is None:
            return None
        self._telemetry.counter(GATE_STOPPED, reason=reason)
        if reason == "timeout":
            message = "the run has no time left for another attempt"
            return _closing(events, before, message, code=GATE_TIMEOUT, retryable=True)
        message = "the run has no tokens left for another attempt"
        return _closing(events, before, message, code=GATE_BUDGET, retryable=False)


class Allowance:
    """One run's `budget` across the gate's attempts (017 H-4): one `timeout_ms` and one
    `max_tokens` for all of them, the judge's tokens included.

    Time is what the run has left: `RunRecord.remaining_s` when the run is registered (from the
    run's start), else `timeout_ms` from when the allowance was made. Tokens are `max_tokens` less
    the larger of two counts of the same spend: the `metrics` tokens of each attempt plus each
    judge call (`spend`), and the record's charged tokens (the model proxy's calls, and the judge
    calls `charge_judge` adds, since the judge calls `ModelPort` directly, not the proxy). The
    larger count, never their sum, so a proxy call that a workload also reports is not counted
    twice.
    """

    def __init__(self, budget: Budget, record: RunRecord | None) -> None:
        self.budget = budget
        self.record = record
        self.started = time.monotonic()
        self.spent = 0

    def spend(self, tokens: int) -> None:
        """Count tokens the gate saw: an attempt's `metrics`."""
        self.spent += max(tokens, 0)

    def charge_judge(self, score: Score) -> None:
        """Count one scoring's tokens, and charge them to the run's record, so the model proxy
        gives the next attempt only what is left. An evaluator with no tokens (the encoder, the
        fake) charges nothing."""
        tokens = score.input_tokens + score.output_tokens
        if tokens <= 0:
            return
        self.spend(tokens)
        if self.record is not None:
            self.record.charge(
                Usage(input_tokens=score.input_tokens, output_tokens=score.output_tokens)
            )

    def left_ms(self) -> int:
        if self.record is not None:
            return int(self.record.remaining_s() * 1000)
        elapsed_ms = (time.monotonic() - self.started) * 1000
        return max(0, int(self.budget.timeout_ms - elapsed_ms))

    def left_tokens(self) -> int:
        left = self.budget.max_tokens - self.spent
        if self.record is not None:
            left = min(left, self.record.remaining_tokens)
        return max(0, left)

    def spent_reason(self) -> str | None:
        """`timeout` or `budget` when nothing is left for another attempt, else None."""
        if self.left_ms() < 1:
            return "timeout"
        if self.left_tokens() < 1:
            return "budget"
        return None

    def bounded(self, request: Request, ctx: Context) -> tuple[Request, Context]:
        """The request and context of the next attempt, with `budget` set to what is left: the
        A2A connector's run deadline reads `request.budget`, a workload reads `ctx.budget`."""
        budget = Budget(max_tokens=max(self.left_tokens(), 1), timeout_ms=max(self.left_ms(), 1))
        return (
            request.model_copy(update={"budget": budget}),
            ctx.model_copy(update={"budget": budget}),
        )


def _tokens(events: list[Event]) -> int:
    return sum(e.input_tokens + e.output_tokens for e in events if isinstance(e, Metrics))


def _metrics_of(events: list[Event], attempt: int) -> list[Event]:
    return [e.model_copy(update={"attempt": attempt}) for e in events if isinstance(e, Metrics)]


def _closing(
    events: list[Event],
    before: list[Event],
    message: str,
    *,
    code: str = GATE_FAILED,
    retryable: bool = False,
) -> list[Event]:
    """A run the gate ends with no answer: the attempt's `start`, `before`, and one
    `error {code}` (`evaluator_gate_failed` by default). The attempt's other events are not
    sent."""
    start = [e for e in events if isinstance(e, Start)][:1]
    return [*start, *before, Error(code=code, message=message, retryable=retryable)]


def _emit(
    events: list[Event], attempt: int, before: list[Event], status: str | None = None
) -> list[Event]:
    """The attempt's events with `before` right after its `start`, each `metrics` with its
    attempt, and the `end` with `status` when given."""
    out: list[Event] = []
    placed = False
    for event in events:
        if isinstance(event, Metrics):
            event = event.model_copy(update={"attempt": attempt})
        elif isinstance(event, End) and status is not None:
            event = event.model_copy(update={"status": status})
        out.append(event)
        if isinstance(event, Start) and not placed:
            out.extend(before)
            placed = True
    return out if placed else [*before, *out]


async def _aclose(stream: AsyncIterator[Event]) -> None:
    closer = getattr(stream, "aclose", None)
    if closer is not None:
        await closer()


def with_gate(bundle: PortBundle, state: Any) -> PortBundle:
    """The bundle with its engine behind the gate when `spec.harness.evaluator_gate` is on (read
    from `state.config`, the first config), else the same bundle. Raises `GateNotConfigured` when
    the gate is on and the evaluator is `NoEvaluator`."""
    config: ChassisConfig = state.config
    if not config.spec.harness.evaluator_gate:
        return bundle
    if isinstance(bundle.evaluator, NoEvaluator):
        raise GateNotConfigured(
            "spec.harness.evaluator_gate is on, but spec.adapters.evaluator is none"
        )
    # The judge takes `spec.model.judge_route` from the live config, so a reload applies.
    # suggested: `bind_route` by name, outside `EvaluatorPort`; the judge is the only adapter
    # with a route the config names. A second one moves the route into the port.
    bind_route = getattr(bundle.evaluator, "bind_route", None)
    if callable(bind_route):
        bind_route(lambda: state.config.spec.model.judge_route)
    gate = GatedEngine(
        bundle.engine,
        bundle.evaluator,
        config=lambda: state.config,
        telemetry=bundle.telemetry,
        runs=state.runs,
    )
    return dataclasses.replace(bundle, engine=gate)
