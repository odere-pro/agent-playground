"""The HTTP surface: `/health`, `/ready`, and `/v1/run` streaming and complete on the public app;
the model proxy on its own app that shares the public app's state; one run record per run.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from chassis import CHASSIS_VERSION
from chassis.core.envelope import Context, Request, Response, TaskInput
from chassis.core.events import Delta, End, Event, Start
from chassis.core.handle import echo
from chassis.fakes import (
    FakeEngine,
    InMemoryConfig,
    InMemoryTelemetry,
    ScriptedModel,
    ScriptRule,
)
from chassis.ports.bundle import PortBundle
from chassis.profiles import REGISTRY, LaneNotAllowed
from chassis.server import ChassisConfig, cli, create_app
from chassis.server.config import RELOADABLE, RESTART_ONLY, load_config
from chassis.server.correlation import RunRecord
from chassis.server.proxy_app import create_proxy_app
from fastapi import FastAPI

CONFIG: dict[str, Any] = {
    "version": "cfg-1",
    "profile": "fake",
    "agent": {"name": "echo", "version": "0.0.1"},
    "spec": {
        "engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"},
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
    assert config.spec.engine.as_mapping()["handle"] == "chassis.core.handle:echo_wire"
    path = tmp_path / "c.yaml"
    path.write_text("profile: fake\nagent: {name: a, version: '1'}\nspec: {model: {route: r}}\n")
    loaded = load_config(path)
    assert loaded.version is not None and len(loaded.version) == 12
    assert loaded.spec.engine.connector == "sidecar", "the default lane is sidecar (ADR-001)"
    assert loaded.spec.model.route == "r"


def test_config_refuses_the_lane_the_profile_forbids() -> None:
    with pytest.raises(ValueError, match="inprocess"):
        ChassisConfig.model_validate({**CONFIG, "profile": "cloud"})


def test_config_refuses_adapters_engine_and_names_the_one_field() -> None:
    """Contract v1 decision 2: `spec.adapters.engine` is removed; the lane is named once."""
    spec = {**CONFIG["spec"], "adapters": {"model": "fake", "engine": "inprocess"}}
    with pytest.raises(ValueError, match=r"spec\.engine\.connector"):
        load_config({**CONFIG, "spec": spec})


async def test_lifespan_refuses_a_built_engine_that_is_not_the_named_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the lifespan builds the ports, the engine's `kind` must be `spec.engine.connector`."""
    monkeypatch.setitem(REGISTRY["engine"], "sidecar", lambda: FakeEngine(handle=echo))
    config = {**CONFIG, "spec": {**CONFIG["spec"], "engine": {"connector": "sidecar"}}}
    app = create_app(ChassisConfig.model_validate(config))
    with pytest.raises(LaneNotAllowed, match=r"spec\.engine\.connector is 'sidecar'"):
        async with app.router.lifespan_context(app):
            pass
    assert app.state.ready is False


class _ClosingModel(ScriptedModel):
    closed = False

    async def aclose(self) -> None:
        self.closed = True


async def test_lifespan_closes_the_model_adapter() -> None:
    model = _ClosingModel()
    ports = PortBundle(
        model=model,
        engine=FakeEngine(handle=echo),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
    )
    app = create_app(ChassisConfig.model_validate(CONFIG), ports)
    async with app.router.lifespan_context(app):
        assert not model.closed
    assert model.closed and isinstance(ports.engine, FakeEngine) and ports.engine.closed


@pytest.mark.parametrize(
    ("field", "value"), [("agent", "someone-else"), ("agent_version", "9.9.9")]
)
async def test_run_refuses_another_agent_with_400(field: str, value: str) -> None:
    """A body may name the agent this chassis serves, or leave it out; any other is a 400."""
    engine = FakeEngine(handle=echo)
    app = create_app(ChassisConfig.model_validate(CONFIG), _ports(engine))
    body = {"input": {"text": "x"}}
    async with _client(app) as client, app.router.lifespan_context(app):
        refused = await client.post("/v1/run", json={**body, field: value})
        streamed = await client.post("/v1/run", json={**body, field: value, "stream": True})
        same = await client.post(
            "/v1/run", json={**body, "agent": "echo", "agent_version": "0.0.1"}
        )
    assert refused.status_code == streamed.status_code == 400
    assert field in refused.json()["detail"] and value in refused.json()["detail"]
    assert same.status_code == 200 and same.json()["agent"] == "echo"
    assert engine.runs == 1, "a refused run never reaches the engine"


async def test_health_is_always_ok_and_ready_waits_for_setup() -> None:
    engine = FakeEngine(handle=echo)
    app = create_app(ChassisConfig.model_validate(CONFIG), _ports(engine))
    async with _client(app) as client:
        assert (await client.get("/health")).json() == {"status": "ok"}
        assert (await client.get("/ready")).status_code == 503
        async with app.router.lifespan_context(app):
            assert engine.setup_config is not None
            assert engine.setup_config["handle"] == "chassis.core.handle:echo_wire"
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
    assert telemetry.counter_value("chassis.requests", agent="echo", interface="native") == 1


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


CHAT: dict[str, Any] = {"model": "fake-route", "messages": [{"role": "user", "content": "spend"}]}


async def test_the_model_proxy_is_on_the_proxy_app_only() -> None:
    """PoC-1 debt paid: the model proxy (the route that reaches `ports.model`) is on the proxy
    app only. The public app has the same path since PoC-3, with another meaning: the OpenAI
    interface, where `model` is the agent, not a route, and nothing reaches `ports.model` directly.
    """
    ports = _ports()
    app = create_app(ChassisConfig.model_validate(CONFIG), ports)
    proxy = create_proxy_app(app)
    model = ports.model
    assert isinstance(model, ScriptedModel)
    telemetry = ports.telemetry
    assert isinstance(telemetry, InMemoryTelemetry)
    async with (
        _client(app) as public,
        _client(proxy) as local,
        app.router.lifespan_context(app),
    ):
        route = await public.post("/v1/chat/completions", json=CHAT)
        assert route.status_code == 404, "a model route is not a model on the public app"
        assert route.json()["error"]["code"] == "model_not_found"
        agent = await public.post("/v1/chat/completions", json={**CHAT, "model": "echo"})
        assert agent.status_code == 200 and agent.json()["object"] == "chat.completion"
        assert model.calls == [], "the public path never reaches ports.model"
        assert telemetry.counter_value("chassis.model_calls", route="fake-route") == 0
        assert (await local.post("/v1/chat/completions", json=CHAT)).status_code == 200
        assert len(model.calls) == 1
        assert telemetry.counter_value("chassis.model_calls", route="fake-route") == 1
        assert (await local.post("/v1/run", json={"input": {"text": "x"}})).status_code == 404
        assert (await local.get("/health")).status_code == 404
    public_op = app.openapi()["paths"]["/v1/chat/completions"]["post"]
    assert public_op["operationId"] == "chat_completions"
    assert public_op["x-chassis-interface"] == "openai"


async def test_the_tool_endpoint_is_on_the_proxy_app_only() -> None:
    """`/mcp` moved off the public port with the model proxy (`deploy/CLAUDE.md`)."""
    app = create_app(ChassisConfig.model_validate(CONFIG), _ports())
    proxy = create_proxy_app(app)
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    async with _client(app) as public, _client(proxy) as local:
        assert (await public.post("/mcp", json=body)).status_code == 404
        assert (await local.post("/mcp", json=body)).status_code == 503, "503 before the lifespan"
        async with app.router.lifespan_context(app):
            assert (await public.post("/mcp", json=body)).status_code == 404
            served = await local.post(
                "/mcp",
                json=body,
                headers={"accept": "application/json, text/event-stream"},
            )
            assert served.status_code == 200, served.text


async def test_the_two_apps_share_state_and_readiness() -> None:
    app = create_app(ChassisConfig.model_validate(CONFIG), _ports())
    proxy = create_proxy_app(app)
    assert proxy.state is app.state
    async with _client(proxy) as local:
        assert (await local.post("/v1/chat/completions", json=CHAT)).status_code == 503
        async with app.router.lifespan_context(app):
            assert proxy.state.ports is app.state.ports
            assert (await local.post("/v1/chat/completions", json=CHAT)).status_code == 200
        assert (await local.post("/v1/chat/completions", json=CHAT)).status_code == 503


async def test_run_holds_a_record_for_its_life_streaming_and_complete() -> None:
    seen: list[RunRecord | None] = []
    holder: dict[str, Any] = {}

    async def look(input: TaskInput, ctx: Context) -> AsyncIterator[Event]:
        yield Start(request_id=ctx.request_id)
        seen.append(holder["app"].state.runs.lookup(ctx.trace_id))
        yield End(status="ok")

    app = create_app(ChassisConfig.model_validate(CONFIG), _ports(FakeEngine(handle=look)))
    holder["app"] = app
    body = {"trace_id": "c" * 32, "input": {"text": "x"}, "budget": {"max_tokens": 7}}
    async with _client(app) as client, app.router.lifespan_context(app):
        await client.post("/v1/run", json=body)
        await client.post("/v1/run", json={**body, "stream": True})
        assert len(app.state.runs) == 0
    assert len(seen) == 2 and all(r is not None for r in seen)
    assert seen[0] is not seen[1]
    assert all(r is not None and r.budget.max_tokens == 7 for r in seen)


async def test_two_concurrent_runs_each_get_their_own_budget() -> None:
    """Exit criterion (PoC-2, chassis half): two concurrent requests in one replica each get their
    own budget. Both runs are in flight at once. Run A spends 15 tokens a call against a budget of
    20: its third call is refused with 429. Run B then calls while A is exhausted and is served.
    """
    holder: dict[str, Any] = {}
    records: dict[str, RunRecord | None] = {}
    both_in_flight = asyncio.Event()
    a_done = asyncio.Event()

    async def spend(input: TaskInput, ctx: Context) -> AsyncIterator[Event]:
        yield Start(request_id=ctx.request_id)
        records[ctx.request_id] = holder["app"].state.runs.lookup(ctx.trace_id)
        if len(records) == 2:
            both_in_flight.set()
        await asyncio.wait_for(both_in_flight.wait(), timeout=5)
        if input.data.get("after_a"):
            await asyncio.wait_for(a_done.wait(), timeout=5)
        statuses: list[str] = []
        headers = {"traceparent": f"00-{ctx.trace_id}-00f067aa0ba902b7-01"}
        async with _client(holder["proxy"]) as proxy:
            for _ in range(int(input.data["calls"])):
                res = await proxy.post("/v1/chat/completions", json=CHAT, headers=headers)
                statuses.append(str(res.status_code))
        if not input.data.get("after_a"):
            a_done.set()
        yield Delta(text=",".join(statuses))
        yield End(status="ok")

    ports = PortBundle(
        model=ScriptedModel([ScriptRule(match="spend", reply="ok")]),
        engine=FakeEngine(handle=spend),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
    )
    app = create_app(ChassisConfig.model_validate(CONFIG), ports)
    holder["app"], holder["proxy"] = app, create_proxy_app(app)
    run_a = {
        "request_id": "run-a",
        "trace_id": "a" * 32,
        "input": {"data": {"calls": 3}},
        "budget": {"max_tokens": 20},
        "stream": True,
    }
    run_b = {
        "request_id": "run-b",
        "trace_id": "b" * 32,
        "input": {"data": {"calls": 1, "after_a": True}},
        "budget": {"max_tokens": 20},
    }
    async with _client(app) as client, app.router.lifespan_context(app):
        streamed, complete = await asyncio.gather(
            client.post("/v1/run", json=run_a), client.post("/v1/run", json=run_b)
        )
        assert len(app.state.runs) == 0, "each record is removed when its run ends"
    a_out = Response.model_validate(_frames(streamed.text)[-1][1])
    b_out = Response.model_validate(complete.json())
    assert a_out.output["text"] == "200,200,429"
    assert b_out.output["text"] == "200"
    record_a, record_b = records["run-a"], records["run-b"]
    assert record_a is not None and record_b is not None and record_a is not record_b
    assert (record_a.model_calls, record_a.spent_tokens, record_a.exhausted) == (2, 30, True)
    assert (record_b.model_calls, record_b.spent_tokens, record_b.exhausted) == (1, 15, False)
    telemetry = ports.telemetry
    assert isinstance(telemetry, InMemoryTelemetry)
    calls = [s for s in telemetry.spans if s.name == "chassis.model.call"]
    assert sorted(s.attributes["request_id"] for s in calls) == ["run-a", "run-a", "run-b"]
    assert telemetry.counter_value("chassis.model_calls_uncorrelated", route="fake-route") == 0


FAKE_YAML = str(Path(__file__).resolve().parents[1] / "configs/fake.yaml")


def test_main_serve_starts_two_servers_from_the_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """`cli.main(["serve", ...])` end to end, with the uvicorn start patched out: it builds the
    public app and the proxy app from the config, on the hosts and ports it was given.
    """
    started: list[uvicorn.Server] = []

    async def fake_serve(self: uvicorn.Server, sockets: Any = None) -> None:
        self.started = True
        started.append(self)

    monkeypatch.setattr(uvicorn.Server, "serve", fake_serve)
    argv = ["serve", "--config", FAKE_YAML, "--host", "0.0.0.0", "--port", "8181"]
    cli.main([*argv, "--proxy-port", "8191"])
    assert len(started) == 2
    by_port = {server.config.port: server.config for server in started}
    public, proxy = by_port[8181], by_port[8191]
    assert (public.host, public.lifespan) == ("0.0.0.0", "on")
    assert (proxy.host, proxy.lifespan) == ("127.0.0.1", "off")
    assert isinstance(public.app, FastAPI) and isinstance(proxy.app, FastAPI)
    assert proxy.app.state is public.app.state
    assert public.app.state.config.agent.name == "echo"
    assert public.app.state.config.spec.engine.connector == "inprocess"
    chat = "/v1/chat/completions"
    # The model proxy is on the proxy app only. The public app's path is the OpenAI interface
    # (an agent call, marked `x-chassis-interface: openai`), never the proxy's operation.
    proxy_op = proxy.app.openapi()["paths"][chat]["post"]
    public_op = public.app.openapi()["paths"][chat]["post"]
    assert "x-chassis-interface" not in proxy_op
    assert public_op["x-chassis-interface"] == "openai"
    assert public_op["operationId"] == "chat_completions" != proxy_op["operationId"]
    mcp = {getattr(r, "path", None) for r in proxy.app.routes}
    assert "/mcp" in mcp and "/mcp" not in {getattr(r, "path", None) for r in public.app.routes}


def test_main_serve_exits_3_when_the_public_server_never_starts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fails(self: uvicorn.Server, sockets: Any = None) -> None:
        return None  # a failed lifespan: `serve` returns without `started`

    monkeypatch.setattr(uvicorn.Server, "serve", fails)
    with pytest.raises(SystemExit) as exc:
        cli.main(["serve", "--config", FAKE_YAML])
    assert exc.value.code == cli.STARTUP_FAILURE == 3


def test_serve_builds_two_listeners_and_one_lifespan() -> None:
    """`chassis serve` runs the public app and the proxy app; only the public one has a
    lifespan, so the ports are built once and both apps see them.
    """
    args = cli.parse_args(["serve", "--config", FAKE_YAML])
    public, proxy = cli.build_servers(args)
    assert (public.config.host, public.config.port) == ("127.0.0.1", 8080)
    assert (proxy.config.host, proxy.config.port) == ("127.0.0.1", 8090)
    assert public.config.lifespan == "on" and proxy.config.lifespan == "off"
    public_app, proxy_app = public.config.app, proxy.config.app
    assert isinstance(public_app, FastAPI) and isinstance(proxy_app, FastAPI)
    assert proxy_app.state is public_app.state
    args = cli.parse_args(
        ["serve", "--config", FAKE_YAML, "--host", "0.0.0.0", "--proxy-host", "::1"]
    )
    public, proxy = cli.build_servers(args)
    assert public.config.host == "0.0.0.0" and proxy.config.host == "::1"


@pytest.mark.parametrize("host", ["0.0.0.0", "10.0.0.5", "::", "chassis.internal"])
def test_serve_refuses_a_proxy_host_off_loopback(
    host: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.parse_args(["serve", "--config", FAKE_YAML, "--proxy-host", host])
    assert exc.value.code == 2
    assert "deploy/CLAUDE.md" in capsys.readouterr().err
    args = cli.parse_args(
        ["serve", "--config", FAKE_YAML, "--proxy-host", host, "--allow-any-proxy-host"]
    )
    assert args.proxy_host == host


def test_serve_refuses_one_port_for_both_listeners(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.parse_args(["serve", "--config", FAKE_YAML, "--port", "9000", "--proxy-port", "9000"])
    assert "--proxy-port" in capsys.readouterr().err


async def test_a_second_concurrent_run_with_an_in_flight_trace_id_is_409() -> None:
    """A caller-set `trace_id` keys the run's budget. While a run holds it, a second `/v1/run`
    with it is refused with 409 `trace_id_in_use` before the engine runs, streaming or not; the
    first run completes normally, and once it ends the trace id can be used again.
    """
    in_flight = asyncio.Event()
    release = asyncio.Event()
    started: list[str] = []

    async def hold(input: TaskInput, ctx: Context) -> AsyncIterator[Event]:
        started.append(ctx.request_id)
        yield Start(request_id=ctx.request_id)
        if ctx.request_id == "first":
            in_flight.set()
            await asyncio.wait_for(release.wait(), timeout=5)
        yield Delta(text=ctx.request_id)
        yield End(status="ok")

    ports = _ports(FakeEngine(handle=hold))
    app = create_app(ChassisConfig.model_validate(CONFIG), ports)
    body = {"trace_id": "d" * 32, "input": {"text": "x"}, "budget": {"max_tokens": 50}}
    async with _client(app) as client, app.router.lifespan_context(app):
        first = asyncio.create_task(client.post("/v1/run", json={**body, "request_id": "first"}))
        await asyncio.wait_for(in_flight.wait(), timeout=5)
        complete = await client.post("/v1/run", json={**body, "request_id": "dup"})
        streamed = await client.post(
            "/v1/run", json={**body, "request_id": "dup-stream", "stream": True}
        )
        assert app.state.runs.lookup("d" * 32).request_id == "first"
        release.set()
        done = await asyncio.wait_for(first, timeout=5)
        assert len(app.state.runs) == 0
        again = await client.post("/v1/run", json={**body, "request_id": "again"})
    for refused in (complete, streamed):
        assert refused.status_code == 409, refused.text
        assert refused.headers["content-type"].startswith("application/json")
        detail = refused.json()["detail"]
        assert detail["code"] == "trace_id_in_use"
        assert detail["message"]
    assert done.status_code == 200
    assert Response.model_validate(done.json()).output["text"] == "first"
    assert again.status_code == 200, "a sequential reuse of the trace id is served"
    assert Response.model_validate(again.json()).output["text"] == "again"
    assert started == ["first", "again"], "a refused run never reaches the engine"
    telemetry = ports.telemetry
    assert isinstance(telemetry, InMemoryTelemetry)
    assert telemetry.counter_value("chassis.requests", agent="echo", interface="native") == 2
    assert telemetry.counter_value("chassis.requests_refused", reason="trace_id_in_use") == 2
    assert len([s for s in telemetry.spans if s.name == "chassis.run"]) == 2


async def test_a_streamed_run_frees_its_trace_id_when_it_ends() -> None:
    app = create_app(ChassisConfig.model_validate(CONFIG), _ports())
    body = {"trace_id": "e" * 32, "input": {"text": "x"}, "stream": True}
    async with _client(app) as client, app.router.lifespan_context(app):
        first = await client.post("/v1/run", json=body)
        second = await client.post("/v1/run", json=body)
        assert len(app.state.runs) == 0
    assert first.status_code == second.status_code == 200


CONFIGS = Path(__file__).resolve().parents[1] / "configs"


def test_poc04_spec_blocks_have_their_suggested_defaults() -> None:
    spec = ChassisConfig.model_validate(CONFIG).spec
    assert spec.idempotency.model_dump() == {
        "enabled": True,
        "ttl_s": 86400,
        "lease_s": 5,
        "wait_poll_ms": 100,
        "max_entry_bytes": 1_048_576,
    }
    assert spec.events.result_events is False
    assert spec.events.consume is None


def test_poc04_spec_blocks_refuse_unknown_keys_and_bad_values() -> None:
    for spec in (
        {"idempotency": {"ttl": 1}},
        {"idempotency": {"lease_s": 0}},
        {"events": {"consume": {"topic": "t", "extra": 1}}},
    ):
        with pytest.raises(ValueError):
            ChassisConfig.model_validate({**CONFIG, "spec": {**CONFIG["spec"], **spec}})
    events = ChassisConfig.model_validate(
        {**CONFIG, "spec": {**CONFIG["spec"], "events": {"consume": None}}}
    ).spec.events
    assert events.consume is None


@pytest.mark.parametrize("consume", [{}, {"topic": "agents.task.requested.v1", "group": "g"}])
def test_events_consume_is_refused_until_event_triggered_runs_exist(
    consume: dict[str, Any], tmp_path: Path
) -> None:
    """Nothing reads `spec.events.consume` yet (019 H-17), so a config that sets it is refused,
    not silently ignored. The type stays so the published schema does not change."""
    spec = {**CONFIG["spec"], "events": {"consume": consume}}
    with pytest.raises(ValueError, match=r"event-triggered runs are not built yet.*019 H-17"):
        ChassisConfig.model_validate({**CONFIG, "spec": spec})
    path = tmp_path / "chassis.yaml"
    path.write_text(json.dumps({**CONFIG, "spec": spec}))
    with pytest.raises(ValueError, match=r"spec\.events\.consume"):
        load_config(path)


def _has_path(model: Any, path: str) -> bool:
    for part in path.split("."):
        fields = type(model).model_fields
        if part not in fields:
            return False
        model = getattr(model, part)
    return True


def test_reloadable_and_restart_only_paths_exist_and_do_not_overlap() -> None:
    config = ChassisConfig.model_validate(
        {**CONFIG, "spec": {**CONFIG["spec"], "adapters": {"model": "fake"}}}
    )
    for path in (*RELOADABLE, *RESTART_ONLY):
        assert _has_path(config, path), path
    assert not set(RELOADABLE) & set(RESTART_ONLY)
    assert "spec.idempotency.ttl_s" in RELOADABLE
    assert "spec.idempotency.lease_s" in RESTART_ONLY


@pytest.mark.parametrize("name", ["local.yaml", "sidecar.yaml"])
def test_the_poc1_to_poc3_local_configs_keep_state_in_memory(name: str) -> None:
    """They run Compose stacks with no Valkey, so they name `state: memory` over `local`."""
    config = load_config(CONFIGS / name)
    assert config.spec.adapters is not None and config.spec.adapters.state == "memory"
