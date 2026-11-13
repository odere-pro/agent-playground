"""Result events on the wire (019 H-17): the CloudEvents envelope, the payload schema, the four
extensions, the key hash, the trace, and one event per call on every public interface.

Over `InMemoryBus`, no socket.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import jsonschema
import pytest
from chassis.core.envelope import Context, TaskInput
from chassis.core.events import Error, Event, Start
from chassis.core.handle import echo
from chassis.core.results import TASK_COMPLETED, TASK_FAILED
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.fakes.events import InMemoryBus
from chassis.fakes.state import InMemoryState
from chassis.ports.bundle import PortBundle
from chassis.schemas import SCHEMA_DIR
from chassis.server import ChassisConfig, create_app
from chassis.server.results import DATASCHEMA, ResultPublisher, key_hash
from chassis_contracts.events import assert_valid_cloudevent
from fastapi import FastAPI

KEY = "key-wire-0001"
TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
TRACEPARENT = f"00-{TRACE_ID}-00f067aa0ba902b7-01"
EXTENSIONS = ("traceparent", "idempotencykey", "configversion", "modelroute")

CALLS: dict[str, tuple[str, dict[str, Any]]] = {
    "native": ("/v1/run", {"input": {"text": "hello"}}),
    "openai": (
        "/v1/chat/completions",
        {"model": "echo", "messages": [{"role": "user", "content": "hello"}]},
    ),
    "anthropic": (
        "/v1/messages",
        {"model": "echo", "max_tokens": 64, "messages": [{"role": "user", "content": "hello"}]},
    ),
}


def _config() -> ChassisConfig:
    return ChassisConfig.model_validate(
        {
            "version": "cfg-1",
            "profile": "fake",
            "agent": {"name": "echo", "version": "0.0.1"},
            "spec": {
                # Required by the schema only: `_app` passes its own FakeEngine in the ports,
                # so this handle is never loaded.
                "engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"},
                "events": {"result_events": True},
            },
        }
    )


async def _fails(input: TaskInput, ctx: Context) -> AsyncIterator[Event]:
    yield Start(request_id=ctx.request_id)
    yield Error(code="workload_failed", message="boom", retryable=True)


def _app(bus: InMemoryBus, engine: FakeEngine | None = None) -> FastAPI:
    ports = PortBundle(
        model=ScriptedModel(),
        engine=engine or FakeEngine(handle=echo),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
        state=InMemoryState(),
        events=bus,
    )
    return create_app(_config(), ports)


@asynccontextmanager
async def _client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://chassis") as client:
            yield client


async def _settle(app: FastAPI) -> None:
    publisher = app.state.result_publisher
    assert isinstance(publisher, ResultPublisher)
    await publisher.wait(5)


def _payload_schema() -> dict[str, Any]:
    schema: dict[str, Any] = json.loads((Path(SCHEMA_DIR) / DATASCHEMA).read_text())
    return schema


@pytest.mark.parametrize(
    ("engine", "topic"),
    [(None, TASK_COMPLETED), (FakeEngine(handle=_fails), TASK_FAILED)],
    ids=["completed", "failed"],
)
async def test_every_result_event_passes_cloudevents_its_payload_schema_and_has_four_extensions(
    engine: FakeEngine | None, topic: str
) -> None:
    bus = InMemoryBus()
    app = _app(bus, engine)
    async with _client(app) as client:
        await client.post("/v1/run", json={"input": {"text": "hello"}})
        await _settle(app)

    (published,) = bus.published
    assert published[0] == topic
    wire = assert_valid_cloudevent(published[1])
    jsonschema.validate(wire["data"], _payload_schema())
    for name in EXTENSIONS:
        assert isinstance(wire.get(name), str) and wire[name], f"missing extension {name!r}"


async def test_the_idempotencykey_is_the_key_hash_and_the_traceparent_continues_the_call() -> None:
    bus = InMemoryBus()
    app = _app(bus)
    async with _client(app) as client:
        answer = await client.post(
            "/v1/run",
            json={"input": {"text": "hello"}},
            headers={"Idempotency-Key": KEY, "traceparent": TRACEPARENT},
        )
        await _settle(app)

    assert answer.status_code == 200
    wire = assert_valid_cloudevent(bus.published[0][1])
    assert wire["idempotencykey"] == key_hash(KEY) == wire["data"]["idempotency_key"]
    version, trace_id, parent_id, flags = wire["traceparent"].split("-")
    assert (version, trace_id, flags) == ("00", TRACE_ID, "01")
    assert re.fullmatch(r"[0-9a-f]{16}", parent_id) and parent_id != "0" * 16


@pytest.mark.parametrize("interface", list(CALLS))
async def test_one_result_event_per_call_and_none_for_a_replay(interface: str) -> None:
    path, body = CALLS[interface]
    bus = InMemoryBus()
    app = _app(bus)
    async with _client(app) as client:
        first = await client.post(path, json=body, headers={"Idempotency-Key": KEY})
        await _settle(app)
        again = await client.post(path, json=body, headers={"Idempotency-Key": KEY})
        await _settle(app)

    assert first.status_code == again.status_code == 200, again.text
    assert again.headers.get("idempotent-replayed") == "true"
    assert [topic for topic, _ in bus.published] == [TASK_COMPLETED]
    assert bus.published[0][1].idempotencykey == key_hash(KEY)
