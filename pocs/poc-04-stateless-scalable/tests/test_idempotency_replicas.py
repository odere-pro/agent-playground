"""PoC-4 exit criterion 2: "A repeated call with the same `Idempotency-Key` returns the same
result on any replica."

Two chassis replicas (`poc04_harness.replicas_on_unix_sockets`) share one `InMemoryState`, which
stands for Valkey; each runs `echo_python` in the `inprocess` lane, and one `HarnessModel` answers
both. A first call goes to replica 0 and its repeat to replica 1. The repeat is the stored result:
the same envelope (or the same frames, `created` aside: a replayed chat format gets a fresh one),
with `Idempotent-Replayed: true`, and no second engine run or model call. The same Valkey case is
`test_idempotency_valkey.py` (`network`).

Offline: Unix sockets only.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from chassis.ports.state import InMemoryState
from poc03_harness import OutboundRouter, patch_outbound
from poc04_harness import (
    FAIL,
    SIMPLIFIED,
    SIMPLIFY,
    SLOW,
    HarnessModel,
    OffsetClock,
    Replicas,
    call,
    replicas_on_unix_sockets,
    without_created,
)

TTL_S = 60.0
"""suggested: short enough to step past with the offset clock."""
IDEMPOTENCY = {"ttl_s": TTL_S, "lease_s": 2, "wait_poll_ms": 20}
CASES = [
    ("native", True),
    ("native", False),
    ("openai", True),
    ("openai", False),
    ("anthropic", True),
    ("anthropic", False),
    ("mcp", False),
]


class Pair:
    def __init__(self, replicas: Replicas, clock: OffsetClock) -> None:
        self.replicas = replicas
        self.clock = clock

    @property
    def model(self) -> HarnessModel:
        return self.replicas.model


@pytest.fixture(scope="module")
def pair() -> Iterator[Pair]:
    """Two replicas, one store, one model, for the whole module. Each test uses its own keys."""
    router = OutboundRouter()
    patch = pytest.MonkeyPatch()
    patch_outbound(patch, router.send)
    clock = OffsetClock()
    try:
        with replicas_on_unix_sockets(
            2,
            "echo_python",
            "inprocess",
            model=HarnessModel(),
            router=router,
            state=InMemoryState(clock=clock),
            idempotency=IDEMPOTENCY,
        ) as replicas:
            yield Pair(replicas, clock)
    finally:
        patch.undo()
    assert router.unrouted == [], router.unrouted


def _key() -> str:
    return f"key-{uuid.uuid4().hex}"


def _text(envelope: dict[str, Any]) -> str:
    output = envelope["output"]
    return str(output.get("text") if isinstance(output, dict) else output)


async def test_a_repeated_key_returns_the_same_result_on_any_replica(pair: Pair) -> None:
    """Exit criterion 2: the repeat on the other replica answers the first run's envelope, byte
    for byte (its `request_id`, `trace_id`, and `versions` too), with `Idempotent-Replayed: true`.
    The first answer carries no such header."""
    key = _key()
    first = await call(pair.replicas[0], "native", SIMPLIFY, key=key)
    again = await call(pair.replicas[1], "native", SIMPLIFY, key=key)
    assert first.status == again.status == 200, (first, again)
    assert first.body["status"] == "ok" and _text(first.body) == SIMPLIFIED, first.body
    assert not first.replayed and again.replayed, (first.headers, again.headers)
    assert again.body == first.body


async def test_a_repeated_key_makes_no_second_model_call(pair: Pair) -> None:
    """Exit criterion 2: a replay calls neither the engine nor the model, on either replica."""
    key = _key()
    runs, calls = pair.replicas.engine_runs(), len(pair.model.calls)
    await call(pair.replicas[0], "native", SIMPLIFY, key=key)
    for replica in (1, 0, 1):
        assert (await call(pair.replicas[replica], "native", SIMPLIFY, key=key)).replayed
    assert pair.replicas.engine_runs() - runs == 1
    assert len(pair.model.calls) - calls == 1


async def test_same_key_other_input_is_a_conflict(pair: Pair) -> None:
    """Exit criterion 2 (its edge): the same key with another input is 422
    `idempotency_conflict` on the other replica, and runs nothing."""
    key = _key()
    await call(pair.replicas[0], "native", SIMPLIFY, key=key)
    runs = pair.replicas.engine_runs()
    other = await call(pair.replicas[1], "native", f"{SIMPLIFY} and more", key=key)
    assert other.status == 422, other
    assert other.body["detail"]["code"] == "idempotency_conflict", other.body
    assert pair.replicas.engine_runs() == runs


async def test_concurrent_duplicates_run_once(pair: Pair) -> None:
    """Exit criterion 2 (in flight): two calls with one key at once, one per replica, run the
    engine once; the duplicate waits for the first result and replays it."""
    key = _key()
    runs, calls = pair.replicas.engine_runs(), len(pair.model.calls)
    a, b = await asyncio.gather(
        call(pair.replicas[0], "native", SLOW, key=key),
        call(pair.replicas[1], "native", SLOW, key=key),
    )
    assert a.status == b.status == 200, (a, b)
    assert a.body == b.body
    assert sorted([a.replayed, b.replayed]) == [False, True]
    assert pair.replicas.engine_runs() - runs == 1
    assert len(pair.model.calls) - calls == 1
    assert pair.replicas.counter("chassis.idempotency.waited") >= 1


async def test_a_cached_result_replays_as_a_stream(pair: Pair) -> None:
    """Exit criterion 2 (either mode): a run done in complete mode replays as a stream on the
    other replica: its deltas spell the stored text and its last event is the stored envelope."""
    key = _key()
    first = await call(pair.replicas[0], "native", SIMPLIFY, key=key)
    streamed = await call(pair.replicas[1], "native", SIMPLIFY, key=key, stream=True)
    assert streamed.status == 200 and streamed.replayed, streamed.headers
    names = [name for name, _ in streamed.body]
    assert names[-1] == "response", names
    assert streamed.body[-1][1] == first.body
    text = "".join(data["text"] for name, data in streamed.body if name == "delta")
    assert text == _text(first.body)


@pytest.mark.parametrize(
    ("interface", "stream"), CASES, ids=[f"{i}-{'stream' if s else 'complete'}" for i, s in CASES]
)
async def test_every_interface_replays_the_same_result(
    pair: Pair, interface: str, stream: bool
) -> None:
    """Exit criterion 2, per interface and mode: the repeat on the other replica is the first
    answer (`created` aside), carries `Idempotent-Replayed: true` (MCP clients cannot see headers:
    the envelope is compared), and makes no second model call."""
    key = _key()
    calls = len(pair.model.calls)
    first = await call(pair.replicas[0], interface, SIMPLIFY, key=key, stream=stream)
    again = await call(pair.replicas[1], interface, SIMPLIFY, key=key, stream=stream)
    assert first.status == again.status == 200, (first, again)
    assert without_created(again.body) == without_created(first.body)
    if interface != "mcp":
        assert not first.replayed and again.replayed, (first.headers, again.headers)
    assert len(pair.model.calls) - calls == 1


async def test_an_error_is_not_cached(pair: Pair) -> None:
    """Exit criterion 2 (its edge): a run that ended with an `error` frees the key, so the retry
    on the other replica runs again instead of replaying the error."""
    key = _key()
    calls = len(pair.model.calls)
    first = await call(pair.replicas[0], "native", FAIL, key=key)
    again = await call(pair.replicas[1], "native", FAIL, key=key)
    assert first.body["status"] == again.body["status"] == "error", (first.body, again.body)
    assert not again.replayed
    assert len(pair.model.calls) - calls == 2


async def test_the_cache_expires_after_ttl(pair: Pair) -> None:
    """Exit criterion 2 (its edge): past `spec.idempotency.ttl_s` the key runs again."""
    key = _key()
    calls = len(pair.model.calls)
    await call(pair.replicas[0], "native", SIMPLIFY, key=key)
    pair.clock.advance(TTL_S + 1)
    again = await call(pair.replicas[1], "native", SIMPLIFY, key=key)
    assert again.status == 200 and not again.replayed
    assert len(pair.model.calls) - calls == 2


async def test_a_call_without_a_key_never_replays(pair: Pair) -> None:
    """Exit criterion 2 (scope): only a key the client sent is checked; two calls without one run
    twice."""
    calls = len(pair.model.calls)
    a = await call(pair.replicas[0], "native", SIMPLIFY, key=None)
    b = await call(pair.replicas[1], "native", SIMPLIFY, key=None)
    assert not a.replayed and not b.replayed
    assert a.body["request_id"] != b.body["request_id"]
    assert len(pair.model.calls) - calls == 2
