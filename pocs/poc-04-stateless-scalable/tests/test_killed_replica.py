"""PoC-4 exit criterion 3, in the README's words:

"A killed replica loses no request that the client retries."

This file covers it at the store, over two replicas: a replica killed while it runs a call, one
killed after it ran the call, and one cut off from the store while it keeps running.

Two replicas share one `InMemoryState` (standing for Valkey). Replica 0 sees the store through a
`SeverableState`; `sever()` is its SIGKILL as the store sees it: from then on nothing it does
reaches the store, so its claim is not renewed and its release never lands.

Lease time is a `HeldClock`, shared by the store and both replicas' `Idempotency`: it moves only
when the test moves it, so the lease cannot run out early on a busy machine. `CountedPolls` is
replica 1's poll sleep, so the test sees the retry try the key again and be held off.

- Killed mid-run: the first call hangs in the model on replica 0, replica 0 dies, and the client
  retries the same key on replica 1. The retry waits while the dead claim lives, takes it over once
  the lease expires, runs once, and answers. A later retry replays that result.
- Killed after the run: the result was cached before replica 0 died, so the retry on replica 1
  replays it and runs nothing.

The Compose drill (`docker kill` of a pair under load) is `test_compose_scale.py`.
Offline: Unix sockets only.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import NamedTuple

import pytest
from chassis.fakes.events import InMemoryBus
from chassis.ports.state import InMemoryState
from chassis.server.idempotency import FENCE_FRACTION
from poc_harness.interfaces import OutboundRouter
from poc_harness.outbound import patch_outbound
from poc_harness.replica_calls import Reply, call
from poc_harness.replica_doubles import HANG, HarnessModel, SeverableState
from poc_harness.replicas import Replicas, replicas_on_unix_sockets
from poc_harness.scenario import SIMPLIFIED, SIMPLIFY
from poc_harness.waits import wait_until_async

LEASE_S = 0.6
"""suggested: the lease in `HeldClock` seconds. Only the test moves that clock."""
RETRY_TIMEOUT_MS = 10_000
HUNG_WAIT_S = 10.0
"""A hang guard: only a hang reaches it."""
JUST_BEFORE_THE_LEASE_ENDS = LEASE_S * 0.99
PAST_THE_FENCE = (FENCE_FRACTION + 1) / 2 * LEASE_S
"""Between the fence (`FENCE_FRACTION * LEASE_S`) and the end of the lease."""
FULL_POLL = 2
"""Polls to count before a claim attempt surely ran after the clock moved: one may be under way."""


class HeldClock:
    """A monotonic clock that stands still until `advance`."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class CountedPolls:
    """The poll sleep of a call that waits on a key: counts each round of the wait."""

    def __init__(self) -> None:
        self.count = 0

    async def __call__(self, seconds: float) -> None:
        self.count += 1
        await asyncio.sleep(seconds)


class Pair(NamedTuple):
    replicas: Replicas
    link: SeverableState
    """Replica 0's link to the store."""
    bus: InMemoryBus
    clock: HeldClock
    retry_polls: CountedPolls
    """Replica 1's waits on a held key."""


@contextmanager
def _pair(*, result_events: bool) -> Iterator[Pair]:
    router = OutboundRouter()
    patch = pytest.MonkeyPatch()
    patch_outbound(patch, router.send)
    clock = HeldClock()
    shared = InMemoryState(clock=clock)
    link = SeverableState(shared)
    bus = InMemoryBus()
    polls = CountedPolls()
    try:
        with replicas_on_unix_sockets(
            2,
            "echo_python",
            "inprocess",
            model=HarnessModel(),
            router=router,
            state=[link, shared],
            events=bus,
            idempotency={"lease_s": LEASE_S, "wait_poll_ms": 20},
            events_spec={"result_events": result_events},
        ) as replicas:
            for app in replicas.apps:
                app.state.pipeline.idempotency.clock = clock
            replicas.apps[1].state.pipeline.idempotency.sleep = polls
            yield Pair(replicas, link, bus, clock, polls)
    finally:
        patch.undo()


@pytest.fixture
def pair() -> Iterator[Pair]:
    """Two replicas, replica 0 behind a kill switch. Fresh per test: a killed replica stays dead."""
    with _pair(result_events=False) as built:
        yield built


async def _held_off(pair: Pair) -> None:
    """Returns once the retry on replica 1 tried the key after the last clock move and has to wait
    on. A retry that took the key stops polling, so this fails rather than returns."""
    seen = pair.retry_polls.count
    await wait_until_async(
        lambda: pair.retry_polls.count >= seen + FULL_POLL,
        "the retry to go on waiting on the key",
        HUNG_WAIT_S,
    )


async def _hung_on_replica_0(pair: Pair, key: str) -> asyncio.Task[Reply]:
    """The first call, once it holds `key` and sits in the model."""
    pair.replicas.model.hang_armed.set()
    first = asyncio.create_task(call(pair.replicas[0], "native", HANG, key=key))
    hung = await asyncio.to_thread(pair.replicas.model.hung.wait, HUNG_WAIT_S)
    assert hung, "the first run never reached the model"
    return first


async def test_a_retry_after_a_dead_owner_runs_once_after_the_lease(pair: Pair) -> None:
    """Exit criterion 3: replica 0 dies mid-run holding the key; the client's retry on replica 1
    is not refused and not lost: it takes the claim over once the lease expires, runs the engine
    once, and answers 200. The next retry replays that answer."""
    replicas, doomed, _, clock, _ = pair
    key = f"killed-{uuid.uuid4().hex}"
    first = await _hung_on_replica_0(pair, key)
    try:
        doomed.sever()  # SIGKILL: no renew, no release from replica 0 from now on
        pending = asyncio.create_task(
            call(replicas[1], "native", HANG, key=key, timeout_ms=RETRY_TIMEOUT_MS)
        )
        await _held_off(pair)
        clock.advance(JUST_BEFORE_THE_LEASE_ENDS)
        await _held_off(pair)
        taken_over_during_the_lease = replicas.counter("chassis.idempotency.taken_over")
        clock.advance(LEASE_S)
        retry = await pending
    finally:
        first.cancel()  # the client gave up on the dead replica
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await first
    assert taken_over_during_the_lease == 0, "replica 1 ran before the dead claim expired"
    assert retry.status == 200, retry
    assert retry.body["status"] == "ok" and retry.body["output"]["text"] == SIMPLIFIED, retry.body
    assert not retry.replayed
    assert replicas.counter("chassis.idempotency.taken_over") == 1
    # `HarnessModel` records a call once it answers: the hung call on replica 0 never did.
    assert len(replicas.model.calls) == 1, "the model answered other than once"
    await wait_until_async(
        lambda: replicas.engine_runs() == 2, "replica 0's dead run and replica 1's", HUNG_WAIT_S
    )

    again = await call(replicas[1], "native", HANG, key=key)
    assert again.replayed and again.body == retry.body


async def test_a_retry_after_a_completed_run_on_a_dead_replica_replays(pair: Pair) -> None:
    """Exit criterion 3: a run that finished before its replica died is not run again: the retry
    on replica 1 replays the cached result."""
    replicas, doomed, *_ = pair
    key = f"done-{uuid.uuid4().hex}"
    first = await call(replicas[0], "native", SIMPLIFY, key=key)
    doomed.sever()
    retry = await call(replicas[1], "native", SIMPLIFY, key=key)
    assert retry.replayed and retry.body == first.body
    assert replicas.engine_runs() == 1
    assert len(replicas.model.calls) == 1


@pytest.fixture
def cut_pair() -> Iterator[Pair]:
    """Two replicas over one store and one bus, with result events on. Replica 0's store link can
    be cut while the replica itself keeps running and serving its client."""
    with _pair(result_events=True) as built:
        yield built


def _pending_publishes(replicas: Replicas) -> int:
    return sum(app.state.result_publisher.pending for app in replicas.apps)


async def test_a_replica_cut_off_from_the_store_mid_run_is_fenced(cut_pair: Pair) -> None:
    """Exit criterion 3, the live-but-cut-off case: replica 0 keeps running but cannot reach the
    store, so it cannot renew. Before its lease can pass to replica 1 it fences itself: its run
    is cancelled, its client gets 409 `idempotency_in_progress` (retryable), and it publishes no
    result. Replica 1's retry takes the key over and is the one run whose result is kept and
    published."""
    replicas, cut, bus, clock, _ = cut_pair
    key = f"cut-{uuid.uuid4().hex}"
    first = await _hung_on_replica_0(cut_pair, key)
    cut.sever()  # not killed: replica 0 still serves its client, it just cannot reach the store
    pending = asyncio.create_task(
        call(replicas[1], "native", HANG, key=key, timeout_ms=RETRY_TIMEOUT_MS)
    )
    await _held_off(cut_pair)

    clock.advance(PAST_THE_FENCE)
    fenced = await first
    await wait_until_async(
        lambda: replicas.counter("chassis.idempotency.run_fenced") == 1,
        "replica 0 to refuse its run's result",
        HUNG_WAIT_S,
    )
    await _held_off(cut_pair)
    taken_over_at_the_fence = replicas.counter("chassis.idempotency.taken_over")
    clock.advance(LEASE_S)
    retry = await pending
    await wait_until_async(lambda: bool(bus.published), "the kept result's publish", HUNG_WAIT_S)
    await wait_until_async(
        lambda: _pending_publishes(replicas) == 0, "the publishes to end", HUNG_WAIT_S
    )

    assert taken_over_at_the_fence == 0, "the fence must come before the takeover"
    assert fenced.status == 409, fenced
    assert fenced.body["detail"]["code"] == "idempotency_in_progress", fenced.body
    assert retry.status == 200 and retry.body["status"] == "ok", retry
    assert replicas.counter("chassis.idempotency.fenced") == 1
    assert len(replicas.model.calls) == 1, "only replica 1's run reached a model answer"
    assert [event.subject for _, event in bus.published] == [retry.body["request_id"]]
