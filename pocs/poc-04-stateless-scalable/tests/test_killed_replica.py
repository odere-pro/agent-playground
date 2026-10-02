"""PoC-4 exit criterion 3: "A replica killed mid-run loses no request that the client retries."

Two replicas share one `InMemoryState` (standing for Valkey). Replica 0 sees the store through a
`SeverableState`; `sever()` is its SIGKILL as the store sees it: from then on nothing it does
reaches the store, so its claim is not renewed and its release never lands. The lease is short
(`LEASE_S`) so the gate stays fast.

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
import time
import uuid
from collections.abc import Iterator

import pytest
from chassis.fakes.events import InMemoryBus
from chassis.ports.state import InMemoryState
from poc03_harness import OutboundRouter, patch_outbound
from poc04_harness import (
    HANG,
    SIMPLIFIED,
    SIMPLIFY,
    HarnessModel,
    Replicas,
    SeverableState,
    call,
    replicas_on_unix_sockets,
)

LEASE_S = 0.6
"""suggested: short, so the takeover happens well inside the retry's own `timeout_ms`."""
RETRY_TIMEOUT_MS = 10_000
HUNG_WAIT_S = 10.0


@pytest.fixture
def pair() -> Iterator[tuple[Replicas, SeverableState]]:
    """Two replicas, replica 0 behind a kill switch. Fresh per test: a killed replica stays dead."""
    router = OutboundRouter()
    patch = pytest.MonkeyPatch()
    patch_outbound(patch, router.send)
    shared = InMemoryState()
    doomed = SeverableState(shared)
    try:
        with replicas_on_unix_sockets(
            2,
            "echo_python",
            "inprocess",
            model=HarnessModel(),
            router=router,
            state=[doomed, shared],
            idempotency={"lease_s": LEASE_S, "wait_poll_ms": 20},
        ) as replicas:
            yield replicas, doomed
    finally:
        patch.undo()


async def test_a_retry_after_a_dead_owner_runs_once_after_the_lease(
    pair: tuple[Replicas, SeverableState],
) -> None:
    """Exit criterion 3: replica 0 dies mid-run holding the key; the client's retry on replica 1
    is not refused and not lost: it takes the claim over once the lease expires, runs the engine
    once, and answers 200. The next retry replays that answer."""
    replicas, doomed = pair
    key = f"killed-{uuid.uuid4().hex}"
    replicas.model.hang_armed.set()
    first = asyncio.create_task(call(replicas[0], "native", HANG, key=key))
    try:
        hung = await asyncio.to_thread(replicas.model.hung.wait, HUNG_WAIT_S)
        assert hung, "the first run never reached the model"
        doomed.sever()  # SIGKILL: no renew, no release from replica 0 from now on
        killed_at = time.monotonic()
        retry = await call(replicas[1], "native", HANG, key=key, timeout_ms=RETRY_TIMEOUT_MS)
        took = time.monotonic() - killed_at
    finally:
        first.cancel()  # the client gave up on the dead replica
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await first
    assert retry.status == 200, retry
    assert retry.body["status"] == "ok" and retry.body["output"]["text"] == SIMPLIFIED, retry.body
    assert not retry.replayed
    assert took >= LEASE_S / 2, f"replica 1 ran before the dead claim could expire: {took:.2f}s"
    assert replicas.counter("chassis.idempotency.taken_over") == 1
    # `HarnessModel` records a call once it answers: the hung call on replica 0 never did.
    assert len(replicas.model.calls) == 1, "the model answered other than once"
    assert replicas.engine_runs() == 2, "replica 0's dead run and replica 1's"

    again = await call(replicas[1], "native", HANG, key=key)
    assert again.replayed and again.body == retry.body


async def test_a_retry_after_a_completed_run_on_a_dead_replica_replays(
    pair: tuple[Replicas, SeverableState],
) -> None:
    """Exit criterion 3: a run that finished before its replica died is not run again: the retry
    on replica 1 replays the cached result."""
    replicas, doomed = pair
    key = f"done-{uuid.uuid4().hex}"
    first = await call(replicas[0], "native", SIMPLIFY, key=key)
    doomed.sever()
    retry = await call(replicas[1], "native", SIMPLIFY, key=key)
    assert retry.replayed and retry.body == first.body
    assert replicas.engine_runs() == 1
    assert len(replicas.model.calls) == 1


@pytest.fixture
def cut_pair() -> Iterator[tuple[Replicas, SeverableState, InMemoryBus]]:
    """Two replicas over one store and one bus, with result events on. Replica 0's store link can
    be cut while the replica itself keeps running and serving its client."""
    router = OutboundRouter()
    patch = pytest.MonkeyPatch()
    patch_outbound(patch, router.send)
    shared = InMemoryState()
    cut = SeverableState(shared)
    bus = InMemoryBus()
    try:
        with replicas_on_unix_sockets(
            2,
            "echo_python",
            "inprocess",
            model=HarnessModel(),
            router=router,
            state=[cut, shared],
            events=bus,
            idempotency={"lease_s": LEASE_S, "wait_poll_ms": 20},
            events_spec={"result_events": True},
        ) as replicas:
            yield replicas, cut, bus
    finally:
        patch.undo()


async def test_a_replica_cut_off_from_the_store_mid_run_is_fenced(
    cut_pair: tuple[Replicas, SeverableState, InMemoryBus],
) -> None:
    """Exit criterion 3, the live-but-cut-off case: replica 0 keeps running but cannot reach the
    store, so it cannot renew. Before its lease can pass to replica 1 it fences itself: its run
    is cancelled, its client gets 409 `idempotency_in_progress` (retryable), and it publishes no
    result. Replica 1's retry takes the key over and is the one run whose result is kept and
    published."""
    replicas, cut, bus = cut_pair
    key = f"cut-{uuid.uuid4().hex}"
    replicas.model.hang_armed.set()
    first = asyncio.create_task(call(replicas[0], "native", HANG, key=key))
    hung = await asyncio.to_thread(replicas.model.hung.wait, HUNG_WAIT_S)
    assert hung, "the first run never reached the model"
    cut.sever()  # not killed: replica 0 still serves its client, it just cannot reach the store
    retry = await call(replicas[1], "native", HANG, key=key, timeout_ms=RETRY_TIMEOUT_MS)
    fenced = await first

    assert fenced.status == 409, fenced
    assert fenced.body["detail"]["code"] == "idempotency_in_progress", fenced.body
    assert retry.status == 200 and retry.body["status"] == "ok", retry
    assert replicas.counter("chassis.idempotency.fenced") == 1
    assert len(replicas.model.calls) == 1, "only replica 1's run reached a model answer"
    for _ in range(250):  # up to 5 s for the background publish
        if bus.published:
            break
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.2)  # a second publish, if any, has time to land
    assert [event.subject for _, event in bus.published] == [retry.body["request_id"]]
