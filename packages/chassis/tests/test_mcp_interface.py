"""MCP inbound: the agent as one MCP tool at `/v1/mcp` on the public app (PoC-3 open note,
sections 5 and 7).

The tool is generated from the chassis's own OpenAPI spec (`FastMCP.from_fastapi`), never written
by hand: its input schema is the `/v1/run` request body, and calling it calls `/v1/run` in
process with `x-chassis-interface: mcp`. This is not the proxy app's `/mcp` (the workload's
tools, `test_tool_endpoint.py`).

Every test reaches `/v1/mcp` with a real `fastmcp.Client` over streamable HTTP through
`httpx2.ASGITransport` on the public app. No socket.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import httpx2
import pytest
from chassis.core.envelope import Context, TaskInput
from chassis.core.events import Delta, End, Error, Event
from chassis.core.handle import echo
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.ports.bundle import PortBundle
from chassis.server import ChassisConfig, create_app
from chassis.server.interfaces.mcp import MCP_PATH
from fastapi import FastAPI
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

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
TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"


def _config(**interfaces: bool) -> ChassisConfig:
    spec = {**CONFIG["spec"], "interfaces": interfaces}
    return ChassisConfig.model_validate({**CONFIG, "spec": spec})


def _app(engine: FakeEngine | None = None, config: ChassisConfig | None = None) -> FastAPI:
    ports = PortBundle(
        model=ScriptedModel(),
        engine=engine or FakeEngine(handle=echo),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
    )
    return create_app(config or _config(), ports)


def _telemetry(app: FastAPI) -> InMemoryTelemetry:
    telemetry = app.state.ports.telemetry
    assert isinstance(telemetry, InMemoryTelemetry)
    return telemetry


def _mcp_client(app: FastAPI) -> Client[Any]:
    def factory(
        headers: dict[str, str] | None = None,
        timeout: httpx2.Timeout | None = None,
        auth: httpx2.Auth | None = None,
        **kw: Any,
    ) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://chassis",
            headers=headers,
            timeout=timeout,
            auth=auth,
            **kw,
        )

    transport = StreamableHttpTransport(f"http://chassis{MCP_PATH}", httpx_client_factory=factory)
    return Client(transport)


@asynccontextmanager
async def _serving(app: FastAPI) -> AsyncIterator[Client[Any]]:
    async with app.router.lifespan_context(app), _mcp_client(app) as client:
        yield client


def _resolve(node: Any, components: dict[str, Any]) -> Any:
    """`node` with every `#/components/schemas/<name>` reference replaced by that schema."""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            return _resolve(components[ref.rsplit("/", 1)[1]], components)
        return {k: _resolve(v, components) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve(v, components) for v in node]
    return node


def _run_body_schema(app: FastAPI) -> dict[str, Any]:
    spec = app.openapi()
    body = spec["paths"]["/v1/run"]["post"]["requestBody"]["content"]["application/json"]
    resolved = _resolve(body["schema"], spec["components"]["schemas"])
    assert isinstance(resolved, dict)
    return resolved


# --- the tool ----------------------------------------------------------------------------------


async def test_lists_exactly_one_tool_named_after_the_agent() -> None:
    async with _serving(_app()) as client:
        tools = await client.list_tools()
    assert [t.name for t in tools] == ["echo"]


async def test_tool_input_schema_is_the_openapi_request_schema_of_run() -> None:
    """The arguments are the `RunRequest` body as the spec gives it. FastMCP keeps the body's
    `properties` and `required` and drops only the body's own `title`, `description`, and
    `additionalProperties` (an unknown argument is then a 422 from `/v1/run`, a tool error).
    """
    app = _app()
    async with _serving(app) as client:
        (tool,) = await client.list_tools()
    body = _run_body_schema(app)
    schema = tool.input_schema
    assert schema["type"] == body["type"] == "object"
    assert schema["properties"] == body["properties"]
    assert schema["required"] == body["required"] == ["input"]
    dropped = set(body) - set(schema)
    assert dropped <= {"title", "description", "additionalProperties"}, dropped


async def test_calling_the_tool_returns_the_native_response_envelope() -> None:
    app = _app()
    async with _serving(app) as client:
        result = await client.call_tool("echo", {"input": {"text": "hello"}})
    assert result.is_error is False
    envelope = result.structured_content
    assert envelope is not None
    assert envelope["status"] == "ok"
    assert envelope["output"] == {"text": "hello"}
    assert envelope["agent"] == "echo"
    assert envelope["versions"]["config"] == "cfg-1"
    assert len(app.state.runs) == 0


async def test_stream_true_in_the_arguments_is_read_as_false() -> None:
    engine = FakeEngine(events=[Delta(text="a"), Delta(text="b"), End(output={"text": "ab"})])
    async with _serving(_app(engine)) as client:
        result = await client.call_tool("echo", {"input": {"text": "x"}, "stream": True})
    assert result.is_error is False
    assert result.structured_content is not None, "an SSE body would not be JSON"
    assert result.structured_content["output"] == {"text": "ab"}
    assert result.structured_content["status"] == "ok"


async def test_telemetry_is_labeled_mcp() -> None:
    app = _app()
    async with _serving(app) as client:
        await client.call_tool("echo", {"input": {"text": "x"}})
    telemetry = _telemetry(app)
    assert telemetry.counter_value("chassis.requests", agent="echo", interface="mcp") == 1
    assert telemetry.counter_value("chassis.requests", agent="echo", interface="native") == 0
    runs = [s for s in telemetry.spans if s.name == "chassis.run"]
    assert [s.attributes["interface"] for s in runs] == ["mcp"]


async def test_a_body_trace_id_in_flight_is_reminted_not_409() -> None:
    in_flight = asyncio.Event()
    release = asyncio.Event()

    async def wait(input: TaskInput, ctx: Context) -> AsyncIterator[Event]:
        if input.text == "first":
            in_flight.set()
            await asyncio.wait_for(release.wait(), timeout=5)
        yield End(output={"trace": ctx.trace_id})

    app = _app(FakeEngine(handle=wait))
    async with _serving(app) as client:
        native = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://c")
        async with native:
            first = asyncio.create_task(
                native.post("/v1/run", json={"trace_id": TRACE, "input": {"text": "first"}})
            )
            await asyncio.wait_for(in_flight.wait(), timeout=5)
            result = await client.call_tool(
                "echo", {"trace_id": TRACE, "input": {"text": "second"}}
            )
            release.set()
            held = await first
    assert held.status_code == 200 and held.json()["trace_id"] == TRACE
    assert result.is_error is False
    envelope = result.structured_content
    assert envelope is not None
    assert envelope["status"] == "ok"
    assert envelope["trace_id"] != TRACE
    assert envelope["output"] == {"trace": envelope["trace_id"]}
    telemetry = _telemetry(app)
    assert telemetry.counter_value("chassis.trace_id_reminted", interface="mcp") == 1


async def test_a_run_with_status_error_is_a_tool_result_not_an_mcp_error() -> None:
    """The known gap (section 4): native and MCP answer a run error as a 200 envelope with
    `status: error`, so the MCP result is not `isError`; the envelope says what failed.
    """
    engine = FakeEngine(events=[Error(code="engine_error", message="boom", retryable=False)])
    async with _serving(_app(engine)) as client:
        result = await client.call_tool("echo", {"input": {"text": "x"}}, raise_on_error=False)
    assert result.is_error is False
    envelope = result.structured_content
    assert envelope is not None
    assert envelope["status"] == "error"
    assert envelope["output"]["error"] == {"code": "engine_error", "message": "boom"}


async def test_a_body_naming_another_agent_is_a_tool_error() -> None:
    async with _serving(_app()) as client:
        result = await client.call_tool(
            "echo", {"agent": "other", "input": {"text": "x"}}, raise_on_error=False
        )
    assert result.is_error is True


# --- the header on /v1/run gives no trust ------------------------------------------------------


async def test_a_direct_caller_sending_the_header_only_loses_streaming() -> None:
    app = _app()
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://c") as client,
    ):
        response = await client.post(
            "/v1/run",
            json={"input": {"text": "x"}, "stream": True},
            headers={"x-chassis-interface": "mcp"},
        )
        other = await client.post(
            "/v1/run",
            json={"input": {"text": "x"}, "stream": True},
            headers={"x-chassis-interface": "openai"},
        )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["status"] == "ok"
    assert other.headers["content-type"].startswith("text/event-stream"), (
        "only `mcp` is read; any other value is native"
    )
    telemetry = _telemetry(app)
    assert telemetry.counter_value("chassis.requests", agent="echo", interface="mcp") == 1
    assert telemetry.counter_value("chassis.requests", agent="echo", interface="native") == 1


# --- mount, readiness, timeout -----------------------------------------------------------------


async def test_503_before_ready() -> None:
    app = _app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://c") as client:
        before = await client.post(MCP_PATH, json={})
    assert before.status_code == 503
    assert before.json() == {"detail": "agent MCP endpoint not ready"}


async def test_503_after_the_lifespan_ends() -> None:
    app = _app()
    async with _serving(app) as client:
        await client.list_tools()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://c") as client:
        after = await client.post(MCP_PATH, json={})
    assert after.status_code == 503


async def test_the_app_serves_twice() -> None:
    """The lifespan builds a fresh streamable HTTP app each time (a session manager runs once)."""
    app = _app()
    for _ in range(2):
        async with _serving(app) as client:
            assert [t.name for t in await client.list_tools()] == ["echo"]


async def test_the_tool_call_has_no_client_timeout() -> None:
    """httpx2's default is 5 s, which would cut a run longer than that. The tool's client has no
    timeout; the run's own `budget.timeout_ms` bounds the call.
    """
    app = _app()
    server = app.state.agent_mcp
    (tool,) = await server.list_tools()
    client: httpx2.AsyncClient = tool._client
    assert httpx2.AsyncClient().timeout == httpx2.Timeout(5.0), "the default this replaces"
    assert client.timeout == httpx2.Timeout(None)
    assert client.headers["x-chassis-interface"] == "mcp"


def test_mcp_path_is_not_in_the_openapi_spec() -> None:
    app = _app()
    assert MCP_PATH not in app.openapi()["paths"]
    assert MCP_PATH in {getattr(r, "path", None) for r in app.routes}


def test_mcp_off_mounts_nothing() -> None:
    app = _app(config=_config(mcp=False))
    assert MCP_PATH not in {getattr(r, "path", None) for r in app.routes}
    assert getattr(app.state, "agent_mcp", None) is None


async def test_mcp_off_is_404() -> None:
    app = _app(config=_config(mcp=False))
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://c") as client,
    ):
        response = await client.post(MCP_PATH, json={})
    assert response.status_code == 404


def test_the_tool_endpoint_on_the_proxy_app_is_another_route() -> None:
    """`/v1/mcp` (the agent, public) and `/mcp` (the workload's tools, proxy) stay distinct."""
    app = _app()
    public = {getattr(r, "path", None) for r in app.routes}
    assert MCP_PATH == "/v1/mcp"
    assert "/mcp" not in public


@pytest.mark.parametrize("name", ["echo", "summarizer"])
async def test_the_tool_is_named_after_the_configured_agent(name: str) -> None:
    config = ChassisConfig.model_validate({**CONFIG, "agent": {"name": name, "version": "1"}})
    app = _app(config=config)
    tools = await app.state.agent_mcp.list_tools()
    assert [t.name for t in tools] == [name]
    assert json.dumps(tools[0].parameters)  # plain JSON, no Python objects


# --- start-up cost and the spec the tool comes from ---------------------------------------------


async def test_create_app_and_its_lifespan_never_build_the_whole_spec() -> None:
    """The whole spec carries the SDK request unions and costs about 0.25 s to build. The tool
    needs `/v1/run` only, so start-up builds that one operation; the whole spec is built when it
    is asked for (`/openapi.json`, `/manifest`).
    """
    app = _app()
    assert app.openapi_schema is None
    async with _serving(app) as client:
        assert [t.name for t in await client.list_tools()] == ["echo"]
    assert app.openapi_schema is None


def test_the_tool_spec_is_the_run_operation_of_the_app_spec() -> None:
    """No hand-written tool: the spec the tool is built from is the app's own `/v1/run`
    operation, byte for byte, with every component it references as the whole spec gives it.
    """
    from chassis.server.interfaces.mcp import run_operation_spec

    app = _app()
    spec = run_operation_spec(app)
    whole = app.openapi()
    assert spec["openapi"] == whole["openapi"] == "3.1.0"
    assert spec["paths"] == {"/v1/run": whole["paths"]["/v1/run"]}
    components = spec["components"]["schemas"]
    assert {"RunRequest", "Response", "TaskInput", "Budget"} <= set(components)
    for name, schema in components.items():
        assert whole["components"]["schemas"][name] == schema, name


async def test_the_tool_is_described_from_the_config() -> None:
    """An MCP client picks a tool by its description, so `/v1/run` carries one built from the
    config (the agent's name and version, and the input it takes) and the tool inherits it.
    """
    config = ChassisConfig.model_validate(
        {**CONFIG, "agent": {"name": "summarizer", "version": "2.1.0"}}
    )
    app = _app(config=config)
    (tool,) = await app.state.agent_mcp.list_tools()
    operation = app.openapi()["paths"]["/v1/run"]["post"]
    assert tool.description == operation["description"]
    assert "summarizer" in operation["summary"]
    for part in ("summarizer", "2.1.0", "input.text", "input.data"):
        assert part in tool.description, part
    assert tool.description != "Run"


# --- the marker is the tool's, never the MCP caller's (reviewer MUST 1) ------------------------


def _mcp_client_with_headers(app: FastAPI, headers: dict[str, str]) -> Client[Any]:
    def factory(
        headers: dict[str, str] | None = None,
        timeout: httpx2.Timeout | None = None,
        auth: httpx2.Auth | None = None,
        **kw: Any,
    ) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://chassis",
            headers=headers,
            timeout=timeout,
            auth=auth,
            **kw,
        )

    transport = StreamableHttpTransport(
        f"http://chassis{MCP_PATH}", headers=headers, httpx_client_factory=factory
    )
    return Client(transport)


def _record_run_headers(app: FastAPI) -> list[dict[str, str]]:
    """The headers of every request that reaches `/v1/run` (the MCP tool's inner hop)."""
    seen: list[dict[str, str]] = []

    @app.middleware("http")
    async def record(request: Any, call_next: Any) -> Any:
        if request.url.path == "/v1/run":
            seen.append({k.lower(): v for k, v in request.headers.items()})
        return await call_next(request)

    return seen


@pytest.mark.parametrize("claimed", ["native", "openai", "MCP", "native, mcp"])
async def test_an_mcp_caller_cannot_override_the_interface_marker(claimed: str) -> None:
    """An MCP caller that sends its own `x-chassis-interface` still gets the MCP tool's run:
    `stream` read as false, labeled `mcp`, a body trace id in use re-minted (not 409).
    """
    in_flight = asyncio.Event()
    release = asyncio.Event()

    async def handle(input: TaskInput, ctx: Context) -> AsyncIterator[Event]:
        if input.text == "first":
            in_flight.set()
            await asyncio.wait_for(release.wait(), timeout=5)
        yield Delta(text="a")
        yield End(output={"trace": ctx.trace_id})

    app = _app(FakeEngine(handle=handle))
    seen = _record_run_headers(app)
    headers = {"x-chassis-interface": claimed}
    client = _mcp_client_with_headers(app, headers)
    async with app.router.lifespan_context(app), client:
        native = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://c")
        async with native:
            first = asyncio.create_task(
                native.post("/v1/run", json={"trace_id": TRACE, "input": {"text": "first"}})
            )
            await asyncio.wait_for(in_flight.wait(), timeout=5)
            result = await client.call_tool(
                "echo",
                {"trace_id": TRACE, "input": {"text": "second"}, "stream": True},
                raise_on_error=False,
            )
            release.set()
            await first
    assert result.is_error is False, result.content
    envelope = result.structured_content
    assert envelope is not None, "an SSE body would not be JSON"
    assert envelope["status"] == "ok"
    assert envelope["trace_id"] != TRACE
    assert len(seen) == 2, "the native call, then the tool's inner hop"
    assert seen[-1].get("x-chassis-interface") == "mcp"
    telemetry = _telemetry(app)
    assert telemetry.counter_value("chassis.requests", agent="echo", interface="mcp") == 1
    assert telemetry.counter_value("chassis.trace_id_reminted", interface="mcp") == 1


async def test_the_inner_hop_forwards_only_the_correlation_headers() -> None:
    """The tool's call to `/v1/run` carries `traceparent` and `Idempotency-Key` from the MCP
    request, its own marker, and nothing else the caller sent.
    """
    app = _app()
    seen = _record_run_headers(app)
    traceparent = f"00-{TRACE}-00f067aa0ba902b7-01"
    caller = {
        "traceparent": traceparent,
        "idempotency-key": "idem-1",
        "x-forwarded-for": "10.0.0.9",
        "x-api-key": "sk-caller",
        "x-chassis-interface": "native",
        "x-custom": "nope",
    }
    client = _mcp_client_with_headers(app, caller)
    async with app.router.lifespan_context(app), client:
        result = await client.call_tool("echo", {"input": {"text": "x"}})
    assert result.is_error is False
    (inner,) = seen
    assert inner["x-chassis-interface"] == "mcp"
    assert inner["traceparent"] == traceparent
    assert inner["idempotency-key"] == "idem-1"
    for name in ("x-forwarded-for", "x-api-key", "x-custom", "mcp-protocol-version"):
        assert name not in inner, name
    envelope = result.structured_content
    assert envelope is not None
    assert envelope["trace_id"] == TRACE


async def test_the_tool_client_forces_the_marker_over_a_header_set_on_the_request() -> None:
    """Whatever FastMCP merges into the request (its tool path keeps the client's default today,
    its resource path lets the caller's header win), the tool's client sends `mcp` last.
    """
    app = _app()
    async with app.router.lifespan_context(app):
        (tool,) = await app.state.agent_mcp.list_tools()
        client: httpx2.AsyncClient = tool._client
        request = client.build_request(
            "POST",
            "/v1/run",
            json={"input": {"text": "x"}, "stream": True},
            headers={"x-chassis-interface": "native", "x-custom": "nope"},
        )
        request.headers["x-chassis-interface"] = "native"
        response = await client.send(request)
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["status"] == "ok"
    telemetry = _telemetry(app)
    assert telemetry.counter_value("chassis.requests", agent="echo", interface="mcp") == 1
    assert telemetry.counter_value("chassis.requests", agent="echo", interface="native") == 0
