"""Regex redaction of what the chassis stores (PoC-7, 022 H-6): the result event a run publishes
and the chassis's own run-error log hold `<EMAIL_ADDRESS>` and `<PHONE_NUMBER>`, never the
values. The answer to the caller is not redacted: it is not a stored record.

Over `InMemoryBus` and `InMemoryTelemetry`, no socket.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from chassis.core.envelope import Context, TaskInput
from chassis.core.events import Delta, End, Error, Event, Start
from chassis.core.results import TASK_COMPLETED, TASK_FAILED, TaskResult
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.fakes.events import InMemoryBus
from chassis.ports.bundle import PortBundle
from chassis.ports.state import InMemoryState
from chassis.server import ChassisConfig, create_app
from chassis.server.results import ResultPublisher
from chassis_contracts.events import assert_valid_cloudevent
from fastapi import FastAPI

EMAIL = "jane.doe@example.com"
PHONE = "+31 20 123 4567"
PII = f"mail {EMAIL} or call {PHONE}"


def _config() -> ChassisConfig:
    return ChassisConfig.model_validate(
        {
            "version": "cfg-1",
            "profile": "fake",
            "agent": {"name": "echo", "version": "0.0.1"},
            "spec": {
                "engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"},
                "events": {"result_events": True},
            },
        }
    )


def _app(bus: InMemoryBus, engine: FakeEngine) -> FastAPI:
    ports = PortBundle(
        model=ScriptedModel(),
        engine=engine,
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


async def _published(app: FastAPI, bus: InMemoryBus) -> tuple[httpx.Response, str, TaskResult]:
    async with _client(app) as client:
        answer = await client.post("/v1/run", json={"input": {"text": PII}})
        publisher = app.state.result_publisher
        assert isinstance(publisher, ResultPublisher)
        await publisher.wait(5)
    ((_, event),) = bus.published
    wire = json.dumps(assert_valid_cloudevent(event), default=str)
    return answer, wire, TaskResult.model_validate(event.data)


def _assert_redacted(text: str) -> None:
    assert EMAIL not in text
    assert PHONE not in text
    assert "<EMAIL_ADDRESS>" in text
    assert "<PHONE_NUMBER>" in text


async def test_a_completed_runs_event_holds_no_pii() -> None:
    bus = InMemoryBus()
    engine = FakeEngine(events=[Delta(text=PII), End(output={"text": PII, "parts": [PII]})])
    answer, wire, result = await _published(_app(bus, engine), bus)
    assert bus.published[0][0] == TASK_COMPLETED
    assert answer.json()["output"]["text"] == PII, "the caller's answer is not redacted"
    _assert_redacted(wire)
    assert result.output == {
        "text": "mail <EMAIL_ADDRESS> or call <PHONE_NUMBER>",
        "parts": ["mail <EMAIL_ADDRESS> or call <PHONE_NUMBER>"],
    }
    assert result.input.text == "mail <EMAIL_ADDRESS> or call <PHONE_NUMBER>"


async def _fails(input: TaskInput, ctx: Context) -> AsyncIterator[Event]:
    yield Start(request_id=ctx.request_id)
    yield Error(code="workload_failed", message=f"no account for {EMAIL}, {PHONE}", retryable=False)


async def test_a_failed_runs_event_and_error_log_hold_no_pii() -> None:
    bus = InMemoryBus()
    app = _app(bus, FakeEngine(handle=_fails))
    _, wire, result = await _published(app, bus)
    assert bus.published[0][0] == TASK_FAILED
    _assert_redacted(wire)
    assert result.output["error"]["message"] == "no account for <EMAIL_ADDRESS>, <PHONE_NUMBER>"
    telemetry = app.state.ports.telemetry
    assert isinstance(telemetry, InMemoryTelemetry)
    (log,) = [entry for entry in telemetry.logs if entry["message"] == "run error"]
    assert log["detail"] == "no account for <EMAIL_ADDRESS>, <PHONE_NUMBER>"
    _assert_redacted(json.dumps(telemetry.logs, default=str))
