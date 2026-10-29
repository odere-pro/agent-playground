"""PoC-8 (022 H-6): rate limits per caller, shared across replicas through `StatePort`.

The bucket is per agent and per caller (the `Principal` subject), kept in `StatePort`, so two
replicas that share one store share one limit. Past it: 429 with `Retry-After`, in each
interface's own error shape. With auth off there is no caller to count, so no limit applies.
Offline: httpx over ASGI, `StaticTokens`, and one `InMemoryState` standing for Valkey.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from chassis.core.handle import echo
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.fakes.auth import StaticTokens
from chassis.ports.auth import Principal
from chassis.ports.bundle import PortBundle
from chassis.ports.state import InMemoryState, StateUnavailable
from chassis.server import ChassisConfig, create_app
from chassis.server.config import LimitsSpec
from chassis.server.rate_limit import RATE_LIMITED, Allowed, RateLimiter, Refused
from fastapi import FastAPI

RUNNER = "tok-runner"
OTHER = "tok-other"
TOKENS = {
    RUNNER: Principal("user-run", frozenset({"agent:run", "agent:feedback", "agent:read"})),
    OTHER: Principal("user-other", frozenset({"agent:run"})),
}
RUN_BODY = {"input": {"text": "hello"}}
OPENAI_BODY = {"model": "echo", "messages": [{"role": "user", "content": "hello"}]}
ANTHROPIC_BODY = {
    "model": "echo",
    "max_tokens": 64,
    "messages": [{"role": "user", "content": "hello"}],
}


class Clock:
    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _limits(**kw: Any) -> LimitsSpec:
    return LimitsSpec.model_validate({"requests_per_minute": 60, "request_burst": 2, **kw})


def _config(limits: dict[str, Any] | None = None, agent: str = "echo") -> ChassisConfig:
    spec: dict[str, Any] = {
        "engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"},
        "limits": {"requests_per_minute": 60, "request_burst": 2, **(limits or {})},
    }
    data = {"version": "cfg-1", "profile": "fake", "agent": {"name": agent, "version": "0.0.1"}}
    return ChassisConfig.model_validate({**data, "spec": spec})


def _app(
    state: InMemoryState | None = None,
    *,
    auth: StaticTokens | None = None,
    limits: dict[str, Any] | None = None,
    telemetry: InMemoryTelemetry | None = None,
    with_auth: bool = True,
) -> FastAPI:
    extra: dict[str, Any] = {"auth": auth or StaticTokens(TOKENS)} if with_auth else {}
    ports = PortBundle(
        model=ScriptedModel(),
        engine=FakeEngine(handle=echo),
        config=InMemoryConfig(),
        telemetry=telemetry or InMemoryTelemetry(),
        state=state or InMemoryState(),
        **extra,
    )
    return create_app(_config(limits), ports)


@asynccontextmanager
async def _client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://chassis"
        ) as client,
    ):
        yield client


def _bearer(token: str = RUNNER) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- the bucket ---------------------------------------------------------------------------------


async def test_the_bucket_allows_the_burst_then_refuses_with_a_retry_after() -> None:
    limiter = RateLimiter(InMemoryState(), clock=Clock())
    limits = _limits()
    first = await limiter.take("echo", "user-run", limits)
    second = await limiter.take("echo", "user-run", limits)
    third = await limiter.take("echo", "user-run", limits)
    assert isinstance(first, Allowed)
    assert isinstance(second, Allowed)
    assert isinstance(third, Refused)
    assert third.retry_after_s == 1  # 60 per minute: one token a second


async def test_the_bucket_refills_with_time() -> None:
    clock = Clock()
    limiter = RateLimiter(InMemoryState(), clock=clock)
    limits = _limits()
    for _ in range(2):
        assert isinstance(await limiter.take("echo", "user-run", limits), Allowed)
    assert isinstance(await limiter.take("echo", "user-run", limits), Refused)
    clock.now += 1.0
    assert isinstance(await limiter.take("echo", "user-run", limits), Allowed)
    assert isinstance(await limiter.take("echo", "user-run", limits), Refused)


async def test_two_replicas_on_one_store_share_one_bucket() -> None:
    store, clock = InMemoryState(), Clock()
    replica_a = RateLimiter(store, clock=clock)
    replica_b = RateLimiter(store, clock=clock)
    limits = _limits()
    assert isinstance(await replica_a.take("echo", "user-run", limits), Allowed)
    assert isinstance(await replica_b.take("echo", "user-run", limits), Allowed)
    assert isinstance(await replica_a.take("echo", "user-run", limits), Refused)
    assert isinstance(await replica_b.take("echo", "user-run", limits), Refused)


async def test_each_caller_and_each_agent_has_its_own_bucket() -> None:
    limiter = RateLimiter(InMemoryState(), clock=Clock())
    limits = _limits(request_burst=1)
    assert isinstance(await limiter.take("echo", "user-run", limits), Allowed)
    assert isinstance(await limiter.take("echo", "user-run", limits), Refused)
    assert isinstance(await limiter.take("echo", "user-other", limits), Allowed)
    assert isinstance(await limiter.take("other-agent", "user-run", limits), Allowed)


async def test_the_store_key_never_holds_the_raw_subject() -> None:
    store = InMemoryState()
    limiter = RateLimiter(store, clock=Clock())
    await limiter.take("echo", "jane.doe@example.com", _limits())
    keys = [key for _, key in store.calls]
    assert keys
    assert all("jane" not in key and "@" not in key for key in keys)
    assert all(key.startswith("ratelimit:echo:") for key in keys)


async def test_a_lost_compare_and_set_is_retried() -> None:
    class Racing(InMemoryState):
        """A store where another replica writes once between our read and our write."""

        def __init__(self) -> None:
            super().__init__()
            self.raced = False

        async def compare_and_set(
            self, key: str, expected: bytes, value: bytes | None, *, ttl_s: float | None = None
        ) -> bool:
            if not self.raced:
                self.raced = True
                await super().set(key, b"0.5:1000.0", ttl_s=60)
                return False
            return await super().compare_and_set(key, expected, value, ttl_s=ttl_s)

    store = Racing()
    limiter = RateLimiter(store, clock=Clock())
    limits = _limits()
    assert isinstance(await limiter.take("echo", "user-run", limits), Allowed)  # creates
    outcome = await limiter.take("echo", "user-run", limits)
    assert store.raced
    assert isinstance(outcome, Refused)  # the other replica left half a token


async def test_a_store_outage_fails_open_by_default() -> None:
    store = InMemoryState()
    store.fail_next(StateUnavailable("down"))
    limiter = RateLimiter(store, clock=Clock())
    outcome = await limiter.take("echo", "user-run", _limits())
    assert isinstance(outcome, Allowed)
    assert outcome.unavailable


async def test_a_store_outage_fails_closed_by_config() -> None:
    store = InMemoryState()
    store.fail_next(StateUnavailable("down"))
    limiter = RateLimiter(store, clock=Clock())
    outcome = await limiter.take(
        "echo", "user-run", _limits(rate_limit_on_unavailable="fail_closed")
    )
    assert isinstance(outcome, Refused)
    assert outcome.code == "state_unavailable"


def test_the_defaults_are_on_and_generous() -> None:
    limits = LimitsSpec()
    assert limits.rate_limit is True
    assert limits.requests_per_minute == 600
    assert limits.request_burst == 100
    assert limits.rate_limit_on_unavailable == "fail_open"


# --- on the public app --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/v1/run", RUN_BODY),
        ("/v1/chat/completions", OPENAI_BODY),
        ("/v1/messages", ANTHROPIC_BODY),
    ],
)
async def test_past_the_limit_every_run_interface_answers_429_in_its_own_shape(
    path: str, body: dict[str, Any]
) -> None:
    telemetry = InMemoryTelemetry()
    async with _client(_app(telemetry=telemetry)) as client:
        for _ in range(2):
            assert (await client.post(path, json=body, headers=_bearer())).status_code == 200
        res = await client.post(path, json=body, headers=_bearer())
    assert res.status_code == 429, res.text
    assert int(res.headers["retry-after"]) >= 1
    data = res.json()
    if path == "/v1/chat/completions":
        assert data["error"]["code"] == RATE_LIMITED
        assert data["error"]["type"] == "rate_limit_error"
    elif path == "/v1/messages":
        assert data["type"] == "error"
        assert data["error"]["type"] == "rate_limit_error"
    else:
        assert data["detail"]["code"] == RATE_LIMITED
    assert any(name == "chassis.rate_limited" for name, _ in telemetry.counters)


async def test_two_replicas_share_the_limit_through_the_store() -> None:
    store = InMemoryState()
    replica_a, replica_b = _app(store), _app(store)
    async with _client(replica_a) as a, _client(replica_b) as b:
        assert (await a.post("/v1/run", json=RUN_BODY, headers=_bearer())).status_code == 200
        assert (await b.post("/v1/run", json=RUN_BODY, headers=_bearer())).status_code == 200
        assert (await a.post("/v1/run", json=RUN_BODY, headers=_bearer())).status_code == 429
        assert (await b.post("/v1/run", json=RUN_BODY, headers=_bearer())).status_code == 429
        other = await b.post("/v1/run", json=RUN_BODY, headers=_bearer(OTHER))
    assert other.status_code == 200


async def test_reads_and_probes_are_not_limited() -> None:
    async with _client(_app(limits={"request_burst": 1})) as client:
        for _ in range(3):
            assert (await client.get("/health")).status_code == 200
            assert (await client.get("/manifest", headers=_bearer())).status_code == 200


async def test_rate_limit_false_turns_it_off() -> None:
    async with _client(_app(limits={"request_burst": 1, "rate_limit": False})) as client:
        for _ in range(3):
            res = await client.post("/v1/run", json=RUN_BODY, headers=_bearer())
            assert res.status_code == 200


async def test_with_auth_off_there_is_no_caller_to_count() -> None:
    async with _client(_app(limits={"request_burst": 1}, with_auth=False)) as client:
        for _ in range(3):
            assert (await client.post("/v1/run", json=RUN_BODY)).status_code == 200


async def test_a_refused_call_never_reaches_the_engine() -> None:
    engine = FakeEngine(handle=echo)
    ports = PortBundle(
        model=ScriptedModel(),
        engine=engine,
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
        auth=StaticTokens(TOKENS),
    )
    app = create_app(_config({"request_burst": 1}), ports)
    async with _client(app) as client:
        await client.post("/v1/run", json=RUN_BODY, headers=_bearer())
        res = await client.post("/v1/run", json=RUN_BODY, headers=_bearer())
    assert res.status_code == 429
    assert engine.runs == 1
