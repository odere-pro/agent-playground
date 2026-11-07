"""The waiter cap on one in-flight idempotency key (R4-10, P-A item 8): one replica lets at most
`MAX_WAITERS_PER_KEY` duplicates poll the store for one key; the next is 409
`idempotency_in_progress` at once. Over `InMemoryState`, no HTTP.
"""

from __future__ import annotations

import asyncio

from chassis.core.envelope import Budget, Request, TaskInput
from chassis.fakes import InMemoryState, InMemoryTelemetry
from chassis.server.config import IdempotencySpec
from chassis.server.idempotency import (
    MAX_WAITERS_PER_KEY,
    WAITERS_FULL,
    Claim,
    Idempotency,
    Refusal,
)

SPEC = IdempotencySpec(ttl_s=60, lease_s=6, wait_poll_ms=100, max_entry_bytes=4096)


class _CountingState(InMemoryState):
    """Counts the reads and claim attempts the waiters send."""

    def __init__(self) -> None:
        super().__init__()
        self.ops = 0

    async def get(self, key: str) -> bytes | None:
        self.ops += 1
        return await super().get(key)

    async def set_if_absent(self, key: str, value: bytes, *, ttl_s: float) -> bool:
        self.ops += 1
        return await super().set_if_absent(key, value, ttl_s=ttl_s)


class _Gate:
    """A poll sleep that never returns (the holder stays in flight); counts the sleeps."""

    def __init__(self) -> None:
        self.sleeps = 0

    async def sleep(self, _seconds: float) -> None:
        self.sleeps += 1
        await asyncio.Event().wait()


async def _never(_seconds: float) -> None:
    await asyncio.Event().wait()


def _request(request_id: str, key: str = "key-1") -> Request:
    return Request(
        request_id=request_id,
        trace_id=f"t-{request_id}",
        idempotency_key=key,
        agent="echo",
        agent_version="0.0.1",
        input=TaskInput(text="hello"),
        budget=Budget(timeout_ms=30_000),
    )


async def _settle() -> None:
    for _ in range(10):
        await asyncio.sleep(0)


def _replica(
    state: InMemoryState, telemetry: InMemoryTelemetry, gate: _Gate, name: str
) -> Idempotency:
    return Idempotency(
        state, SPEC, telemetry, replica_id=name, sleep=gate.sleep, renew_sleep=_never
    )


async def test_the_17th_waiter_on_one_in_flight_key_gets_409_at_once_and_the_store_sees_no_more_polls() -> (  # noqa: E501
    None
):
    """16 duplicates wait; the 17th is refused before it sleeps, after one claim try and one
    read. Cancelled (a client gone), the 16 free the counter."""
    assert MAX_WAITERS_PER_KEY == 16
    state, telemetry, gate = _CountingState(), InMemoryTelemetry(), _Gate()
    holder = _replica(state, telemetry, gate, "a")
    waiters = _replica(state, telemetry, gate, "b")
    claim = await holder.begin(_request("r0"), wait_ms=0)
    assert isinstance(claim, Claim)
    tasks = [
        asyncio.create_task(waiters.begin(_request(f"r{i}"), wait_ms=10_000))
        for i in range(1, MAX_WAITERS_PER_KEY + 1)
    ]
    await _settle()
    assert gate.sleeps == MAX_WAITERS_PER_KEY
    ops, sleeps = state.ops, gate.sleeps

    outcome = await asyncio.wait_for(waiters.begin(_request("r17"), wait_ms=10_000), 1)

    assert outcome == Refusal("idempotency_in_progress")
    assert outcome.status == 409 and outcome.retryable is True
    assert state.ops - ops == 2  # one claim try and one read, then no poll
    assert gate.sleeps == sleeps
    assert telemetry.counter_value(WAITERS_FULL) == 1

    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    assert waiters._waiters == {}
    await claim.release()


async def test_the_cap_is_per_key_and_another_replica_has_its_own() -> None:
    """A full key on one replica refuses nobody on another key, nor on another replica."""
    state, telemetry, gate = _CountingState(), InMemoryTelemetry(), _Gate()
    holder = _replica(state, telemetry, gate, "a")
    full = _replica(state, telemetry, gate, "b")
    other = _replica(state, telemetry, gate, "c")
    claims = [await holder.begin(_request("h1", key), wait_ms=0) for key in ("key-1", "key-2")]
    assert all(isinstance(c, Claim) for c in claims)
    tasks = [
        asyncio.create_task(full.begin(_request(f"r{i}"), wait_ms=10_000))
        for i in range(MAX_WAITERS_PER_KEY)
    ]
    tasks.append(asyncio.create_task(full.begin(_request("k2", "key-2"), wait_ms=10_000)))
    tasks.append(asyncio.create_task(other.begin(_request("c1"), wait_ms=10_000)))
    await _settle()

    assert gate.sleeps == MAX_WAITERS_PER_KEY + 2
    assert telemetry.counter_value(WAITERS_FULL) == 0

    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    assert full._waiters == {} and other._waiters == {}
    for claim in claims:
        assert isinstance(claim, Claim)
        await claim.release()


async def test_a_cancelled_waiter_frees_its_slot() -> None:
    """At the cap, one waiter cancelled (its client gone) frees exactly one slot: the next
    duplicate waits instead of a 409. With all cancelled, the counter is empty."""
    state, telemetry, gate = _CountingState(), InMemoryTelemetry(), _Gate()
    holder = _replica(state, telemetry, gate, "a")
    waiters = _replica(state, telemetry, gate, "b")
    claim = await holder.begin(_request("r0"), wait_ms=0)
    assert isinstance(claim, Claim)
    tasks = [
        asyncio.create_task(waiters.begin(_request(f"r{i}"), wait_ms=10_000))
        for i in range(1, MAX_WAITERS_PER_KEY + 1)
    ]
    await _settle()
    (key,) = waiters._waiters
    assert waiters._waiters[key] == MAX_WAITERS_PER_KEY

    gone = tasks.pop()
    gone.cancel()
    await asyncio.gather(gone, return_exceptions=True)
    assert waiters._waiters[key] == MAX_WAITERS_PER_KEY - 1
    tasks.append(asyncio.create_task(waiters.begin(_request("next"), wait_ms=10_000)))
    await _settle()

    assert waiters._waiters[key] == MAX_WAITERS_PER_KEY
    assert telemetry.counter_value(WAITERS_FULL) == 0
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    assert waiters._waiters == {}
    await claim.release()
