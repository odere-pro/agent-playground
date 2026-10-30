"""The PoC-4 seams: the types the wave-1 packages build on. The port promises themselves are in
`chassis_contracts.state` and `chassis_contracts.events`, bound by their own packages.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from chassis.core.envelope import TaskInput, Versions
from chassis.core.results import TASK_COMPLETED, TaskResult
from chassis.fakes import InMemoryState
from chassis.ports.bundle import PortBundle
from chassis.ports.events import CloudEvent, NoEvents
from chassis.ports.state import StateUnavailable
from pydantic import ValidationError


def test_a_cloud_event_has_the_spec_defaults_and_drops_unset_extensions_on_the_wire() -> None:
    event = CloudEvent(
        id="e1",
        source="/agents/echo",
        type=TASK_COMPLETED,
        time=datetime(2026, 10, 1, tzinfo=UTC),
        data={"a": 1},
        partitionkey="k",
    )
    wire = json.loads(event.to_wire())
    assert wire["specversion"] == "1.0" and wire["datacontenttype"] == "application/json"
    assert wire["partitionkey"] == "k" and "traceparent" not in wire
    with pytest.raises(ValidationError):
        CloudEvent.model_validate({**wire, "unknown": 1})


async def test_no_events_drops_a_publish_and_refuses_a_subscription() -> None:
    events = NoEvents()
    event = CloudEvent(id="e", source="/s", type="t", time=datetime.now(UTC), data={})
    await events.publish("t", event)

    async def handler(_: CloudEvent) -> None:
        return None

    with pytest.raises(RuntimeError, match="no events adapter"):
        await events.subscribe("t", handler, group="g")


def test_a_task_result_carries_the_run_and_refuses_unknown_fields() -> None:
    result = TaskResult(
        agent="echo",
        agent_version="0.0.1",
        request_id="r",
        idempotency_key="k",
        status="ok",
        input=TaskInput(text="hi"),
        versions=Versions(chassis="0"),
    )
    assert result.output == {} and result.usage == {}
    with pytest.raises(ValidationError):
        TaskResult.model_validate({**result.model_dump(), "extra": 1})


async def test_in_memory_state_expires_on_its_clock_and_scripts_one_failure() -> None:
    now = [0.0]
    state = InMemoryState(clock=lambda: now[0])
    assert await state.set_if_absent("k", b"a", ttl_s=1)
    assert not await state.set_if_absent("k", b"b", ttl_s=1)
    now[0] = 1.0
    assert await state.get("k") is None
    state.fail_next()
    with pytest.raises(StateUnavailable):
        await state.get("k")
    assert await state.get("k") is None
    assert state.calls[0] == ("set_if_absent", "k")


def test_a_bundle_built_before_poc4_gets_a_store_and_no_events() -> None:
    from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel

    bundle = PortBundle(
        model=ScriptedModel(),
        engine=FakeEngine(events=[]),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
    )
    assert isinstance(bundle.state, InMemoryState) and isinstance(bundle.events, NoEvents)
