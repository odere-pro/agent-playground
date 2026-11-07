"""Idempotency entries are scoped by the caller and hold no raw key (review R3-06, R4-29).

With auth on, the store key hashes the caller's subject with the client's key, so caller B never
gets caller A's stored result. With auth off, the store key is the same as before. A `done`
entry holds the sha256 hex of the client's key, never the key; a replay carries the caller's own
key. Offline: `InMemoryState`, and httpx over ASGI with `StaticTokens` as the auth port.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from chassis.core.envelope import Budget, Request, Response, TaskInput, Versions
from chassis.core.events import End, Event, Start
from chassis.core.handle import echo
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.fakes.auth import StaticTokens
from chassis.fakes.state import InMemoryState
from chassis.ports.auth import Principal
from chassis.ports.bundle import PortBundle
from chassis.server import ChassisConfig, create_app
from chassis.server.config import IdempotencySpec
from chassis.server.idempotency import Claim, Idempotency, Replay, store_key
from chassis.server.results import key_hash
from fastapi import FastAPI

AGENT = "echo"
KEY = "order-4711-customer-alice"
VERSIONS = Versions(chassis="c", config="cfg", prompt="p", model_route="route")
CONFIG: dict[str, Any] = {
    "version": "cfg-1",
    "profile": "fake",
    "agent": {"name": AGENT, "version": "0.0.1"},
    "spec": {"engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"}},
}
TOKENS = {
    "tok-a": Principal("user-a", frozenset({"agent:run"})),
    "tok-b": Principal("user-b", frozenset({"agent:run"})),
}
REPLAYED = "idempotent-replayed"


def _request(request_id: str = "r1", key: str = KEY) -> Request:
    return Request(
        request_id=request_id,
        trace_id=f"t-{request_id}",
        idempotency_key=key,
        agent=AGENT,
        agent_version="0.0.1",
        input=TaskInput(text="hello"),
        budget=Budget(timeout_ms=1000),
    )


def _events(request: Request) -> list[Event]:
    return [Start(request_id=request.request_id), End(output={"text": "HELLO"})]


def _response(request: Request) -> Response:
    return Response(
        request_id=request.request_id,
        trace_id=request.trace_id,
        idempotency_key=request.idempotency_key,
        agent=AGENT,
        agent_version="0.0.1",
        output={"text": "HELLO"},
        status="ok",
        versions=VERSIONS,
    )


def _idempotency(state: InMemoryState) -> Idempotency:
    return Idempotency(state, IdempotencySpec(), InMemoryTelemetry(), replica_id="replica-a")


async def _finished(idem: Idempotency, request: Request, subject: str | None = None) -> Claim:
    claim = await idem.begin(request, wait_ms=0, subject=subject)
    assert isinstance(claim, Claim), claim
    assert await claim.finish(_events(request), _response(request)) is True
    return claim


# The store key


def test_the_store_key_with_no_subject_is_byte_for_byte_as_before() -> None:
    before = "chassis:idem:v1:echo:" + hashlib.sha256(KEY.encode()).hexdigest()
    assert store_key(AGENT, KEY) == before
    assert store_key(AGENT, KEY, subject=None) == before


def test_the_store_key_with_a_subject_hashes_the_subject_and_the_key() -> None:
    canonical = json.dumps(["user-a", KEY], separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    assert store_key(AGENT, KEY, subject="user-a") == f"chassis:idem:v1:echo:{digest}"
    assert store_key(AGENT, KEY, subject="user-a") != store_key(AGENT, KEY, subject="user-b")
    assert store_key(AGENT, KEY, subject="user-a") != store_key(AGENT, KEY)
    assert "user-a" not in store_key(AGENT, KEY, subject="user-a")


def test_the_store_key_cannot_be_shifted_between_the_subject_and_the_key() -> None:
    assert store_key(AGENT, "b:c", subject="a") != store_key(AGENT, "c", subject="a:b")
    assert store_key(AGENT, "\ud800", subject="user-a").startswith("chassis:idem:v1:echo:")


async def test_caller_b_never_gets_caller_a_result_for_the_same_key() -> None:
    state = InMemoryState()
    idem = _idempotency(state)
    await _finished(idem, _request("r1"), subject="user-a")
    outcome = await idem.begin(_request("r2"), wait_ms=0, subject="user-b")
    assert isinstance(outcome, Claim)
    await outcome.release()
    again = await idem.begin(_request("r3"), wait_ms=0, subject="user-a")
    assert isinstance(again, Replay)


# The done entry


async def test_the_done_entry_never_holds_the_raw_key() -> None:
    state = InMemoryState()
    claim = await _finished(_idempotency(state), _request())
    raw = await state.get(claim.key)
    assert raw is not None and KEY.encode() not in raw
    entry = json.loads(raw)
    assert entry["v"] == 1
    assert entry["request"]["idempotency_key"] == key_hash(KEY)
    assert entry["response"]["idempotency_key"] == key_hash(KEY)
    assert len(entry["request"]["idempotency_key"]) == 64


async def test_a_replay_carries_the_caller_key_as_before() -> None:
    state = InMemoryState()
    idem = _idempotency(state)
    first = _request("r1")
    await _finished(idem, first)
    outcome = await idem.begin(_request("r2"), wait_ms=0)
    assert isinstance(outcome, Replay)
    assert outcome.request == first
    assert outcome.response == _response(first)


async def test_an_entry_written_with_the_raw_key_still_replays() -> None:
    """A `done` entry from a replica before this change holds the raw key; it still replays."""
    state = InMemoryState()
    first = _request("r1")
    old = {
        "v": 1,
        "state": "done",
        "fingerprint": Claim(_idempotency(state), first, "k", b"", since=0)._fingerprint,
        "request": first.model_dump(mode="json"),
        "events": [event.model_dump(mode="json") for event in _events(first)],
        "response": _response(first).model_dump(mode="json"),
    }
    await state.set(store_key(AGENT, KEY), json.dumps(old).encode(), ttl_s=60)
    outcome = await _idempotency(state).begin(_request("r2"), wait_ms=0)
    assert isinstance(outcome, Replay)
    assert outcome.request == first and outcome.response == _response(first)


# Through the HTTP app, auth on


def _app(state: InMemoryState) -> FastAPI:
    ports = PortBundle(
        model=ScriptedModel(),
        engine=FakeEngine(handle=echo),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
        state=state,
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


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Idempotency-Key": KEY}


async def test_over_http_caller_b_never_gets_caller_a_stored_response() -> None:
    state = InMemoryState()
    app = _app(state)
    body = {"input": {"text": "hello"}}
    async with _client(app) as client:
        first = await client.post("/v1/run", json=body, headers=_headers("tok-a"))
        other = await client.post("/v1/run", json=body, headers=_headers("tok-b"))
        again = await client.post("/v1/run", json=body, headers=_headers("tok-a"))
    assert first.status_code == 200 and REPLAYED not in first.headers
    assert other.status_code == 200 and REPLAYED not in other.headers
    assert other.json()["request_id"] != first.json()["request_id"]
    assert again.headers[REPLAYED] == "true"
    assert again.json()["request_id"] == first.json()["request_id"]
    assert again.json()["idempotency_key"] == KEY
    engine = app.state.ports.engine
    assert isinstance(engine, FakeEngine) and engine.runs == 2
    for subject in ("user-a", "user-b"):
        raw = await state.get(store_key(AGENT, KEY, subject=subject))
        assert raw is not None and KEY.encode() not in raw
