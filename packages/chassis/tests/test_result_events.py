"""Result events (PoC-4 P14; plan section 2, use 1; 019 H-17).

With `spec.events.result_events: true` the chassis publishes `agents.task.completed.v1` after a
run that ended with `end` and `agents.task.failed.v1` after one that ended with `error`. The
payload is a `TaskResult` inside a CloudEvents 1.0 envelope, partitioned by the idempotency key.
Publishing runs in a background task: a failing broker never changes the answer. A replay
publishes nothing. Shutdown waits for pending publishes. Over `InMemoryBus`, no socket.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
from chassis.core.envelope import Context, TaskInput
from chassis.core.events import Error, Event, Start
from chassis.core.handle import echo
from chassis.core.results import TASK_COMPLETED, TASK_FAILED, TaskResult
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.fakes.events import InMemoryBus
from chassis.ports.bundle import PortBundle
from chassis.ports.events import CloudEvent
from chassis.ports.state import InMemoryState
from chassis.server import ChassisConfig, create_app, results
from chassis.server.results import (
    PUBLISH_ABANDONED,
    PUBLISH_FAILED,
    PUBLISHED,
    ResultPublisher,
    event_id,
)
from chassis_contracts.events import assert_valid_cloudevent
from fastapi import FastAPI

AGENT = "echo"
KEY = "key-0001"
KEY_HASH = hashlib.sha256(KEY.encode()).hexdigest()


def _config(*, result_events: bool = True) -> ChassisConfig:
    return ChassisConfig.model_validate(
        {
            "version": "cfg-1",
            "profile": "fake",
            "agent": {"name": AGENT, "version": "0.0.1"},
            "spec": {
                "engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"},
                "events": {"result_events": result_events},
            },
        }
    )


async def _fails(input: TaskInput, ctx: Context) -> AsyncIterator[Event]:
    yield Start(request_id=ctx.request_id)
    yield Error(code="workload_failed", message="boom", retryable=True)


def _app(
    bus: InMemoryBus, *, result_events: bool = True, engine: FakeEngine | None = None
) -> FastAPI:
    ports = PortBundle(
        model=ScriptedModel(),
        engine=engine or FakeEngine(handle=echo),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
        state=InMemoryState(),
        events=bus,
    )
    return create_app(_config(result_events=result_events), ports)


@asynccontextmanager
async def _client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://chassis") as client:
            yield client


def _telemetry(app: FastAPI) -> InMemoryTelemetry:
    telemetry = app.state.ports.telemetry
    assert isinstance(telemetry, InMemoryTelemetry)
    return telemetry


def _publisher(app: FastAPI) -> ResultPublisher:
    publisher = app.state.result_publisher
    assert isinstance(publisher, ResultPublisher)
    return publisher


async def _run(client: httpx.AsyncClient, text: str = "hello", **headers: str) -> httpx.Response:
    return await client.post("/v1/run", json={"input": {"text": text}}, headers=headers)


async def test_a_completed_run_publishes_one_valid_cloudevent() -> None:
    bus = InMemoryBus()
    app = _app(bus)
    async with _client(app) as client:
        answer = await _run(client, **{"Idempotency-Key": KEY})
        assert answer.status_code == 200
        await _publisher(app).wait(5)
        body = answer.json()
        assert len(bus.published) == 1
        topic, event = bus.published[0]
        assert topic == TASK_COMPLETED
        wire = assert_valid_cloudevent(event)
        assert wire["type"] == TASK_COMPLETED
        assert wire["source"] == f"/agents/{AGENT}"
        assert wire["subject"] == body["request_id"]
        assert wire["partitionkey"] == KEY_HASH
        assert wire["idempotencykey"] == KEY_HASH
        assert wire["id"] == event_id(body["request_id"], TASK_COMPLETED)
        assert wire["configversion"] == "cfg-1"
        assert wire["dataschema"] == "task-result.v1.json"
        result = TaskResult.model_validate(event.data)
        assert result.status == "ok"
        assert result.agent == AGENT
        assert result.request_id == body["request_id"]
        assert result.idempotency_key == KEY_HASH
        assert KEY not in json.dumps(wire, default=str)  # the raw key is nowhere in the event
        assert result.input.text == "hello"
        assert result.output == body["output"]
        assert _telemetry(app).counter_value(PUBLISHED, topic=TASK_COMPLETED) == 1


async def test_a_failed_run_publishes_the_failed_type() -> None:
    bus = InMemoryBus()
    app = _app(bus, engine=FakeEngine(handle=_fails))
    async with _client(app) as client:
        await _run(client)
        await _publisher(app).wait(5)
    assert [topic for topic, _ in bus.published] == [TASK_FAILED]
    event = bus.published[0][1]
    assert_valid_cloudevent(event)
    assert event.type == TASK_FAILED
    assert TaskResult.model_validate(event.data).status == "error"


async def test_a_replayed_idempotent_call_publishes_nothing_new() -> None:
    bus = InMemoryBus()
    app = _app(bus)
    async with _client(app) as client:
        first = await _run(client, **{"Idempotency-Key": KEY})
        await _publisher(app).wait(5)
        again = await _run(client, **{"Idempotency-Key": KEY})
        await _publisher(app).wait(5)
    assert first.status_code == again.status_code == 200
    assert again.headers.get("idempotent-replayed") == "true"
    assert len(bus.published) == 1


async def test_result_events_off_publishes_nothing() -> None:
    bus = InMemoryBus()
    app = _app(bus, result_events=False)
    async with _client(app) as client:
        assert (await _run(client)).status_code == 200
        assert app.state.result_publisher is None
        assert app.state.pipeline.on_finished == []
    assert bus.published == []


async def test_a_failing_broker_does_not_fail_the_run() -> None:
    bus = InMemoryBus()
    bus.fail_next_publish()
    app = _app(bus)
    async with _client(app) as client:
        answer = await _run(client)
        assert answer.status_code == 200
        assert answer.json()["status"] == "ok"
        await _publisher(app).wait(5)
        telemetry = _telemetry(app)
        assert telemetry.counter_value(PUBLISH_FAILED, topic=TASK_COMPLETED) == 1
        assert telemetry.counter_value(PUBLISHED, topic=TASK_COMPLETED) == 0
        logged = [log for log in telemetry.logs if log["message"] == "result event not published"]
        assert len(logged) == 1
        assert "hello" not in repr(logged[0])  # never the payload
        assert (await _run(client)).status_code == 200  # the next one goes out
        await _publisher(app).wait(5)
    assert len(bus.published) == 1


class _SlowBus(InMemoryBus):
    def __init__(self) -> None:
        super().__init__()
        self.gate = asyncio.Event()

    async def publish(self, topic: str, event: CloudEvent) -> None:
        await self.gate.wait()
        await super().publish(topic, event)


async def test_a_slow_broker_never_delays_the_answer() -> None:
    bus = _SlowBus()
    app = _app(bus)
    async with _client(app) as client:
        answer = await asyncio.wait_for(_run(client), timeout=5)
        assert answer.status_code == 200
        assert bus.published == []
        assert _publisher(app).pending == 1
        bus.gate.set()
        await _publisher(app).wait(5)
        assert len(bus.published) == 1


async def test_pending_publishes_are_flushed_at_shutdown() -> None:
    bus = _SlowBus()
    app = _app(bus)
    async with _client(app) as client:
        await _run(client)
        await _run(client, "again")
        assert bus.published == []
        asyncio.get_running_loop().call_later(0.05, bus.gate.set)
    # the lifespan waited for both before it closed the ports
    assert [topic for topic, _ in bus.published] == [TASK_COMPLETED, TASK_COMPLETED]


async def test_a_publish_stuck_past_the_shutdown_bound_is_dropped_and_counted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(results, "SHUTDOWN_WAIT_S", 0.05)
    bus = _SlowBus()  # never opens
    app = _app(bus)
    async with _client(app) as client:
        await _run(client)
    assert bus.published == []
    assert _telemetry(app).counter_value(PUBLISH_ABANDONED) == 1


async def test_the_publisher_is_registered_once_per_lifespan() -> None:
    bus = InMemoryBus()
    app = _app(bus)
    for _ in range(2):
        async with _client(app) as client:
            assert len(app.state.pipeline.on_finished) == 1
            await _run(client)
    assert app.state.pipeline.on_finished == []
    assert len(bus.published) == 2


def test_the_event_id_is_fixed_per_run_and_type() -> None:
    """A consumer dedupes a second delivery of one result by `id`: the same run and type give
    the same id; another run or type gives another."""
    one = event_id("r1", TASK_COMPLETED)
    assert one == event_id("r1", TASK_COMPLETED)
    assert one != event_id("r2", TASK_COMPLETED)
    assert one != event_id("r1", TASK_FAILED)
    assert one and len(one) == 32
