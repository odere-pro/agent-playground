"""The MCP tool endpoint: `/mcp` on the chassis's localhost-only proxy app serves `ports.tools`
over streamable HTTP; the public app's lifespan builds it. The tool is defined once, in the port;
the MCP listing is that definition, not a copy.

Every test reaches `/mcp` through the ASGI app in process (`httpx2.ASGITransport`, the HTTP
client the MCP SDK uses). No socket.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import httpx2
import pytest
from chassis import CHASSIS_VERSION
from chassis.core.envelope import Budget, Context, Request, TaskInput, Versions
from chassis.core.handle import echo
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.fakes.tool import GLOSSARY, InMemoryTools, default_tools
from chassis.ports.bundle import PortBundle
from chassis.profiles import (
    PROFILE_DEFAULTS,
    AdapterNotAvailable,
    AdapterSpec,
    build_ports,
    merge_adapters,
)
from chassis.server import ChassisConfig, create_app
from chassis.server.proxy_app import create_proxy_app
from fastapi import FastAPI
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

CONFIG: dict[str, Any] = {
    "profile": "fake",
    "agent": {"name": "echo", "version": "0.0.1"},
    "spec": {"engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"}},
}
TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
TRACEPARENT = f"00-{TRACE}-00f067aa0ba902b7-01"


def _ports() -> PortBundle:
    return PortBundle(
        model=ScriptedModel(),
        engine=FakeEngine(handle=echo),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
        tools=default_tools(),
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
    """The proxy app, which serves `/mcp`, while the public app's lifespan runs."""
    app = create_app(ChassisConfig.model_validate(CONFIG), ports)
    proxy = create_proxy_app(app)
    async with app.router.lifespan_context(app):
        yield proxy


async def test_listing_is_the_port_definitions_defined_once() -> None:
    ports = _ports()
    async with _serving(ports) as app, _mcp_client(app) as client:
        listed = await client.list_tools()
    defined = ports.tools.list_tools()
    assert (
        [t.name for t in listed]
        == [d.name for d in defined]
        == [
            "glossary_lookup",
            "acronym_expand",
        ]
    )
    for tool, definition in zip(listed, defined, strict=True):
        assert tool.description == definition.description
        assert tool.input_schema == definition.parameters
        assert tool.annotations is not None and tool.annotations.read_only_hint is True


async def test_glossary_lookup_answers_a_known_term() -> None:
    async with _serving(_ports()) as app, _mcp_client(app) as client:
        result = await client.call_tool("glossary_lookup", {"term": "SLM"})
    assert result.is_error is False
    assert result.structured_content == {"term": "SLM", "definition": GLOSSARY["SLM"]}


async def test_acronym_expand_is_the_second_read_only_tool() -> None:
    async with _serving(_ports()) as app, _mcp_client(app) as client:
        known = await client.call_tool("acronym_expand", {"acronym": "rag"})
        unknown = await client.call_tool("acronym_expand", {"acronym": "zzz"})
    assert known.structured_content == {
        "acronym": "rag",
        "expansion": "retrieval-augmented generation",
    }
    assert unknown.is_error is False
    assert unknown.structured_content == {"acronym": "zzz", "expansion": None}


async def test_unknown_term_is_not_an_error() -> None:
    async with _serving(_ports()) as app, _mcp_client(app) as client:
        result = await client.call_tool("glossary_lookup", {"term": "flux capacitor"})
    assert result.is_error is False
    assert result.structured_content == {"term": "flux capacitor", "definition": None}


async def test_bad_arguments_are_a_tool_error_not_a_500() -> None:
    async with _serving(_ports()) as app, _mcp_client(app) as client:
        result = await client.call_tool("glossary_lookup", {"word": "SLM"}, raise_on_error=False)
    assert result.is_error is True
    assert result.structured_content is not None
    assert result.structured_content["code"] == "bad_arguments"


async def test_unknown_tool_is_a_tool_error_not_a_500() -> None:
    async with _serving(_ports()) as app, _mcp_client(app) as client:
        result = await client.call_tool("rm_rf", {}, raise_on_error=False)
    assert result.is_error is True


async def test_one_span_per_call_with_the_tool_and_the_inbound_trace_id() -> None:
    """A valid `traceparent` is parsed; the span gets its 32-hex `trace_id`, not the raw header."""
    ports = _ports()
    headers = {"traceparent": TRACEPARENT}
    async with _serving(ports) as app, _mcp_client(app, headers) as client:
        await client.call_tool("glossary_lookup", {"term": "MCP"})
    telemetry = ports.telemetry
    assert isinstance(telemetry, InMemoryTelemetry)
    spans = [s for s in telemetry.spans if s.name == "chassis.tool.call"]
    assert len(spans) == 1 and spans[0].ended
    assert spans[0].attributes == {"tool": "glossary_lookup", "trace_id": TRACE}
    assert telemetry.counter_value("chassis.tool_calls", tool="glossary_lookup") == 1
    tools = ports.tools
    assert isinstance(tools, InMemoryTools)
    assert tools.calls == [("glossary_lookup", {"term": "MCP"})]


@pytest.mark.parametrize(
    "header",
    [
        "garbage",
        f"ff-{TRACE}-00f067aa0ba902b7-01",
        f"00-{'0' * 32}-00f067aa0ba902b7-01",
        f"00-{TRACE}-00f067aa0ba902b7-01 injected",
    ],
    ids=["garbage", "version-ff", "zero-trace", "trailing"],
)
async def test_an_invalid_traceparent_records_no_trace_id(header: str) -> None:
    ports = _ports()
    async with _serving(ports) as app, _mcp_client(app, {"traceparent": header}) as client:
        await client.call_tool("glossary_lookup", {"term": "MCP"})
    telemetry = ports.telemetry
    assert isinstance(telemetry, InMemoryTelemetry)
    (span,) = [s for s in telemetry.spans if s.name == "chassis.tool.call"]
    assert span.attributes == {"tool": "glossary_lookup"}


async def test_a_call_of_an_in_flight_run_names_its_request_id() -> None:
    ports = _ports()
    request = Request(
        request_id="req-mcp",
        trace_id=TRACE,
        idempotency_key="idem-mcp",
        agent="echo",
        agent_version="0.0.1",
        input=TaskInput(text="x"),
        budget=Budget(max_tokens=10),
    )
    ctx = Context(
        request_id=request.request_id,
        trace_id=request.trace_id,
        idempotency_key=request.idempotency_key,
        agent=request.agent,
        agent_version=request.agent_version,
        budget=request.budget,
        versions=Versions(chassis=CHASSIS_VERSION),
    )
    async with (
        _serving(ports) as app,
        app.state.runs.register(request, ctx) as record,
        _mcp_client(app, {"traceparent": TRACEPARENT}) as client,
    ):
        await client.call_tool("glossary_lookup", {"term": "MCP"})
    telemetry = ports.telemetry
    assert isinstance(telemetry, InMemoryTelemetry)
    (span,) = [s for s in telemetry.spans if s.name == "chassis.tool.call"]
    assert span.attributes == {
        "tool": "glossary_lookup",
        "trace_id": TRACE,
        "request_id": "req-mcp",
    }
    assert (record.model_calls, record.spent_tokens) == (0, 0), "tool calls are not charged"


async def test_no_traceparent_leaves_the_attribute_unset() -> None:
    ports = _ports()
    async with _serving(ports) as app, _mcp_client(app) as client:
        await client.call_tool("glossary_lookup", {"term": "MCP"})
    telemetry = ports.telemetry
    assert isinstance(telemetry, InMemoryTelemetry)
    (span,) = [s for s in telemetry.spans if s.name == "chassis.tool.call"]
    assert span.attributes == {"tool": "glossary_lookup"}


async def test_mcp_answers_503_before_the_lifespan() -> None:
    app = create_proxy_app(create_app(ChassisConfig.model_validate(CONFIG), _ports()))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://chassis") as client:
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        res = await client.post("/mcp", content=json.dumps(body))
    assert res.status_code == 503


def test_fake_profile_builds_the_fake_tools() -> None:
    assert PROFILE_DEFAULTS["fake"].tools == "fake"
    ports = build_ports("fake")
    assert isinstance(ports.tools, InMemoryTools)
    assert [t.name for t in ports.tools.list_tools()] == ["glossary_lookup", "acronym_expand"]


@pytest.mark.parametrize("profile", ["local", "cloud"])
def test_real_tool_adapter_is_not_configured_without_its_variables(
    profile: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PoC-5 ships `McpGatewayTools`; with `LITELLM_MCP_URL` unset it is "not configured" and the
    message names the variable."""
    assert PROFILE_DEFAULTS[profile].tools == "mcp"  # type: ignore[index]
    monkeypatch.delenv("LITELLM_MCP_URL", raising=False)
    monkeypatch.delenv("LITELLM_API_KEY", raising=False)
    with pytest.raises(AdapterNotAvailable, match="tools: adapter 'mcp' is not configured") as err:
        build_ports("fake", AdapterSpec(tools="mcp"))
    assert "LITELLM_MCP_URL" in str(err.value)


def test_an_override_without_tools_gets_the_profile_default() -> None:
    """`spec.adapters` merges over the profile per field: no `tools` is the profile's `tools`."""
    assert AdapterSpec(model="fake").tools is None
    assert merge_adapters("fake", AdapterSpec(model="fake")).tools == "fake"
    assert merge_adapters("local", AdapterSpec(model="fake")).tools == "mcp"
