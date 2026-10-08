"""Idempotency in `serve` on every interface (PoC-4 P11; plan section 4).

Two app instances share one `InMemoryState`, which stands for Valkey: two replicas. A key the
client sent runs once; a repeat on either replica replays the cached result with the header
`Idempotent-Replayed: true`, in complete and stream mode, without calling the engine. The same key
with another input is 422 `idempotency_conflict`; a duplicate still in flight past its own
`budget.timeout_ms` is 409 `idempotency_in_progress`; a failing store is 503 `state_unavailable`.
A call with no key never touches the store. No socket: `httpx.ASGITransport`.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

import httpx
import pytest
from chassis.core.envelope import Context, TaskInput
from chassis.core.events import End, Error, Event, Start
from chassis.core.handle import echo
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.fakes.events import InMemoryBus
from chassis.ports.bundle import PortBundle
from chassis.ports.state import InMemoryState, StateUnavailable
from chassis.server import ChassisConfig, create_app
from chassis.server.idempotency import Idempotency, store_key
from chassis.server.interfaces.ids import resolve_ids
from chassis.server.pipeline import Run
from fastapi import FastAPI

AGENT = "echo"
CONFIG: dict[str, Any] = {
    "version": "cfg-1",
    "profile": "fake",
    "agent": {"name": AGENT, "version": "0.0.1"},
    "spec": {"engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"}},
}
REPLAYED = "idempotent-replayed"
KEY = "key-0001"


def _app(state: InMemoryState, engine: FakeEngine | None = None) -> FastAPI:
    ports = PortBundle(
        model=ScriptedModel(),
        engine=engine or FakeEngine(handle=echo),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
        state=state,
    )
    return create_app(ChassisConfig.model_validate(CONFIG), ports)


@asynccontextmanager
async def _clients(*apps: FastAPI) -> AsyncIterator[list[httpx.AsyncClient]]:
    async with AsyncExitStack() as stack:
        clients = []
        for app in apps:
            await stack.enter_async_context(app.router.lifespan_context(app))
            transport = httpx.ASGITransport(app=app)
            client = httpx.AsyncClient(transport=transport, base_url="http://chassis")
            clients.append(await stack.enter_async_context(client))
        yield clients


def _engine(app: FastAPI) -> FakeEngine:
    engine = app.state.ports.engine
    assert isinstance(engine, FakeEngine)
    return engine


def _telemetry(app: FastAPI) -> InMemoryTelemetry:
    telemetry = app.state.ports.telemetry
    assert isinstance(telemetry, InMemoryTelemetry)
    return telemetry


def _call(interface: str, text: str = "hello", *, stream: bool = False) -> tuple[str, Any]:
    if interface == "native":
        return "/v1/run", {"input": {"text": text}, "stream": stream}
    if interface == "openai":
        body: dict[str, Any] = {"model": AGENT, "messages": [{"role": "user", "content": text}]}
        return "/v1/chat/completions", {**body, "stream": stream}
    body = {"model": AGENT, "max_tokens": 64, "messages": [{"role": "user", "content": text}]}
    return "/v1/messages", {**body, "stream": stream}


def _without_created(value: Any) -> Any:
    """A chat body with the reply time (`created`) dropped: it is the time of the answer."""
    if isinstance(value, dict):
        return {k: _without_created(v) for k, v in value.items() if k != "created"}
    if isinstance(value, list):
        return [_without_created(v) for v in value]
    return value


def _frames(text: str) -> list[Any]:
    out: list[Any] = []
    for block in text.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: ") and line != "data: [DONE]":
                out.append(_without_created(json.loads(line.removeprefix("data: "))))
    return out


INTERFACES = ["native", "openai", "anthropic"]


@pytest.mark.parametrize("interface", INTERFACES)
async def test_complete_replays_on_another_replica(interface: str) -> None:
    state = InMemoryState()
    first, second = _app(state), _app(state)
    path, body = _call(interface)
    headers = {"Idempotency-Key": KEY}
    async with _clients(first, second) as (one, two):
        original = await one.post(path, json=body, headers=headers)
        replay = await two.post(path, json=body, headers=headers)
    assert original.status_code == 200, original.text
    assert REPLAYED not in original.headers
    assert replay.status_code == 200, replay.text
    assert replay.headers[REPLAYED] == "true"
    assert _without_created(replay.json()) == _without_created(original.json())
    assert _engine(first).runs == 1 and _engine(second).runs == 0
    telemetry = _telemetry(second)
    assert telemetry.counter_value("chassis.idempotency.replayed", interface=interface) == 1
    spans = [s for s in telemetry.spans if s.name == "chassis.idempotent_replay"]
    assert [s.attributes for s in spans] == [{"interface": interface, "mode": "complete"}]
    assert not [s for s in telemetry.spans if s.name == "chassis.run"]


@pytest.mark.parametrize("interface", INTERFACES)
async def test_stream_replay_sends_the_same_events(interface: str) -> None:
    state = InMemoryState()
    first, second = _app(state), _app(state)
    path, body = _call(interface, stream=True)
    headers = {"Idempotency-Key": KEY}
    async with _clients(first, second) as (one, two):
        original = await one.post(path, json=body, headers=headers)
        replay = await two.post(path, json=body, headers=headers)
    assert original.status_code == 200 and replay.status_code == 200, replay.text
    assert REPLAYED not in original.headers
    assert replay.headers[REPLAYED] == "true"
    assert replay.headers["content-type"].startswith("text/event-stream")
    assert _frames(replay.text) == _frames(original.text) != []
    assert _engine(second).runs == 0
    spans = [s for s in _telemetry(second).spans if s.name == "chassis.idempotent_replay"]
    assert [s.attributes["mode"] for s in spans] == ["stream"]


async def test_a_retry_that_switches_to_streaming_replays_the_stored_run() -> None:
    state = InMemoryState()
    app = _app(state)
    headers = {"Idempotency-Key": KEY}
    async with _clients(app) as (client,):
        first = await client.post("/v1/run", json={"input": {"text": "a"}}, headers=headers)
        again = await client.post(
            "/v1/run", json={"input": {"text": "a"}, "stream": True}, headers=headers
        )
    assert again.headers[REPLAYED] == "true"
    response = [f for f in again.text.split("\n\n") if f.startswith("event: response")]
    assert json.loads(response[0].split("data: ", 1)[1]) == first.json()
    assert _engine(app).runs == 1


async def test_body_key_is_a_client_key() -> None:
    state = InMemoryState()
    first, second = _app(state), _app(state)
    body = {"input": {"text": "a"}, "idempotency_key": KEY}
    async with _clients(first, second) as (one, two):
        original = await one.post("/v1/run", json=body)
        replay = await two.post("/v1/run", json=body)
    assert replay.headers[REPLAYED] == "true"
    assert replay.json() == original.json()
    assert replay.json()["request_id"] == original.json()["request_id"]


def test_key_from_names_where_the_key_came_from() -> None:
    assert resolve_ids({"idempotency-key": "h"}, idempotency_key="b").key_from == "header"
    assert resolve_ids({}, idempotency_key="b").key_from == "body"
    assert resolve_ids({}).key_from == "minted"
    assert resolve_ids({"idempotency-key": ""}).key_from == "minted"


async def test_no_key_means_no_check() -> None:
    state = InMemoryState()
    first, second = _app(state), _app(state)
    async with _clients(first, second) as (one, two):
        a = await one.post("/v1/run", json={"input": {"text": "a"}})
        b = await two.post("/v1/run", json={"input": {"text": "a"}})
    assert a.status_code == b.status_code == 200
    assert REPLAYED not in b.headers
    assert _engine(first).runs == _engine(second).runs == 1
    assert state.calls == []


async def test_disabled_means_no_check() -> None:
    state = InMemoryState()
    config = {**CONFIG, "spec": {**CONFIG["spec"], "idempotency": {"enabled": False}}}
    ports = PortBundle(
        model=ScriptedModel(),
        engine=FakeEngine(handle=echo),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
        state=state,
    )
    app = create_app(ChassisConfig.model_validate(config), ports)
    async with _clients(app) as (client,):
        for _ in range(2):
            res = await client.post(
                "/v1/run", json={"input": {"text": "a"}}, headers={"Idempotency-Key": KEY}
            )
            assert REPLAYED not in res.headers
    assert _engine(app).runs == 2 and state.calls == []


@pytest.mark.parametrize("interface", INTERFACES)
async def test_same_key_other_input_is_422(interface: str) -> None:
    state = InMemoryState()
    first, second = _app(state), _app(state)
    headers = {"Idempotency-Key": KEY}
    async with _clients(first, second) as (one, two):
        path, body = _call(interface, "first")
        assert (await one.post(path, json=body, headers=headers)).status_code == 200
        path, body = _call(interface, "second")
        res = await two.post(path, json=body, headers=headers)
    assert res.status_code == 422, res.text
    assert REPLAYED not in res.headers
    data = res.json()
    if interface == "native":
        assert data["detail"]["code"] == "idempotency_conflict"
        assert data["detail"]["message"]
    elif interface == "openai":
        assert data["error"]["code"] == "idempotency_conflict"
        assert data["error"]["type"] == "invalid_request_error"
        assert res.headers["x-should-retry"] == "false"
    else:
        assert data["error"]["type"] == "invalid_request_error"
        assert data["error"]["message"].startswith("idempotency_conflict")
        assert res.headers["x-should-retry"] == "false"
    assert _engine(second).runs == 0


async def _hold_key(state: InMemoryState, app: FastAPI, text: str) -> None:
    """Claim the key as another replica would with a run in flight (same fingerprint)."""
    pipeline = app.state.pipeline
    request = pipeline.to_request(input=TaskInput(text=text), idempotency_key=KEY)
    idem = Idempotency(state, pipeline.config.spec.idempotency, InMemoryTelemetry())
    claim = await idem.begin(request, wait_ms=0)
    assert claim.__class__.__name__ == "Claim"


async def test_duplicate_in_flight_past_its_timeout_is_409() -> None:
    state = InMemoryState()
    app = _app(state)
    async with _clients(app) as (client,):
        await _hold_key(state, app, "a")
        body = {"input": {"text": "a"}, "budget": {"timeout_ms": 30}}
        res = await client.post("/v1/run", json=body, headers={"Idempotency-Key": KEY})
    assert res.status_code == 409, res.text
    assert res.json()["detail"]["code"] == "idempotency_in_progress"
    assert _engine(app).runs == 0


class _BrokenState(InMemoryState):
    async def set_if_absent(self, key: str, value: bytes, *, ttl_s: float | None = None) -> bool:
        raise StateUnavailable("down")


@pytest.mark.parametrize("interface", INTERFACES)
async def test_state_unavailable_is_503_for_a_keyed_call(interface: str) -> None:
    app = _app(_BrokenState())
    path, body = _call(interface)
    async with _clients(app) as (client,):
        keyed = await client.post(path, json=body, headers={"Idempotency-Key": KEY})
        plain = await client.post(path, json=body)
    assert keyed.status_code == 503, keyed.text
    assert plain.status_code == 200
    data = keyed.json()
    if interface == "native":
        assert isinstance(data["detail"], str)
    elif interface == "openai":
        assert data["error"]["type"] == "server_error"
        assert keyed.headers["x-should-retry"] == "true"
    else:
        assert data["error"]["type"] == "overloaded_error"
    assert _engine(app).runs == 1


async def _fails(input: TaskInput, ctx: Context) -> AsyncIterator[Event]:
    yield Start(request_id=ctx.request_id)
    yield Error(code="workload_failed", message="boom", retryable=True)


async def test_a_run_error_is_not_cached() -> None:
    state = InMemoryState()
    app = _app(state, FakeEngine(handle=_fails))
    async with _clients(app) as (client,):
        for _ in range(2):
            res = await client.post(
                "/v1/run", json={"input": {"text": "a"}}, headers={"Idempotency-Key": KEY}
            )
            assert res.json()["status"] == "error"
            assert REPLAYED not in res.headers
    assert _engine(app).runs == 2
    assert await state.get(store_key(AGENT, KEY)) is None


async def test_a_stream_run_is_cached_after_its_end() -> None:
    state = InMemoryState()
    app = _app(state)
    async with _clients(app) as (client,):
        body = {"input": {"text": "a"}, "stream": True}
        res = await client.post("/v1/run", json=body, headers={"Idempotency-Key": KEY})
        assert res.status_code == 200
    raw = await state.get(store_key(AGENT, KEY))
    assert raw is not None and json.loads(raw)["state"] == "done"
    assert json.loads(raw)["events"][-1]["type"] == End().type


@pytest.mark.parametrize("stream", [False, True])
async def test_on_finished_runs_once_after_each_run(stream: bool) -> None:
    app = _app(InMemoryState())
    seen: list[tuple[bool, int]] = []

    async def record(run: Run) -> None:
        seen.append((run.done, len(run.seen)))

    async with _clients(app) as (client,):
        app.state.pipeline.on_finished.append(record)
        res = await client.post("/v1/run", json={"input": {"text": "a"}, "stream": stream})
        assert res.status_code == 200
    assert len(seen) == 1 and seen[0][0] is True and seen[0][1] > 0


async def test_a_failing_on_finished_callback_does_not_change_the_answer() -> None:
    app = _app(InMemoryState())

    async def broken(run: Run) -> None:
        raise RuntimeError("publisher down")

    async with _clients(app) as (client,):
        app.state.pipeline.on_finished.append(broken)
        res = await client.post("/v1/run", json={"input": {"text": "a"}})
    assert res.status_code == 200
    assert any(log["message"] == "on_finished callback failed" for log in _telemetry(app).logs)


# --- the refusals match the declared OpenAPI responses (the property test is not the only guard)


def _declared(app: FastAPI, path: str, status: int) -> dict[str, Any]:
    spec = app.openapi()
    response = spec["paths"][path]["post"]["responses"][str(status)]
    schema = response["content"]["application/json"]["schema"]
    return {**schema, "components": spec["components"]}


async def test_native_refusal_bodies_match_their_declared_schemas() -> None:
    import jsonschema

    state = InMemoryState()
    app = _app(state)
    headers = {"Idempotency-Key": KEY}
    async with _clients(app, _app(_BrokenState())) as (client, broken):
        await client.post("/v1/run", json={"input": {"text": "a"}}, headers=headers)
        conflict = await client.post("/v1/run", json={"input": {"text": "b"}}, headers=headers)
        invalid = await client.post("/v1/run", json={"input": {"text": 1}})
        unavailable = await broken.post("/v1/run", json={"input": {"text": "a"}}, headers=headers)
    assert (conflict.status_code, invalid.status_code) == (422, 422)
    assert conflict.json()["detail"]["code"] == "idempotency_conflict"
    assert isinstance(invalid.json()["detail"], list)
    assert unavailable.status_code == 503
    for res in (conflict, invalid, unavailable):
        jsonschema.Draft202012Validator(_declared(app, "/v1/run", res.status_code)).validate(
            res.json()
        )


async def test_native_409_in_progress_matches_its_declared_schema() -> None:
    import jsonschema

    state = InMemoryState()
    app = _app(state)
    async with _clients(app) as (client,):
        await _hold_key(state, app, "a")
        body = {"input": {"text": "a"}, "budget": {"timeout_ms": 1}}
        res = await client.post("/v1/run", json=body, headers={"Idempotency-Key": KEY})
    assert res.status_code == 409
    jsonschema.Draft202012Validator(_declared(app, "/v1/run", 409)).validate(res.json())


CHAT_PATHS = {"openai": "/v1/chat/completions", "anthropic": "/v1/messages"}


@pytest.mark.parametrize("interface", ["openai", "anthropic"])
async def test_chat_refusal_bodies_match_their_declared_schemas(
    interface: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """409, 422, and 503 on the OpenAI and Anthropic routes, each checked against the app's own
    OpenAPI response for that status (contract v3, "Refusals")."""
    import jsonschema

    state = InMemoryState()
    app, broken_app = _app(state), _app(_BrokenState())
    path = CHAT_PATHS[interface]
    headers = {"Idempotency-Key": KEY}
    async with _clients(app, broken_app) as (client, broken):
        _, first = _call(interface, "first")
        assert (await client.post(path, json=first, headers=headers)).status_code == 200
        _, second = _call(interface, "second")
        conflict = await client.post(path, json=second, headers=headers)
        _, body = _call(interface, "a")
        unavailable = await broken.post(path, json=body, headers=headers)

        # A chat body cannot set `budget.timeout_ms`, so the wait is cut to 1 ms here, and the
        # key is claimed first with the call's own request (same fingerprint): a run in flight.
        original = Idempotency.begin

        async def behind_a_held_claim(self: Idempotency, request: Any, *, wait_ms: int) -> Any:
            held = await original(self, request, wait_ms=0)
            assert held.__class__.__name__ == "Claim"
            return await original(self, request, wait_ms=1)

        monkeypatch.setattr(Idempotency, "begin", behind_a_held_claim)
        _, body = _call(interface, "in flight")
        in_progress = await client.post(path, json=body, headers={"Idempotency-Key": "key-0002"})
    assert conflict.status_code == 422, conflict.text
    assert unavailable.status_code == 503, unavailable.text
    assert in_progress.status_code == 409, in_progress.text
    for res in (conflict, unavailable, in_progress):
        declared = _declared(app, path, res.status_code)
        # FastAPI's own 422 (`HTTPValidationError`) would accept any object; it must not stay.
        assert "HTTPValidationError" not in json.dumps(
            {k: v for k, v in declared.items() if k != "components"}
        )
        jsonschema.Draft202012Validator(declared).validate(res.json())


# --- live config: a reload reaches the next request -------------------------------------------


async def test_a_reloaded_ttl_reaches_the_lazy_idempotency_layer() -> None:
    app = _app(InMemoryState())
    async with _clients(app):
        pipeline = app.state.pipeline
        assert pipeline.idempotency.spec.ttl_s == 86_400
        spec = {**CONFIG["spec"], "idempotency": {"ttl_s": 60}}
        app.state.config = ChassisConfig.model_validate({**CONFIG, "spec": spec})
        assert pipeline.idempotency.spec.ttl_s == 60


@pytest.mark.parametrize("interface", ["openai", "anthropic"])
async def test_a_reloaded_messages_max_applies_to_the_next_chat_request(interface: str) -> None:
    app = _app(InMemoryState())
    path, body = _call(interface)
    body = {**body, "messages": [{"role": "user", "content": "a"}] * 3}
    async with _clients(app) as (client,):
        assert (await client.post(path, json=body)).status_code == 200
        spec = {**CONFIG["spec"], "limits": {"messages_max": 2}}
        app.state.config = ChassisConfig.model_validate({**CONFIG, "spec": spec})
        refused = await client.post(path, json=body)
    assert refused.status_code == 400, refused.text
    assert "limit_exceeded" in refused.text


# MUST FIX 1: a replica cut off from the store mid-run is fenced; one result, one event


class _FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds
        await asyncio.sleep(0)


class _Ticker:
    """The renew task's sleep: returns only on `tick`."""

    def __init__(self) -> None:
        self.ticks: asyncio.Queue[None] = asyncio.Queue()
        self.waits: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.waits.append(seconds)
        await self.ticks.get()


class _CutOff:
    """One replica's view of the shared store; raises `StateUnavailable` while `cut`."""

    def __init__(self, inner: InMemoryState) -> None:
        self.inner = inner
        self.cut = False

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self.inner, name)
        if name.startswith("_") or not callable(attr):
            return attr

        async def call(*args: Any, **kwargs: Any) -> Any:
            if self.cut:
                raise StateUnavailable("cut off")
            return await attr(*args, **kwargs)

        return call


@pytest.mark.parametrize(
    ("interface", "stream"), [("native", False), ("native", True), ("openai", True)]
)
async def test_a_replica_cut_off_from_the_store_is_fenced_and_one_result_is_kept(
    interface: str, stream: bool
) -> None:
    clock, ticker = _FakeClock(), _Ticker()
    shared = InMemoryState(clock=clock)
    view = _CutOff(shared)
    bus = InMemoryBus()
    started = asyncio.Event()
    cancelled: list[bool] = []

    async def stuck(input: TaskInput, ctx: Context) -> AsyncIterator[Event]:
        yield Start(request_id=ctx.request_id)
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(True)
            raise
        yield End(output={"text": "never"})

    config = ChassisConfig.model_validate(
        {**CONFIG, "spec": {**CONFIG["spec"], "events": {"result_events": True}}}
    )

    def build(state: Any, engine: FakeEngine) -> FastAPI:
        ports = PortBundle(
            model=ScriptedModel(),
            engine=engine,
            config=InMemoryConfig(),
            telemetry=InMemoryTelemetry(),
            state=state,
            events=bus,
        )
        return create_app(config, ports)

    first = build(view, FakeEngine(handle=stuck))
    second = build(shared, FakeEngine(handle=echo))
    path, body = _call(interface, "a", stream=stream)
    headers = {"Idempotency-Key": KEY}
    async with _clients(first, second) as (one, two):
        spec = config.spec.idempotency
        first.state.pipeline._idempotency = Idempotency(
            view,
            spec,
            _telemetry(first),
            replica_id="replica-a",
            clock=clock,
            sleep=clock.sleep,
            renew_sleep=ticker.sleep,
        )
        second.state.pipeline._idempotency = Idempotency(
            shared,
            spec,
            _telemetry(second),
            replica_id="replica-b",
            clock=clock,
            sleep=clock.sleep,
            renew_sleep=_Ticker().sleep,
        )
        call_a = asyncio.create_task(one.post(path, json=body, headers=headers))
        await asyncio.wait_for(started.wait(), 2)
        await asyncio.sleep(0)
        view.cut = True
        start = clock.now
        for _ in range(10):
            if _telemetry(first).counter_value("chassis.idempotency.fenced"):
                break
            clock.now += ticker.waits[-1]
            ticker.ticks.put_nowait(None)
            for _ in range(5):
                await asyncio.sleep(0)
        assert clock.now - start < spec.lease_s  # fenced before the lease can pass on
        answer_a = await asyncio.wait_for(call_a, 2)

        clock.now += spec.lease_s  # the store lease of replica A ends
        view.cut = False
        answer_b = await two.post(path, json=body, headers=headers)
        again_a = await one.post(path, json=body, headers=headers)

    assert cancelled == [True]
    if stream and interface == "native":
        assert answer_a.status_code == 200
        frames = _frames(answer_a.text)
        errors = [f for f in frames if f.get("type") == "error"]
        assert [(e["code"], e["retryable"]) for e in errors] == [("idempotency_in_progress", True)]
        assert frames[-1]["status"] == "error"
    else:
        assert answer_a.status_code == 409, answer_a.text
        if interface == "native":
            assert answer_a.json()["detail"]["code"] == "idempotency_in_progress"
        else:
            assert answer_a.headers["x-should-retry"] == "true"
    assert answer_b.status_code == 200 and REPLAYED not in answer_b.headers
    assert again_a.status_code == 200 and again_a.headers[REPLAYED] == "true"
    assert _engine(first).runs == 1 and _engine(second).runs == 1
    assert len(bus.published) == 1  # only replica B's run is reported
    entry = json.loads(await shared.get(store_key(AGENT, KEY)) or b"")
    assert entry["state"] == "done"
    assert entry["request"]["request_id"] == bus.published[0][1].subject


async def test_a_takeover_after_a_wait_runs_with_the_budget_left() -> None:
    """The duplicate waited for a dead holder's lease; its run gets what is left of its own
    `timeout_ms`, not a fresh one, so the client never waits about twice its timeout."""
    clock = _FakeClock()
    shared = InMemoryState(clock=clock)
    budgets: list[int] = []

    async def record(input: TaskInput, ctx: Context) -> AsyncIterator[Event]:
        budgets.append(ctx.budget.timeout_ms)
        yield Start(request_id=ctx.request_id)
        yield End(output={"text": input.text})

    app = _app(shared, FakeEngine(handle=record))
    async with _clients(app) as (client,):
        pipeline = app.state.pipeline
        spec = pipeline.config.spec.idempotency
        dead = Idempotency(
            shared, spec, InMemoryTelemetry(), clock=clock, renew_sleep=_Ticker().sleep
        )
        request = pipeline.to_request(input=TaskInput(text="a"), idempotency_key=KEY)
        assert (await dead.begin(request, wait_ms=0)).__class__.__name__ == "Claim"
        pipeline._idempotency = Idempotency(
            shared, spec, _telemetry(app), clock=clock, sleep=clock.sleep
        )
        timeout_ms = int(spec.lease_s * 1000) + 3000
        body = {"input": {"text": "a"}, "budget": {"timeout_ms": timeout_ms}}
        res = await client.post("/v1/run", json=body, headers={"Idempotency-Key": KEY})
    assert res.status_code == 200, res.text
    assert len(budgets) == 1 and 2500 <= budgets[0] <= 3000, budgets
