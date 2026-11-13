"""022 H-6: one caller cannot add more new idempotency keys than its cap, so it cannot fill the
shared store (`chassis.server.idempotency_quota`).

The rate limit is a rate, not a cap. So each new entry a checked caller adds counts against
`spec.idempotency.new_keys_per_caller` per `ttl_s` window, in `StatePort`. Past it: 429
`rate_limited` with `Retry-After`, in each interface's own shape, and the key stays free. A
replay is not counted, another caller has its own count, and with auth off nothing is counted.
Offline: `InMemoryState`, and httpx over ASGI with `StaticTokens` as the auth port.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from chassis.core.envelope import Budget, Request, TaskInput
from chassis.core.handle import echo
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.fakes.auth import StaticTokens
from chassis.fakes.state import InMemoryState
from chassis.ports.auth import Principal
from chassis.ports.bundle import PortBundle
from chassis.ports.state import StateUnavailable
from chassis.server import ChassisConfig, create_app
from chassis.server.config import IdempotencySpec
from chassis.server.idempotency import Claim, Idempotency, Refusal, store_key
from chassis.server.idempotency_quota import NewKeyQuota, QuotaFull, quota_key
from fastapi import FastAPI

AGENT = "echo"
CAP = 2
WINDOW_S = 600.0
TOKENS = {
    "tok-a": Principal("user-a", frozenset({"agent:run"})),
    "tok-b": Principal("user-b", frozenset({"agent:run"})),
}
CONFIG: dict[str, Any] = {
    "version": "cfg-1",
    "profile": "fake",
    "agent": {"name": AGENT, "version": "0.0.1"},
    "spec": {
        "engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"},
        "idempotency": {"new_keys_per_caller": CAP, "ttl_s": WINDOW_S},
    },
}
RUN_BODY = {"input": {"text": "hello"}}


class WallClock:
    """A wall clock the test moves."""

    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _request(key: str) -> Request:
    return Request(
        request_id=f"r-{key}",
        trace_id=f"t-{key}",
        idempotency_key=key,
        agent=AGENT,
        agent_version="0.0.1",
        input=TaskInput(text="hello"),
        budget=Budget(timeout_ms=1000),
    )


def _idempotency(state: InMemoryState, clock: WallClock | None = None) -> Idempotency:
    spec = IdempotencySpec(new_keys_per_caller=CAP, ttl_s=WINDOW_S)
    return Idempotency(
        state, spec, InMemoryTelemetry(), replica_id="replica-a", wall_clock=clock or WallClock()
    )


async def _release(outcome: Claim | object) -> None:
    assert isinstance(outcome, Claim), outcome
    await outcome.release()


# --- The counter ---


async def test_the_counter_refuses_past_the_cap_until_its_window_ends() -> None:
    """N keys pass, the next is refused with the seconds left, and a new window starts at 1."""
    clock = WallClock()
    quota = NewKeyQuota(InMemoryState(), clock=clock)

    taken = [await quota.take(AGENT, "user-a", cap=CAP, window_s=WINDOW_S) for _ in range(CAP)]
    clock.now += 100
    full = await quota.take(AGENT, "user-a", cap=CAP, window_s=WINDOW_S)
    clock.now += WINDOW_S - 100
    again = await quota.take(AGENT, "user-a", cap=CAP, window_s=WINDOW_S)

    assert taken == [None] * CAP
    assert full == QuotaFull(retry_after_s=int(WINDOW_S - 100))
    assert again is None


async def test_two_replicas_on_one_store_share_one_count() -> None:
    """The count lives in the store, so a second replica sees the first one's keys."""
    state = InMemoryState()
    first, second = NewKeyQuota(state, clock=WallClock()), NewKeyQuota(state, clock=WallClock())

    outcomes = [
        await first.take(AGENT, "user-a", cap=CAP, window_s=WINDOW_S),
        await second.take(AGENT, "user-a", cap=CAP, window_s=WINDOW_S),
        await first.take(AGENT, "user-a", cap=CAP, window_s=WINDOW_S),
    ]

    assert outcomes[:2] == [None, None]
    assert isinstance(outcomes[2], QuotaFull)


async def test_the_counter_key_holds_a_hash_of_the_subject_never_the_subject() -> None:
    """The store key ends with `sha256(subject)[:32]`, under its own prefix."""
    state = InMemoryState()
    await NewKeyQuota(state, clock=WallClock()).take(AGENT, "user-a", cap=CAP, window_s=WINDOW_S)

    key = quota_key(AGENT, "user-a")

    assert key == f"chassis:idemquota:v1:{AGENT}:{hashlib.sha256(b'user-a').hexdigest()[:32]}"
    assert await state.get(key) is not None
    assert "user-a" not in key


async def test_an_unreadable_counter_starts_a_new_window() -> None:
    """A value that cannot be read is replaced with a count of 1, never a crash."""
    state = InMemoryState()
    key = quota_key(AGENT, "user-a")
    await state.set(key, b"\xff not a count")
    clock = WallClock()

    outcome = await NewKeyQuota(state, clock=clock).take(
        AGENT, "user-a", cap=CAP, window_s=WINDOW_S
    )

    assert outcome is None
    assert await state.get(key) == f"1:{clock.now:.6f}".encode()


# --- Idempotency.begin ---


async def test_begin_refuses_a_new_key_past_the_cap_and_leaves_it_free() -> None:
    """Past the cap, `begin` is 429 `rate_limited` with `Retry-After`, and the key is free."""
    state = InMemoryState()
    idem = _idempotency(state)
    for key in ("k1", "k2"):
        await _release(await idem.begin(_request(key), wait_ms=0, subject="user-a"))

    refused = await idem.begin(_request("k3"), wait_ms=0, subject="user-a")

    assert isinstance(refused, Refusal)
    assert (refused.code, refused.status, refused.retryable) == ("rate_limited", 429, True)
    assert refused.retry_after_s == int(WINDOW_S)
    assert "Idempotency-Key" in refused.message and str(CAP) not in refused.message
    assert await state.get(store_key(AGENT, "k3", subject="user-a")) is None
    telemetry = idem.telemetry
    assert isinstance(telemetry, InMemoryTelemetry)
    assert telemetry.counter_value("chassis.idempotency.refused", code="rate_limited") == 1


async def test_begin_counts_each_caller_on_its_own_and_not_with_auth_off() -> None:
    """Caller B has its own count; with no subject (auth off) no key is counted."""
    idem = _idempotency(InMemoryState())
    for key in ("k1", "k2"):
        await _release(await idem.begin(_request(key), wait_ms=0, subject="user-a"))

    other = await idem.begin(_request("k3"), wait_ms=0, subject="user-b")
    anonymous = [await idem.begin(_request(f"anon-{i}"), wait_ms=0) for i in range(CAP + 2)]

    await _release(other)
    for outcome in anonymous:
        await _release(outcome)


async def test_a_store_failure_while_counting_is_503_and_frees_the_key() -> None:
    """The count fails closed with the keyed call (503 `state_unavailable`); the key is free."""
    state = InMemoryState()
    idem = _idempotency(state)
    original = state.get

    async def failing_get(key: str) -> bytes | None:
        if key.startswith("chassis:idemquota:"):
            raise StateUnavailable("valkey down")
        return await original(key)

    state.get = failing_get  # type: ignore[method-assign]

    outcome = await idem.begin(_request("k1"), wait_ms=0, subject="user-a")

    assert isinstance(outcome, Refusal) and outcome.code == "state_unavailable"
    assert await original(store_key(AGENT, "k1", subject="user-a")) is None


# --- Over ASGI, every run interface ---


def _app() -> FastAPI:
    ports = PortBundle(
        model=ScriptedModel(),
        engine=FakeEngine(handle=echo),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
        state=InMemoryState(),
        auth=StaticTokens(TOKENS),
    )
    return create_app(ChassisConfig.model_validate(CONFIG), ports)


@asynccontextmanager
async def _client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://chassis"
        ) as client,
    ):
        yield client


def _headers(token: str, key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Idempotency-Key": key}


async def test_a_caller_past_its_cap_gets_429_and_a_replay_or_another_caller_still_runs() -> None:
    """Over `/v1/run`: two keys run, the third is 429 with `Retry-After`; a retry of the first
    replays, and caller B's third key runs."""
    async with _client(_app()) as client:
        first = await client.post("/v1/run", json=RUN_BODY, headers=_headers("tok-a", "k1"))
        await client.post("/v1/run", json=RUN_BODY, headers=_headers("tok-a", "k2"))
        refused = await client.post("/v1/run", json=RUN_BODY, headers=_headers("tok-a", "k3"))
        replay = await client.post("/v1/run", json=RUN_BODY, headers=_headers("tok-a", "k1"))
        other = await client.post("/v1/run", json=RUN_BODY, headers=_headers("tok-b", "k3"))

    assert first.status_code == 200, first.text
    assert refused.status_code == 429, refused.text
    assert refused.json()["detail"]["code"] == "rate_limited"
    assert int(refused.headers["retry-after"]) >= 1
    assert (replay.status_code, replay.headers.get("idempotent-replayed")) == (200, "true")
    assert other.status_code == 200, other.text


async def test_a_body_key_counts_like_a_header_key() -> None:
    """The native body's `idempotency_key` is a client key too, so it is counted."""
    headers = {"Authorization": "Bearer tok-a"}
    async with _client(_app()) as client:
        codes = [
            (
                await client.post(
                    "/v1/run", json={**RUN_BODY, "idempotency_key": f"b{i}"}, headers=headers
                )
            ).status_code
            for i in range(CAP + 1)
        ]

    assert codes == [200] * CAP + [429]


@pytest.mark.parametrize(
    ("path", "body", "kind"),
    [
        (
            "/v1/chat/completions",
            {"model": AGENT, "messages": [{"role": "user", "content": "hello"}]},
            lambda r: r.json()["error"]["type"],
        ),
        (
            "/v1/messages",
            {"model": AGENT, "max_tokens": 64, "messages": [{"role": "user", "content": "hi"}]},
            lambda r: r.json()["error"]["type"],
        ),
    ],
    ids=["openai", "anthropic"],
)
async def test_the_chat_interfaces_answer_429_in_their_own_shape(
    path: str, body: dict[str, Any], kind: Any
) -> None:
    """The OpenAI and Anthropic interfaces answer `rate_limit_error`, with `Retry-After`."""
    async with _client(_app()) as client:
        codes = [
            (await client.post(path, json=body, headers=_headers("tok-a", f"c{i}"))).status_code
            for i in range(CAP)
        ]
        refused = await client.post(path, json=body, headers=_headers("tok-a", "c-last"))

    assert codes == [200] * CAP
    assert refused.status_code == 429, refused.text
    assert kind(refused) == "rate_limit_error"
    assert int(refused.headers["retry-after"]) >= 1
