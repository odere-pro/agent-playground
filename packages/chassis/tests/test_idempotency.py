"""The idempotency core (PoC-4 P6, 018 H-18): claims, replays, conflicts, waits, and lease
takeover over `InMemoryState` with a fake clock. No HTTP; the wiring into `serve` is P11.

Two "replicas" are two `Idempotency` objects with their own replica id over one shared
`InMemoryState`, as two chassis share one Valkey.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Sequence

import pytest
from chassis.core.envelope import Budget, Request, Response, TaskInput, Versions
from chassis.core.events import Delta, End, Error, Event, Metrics, Start
from chassis.core.inbound import PUBLIC_MESSAGES
from chassis.fakes import InMemoryState, InMemoryTelemetry
from chassis.ports.state import StateUnavailable
from chassis.server.config import IdempotencySpec
from chassis.server.idempotency import (
    MIN_RUN_MS,
    Claim,
    Idempotency,
    Refusal,
    Replay,
    fingerprint,
    store_key,
)

VERSIONS = Versions(chassis="c", config="cfg", prompt="p", model_route="route")
SPEC = IdempotencySpec(ttl_s=60, lease_s=6, wait_poll_ms=100, max_entry_bytes=4096)


class Clock:
    """A fake monotonic clock. `sleep` moves it forward and yields once to the loop."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds

    async def sleep(self, seconds: float) -> None:
        self.advance(seconds)
        await asyncio.sleep(0)


class Ticker:
    """The renew task's sleep: it returns only when the test calls `tick`. Never ticked, the
    holder stands for a killed replica that stopped renewing."""

    def __init__(self) -> None:
        self._ticks: asyncio.Queue[None] = asyncio.Queue()
        self.waits: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.waits.append(seconds)
        await self._ticks.get()

    async def tick(self) -> None:
        self._ticks.put_nowait(None)
        for _ in range(5):
            await asyncio.sleep(0)


async def _yield_only(_seconds: float) -> None:
    await asyncio.sleep(0)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def state(clock: Clock) -> InMemoryState:
    return InMemoryState(clock=clock)


@pytest.fixture
def telemetry() -> InMemoryTelemetry:
    return InMemoryTelemetry()


@pytest.fixture
def ticker() -> Ticker:
    return Ticker()


def _replica(
    name: str,
    state: InMemoryState,
    clock: Clock,
    telemetry: InMemoryTelemetry,
    ticker: Ticker,
    spec: IdempotencySpec = SPEC,
) -> Idempotency:
    return Idempotency(
        state,
        spec,
        telemetry,
        replica_id=name,
        clock=clock,
        sleep=clock.sleep,
        renew_sleep=ticker.sleep,
    )


@pytest.fixture
def a(
    state: InMemoryState, clock: Clock, telemetry: InMemoryTelemetry, ticker: Ticker
) -> Idempotency:
    return _replica("replica-a", state, clock, telemetry, ticker)


@pytest.fixture
def b(
    state: InMemoryState, clock: Clock, telemetry: InMemoryTelemetry, ticker: Ticker
) -> Idempotency:
    return _replica("replica-b", state, clock, telemetry, ticker)


def _request(
    text: str = "hello",
    *,
    request_id: str = "r1",
    trace_id: str = "t1",
    key: str = "key-1",
    agent: str = "echo",
    stream: bool = False,
    timeout_ms: int = 30_000,
    context_ref: str | None = None,
    agent_version: str = "0.0.1",
) -> Request:
    return Request(
        request_id=request_id,
        trace_id=trace_id,
        idempotency_key=key,
        agent=agent,
        agent_version=agent_version,
        input=TaskInput(text=text),
        context_ref=context_ref,
        stream=stream,
        budget=Budget(timeout_ms=timeout_ms),
    )


def _events(request: Request, text: str = "HELLO") -> list[Event]:
    return [
        Start(request_id=request.request_id),
        Delta(text=text),
        Metrics(input_tokens=3, output_tokens=2),
        End(output={"text": text}),
    ]


def _response(request: Request, text: str = "HELLO") -> Response:
    return Response(
        request_id=request.request_id,
        trace_id=request.trace_id,
        idempotency_key=request.idempotency_key,
        agent=request.agent,
        agent_version=request.agent_version,
        output={"text": text},
        metrics={"input_tokens": 3, "output_tokens": 2},
        status="ok",
        versions=VERSIONS,
    )


async def _claim(idem: Idempotency, request: Request, wait_ms: int = 0) -> Claim:
    outcome = await idem.begin(request, wait_ms=wait_ms)
    assert isinstance(outcome, Claim), outcome
    return outcome


async def _finish(claim: Claim, request: Request, events: Sequence[Event] | None = None) -> bool:
    return await claim.finish(_events(request) if events is None else events, _response(request))


# The key and the fingerprint


def test_the_store_key_is_scoped_by_agent_and_hashed() -> None:
    odd = "a key with spaces / and : colons " * 40
    key = store_key("echo", odd)
    assert key == "chassis:idem:v1:echo:" + hashlib.sha256(odd.encode()).hexdigest()
    assert odd not in key and len(key) < 100
    assert store_key("echo", "k") != store_key("other", "k")


def test_the_fingerprint_covers_agent_input_and_context_ref_only() -> None:
    base = fingerprint(_request())
    same = _request(
        request_id="r9",
        trace_id="t9",
        key="key-9",
        stream=True,
        timeout_ms=5,
        agent_version="9.9.9",
    )
    assert fingerprint(same) == base
    assert fingerprint(_request("other")) != base
    assert fingerprint(_request(context_ref="ctx-1")) != base
    assert fingerprint(_request(agent="other")) != base
    canonical = json.dumps(
        {"agent": "echo", "context_ref": None, "input": {"text": "hello", "data": {}}},
        sort_keys=True,
        separators=(",", ":"),
    )
    assert base == hashlib.sha256(canonical.encode()).hexdigest()


@pytest.mark.parametrize(
    ("enabled", "key_from", "applies"),
    [
        (True, "header", True),
        (True, "body", True),
        (True, "minted", False),
        (False, "header", False),
        (False, "body", False),
    ],
)
def test_it_applies_only_to_a_key_the_client_sent(
    state: InMemoryState, telemetry: InMemoryTelemetry, enabled: bool, key_from: str, applies: bool
) -> None:
    idem = Idempotency(state, IdempotencySpec(enabled=enabled), telemetry)
    assert idem.applies(key_from) is applies


# Claim, finish, replay


async def test_the_first_call_claims_a_lease_that_expires(
    a: Idempotency, state: InMemoryState, clock: Clock
) -> None:
    request = _request()
    claim = await _claim(a, request)
    key = store_key("echo", "key-1")
    assert claim.key == key and claim.request == request
    entry = json.loads(await state.get(key) or b"")
    assert entry["v"] == 1 and entry["state"] == "running"
    assert entry["owner"].startswith("replica-a:")
    assert entry["fingerprint"] == fingerprint(request)
    clock.advance(SPEC.lease_s)
    assert await state.get(key) is None
    await claim.release()


async def test_a_repeated_key_replays_the_same_result_on_another_replica(
    a: Idempotency, b: Idempotency, state: InMemoryState
) -> None:
    first = _request()
    claim = await _claim(a, first)
    assert await _finish(claim, first) is True

    retry = _request(request_id="r2", trace_id="t2", stream=True, timeout_ms=1000)
    outcome = await b.begin(retry, wait_ms=1000)
    assert isinstance(outcome, Replay)
    assert outcome.request == first
    assert outcome.response == _response(first)
    assert list(outcome.events) == _events(first)
    entry = json.loads(await state.get(claim.key) or b"")
    assert entry["state"] == "done" and "owner" not in entry


async def test_a_cached_result_lives_ttl_s(a: Idempotency, b: Idempotency, clock: Clock) -> None:
    request = _request()
    assert await _finish(await _claim(a, request), request)
    clock.advance(SPEC.ttl_s - 1)
    assert isinstance(await b.begin(request, wait_ms=0), Replay)
    clock.advance(1)
    claim = await _claim(b, request)
    await claim.release()


async def test_same_key_other_input_is_a_conflict_while_running_and_when_done(
    a: Idempotency, b: Idempotency, telemetry: InMemoryTelemetry
) -> None:
    request = _request()
    claim = await _claim(a, request)
    running = await b.begin(_request("other"), wait_ms=5000)
    assert running == Refusal("idempotency_conflict")
    await _finish(claim, request)
    done = await b.begin(_request("other"), wait_ms=5000)
    assert done == Refusal("idempotency_conflict")
    assert done.status == 422 and done.retryable is False
    assert done.message == PUBLIC_MESSAGES["idempotency_conflict"]
    assert telemetry.counter_value("chassis.idempotency.refused", code="idempotency_conflict") == 2


@pytest.mark.parametrize(
    "events",
    [
        [Start(request_id="r1"), Error(code="engine_error", message="boom", retryable=True)],
        [Start(request_id="r1"), Delta(text="half")],
        [],
    ],
    ids=["error", "closed_early", "no_events"],
)
async def test_a_run_without_an_end_is_not_cached(
    a: Idempotency,
    b: Idempotency,
    state: InMemoryState,
    telemetry: InMemoryTelemetry,
    events: list[Event],
) -> None:
    request = _request()
    claim = await _claim(a, request)
    assert await claim.finish(events, _response(request)) is False
    assert await state.get(claim.key) is None
    assert telemetry.counter_value("chassis.idempotency.not_cached", reason="no_end") == 1
    again = await _claim(b, request)
    await again.release()


async def test_a_result_over_max_entry_bytes_is_not_cached(
    state: InMemoryState, clock: Clock, telemetry: InMemoryTelemetry, ticker: Ticker
) -> None:
    small = _replica("a", state, clock, telemetry, ticker, IdempotencySpec(max_entry_bytes=200))
    request = _request()
    claim = await _claim(small, request)
    assert await _finish(claim, request) is False
    entry = json.loads(await state.get(claim.key) or b"")
    assert entry["state"] == "too_large" and "events" not in entry  # a small marker, for ttl_s
    assert telemetry.counter_value("chassis.idempotency.not_cached", reason="too_large") == 1
    # A repeat with the same key and input is refused for good, never run a second time.
    again = await small.begin(_request(request_id="r2"), wait_ms=1000)
    assert isinstance(again, Refusal) and again.code == "idempotency_in_progress"
    assert again.status == 409 and again.retryable is False
    assert "too large" in again.message
    # Another input with the key is still a conflict.
    other = await small.begin(_request("other", request_id="r3"), wait_ms=0)
    assert other == Refusal("idempotency_conflict")


async def test_release_frees_the_key_and_finish_and_release_run_once(
    a: Idempotency, b: Idempotency, state: InMemoryState
) -> None:
    request = _request()
    claim = await _claim(a, request)
    await claim.release()
    assert await state.get(claim.key) is None
    calls = len(state.calls)
    await claim.release()
    assert await _finish(claim, request) is False
    assert len(state.calls) == calls

    second = await _claim(b, request)
    assert await _finish(second, request) is True
    assert await _finish(second, request) is False
    await second.release()
    assert isinstance(await a.begin(request, wait_ms=0), Replay)


# A duplicate in flight


async def test_a_duplicate_waits_for_the_first_result_then_replays(
    a: Idempotency, b: Idempotency, clock: Clock, telemetry: InMemoryTelemetry
) -> None:
    request = _request()
    claim = await _claim(a, request)
    start = clock.now
    waiter = asyncio.create_task(b.begin(_request(request_id="r2"), wait_ms=3000))
    for _ in range(5):
        await asyncio.sleep(0)
    assert not waiter.done()
    await _finish(claim, request)
    outcome = await waiter
    assert isinstance(outcome, Replay) and outcome.response == _response(request)
    assert 0 < clock.now - start < 3
    assert telemetry.counter_value("chassis.idempotency.waited") == 1


async def test_a_duplicate_past_its_own_timeout_is_in_progress(
    a: Idempotency, b: Idempotency, clock: Clock
) -> None:
    request = _request()
    claim = await _claim(a, request)
    start = clock.now
    outcome = await b.begin(_request(request_id="r2", timeout_ms=1500), wait_ms=1500)
    assert outcome == Refusal("idempotency_in_progress")
    assert outcome.status == 409 and outcome.retryable is True
    assert clock.now - start == pytest.approx(1.5)
    assert await b.begin(request, wait_ms=0) == Refusal("idempotency_in_progress")
    await claim.release()


async def test_concurrent_duplicates_on_two_replicas_run_once(
    state: InMemoryState, clock: Clock, telemetry: InMemoryTelemetry, ticker: Ticker
) -> None:
    replicas = [
        Idempotency(
            state,
            SPEC,
            telemetry,
            replica_id=f"r{i}",
            clock=clock,
            sleep=_yield_only,
            renew_sleep=ticker.sleep,
        )
        for i in range(2)
    ]
    request = _request()
    tasks = [
        asyncio.create_task(replicas[i % 2].begin(_request(request_id=f"r{i}"), wait_ms=10_000))
        for i in range(10)
    ]
    for _ in range(5):
        await asyncio.sleep(0)
    claims = [t.result() for t in tasks if t.done() and isinstance(t.result(), Claim)]
    assert len(claims) == 1
    claim = claims[0]
    assert isinstance(claim, Claim)
    await claim.finish(_events(claim.request), _response(claim.request))
    outcomes = await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)
    replays = [o for o in outcomes if isinstance(o, Replay)]
    assert len(replays) == 9
    assert all(r.response == _response(claim.request) for r in replays)
    assert request.idempotency_key == claim.request.idempotency_key


# The lease


async def test_a_live_holder_renews_its_lease(
    a: Idempotency, b: Idempotency, state: InMemoryState, clock: Clock, ticker: Ticker
) -> None:
    request = _request()
    claim = await _claim(a, request)
    for _ in range(6):
        clock.advance(SPEC.lease_s / 3)
        await ticker.tick()
    assert clock.now > 1000 + SPEC.lease_s
    assert ticker.waits and set(ticker.waits) == {SPEC.lease_s / 3}
    assert claim.lost is False
    assert await b.begin(_request(request_id="r2"), wait_ms=0) == Refusal("idempotency_in_progress")
    assert await _finish(claim, request) is True


async def test_a_killed_holder_is_taken_over_after_its_lease(
    a: Idempotency,
    b: Idempotency,
    state: InMemoryState,
    clock: Clock,
    telemetry: InMemoryTelemetry,
) -> None:
    request = _request()
    dead = await _claim(a, request)  # replica-a dies here: its renew task is never ticked
    start = clock.now
    retry = _request(request_id="r2", trace_id="t2")
    outcome = await b.begin(retry, wait_ms=30_000)
    assert isinstance(outcome, Claim)
    assert SPEC.lease_s - 0.01 <= clock.now - start < SPEC.lease_s + 0.2
    assert telemetry.counter_value("chassis.idempotency.taken_over") == 1
    entry = json.loads(await state.get(outcome.key) or b"")
    assert entry["owner"].startswith("replica-b:")

    assert await _finish(dead, request) is False
    assert dead.lost is True
    assert telemetry.counter_value("chassis.idempotency.lease_lost") == 1
    assert await _finish(outcome, retry) is True
    replay = await a.begin(request, wait_ms=0)
    assert isinstance(replay, Replay) and replay.request == retry


async def test_a_renew_after_a_takeover_loses_the_lease(
    a: Idempotency, b: Idempotency, clock: Clock, telemetry: InMemoryTelemetry
) -> None:
    request = _request()
    old = await _claim(a, request)
    clock.advance(SPEC.lease_s)
    new = await _claim(b, request)
    assert await old.renew() is False
    assert old.lost is True
    await old.release()
    assert telemetry.counter_value("chassis.idempotency.lease_lost") == 1
    assert await _finish(new, request) is True


# The store fails


async def test_a_store_failure_in_begin_is_state_unavailable(
    a: Idempotency, state: InMemoryState, telemetry: InMemoryTelemetry
) -> None:
    state.fail_next()
    outcome = await a.begin(_request(), wait_ms=1000)
    assert outcome == Refusal("state_unavailable")
    assert outcome.status == 503 and outcome.retryable is True
    assert outcome.message == PUBLIC_MESSAGES["state_unavailable"]
    assert telemetry.counter_value("chassis.idempotency.refused", code="state_unavailable") == 1


async def test_a_store_failure_while_waiting_is_state_unavailable(
    a: Idempotency, b: Idempotency, state: InMemoryState
) -> None:
    request = _request()
    claim = await _claim(a, request)
    waiter = asyncio.create_task(b.begin(_request(request_id="r2"), wait_ms=3000))
    await asyncio.sleep(0)
    state.fail_next()
    assert await waiter == Refusal("state_unavailable")
    await claim.release()


async def test_an_unreadable_entry_is_state_unavailable(
    a: Idempotency, state: InMemoryState, telemetry: InMemoryTelemetry
) -> None:
    await state.set(store_key("echo", "key-1"), b"\x00not json", ttl_s=10)
    assert await a.begin(_request(), wait_ms=0) == Refusal("state_unavailable")
    assert telemetry.logs and "key-1" not in json.dumps(telemetry.logs, default=str)


async def test_a_store_failure_in_finish_is_counted_never_raised(
    a: Idempotency, state: InMemoryState, telemetry: InMemoryTelemetry
) -> None:
    request = _request()
    claim = await _claim(a, request)
    state.fail_next()
    assert await _finish(claim, request) is False
    assert telemetry.counter_value("chassis.idempotency.finish_failed") == 1


async def test_a_store_failure_in_renew_keeps_the_claim(
    a: Idempotency, state: InMemoryState, telemetry: InMemoryTelemetry
) -> None:
    request = _request()
    claim = await _claim(a, request)
    state.fail_next()
    assert await claim.renew() is True
    assert claim.lost is False
    assert telemetry.counter_value("chassis.idempotency.renew_failed") == 1
    assert await claim.renew() is True
    await claim.release()


# The fence: a holder cut off from the store stops before its lease can pass to another replica


class _CutOff:
    """One replica's view of the shared store. While `cut`, every call raises
    `StateUnavailable`, as a replica that lost its network path to Valkey sees it."""

    def __init__(self, inner: InMemoryState) -> None:
        self.inner = inner
        self.cut = False

    def _check(self) -> None:
        if self.cut:
            raise StateUnavailable("cut off")

    async def get(self, key: str) -> bytes | None:
        self._check()
        return await self.inner.get(key)

    async def set(self, key: str, value: bytes, *, ttl_s: float | None = None) -> None:
        self._check()
        await self.inner.set(key, value, ttl_s=ttl_s)

    async def set_if_absent(self, key: str, value: bytes, *, ttl_s: float) -> bool:
        self._check()
        return await self.inner.set_if_absent(key, value, ttl_s=ttl_s)

    async def compare_and_set(
        self, key: str, expected: bytes, value: bytes | None, *, ttl_s: float | None = None
    ) -> bool:
        self._check()
        return await self.inner.compare_and_set(key, expected, value, ttl_s=ttl_s)

    async def delete(self, key: str) -> None:
        self._check()
        await self.inner.delete(key)

    async def aclose(self) -> None:
        return None


async def test_a_holder_cut_off_from_the_store_is_fenced_before_its_lease_ends(
    b: Idempotency,
    state: InMemoryState,
    clock: Clock,
    telemetry: InMemoryTelemetry,
    ticker: Ticker,
) -> None:
    view = _CutOff(state)
    a = _replica("replica-a", view, clock, telemetry, ticker)  # type: ignore[arg-type]
    request = _request()
    claim = await _claim(a, request)
    fenced_at: list[float] = []
    claim.on_lost(lambda: fenced_at.append(clock.now))
    await asyncio.sleep(0)  # the renew task asks for its first sleep
    view.cut = True
    start = clock.now
    for _ in range(10):
        if claim.lost:
            break
        clock.advance(ticker.waits[-1])
        await ticker.tick()
    assert claim.lost is True
    assert fenced_at and fenced_at[0] - start < SPEC.lease_s  # before the store lease ends
    assert telemetry.counter_value("chassis.idempotency.fenced") == 1
    assert telemetry.counter_value("chassis.idempotency.renew_failed") >= 1

    clock.advance(SPEC.lease_s)
    retry = _request(request_id="r2", trace_id="t2")
    taken = await _claim(b, retry)
    view.cut = False
    assert await _finish(claim, request) is False  # a fenced claim never writes
    assert await _finish(taken, retry) is True
    replay = await b.begin(request, wait_ms=0)
    assert isinstance(replay, Replay) and replay.request == retry


async def test_finish_after_the_fence_time_does_not_write(
    a: Idempotency, state: InMemoryState, clock: Clock, telemetry: InMemoryTelemetry
) -> None:
    request = _request()
    claim = await _claim(a, request)  # its renew task never runs: the ticker is never ticked
    clock.advance(SPEC.lease_s * 0.9)
    assert await _finish(claim, request) is False
    assert claim.lost is True
    assert telemetry.counter_value("chassis.idempotency.fenced") == 1
    raw = await state.get(store_key("echo", "key-1"))
    assert raw is not None and json.loads(raw)["state"] == "running"


async def test_on_lost_runs_when_another_replica_owns_the_key(
    a: Idempotency, b: Idempotency, clock: Clock
) -> None:
    request = _request()
    old = await _claim(a, request)
    called: list[str] = []
    old.on_lost(lambda: called.append("lost"))
    clock.advance(SPEC.lease_s)
    new = await _claim(b, request)
    assert await old.renew() is False
    assert called == ["lost"]
    late: list[str] = []
    old.on_lost(lambda: late.append("lost"))  # already lost: called at once
    assert late == ["lost"]
    await new.release()


# The wait loop: an entry gone between the claim and the read; the budget left after a wait


class _Flapping(InMemoryState):
    """A store where the key is always taken and always gone a moment later."""

    async def set_if_absent(self, key: str, value: bytes, *, ttl_s: float) -> bool:
        return False


async def test_an_entry_gone_between_claim_and_read_still_meets_the_deadline(
    clock: Clock, telemetry: InMemoryTelemetry, ticker: Ticker
) -> None:
    idem = _replica("a", _Flapping(clock=clock), clock, telemetry, ticker)
    start = clock.now
    outcome = await asyncio.wait_for(idem.begin(_request(), wait_ms=1000), 2)
    assert outcome == Refusal("idempotency_in_progress")
    assert 1.0 <= clock.now - start < 1.2  # it slept `wait_poll_ms` steps up to the deadline


async def test_a_takeover_after_a_wait_runs_with_the_budget_left(
    a: Idempotency, b: Idempotency, clock: Clock
) -> None:
    await _claim(a, _request())  # replica-a dies: never renewed
    start = clock.now
    taken = await _claim(b, _request(request_id="r2"), wait_ms=8000)
    waited_ms = (clock.now - start) * 1000
    assert taken.timeout_ms is not None
    assert abs(taken.timeout_ms - (8000 - waited_ms)) < 1


async def test_a_takeover_near_the_deadline_gets_the_floor(a: Idempotency, b: Idempotency) -> None:
    await _claim(a, _request())
    taken = await _claim(b, _request(request_id="r2"), wait_ms=6050)
    assert taken.timeout_ms == MIN_RUN_MS


async def test_a_first_claim_keeps_its_own_budget(a: Idempotency) -> None:
    assert (await _claim(a, _request())).timeout_ms is None


async def test_an_entry_of_another_version_is_refused_not_deleted(
    a: Idempotency, state: InMemoryState, telemetry: InMemoryTelemetry
) -> None:
    key = store_key("echo", "key-1")
    other = json.dumps({"v": 2, "state": "done", "whatever": True}).encode()
    await state.set(key, other, ttl_s=60)
    outcome = await a.begin(_request(), wait_ms=1000)
    assert isinstance(outcome, Refusal) and outcome.code == "idempotency_in_progress"
    assert outcome.status == 409 and outcome.retryable is False
    assert "another version" in outcome.message
    assert await state.get(key) == other
    assert telemetry.counter_value("chassis.idempotency.unknown_version") == 1
