"""The tool endpoint starts and stops a tools port that has a lifecycle (plan section 2.7).

`McpGatewayTools` keeps a cached tool list. The tool endpoint's lifespan fills it with
`await refresh()` before `/mcp` opens, then `start()`s the background refresh, and
`await aclose()`s it on shutdown. A failed first refresh does not stop the chassis: `/mcp` opens
with the last (here empty) list, and `chassis.tools.refresh_failed` is counted. A stub port
stands in for the gateway, so nothing here needs a socket.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any

import httpx2
from chassis.core.envelope import TaskInput
from chassis.core.handle import echo
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.ports.bundle import PortBundle
from chassis.ports.tool import ToolDefinition, ToolResult
from chassis.server import ChassisConfig, create_app
from chassis.server.proxy_app import create_proxy_app
from chassis.server.remote_auth import create_remote_proxy_app
from fastapi import FastAPI
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

CONFIG: dict[str, Any] = {
    "profile": "fake",
    "agent": {"name": "echo", "version": "0.0.1"},
    "spec": {"engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"}},
}

LOOKUP = ToolDefinition(
    name="gateway_lookup",
    description="A tool only the gateway lists.",
    parameters={"type": "object", "properties": {}},
)


class StubGateway:
    """A tools port with the gateway adapter's lifecycle: an empty list until `refresh()`."""

    name = "stub"

    def __init__(self, *, refresh_ok: bool = True) -> None:
        self.refresh_ok = refresh_ok
        self.on_refresh_failed: Callable[[], None] | None = None
        self.calls: list[str] = []
        self.serves: tuple[ToolDefinition, ...] = (LOOKUP,)
        self._tools: tuple[ToolDefinition, ...] = ()

    def list_tools(self) -> Sequence[ToolDefinition]:
        return self._tools

    async def refresh(self) -> bool:
        self.calls.append("refresh")
        if not self.refresh_ok:
            if self.on_refresh_failed is not None:
                self.on_refresh_failed()
            return False
        self._tools = self.serves
        return True

    def start(self) -> None:
        self.calls.append("start")

    async def aclose(self) -> None:
        self.calls.append("aclose")

    async def call(
        self, name: str, arguments: Mapping[str, Any], *, idempotency_key: str | None = None
    ) -> ToolResult:
        self.calls.append(f"call:{name}")
        return ToolResult(content={"ok": True})


def _ports(tools: StubGateway, telemetry: InMemoryTelemetry) -> PortBundle:
    return PortBundle(
        model=ScriptedModel(),
        engine=FakeEngine(handle=echo),
        config=InMemoryConfig(),
        telemetry=telemetry,
        tools=tools,
    )


def _mcp_client(app: FastAPI, headers: dict[str, str] | None = None) -> Client[Any]:
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
        "http://chassis/mcp", headers=headers, httpx_client_factory=factory
    )
    return Client(transport)


@asynccontextmanager
async def _serving(ports: PortBundle) -> AsyncIterator[FastAPI]:
    app = create_app(ChassisConfig.model_validate(CONFIG), ports)
    proxy = create_proxy_app(app)
    async with app.router.lifespan_context(app):
        yield proxy


async def test_the_first_refresh_runs_before_mcp_opens_and_lists_the_gateway_tools() -> None:
    tools = StubGateway()
    async with _serving(_ports(tools, InMemoryTelemetry())) as app:
        assert tools.calls == ["refresh", "start"]
        async with _mcp_client(app) as client:
            listed = await client.list_tools()
    assert [t.name for t in listed] == ["gateway_lookup"]


async def test_shutdown_closes_the_tools_port() -> None:
    tools = StubGateway()
    async with _serving(_ports(tools, InMemoryTelemetry())):
        pass
    assert tools.calls == ["refresh", "start", "aclose"]


async def test_a_failed_first_refresh_does_not_stop_startup_and_is_counted() -> None:
    tools = StubGateway(refresh_ok=False)
    telemetry = InMemoryTelemetry()
    async with _serving(_ports(tools, telemetry)) as app:
        async with _mcp_client(app) as client:
            listed = await client.list_tools()
        assert listed == []
        assert tools.calls == ["refresh", "start"]
        assert telemetry.counter_value("chassis.tools.refresh_failed") == 1
        # A later background failure is counted through the same hook.
        await tools.refresh()
        assert telemetry.counter_value("chassis.tools.refresh_failed") == 2
    assert tools.calls[-1] == "aclose"


async def test_a_refresh_that_raises_does_not_stop_startup() -> None:
    class Raising(StubGateway):
        async def refresh(self) -> bool:
            raise RuntimeError("gateway down")

    tools = Raising()
    telemetry = InMemoryTelemetry()
    async with _serving(_ports(tools, telemetry)) as app, _mcp_client(app) as client:
        assert await client.list_tools() == []
    assert telemetry.counter_value("chassis.tools.refresh_failed") == 1
    assert tools.calls == ["start", "aclose"]


async def test_a_port_without_a_lifecycle_is_left_alone() -> None:
    """`InMemoryTools` has no `refresh`, `start`, or `aclose`: the endpoint serves it as before."""
    from chassis.fakes.tool import default_tools

    ports = _ports(StubGateway(), InMemoryTelemetry())
    ports = PortBundle(
        model=ports.model,
        engine=ports.engine,
        config=ports.config,
        telemetry=ports.telemetry,
        tools=default_tools(),
    )
    async with _serving(ports) as app, _mcp_client(app) as client:
        listed = await client.list_tools()
    assert [t.name for t in listed] == ["glossary_lookup", "acronym_expand"]


async def test_a_tool_shown_after_a_failed_first_refresh_is_listed_without_restart() -> None:
    """Review H1: the first refresh fails, a later one succeeds, and `/mcp` lists the tool."""
    tools = StubGateway(refresh_ok=False)
    async with _serving(_ports(tools, InMemoryTelemetry())) as app:
        async with _mcp_client(app) as client:
            assert await client.list_tools() == []
        tools.refresh_ok = True
        assert await tools.refresh()  # what the background loop does
        async with _mcp_client(app) as client:
            listed = await client.list_tools()
            result = await client.call_tool("gateway_lookup", {})
    assert [t.name for t in listed] == ["gateway_lookup"]
    assert result.structured_content == {"ok": True}
    assert "call:gateway_lookup" in tools.calls


async def test_a_tool_granted_or_removed_later_follows_the_port_list() -> None:
    granted = ToolDefinition(
        name="granted_later",
        description="A tool added to the key's allow-list after startup.",
        parameters={"type": "object", "properties": {}},
    )
    tools = StubGateway()
    async with _serving(_ports(tools, InMemoryTelemetry())) as app:
        tools.serves = (LOOKUP, granted)
        await tools.refresh()
        async with _mcp_client(app) as client:
            added = [t.name for t in await client.list_tools()]
        tools.serves = (granted,)
        await tools.refresh()
        async with _mcp_client(app) as client:
            removed = [t.name for t in await client.list_tools()]
    assert added == ["gateway_lookup", "granted_later"]
    assert removed == ["granted_later"]


async def test_a_call_to_a_tool_no_longer_listed_still_goes_to_the_port() -> None:
    """The cache is not a control: the port (the gateway) decides every call it is asked. An
    unlisted name counts as a write tool, so it needs a run; outside one it never leaves."""
    tools = StubGateway()
    async with _serving(_ports(tools, InMemoryTelemetry())) as app:
        tools.serves = ()
        await tools.refresh()
        async with _mcp_client(app) as client:
            outside = await client.call_tool_mcp("gateway_lookup", {})
        assert "call:gateway_lookup" not in tools.calls
        pipeline = app.state.pipeline
        request = pipeline.to_request(input=TaskInput(text="x"))
        traceparent = f"00-{request.trace_id}-00f067aa0ba902b7-01"
        async with (
            app.state.runs.register(request, pipeline.context_for(request)),
            _mcp_client(app, {"traceparent": traceparent}) as client,
        ):
            inside = await client.call_tool_mcp("gateway_lookup", {})
    assert outside.is_error and outside.structured_content is not None
    assert outside.structured_content["code"] == "idempotency_key_required"
    assert not inside.is_error
    assert "call:gateway_lookup" in tools.calls


async def test_the_tools_port_starts_and_stops_once_with_the_remote_listener_on() -> None:
    """Review L1: the proxy and the remote listener both mount `/mcp`; the port starts once."""
    tools = StubGateway()
    app = create_app(ChassisConfig.model_validate(CONFIG), _ports(tools, InMemoryTelemetry()))
    proxy = create_proxy_app(app)
    create_remote_proxy_app(app, ("a-test-token",))
    async with app.router.lifespan_context(app):
        assert tools.calls == ["refresh", "start"]
        async with _mcp_client(proxy) as client:
            assert [t.name for t in await client.list_tools()] == ["gateway_lookup"]
    assert tools.calls == ["refresh", "start", "aclose"]
