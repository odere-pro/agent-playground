"""PoC-1 walking skeleton: the plain-Python plug-in over A2A in memory, and contract v0.

Each test names the exit criterion in docs/planning/poc/001-PoC-1-walking-skeleton.md it covers.
No network: the chassis calls the template A2A server through an ASGI transport, and the
workload's model call goes back into the chassis app, through the proxy, to the scripted model.
"""

from __future__ import annotations

import importlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from a2a.types import SendMessageRequest, StreamResponse, TaskState
from chassis.adapters.a2a import InProcessConnector
from chassis.adapters.a2a.mapping import EVENT_KEY, request_to_message, update_to_event
from chassis.core.envelope import Response
from chassis.core.events import parse_event
from chassis.fakes import InMemoryConfig, InMemoryTelemetry, ScriptedModel, ScriptRule
from chassis.ports.bundle import PortBundle
from chassis.server import ChassisConfig, create_app
from chassis.server.proxy_app import create_proxy_app
from google.protobuf import json_format

ROOT = Path(__file__).resolve().parents[3]
FAKE_CONFIG = ROOT / "packages/chassis/configs/fake.yaml"
CONTRACT = ROOT / "docs/contracts/contract-v0.md"
SIMPLIFIED = "Plain words. Short sentences. Same facts."

CONFIG: dict[str, Any] = {
    "version": "cfg-poc1",
    "profile": "fake",
    "agent": {"name": "simplifier", "version": "0.0.1"},
    "spec": {
        "adapters": {"model": "fake"},
        "engine": {"connector": "inprocess", "handle": "echo_python:handle"},
        "model": {"route": "big-default"},
        "prompt": {"version": "simplifier-v1"},
    },
}


def _ports() -> PortBundle:
    return PortBundle(
        model=ScriptedModel([ScriptRule(match="simplify", reply=SIMPLIFIED)]),
        engine=InProcessConnector(),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
    )


@pytest.fixture
def app() -> Iterator[Any]:
    """The chassis app with `engine: inprocess`, and the workload's model call routed into its
    proxy app over an ASGI transport, so it goes through the chassis proxy to `ScriptedModel`.
    """
    app = create_app(ChassisConfig.model_validate(CONFIG), _ports())
    workload: Any = importlib.import_module("echo_python.handle")
    workload.transport = httpx.ASGITransport(app=create_proxy_app(app))
    try:
        yield app
    finally:
        workload.transport = None


def _frames(text: str) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for block in text.split("\n\n"):
        if block.strip():
            event, data = block.splitlines()[:2]
            out.append((event.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return out


async def test_plugin_answers_over_a2a_in_memory(app: Any) -> None:
    """Exit criterion: the plain-Python plug-in answers over A2A in memory. `POST /v1/run`, complete
    and streamed, returns the scripted simplified text; the lane is the A2A `inprocess` connector.
    """
    body = {"input": {"text": "simplify: the quick brown fox"}}
    transport = httpx.ASGITransport(app=app)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://chassis") as client,
        app.router.lifespan_context(app),
    ):
        assert isinstance(app.state.ports.engine, InProcessConnector)
        complete = Response.model_validate((await client.post("/v1/run", json=body)).json())
        streamed = await client.post("/v1/run", json={**body, "stream": True})
    assert complete.status == "ok"
    assert complete.output["text"] == SIMPLIFIED
    assert complete.metrics["input_tokens"] == 10 and complete.metrics["output_tokens"] == 5
    assert complete.metrics["model_route"] == "big-default"
    assert complete.versions.prompt == "simplifier-v1"
    frames = _frames(streamed.text)
    assert [f[0] for f in frames][:2] == ["start", "delta"]
    assert [f[0] for f in frames][-3:] == ["metrics", "end", "response"]
    assert "".join(f[1]["text"] for f in frames if f[0] == "delta") == SIMPLIFIED
    telemetry = app.state.ports.telemetry
    assert telemetry.counter_value("chassis.model_calls", route="big-default") == 2
    assert [s.name for s in telemetry.spans].count("chassis.engine.run") == 2
    assert all(
        s.attributes.get("a2a.task_id") for s in telemetry.spans if s.name == "chassis.engine.run"
    )


async def test_a2a_messages_validate_against_the_sdk_types(app: Any) -> None:
    """Exit criterion: the A2A messages validate against the a2a-sdk types. The request the
    connector sends and every stream event the server answers with serialize to JSON and parse
    back as `SendMessageRequest` and `StreamResponse`; each carries one valid chassis event.
    """
    async with app.router.lifespan_context(app):
        connector: InProcessConnector = app.state.ports.engine
        ctx = {
            "request_id": "req-poc1",
            "trace_id": "trace-poc1",
            "idempotency_key": "idem-poc1",
            "agent": "simplifier",
            "agent_version": "0.0.1",
            "budget": {"max_tokens": 2000, "timeout_ms": 30000},
            "versions": {"chassis": "0.1.0", "prompt": "simplifier-v1"},
            "model_route": "big-default",
        }
        request = request_to_message({"text": "simplify: the quick brown fox", "data": {}}, ctx)
        wire_request = json_format.ParseDict(
            json_format.MessageToDict(request), SendMessageRequest()
        )
        assert wire_request.message.context_id == "trace-poc1"
        assert connector._client is not None
        responses: list[StreamResponse] = [
            json_format.ParseDict(json_format.MessageToDict(r), StreamResponse())
            async for r in connector._client.send_message(wire_request)
        ]
    assert responses[0].HasField("task")
    assert responses[0].task.context_id == "trace-poc1"
    events = []
    for r in responses[1:]:
        raw = update_to_event(r)
        assert raw is not None, "every update after the task carries one chassis event"
        events.append(parse_event(raw))
        holder = (
            r.status_update.metadata
            if r.HasField("status_update")
            else r.artifact_update.artifact.metadata
        )
        assert EVENT_KEY in json_format.MessageToDict(holder)
    assert events[0].type == "start" and events[0].request_id == "req-poc1"
    assert events[-1].type == "end"
    assert responses[-1].status_update.status.state == TaskState.TASK_STATE_COMPLETED
    assert "".join(e.text for e in events if e.type == "delta") == SIMPLIFIED


def test_contract_v0_is_written_down() -> None:
    """Exit criterion: contract v0 is written down, including the mapping of chassis events to
    A2A; the fake profile names the `inprocess` lane and the plug-in.
    """
    text = CONTRACT.read_text()
    for section in (
        "## The `handle` contract",
        "## Events (`events.v0.json`)",
        "## Envelope",
        "## Ports (day 0)",
        "## `EngineConnector`, draft",
        "## Chassis events over A2A",
        "## Where the template A2A server lives",
    ):
        assert section in text, section
    for name in ("`start`", "`delta`", "`tool_call`", "`metrics`", "`end`", "`error`"):
        assert name in text
    config = yaml.safe_load(FAKE_CONFIG.read_text())
    assert "engine" not in config["spec"]["adapters"], "the lane is named once (v1 decision 2)"
    assert config["spec"]["engine"]["connector"] == "inprocess"
    assert config["spec"]["engine"]["handle"] == "echo_python:handle"
