"""Result events across lanes (019 H-17): a workload's events reach the broker only through the
chassis, with the same payload in the `inprocess` and `sidecar` lanes.

The chassis app runs with `spec.events.result_events: true` over `InMemoryBus`. One call goes
through the `inprocess` lane and one through the `sidecar` lane, the workload's A2A server served
over a Unix socket (`a2a_uds`, as in `test_lane_contract.py`). The published CloudEvent data must be
equal, but for the per-call `request_id`. The workload side is built with no ports and its handle
is given no event port or broker config. Offline: the gate refuses TCP and allows Unix sockets.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from a2a_uds import SIDECAR_URL, serve_uds
from chassis.adapters.a2a import InProcessConnector, SidecarConnector
from chassis.adapters.a2a.inprocess import load_handle
from chassis.adapters.a2a.server import build_agent_card, build_app
from chassis.core.handle import echo_wire
from chassis.core.results import TASK_COMPLETED
from chassis.fakes import InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.fakes.events import InMemoryBus
from chassis.fakes.state import InMemoryState
from chassis.ports.bundle import PortBundle
from chassis.ports.engine import EngineConnector
from chassis.server import ChassisConfig, create_app
from chassis.server.results import ResultPublisher
from chassis_contracts.events import assert_valid_cloudevent

AGENT = "echo"
HANDLE = "test_lane_result_events:recording_handle"
SEEN_CONTEXTS: list[dict[str, Any]] = []
"""The `ctx` every call of `recording_handle` received, in either lane."""
BROKER_WORDS = ("event", "broker", "kafka", "dapr", "topic", "bootstrap", "sasl")
PER_CALL = ("request_id",)
"""Data fields that differ per call by design; everything else must match across lanes."""


async def recording_handle(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
    """`echo_wire`, recording the context the workload is given."""
    SEEN_CONTEXTS.append(dict(ctx))
    async for event in echo_wire(input, ctx):
        yield event


def _config(engine: dict[str, Any]) -> ChassisConfig:
    return ChassisConfig.model_validate(
        {
            "version": "cfg-1",
            "profile": "fake",
            "agent": {"name": AGENT, "version": "0.0.1"},
            "spec": {"engine": engine, "events": {"result_events": True}},
        }
    )


def _ports(engine: EngineConnector, bus: InMemoryBus) -> PortBundle:
    return PortBundle(
        model=ScriptedModel(),
        engine=engine,
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
        state=InMemoryState(),
        events=bus,
    )


@asynccontextmanager
async def _chassis(
    engine: EngineConnector, spec: dict[str, Any], bus: InMemoryBus
) -> AsyncIterator[tuple[httpx.AsyncClient, ResultPublisher]]:
    app = create_app(_config(spec), _ports(engine, bus))
    async with app.router.lifespan_context(app):
        publisher = app.state.result_publisher
        assert isinstance(publisher, ResultPublisher)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://chassis") as client:
            yield client, publisher


async def _published(client: httpx.AsyncClient, publisher: ResultPublisher) -> dict[str, Any]:
    answer = await client.post(
        "/v1/run", json={"input": {"text": "hello"}}, headers={"Idempotency-Key": "key-0001"}
    )
    assert answer.status_code == 200, answer.text
    await publisher.wait(5)
    body: dict[str, Any] = answer.json()
    return body


async def _run_inprocess() -> tuple[dict[str, Any], InMemoryBus]:
    bus = InMemoryBus()
    spec = {"connector": "inprocess", "handle": HANDLE}
    async with _chassis(InProcessConnector(), spec, bus) as (client, publisher):
        result = await _published(client, publisher)
    return result, bus


async def _run_sidecar() -> tuple[dict[str, Any], InMemoryBus]:
    bus = InMemoryBus()
    workload = build_app(
        load_handle(HANDLE), build_agent_card(name=AGENT, version="1", url=SIDECAR_URL)
    )
    async with serve_uds(workload) as uds:
        spec = {"connector": "sidecar", "url": SIDECAR_URL, "uds": uds}
        async with _chassis(SidecarConnector(), spec, bus) as (client, publisher):
            result = await _published(client, publisher)
    return result, bus


def _only_event(bus: InMemoryBus) -> dict[str, Any]:
    assert [topic for topic, _ in bus.published] == [TASK_COMPLETED]
    wire = assert_valid_cloudevent(bus.published[0][1])
    return dict(wire)


def _stable(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if key not in PER_CALL}


@pytest.fixture(autouse=True)
def _fresh_contexts() -> None:
    SEEN_CONTEXTS.clear()


async def test_inprocess_and_sidecar_publish_the_same_result_event_data() -> None:
    inprocess, inprocess_bus = await _run_inprocess()
    sidecar, sidecar_bus = await _run_sidecar()

    first, second = _only_event(inprocess_bus), _only_event(sidecar_bus)
    assert first["data"]["request_id"] == inprocess["request_id"]
    assert second["data"]["request_id"] == sidecar["request_id"]
    assert first["data"]["output"] == {"text": "hello"}
    assert _stable(first["data"]) == _stable(second["data"])
    for attribute in ("type", "source", "dataschema", "idempotencykey", "configversion"):
        assert first[attribute] == second[attribute], attribute


async def test_the_workload_side_has_no_event_port_or_broker_config() -> None:
    await _run_inprocess()
    await _run_sidecar()

    # The workload's A2A server is built from a handle and a card only: no ports to hand it a bus.
    params = set(inspect.signature(build_app).parameters)
    assert params == {"handle", "card", "task_store"}
    # In both lanes the handle's context names no event port, topic, or broker setting.
    assert len(SEEN_CONTEXTS) == 2
    for ctx in SEEN_CONTEXTS:
        flat = json.dumps(ctx, default=str).lower()
        keys = " ".join(ctx).lower()
        assert not [word for word in BROKER_WORDS if word in keys], ctx
        assert "agents.task" not in flat
