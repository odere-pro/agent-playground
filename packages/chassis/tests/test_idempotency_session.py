"""With session memory on, the idempotency fingerprint and store key carry `session.id` (CH-20).

Two sessions that send the same input under the same client key each get their own answer: a
run's memory is per session, so session B must never replay session A's answer. With session
memory off, nothing changes: the fingerprint and the store key are byte-for-byte as before.
The run key the tool endpoint derives write keys from carries the session too, so one session's
write-tool call is never deduped against another's. Offline: `InMemoryState`, and httpx over ASGI.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from chassis.core.envelope import Budget, Request, TaskInput
from chassis.core.handle import echo
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.fakes.state import InMemoryState
from chassis.ports.bundle import PortBundle
from chassis.server import ChassisConfig, create_app
from chassis.server.config import IdempotencySpec
from chassis.server.correlation import RUN_KEY_PREFIX, keyed_run_key
from chassis.server.idempotency import Claim, Idempotency, fingerprint, store_key
from chassis.server.pipeline import SESSION_ID_MAX, Observed, RunPipeline
from chassis.server.results import key_hash
from fastapi import FastAPI

AGENT = "echo"
KEY = "order-4711-customer-alice"
REPLAYED = "idempotent-replayed"


def _config(*, memory: bool) -> dict[str, Any]:
    return {
        "version": "cfg-1",
        "profile": "fake",
        "agent": {"name": AGENT, "version": "0.0.1"},
        "spec": {
            "engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"},
            "memory": {"enabled": memory},
        },
    }


def _request(request_id: str = "r1") -> Request:
    return Request(
        request_id=request_id,
        trace_id=f"t-{request_id}",
        idempotency_key=KEY,
        agent=AGENT,
        agent_version="0.0.1",
        input=TaskInput(text="hello"),
        budget=Budget(timeout_ms=1000),
    )


# The fingerprint and the store key


def test_the_fingerprint_with_no_session_is_byte_for_byte_as_before() -> None:
    request = _request()
    body = {
        "agent": AGENT,
        "input": request.input.model_dump(mode="json"),
        "context_ref": None,
    }
    before = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert fingerprint(request) == before
    assert fingerprint(request, session_id=None) == before


def test_the_fingerprint_includes_the_session_id() -> None:
    request = _request()
    a = fingerprint(request, session_id="session-a")
    b = fingerprint(request, session_id="session-b")
    assert a != b
    assert a != fingerprint(request)
    assert fingerprint(_request("r2"), session_id="session-a") == a


def test_the_store_key_with_a_session_is_its_own_key() -> None:
    assert store_key(AGENT, KEY, session_id=None) == store_key(AGENT, KEY)
    assert store_key(AGENT, KEY, subject="u", session_id=None) == store_key(AGENT, KEY, subject="u")
    a = store_key(AGENT, KEY, session_id="session-a")
    assert a != store_key(AGENT, KEY, session_id="session-b")
    assert a != store_key(AGENT, KEY)
    assert a != store_key(AGENT, KEY, subject="u", session_id="session-a")
    assert "session-a" not in a and KEY not in a


async def test_two_sessions_with_the_same_input_and_key_each_claim_their_own_run() -> None:
    state = InMemoryState()
    idem = Idempotency(state, IdempotencySpec(), InMemoryTelemetry(), replica_id="replica-a")
    first = await idem.begin(_request("r1"), wait_ms=0, session_id="session-a")
    assert isinstance(first, Claim)
    other = await idem.begin(_request("r2"), wait_ms=0, session_id="session-b")
    assert isinstance(other, Claim)
    assert other.key != first.key
    await first.release()
    await other.release()


# The run key the tool endpoint derives write keys from


def test_the_run_key_with_no_session_is_byte_for_byte_as_before() -> None:
    request = _request()
    payload = "|".join((AGENT, key_hash(KEY), fingerprint(request)))
    before = hashlib.sha256((RUN_KEY_PREFIX + payload).encode("utf-8")).hexdigest()
    assert keyed_run_key(request) == before
    assert keyed_run_key(request, session_id=None) == before


@asynccontextmanager
async def _pipeline(*, memory: bool) -> AsyncIterator[RunPipeline]:
    app = _app(InMemoryState(), memory=memory)
    async with app.router.lifespan_context(app):
        pipeline = app.state.pipeline
        assert isinstance(pipeline, RunPipeline)
        yield pipeline


def _run_key(pipeline: RunPipeline, request: Request, session: str | None) -> str | None:
    run = pipeline.open(request, observed=Observed(session_id=session))
    try:
        return run.record.keyed_run_key
    finally:
        pipeline.runs.close(run.record)


async def test_with_memory_on_two_sessions_under_one_key_get_their_own_run_key() -> None:
    async with _pipeline(memory=True) as pipeline:
        a = _run_key(pipeline, _request("r1"), "session-a")
        b = _run_key(pipeline, _request("r2"), "session-b")
        none = _run_key(pipeline, _request("r3"), None)
    assert a is not None and b is not None and a != b
    assert a == keyed_run_key(_request("r1"), session_id="session-a")
    assert none == keyed_run_key(_request("r3"))


async def test_with_memory_off_the_session_does_not_scope_the_run_key() -> None:
    async with _pipeline(memory=False) as pipeline:
        a = _run_key(pipeline, _request("r1"), "session-a")
        b = _run_key(pipeline, _request("r2"), "session-b")
    assert a == b == keyed_run_key(_request("r1"))


# Through the HTTP app, auth off, profile `fake`


def _app(state: InMemoryState, *, memory: bool) -> FastAPI:
    ports = PortBundle(
        model=ScriptedModel(),
        engine=FakeEngine(handle=echo),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
        state=state,
    )
    return create_app(ChassisConfig.model_validate(_config(memory=memory)), ports)


@asynccontextmanager
async def _client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://chassis"
        ) as client,
    ):
        yield client


def _headers(session: str | None) -> dict[str, str]:
    if session is None:
        return {"Idempotency-Key": KEY}
    return {"Idempotency-Key": KEY, "x-session-id": session}


async def _post(client: httpx.AsyncClient, session: str | None) -> httpx.Response:
    return await client.post(
        "/v1/run", json={"input": {"text": "hello"}}, headers=_headers(session)
    )


async def test_with_memory_on_two_sessions_get_their_own_answers() -> None:
    state = InMemoryState()
    app = _app(state, memory=True)
    async with _client(app) as client:
        a = await _post(client, "session-a")
        b = await _post(client, "session-b")
        a_again = await _post(client, "session-a")
        b_again = await _post(client, "session-b")
    assert a.status_code == 200 and REPLAYED not in a.headers
    assert b.status_code == 200 and REPLAYED not in b.headers
    assert b.json()["request_id"] != a.json()["request_id"]
    assert a_again.headers[REPLAYED] == "true"
    assert a_again.json()["request_id"] == a.json()["request_id"]
    assert b_again.headers[REPLAYED] == "true"
    assert b_again.json()["request_id"] == b.json()["request_id"]
    engine = app.state.ports.engine
    assert isinstance(engine, FakeEngine) and engine.runs == 2
    for session in ("session-a", "session-b"):
        assert await state.get(store_key(AGENT, KEY, session_id=session)) is not None
    assert await state.get(store_key(AGENT, KEY)) is None


@pytest.mark.parametrize("session", [None, "x" * (SESSION_ID_MAX + 1)])
async def test_with_memory_on_and_no_usable_session_the_key_is_unscoped(
    session: str | None,
) -> None:
    state = InMemoryState()
    app = _app(state, memory=True)
    async with _client(app) as client:
        a = await _post(client, session)
        b = await _post(client, session)
    assert a.status_code == 200 and REPLAYED not in a.headers
    assert b.headers[REPLAYED] == "true"
    assert b.json()["request_id"] == a.json()["request_id"]
    engine = app.state.ports.engine
    assert isinstance(engine, FakeEngine) and engine.runs == 1
    assert await state.get(store_key(AGENT, KEY)) is not None


async def test_with_memory_off_the_session_header_does_not_scope_the_key() -> None:
    state = InMemoryState()
    app = _app(state, memory=False)
    async with _client(app) as client:
        a = await _post(client, "session-a")
        b = await _post(client, "session-b")
    assert a.status_code == 200 and REPLAYED not in a.headers
    assert b.headers[REPLAYED] == "true"
    assert b.json()["request_id"] == a.json()["request_id"]
    engine = app.state.ports.engine
    assert isinstance(engine, FakeEngine) and engine.runs == 1
    assert await state.get(store_key(AGENT, KEY)) is not None
