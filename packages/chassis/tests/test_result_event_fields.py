"""Two result-event payload fields (019 H-17, decided 2026-11-13).

- `input` is left out of a result event unless `spec.events.include_input` is true (default
  false): until topic ACLs per service are in (020 X-8), caller data stays off a shared broker.
  Checked in the `inprocess` and `sidecar` lanes, through `InMemoryBus`.
- The model-source field (which model produced the original text) is `original_model`. It is
  declared once in code (`TaskResult`), sits in the published schema and on every published
  event (null until 043 A-3 fills it), and is defined in the result-events section of
  `docs/specs/contract.md`. `TaskResult` never serializes a null `input`.

The change to `task-result.v1.json` is a break made in place by user decision, before any consumer
exists (ADR-016): a new default event fails the previous v1 shape, which a test records. The new
schema still reads a payload in the previous v1 shape. Offline: Unix sockets only, no TCP, no keys.
"""

from __future__ import annotations

import ast
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import jsonschema
import pytest
from a2a_uds import SIDECAR_URL, serve_uds
from chassis.adapters.a2a import InProcessConnector, SidecarConnector
from chassis.adapters.a2a.inprocess import load_handle
from chassis.adapters.a2a.server import build_agent_card, build_app
from chassis.core.envelope import TaskInput, Versions
from chassis.core.results import TASK_COMPLETED, TaskResult
from chassis.fakes import InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.fakes.events import InMemoryBus
from chassis.fakes.state import InMemoryState
from chassis.ports.bundle import PortBundle
from chassis.ports.engine import EngineConnector
from chassis.schemas import SCHEMA_DIR
from chassis.server import ChassisConfig, create_app
from chassis.server.config import EventsSpec
from chassis.server.results import DATASCHEMA, ResultPublisher
from chassis_contracts.events import assert_valid_cloudevent

AGENT = "echo"
HANDLE = "chassis.core.handle:echo_wire"
FIELD = "original_model"
TEXT = "hello"
REPO = Path(__file__).resolve().parents[3]
CHASSIS_SRC = REPO / "packages" / "chassis" / "src" / "chassis"
SPEC_DOCS = REPO / "docs" / "specs"
LANES = ("inprocess", "sidecar")


def _config(engine: dict[str, Any], events: dict[str, Any]) -> ChassisConfig:
    return ChassisConfig.model_validate(
        {
            "version": "cfg-1",
            "profile": "fake",
            "agent": {"name": AGENT, "version": "0.0.1"},
            "spec": {"engine": engine, "events": {"result_events": True, **events}},
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


async def _call(
    engine: EngineConnector, spec: dict[str, Any], events: dict[str, Any], bus: InMemoryBus
) -> None:
    app = create_app(_config(spec, events), _ports(engine, bus))
    async with app.router.lifespan_context(app):
        publisher = app.state.result_publisher
        assert isinstance(publisher, ResultPublisher)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://chassis") as client:
            answer = await client.post("/v1/run", json={"input": {"text": TEXT}})
            assert answer.status_code == 200, answer.text
            await publisher.wait(5)


@asynccontextmanager
async def _lane(lane: str) -> AsyncIterator[tuple[EngineConnector, dict[str, Any]]]:
    if lane == "inprocess":
        yield InProcessConnector(), {"connector": "inprocess", "handle": HANDLE}
        return
    workload = build_app(
        load_handle(HANDLE), build_agent_card(name=AGENT, version="1", url=SIDECAR_URL)
    )
    async with serve_uds(workload) as uds:
        yield SidecarConnector(), {"connector": "sidecar", "url": SIDECAR_URL, "uds": uds}


async def _published_data(lane: str, events: dict[str, Any]) -> dict[str, Any]:
    bus = InMemoryBus()
    async with _lane(lane) as (engine, spec):
        await _call(engine, spec, events, bus)
    assert [topic for topic, _ in bus.published] == [TASK_COMPLETED]
    wire = assert_valid_cloudevent(bus.published[0][1])
    data: dict[str, Any] = dict(wire["data"])
    jsonschema.validate(data, _payload_schema())
    return data


def _payload_schema() -> dict[str, Any]:
    schema: dict[str, Any] = json.loads((Path(SCHEMA_DIR) / DATASCHEMA).read_text())
    return schema


def test_include_input_defaults_to_false() -> None:
    assert EventsSpec().include_input is False


@pytest.mark.parametrize("lane", LANES)
async def test_a_result_event_has_no_input_by_default(lane: str) -> None:
    data = await _published_data(lane, {})
    assert "input" not in data
    assert TEXT not in json.dumps({k: v for k, v in data.items() if k != "output"})


@pytest.mark.parametrize("lane", LANES)
async def test_a_result_event_has_the_input_when_include_input_is_true(lane: str) -> None:
    data = await _published_data(lane, {"include_input": True})
    assert data["input"] == {"text": TEXT, "data": {}}


@pytest.mark.parametrize("lane", LANES)
async def test_the_published_event_carries_original_model(lane: str) -> None:
    data = await _published_data(lane, {})
    assert FIELD in data
    assert data[FIELD] is None  # filled for the simplifier by 043 A-3, null until then


def _field_declarations(name: str) -> list[str]:
    """Every annotated `name: ...` in a class body under `chassis/`, as `file:Class`."""
    found = []
    for path in sorted(CHASSIS_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for item in node.body:
                target = item.target if isinstance(item, ast.AnnAssign) else None
                if isinstance(target, ast.Name) and target.id == name:
                    found.append(f"{path.relative_to(CHASSIS_SRC).as_posix()}:{node.name}")
    return found


def test_original_model_is_defined_once_in_code_and_in_the_schema() -> None:
    assert FIELD in TaskResult.model_fields
    schema = _payload_schema()
    assert FIELD in schema["properties"]
    assert schema["properties"][FIELD].get("description"), "the schema documents the field"
    assert _field_declarations(FIELD) == ["core/results.py:TaskResult"]


def _result_events_section() -> str:
    contract = (SPEC_DOCS / "contract.md").read_text()
    (section,) = [part for part in contract.split("\n\n") if part.startswith("**Result events**")]
    return section


def test_original_model_is_documented_in_the_result_events_section() -> None:
    section = _result_events_section()
    assert f"`{FIELD}` is the model-source field: the model that produced the original text" in (
        section
    )


def _minimal_result(**fields: Any) -> TaskResult:
    return TaskResult(
        agent=AGENT,
        agent_version="0.0.1",
        request_id="req-1",
        idempotency_key="0" * 64,
        status="ok",
        versions=Versions(chassis="0.0.0"),
        **fields,
    )


def test_a_task_result_never_serializes_a_null_input() -> None:
    unset = _minimal_result().model_dump(mode="json")
    assert "input" not in unset
    assert unset[FIELD] is None
    jsonschema.validate(unset, _payload_schema())
    given = _minimal_result(input=TaskInput(text=TEXT)).model_dump(mode="json")
    assert given["input"] == {"text": TEXT, "data": {}}
    jsonschema.validate(given, _payload_schema())


PREVIOUS_V1_REQUIRED = (
    "agent",
    "agent_version",
    "request_id",
    "idempotency_key",
    "status",
    "input",
    "versions",
)
PREVIOUS_V1_PROPERTIES = (*PREVIOUS_V1_REQUIRED, "output", "usage")
"""Frozen from `task-result.v1.json` before 2026-11-13: closed, `input` required."""


def _previous_v1_schema() -> dict[str, Any]:
    """The previous v1 shape: the current field schemas, cut back to the old set and rules."""
    current = _payload_schema()
    return {
        **current,
        "properties": {name: current["properties"][name] for name in PREVIOUS_V1_PROPERTIES},
        "required": list(PREVIOUS_V1_REQUIRED),
        "additionalProperties": False,
    }


async def test_a_new_default_event_breaks_the_previous_v1_shape_known_and_decided() -> None:
    """ADR-016: the break of the v1 payload is known and decided, made before any consumer."""
    data = await _published_data("inprocess", {})
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(data, _previous_v1_schema())


def test_an_older_v1_payload_is_still_readable() -> None:
    """Read-side only: the new schema and model accept a payload in the earlier v1 shape."""
    previous = {
        "agent": AGENT,
        "agent_version": "0.0.1",
        "request_id": "req-1",
        "idempotency_key": "0" * 64,
        "status": "ok",
        "input": {"text": TEXT, "data": {}},
        "output": {"text": TEXT},
        "usage": {},
        "versions": {"chassis": "0.0.0"},
    }
    jsonschema.validate(previous, _payload_schema())
    assert TaskResult.model_validate(previous).input is not None
    # `input` is absent or a `TaskInput` on the wire, never null.
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**previous, "input": None}, _payload_schema())
