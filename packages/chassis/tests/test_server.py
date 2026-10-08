"""The HTTP surface: `/health`, `/ready`, and `/v1/run` streaming and complete."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from chassis import CHASSIS_VERSION
from chassis.core.envelope import Context, Request, Response, TaskInput
from chassis.core.events import Delta, Event, Start
from chassis.core.handle import echo
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.ports.bundle import PortBundle
from chassis.server import ChassisConfig, create_app
from chassis.server.config import load_config

CONFIG: dict[str, Any] = {
    "version": "cfg-1",
    "profile": "fake",
    "agent": {"name": "echo", "version": "0.0.1"},
    "spec": {
        "engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo"},
        "model": {"route": "fake-route"},
        "prompt": {"version": "p1"},
    },
}


def _ports(engine: FakeEngine | None = None) -> PortBundle:
    return PortBundle(
        model=ScriptedModel(),
        engine=engine or FakeEngine(handle=echo),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
    )


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://chassis")


def _frames(text: str) -> list[tuple[str, dict[str, Any]]]:
    frames: list[tuple[str, dict[str, Any]]] = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        lines = block.splitlines()
        assert lines[0].startswith("event: ") and lines[1].startswith("data: "), block
        frames.append((lines[0][7:], json.loads(lines[1][6:])))
    return frames


def test_config_loads_from_a_dict_and_a_file(tmp_path: Any) -> None:
    config = ChassisConfig.model_validate(CONFIG)
    assert config.version == "cfg-1"
    assert config.spec.engine.connector == "inprocess"
    assert config.spec.engine.as_mapping()["handle"] == "chassis.core.handle:echo"
    path = tmp_path / "c.yaml"
    path.write_text("profile: fake\nagent: {name: a, version: '1'}\nspec: {model: {route: r}}\n")
    loaded = load_config(path)
    assert loaded.version is not None and len(loaded.version) == 12
    assert loaded.spec.engine.connector == "inprocess"
    assert loaded.spec.model.route == "r"


def test_config_refuses_the_lane_the_profile_forbids() -> None:
    with pytest.raises(ValueError, match="inprocess"):
        ChassisConfig.model_validate({**CONFIG, "profile": "cloud"})


async def test_health_is_always_ok_and_ready_waits_for_setup() -> None:
    engine = FakeEngine(handle=echo)
    app = create_app(ChassisConfig.model_validate(CONFIG), _ports(engine))
    async with _client(app) as client:
        assert (await client.get("/health")).json() == {"status": "ok"}
        assert (await client.get("/ready")).status_code == 503
        async with app.router.lifespan_context(app):
            assert engine.setup_config is not None
            assert engine.setup_config["handle"] == "chassis.core.handle:echo"
            ready = await client.get("/ready")
            assert ready.status_code == 200 and ready.json() == {"status": "ready"}
        assert engine.closed
        assert (await client.get("/ready")).status_code == 503


async def test_complete_run_returns_a_response_with_versions() -> None:
    ports = _ports()
    app = create_app(ChassisConfig.model_validate(CONFIG), ports)
    async with _client(app) as client, app.router.lifespan_context(app):
        res = await client.post("/v1/run", json={"input": {"text": "hello big world"}})
    assert res.status_code == 200, res.text
    response = Response.model_validate(res.json())
    assert response.status == "ok"
    assert response.output["text"] == "hello big world"
    assert response.agent == "echo" and response.agent_version == "0.0.1"
    assert len(response.request_id) == 32 and response.trace_id and response.idempotency_key
    assert response.versions.chassis == CHASSIS_VERSION
    assert response.versions.config == "cfg-1"
    assert response.versions.prompt == "p1"
    assert response.versions.model_route == "fake-route"
    assert response.metrics["model_route"] == "fake-route"
    telemetry = ports.telemetry
    assert isinstance(telemetry, InMemoryTelemetry)
    assert [s.name for s in telemetry.spans] == ["chassis.run"]
    assert telemetry.spans[0].attributes["request_id"] == response.request_id
    assert telemetry.counter_value("chassis.requests", agent="echo") == 1


async def test_caller_ids_are_kept() -> None:
    app = create_app(ChassisConfig.model_validate(CONFIG), _ports())
    body = {"request_id": "r1", "trace_id": "t1", "idempotency_key": "i1", "input": {"text": "x"}}
    async with _client(app) as client, app.router.lifespan_context(app):
        res = await client.post("/v1/run", json=body)
    out = res.json()
    assert (out["request_id"], out["trace_id"], out["idempotency_key"]) == ("r1", "t1", "i1")


async def test_stream_and_complete_carry_the_same_output() -> None:
    app = create_app(ChassisConfig.model_validate(CONFIG), _ports())
    body = {"input": {"text": "the quick brown fox"}}
    async with _client(app) as client, app.router.lifespan_context(app):
        complete = (await client.post("/v1/run", json=body)).json()
        streamed = await client.post("/v1/run", json={**body, "stream": True})
    assert streamed.headers["content-type"].startswith("text/event-stream")
    frames = _frames(streamed.text)
    assert frames[0][0] == "start"
    assert frames[-1][0] == "response"
    assert [f[0] for f in frames[1:-1]].count("end") == 1
    text = "".join(f[1]["text"] for f in frames if f[0] == "delta")
    assert text == complete["output"]["text"] == "the quick brown fox"
    response = Response.model_validate(frames[-1][1])
    assert response.output["text"] == text
    assert response.versions == Response.model_validate(complete).versions


async def _boom(input: TaskInput, ctx: Context) -> AsyncIterator[Event]:
    yield Start(request_id=ctx.request_id)
    yield Delta(text="partial")
    raise RuntimeError("engine fell over")


async def test_engine_error_becomes_status_error() -> None:
    app = create_app(ChassisConfig.model_validate(CONFIG), _ports(FakeEngine(handle=_boom)))
    body = {"input": {"text": "x"}}
    async with _client(app) as client, app.router.lifespan_context(app):
        complete = await client.post("/v1/run", json=body)
        streamed = await client.post("/v1/run", json={**body, "stream": True})
    assert complete.status_code == 200
    out = complete.json()
    assert out["status"] == "error"
    assert out["output"]["error"]["code"] == "engine_error"
    assert "engine fell over" in out["output"]["error"]["message"]
    assert streamed.status_code == 200
    frames = _frames(streamed.text)
    assert [f[0] for f in frames] == ["start", "delta", "error", "response"]
    assert frames[-1][1]["status"] == "error"


async def test_unknown_field_is_422() -> None:
    app = create_app(ChassisConfig.model_validate(CONFIG), _ports())
    async with _client(app) as client, app.router.lifespan_context(app):
        res = await client.post("/v1/run", json={"input": {"text": "x"}, "bogus": 1})
    assert res.status_code == 422


async def test_ports_are_built_from_the_profile_when_not_injected() -> None:
    app = create_app(ChassisConfig.model_validate(CONFIG))
    async with _client(app) as client:
        assert (await client.get("/ready")).status_code == 503
        async with app.router.lifespan_context(app):
            assert (await client.get("/ready")).status_code == 200
            res = await client.post("/v1/run", json={"input": {"text": "hi there"}})
    assert res.json()["output"]["text"] == "hi there"
    assert Request.model_validate(
        {
            "request_id": "r",
            "trace_id": "t",
            "idempotency_key": "i",
            "agent": "echo",
            "agent_version": "0.0.1",
            "input": {"text": "hi there"},
        }
    )
