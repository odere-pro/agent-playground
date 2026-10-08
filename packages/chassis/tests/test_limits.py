"""`spec.limits` on every public interface: the budget ceiling and floor, the body cap, and the
`messages` cap (`chassis.server.interfaces.limits`).

The caller sets the run budget on every interface (`budget` on native and MCP, `max_tokens` on
the chat formats). A value above the config's ceiling is refused, never clamped, with 400 in the
format's own shape and the code `limit_exceeded`. A body over `body_bytes_max` is 413 before it
is parsed or validated. No socket: every call goes through `httpx.ASGITransport`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import httpx2
import pytest
from chassis.core.handle import echo
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.ports.bundle import PortBundle
from chassis.server import ChassisConfig, create_app
from chassis.server.config import LimitsSpec
from chassis.server.interfaces.mcp import MCP_PATH
from fastapi import FastAPI
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from mcp.shared.exceptions import MCPError

AGENT = "echo"
CONFIG: dict[str, Any] = {
    "version": "cfg-1",
    "profile": "fake",
    "agent": {"name": AGENT, "version": "0.0.1"},
    "spec": {"engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"}},
}
LIMITS = LimitsSpec()
INTERFACES = ["native", "openai", "anthropic", "mcp"]


def _app(**limits: int) -> FastAPI:
    spec = {**CONFIG["spec"], "limits": limits}
    config = ChassisConfig.model_validate({**CONFIG, "spec": spec})
    ports = PortBundle(
        model=ScriptedModel(),
        engine=FakeEngine(handle=echo),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
    )
    return create_app(config, ports)


@asynccontextmanager
async def _client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://chassis") as client,
    ):
        yield client


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

    return Client(
        StreamableHttpTransport(f"http://chassis{MCP_PATH}", httpx_client_factory=factory)
    )


async def _call(
    interface: str, *, max_tokens: int | None = None, timeout_ms: int | None = None
) -> tuple[int, Any]:
    """One call with the budget given; `(status, body)`. MCP: `(200 or 400, the tool result)`."""
    app = _app()
    budget: dict[str, int] = {}
    if max_tokens is not None:
        budget["max_tokens"] = max_tokens
    if timeout_ms is not None:
        budget["timeout_ms"] = timeout_ms
    if interface == "mcp":
        async with app.router.lifespan_context(app), _mcp_client(app) as client:
            args: dict[str, Any] = {"input": {"text": "hi"}}
            if budget:
                args["budget"] = budget
            result = await client.call_tool(AGENT, args, raise_on_error=False)
        return (400 if result.is_error else 200), result
    async with _client(app) as client:
        if interface == "native":
            body: dict[str, Any] = {"input": {"text": "hi"}}
            if budget:
                body["budget"] = budget
            reply = await client.post("/v1/run", json=body)
        elif interface == "openai":
            body = {"model": AGENT, "messages": [{"role": "user", "content": "hi"}]}
            if max_tokens is not None:
                body["max_completion_tokens"] = max_tokens
            reply = await client.post("/v1/chat/completions", json=body)
        else:
            body = {
                "model": AGENT,
                "max_tokens": max_tokens if max_tokens is not None else 100,
                "messages": [{"role": "user", "content": "hi"}],
            }
            reply = await client.post("/v1/messages", json=body)
    return reply.status_code, reply.json()


def _assert_limit_refusal(interface: str, status: int, body: Any, needle: str) -> None:
    assert status == 400, body
    if interface == "native":
        assert needle in body["detail"]
    elif interface == "openai":
        assert body["error"]["code"] == "limit_exceeded"
        assert body["error"]["type"] == "invalid_request_error"
        assert needle in body["error"]["message"]
    elif interface == "anthropic":
        assert body["type"] == "error"
        assert body["error"]["type"] == "invalid_request_error"
        assert needle in body["error"]["message"]
    else:
        text = "".join(getattr(part, "text", "") for part in body.content)
        assert "400" in text and needle in text, text


# --- budget ceiling and floor ------------------------------------------------------------------


@pytest.mark.parametrize("interface", INTERFACES)
async def test_caller_budget_above_the_config_ceiling_is_refused(interface: str) -> None:
    status, body = await _call(interface, max_tokens=10**15)
    _assert_limit_refusal(interface, status, body, str(LIMITS.max_tokens_max))


@pytest.mark.parametrize("interface", ["native", "mcp"])
async def test_caller_timeout_above_the_config_ceiling_is_refused(interface: str) -> None:
    status, body = await _call(interface, timeout_ms=10**12)
    _assert_limit_refusal(interface, status, body, str(LIMITS.timeout_ms_max))


@pytest.mark.parametrize("interface", INTERFACES)
async def test_caller_budget_below_one_is_refused(interface: str) -> None:
    """The floor. Native keeps its validation answer, 422 (contract v1); MCP sees it as a tool
    error; the chat formats answer 400 in their own shape."""
    status, body = await _call(interface, max_tokens=0)
    if interface == "native":
        assert status == 422, body
    elif interface == "mcp":
        assert status == 400
        assert body.is_error is True
    else:
        assert status == 400, body
        assert "tokens" in body["error"]["message"]


@pytest.mark.parametrize("interface", ["native", "mcp"])
async def test_caller_timeout_below_one_is_refused(interface: str) -> None:
    status, _ = await _call(interface, timeout_ms=0)
    assert status == (422 if interface == "native" else 400)


@pytest.mark.parametrize("interface", INTERFACES)
async def test_the_ceiling_itself_and_the_defaults_pass(interface: str) -> None:
    status, body = await _call(interface)
    assert status == 200, body
    status, body = await _call(
        interface, max_tokens=LIMITS.max_tokens_max, timeout_ms=LIMITS.timeout_ms_max
    )
    assert status == 200, body


async def test_the_ceiling_comes_from_the_config() -> None:
    async with _client(_app(max_tokens_max=50)) as client:
        reply = await client.post(
            "/v1/run", json={"input": {"text": "hi"}, "budget": {"max_tokens": 51}}
        )
    assert reply.status_code == 400
    assert "50" in reply.json()["detail"]


def test_limits_must_be_positive() -> None:
    with pytest.raises(ValueError):
        LimitsSpec(max_tokens_max=0)


async def test_the_mcp_tool_description_states_the_ceilings() -> None:
    app = _app()
    async with app.router.lifespan_context(app), _mcp_client(app) as client:
        (tool,) = await client.list_tools()
    assert tool.description is not None
    assert str(LIMITS.max_tokens_max) in tool.description
    assert str(LIMITS.timeout_ms_max) in tool.description


# --- the body cap ------------------------------------------------------------------------------

PATHS = {
    "native": "/v1/run",
    "openai": "/v1/chat/completions",
    "anthropic": "/v1/messages",
    "mcp": MCP_PATH,
}


def _assert_413(interface: str, reply: httpx.Response) -> None:
    assert reply.status_code == 413, reply.text
    body = reply.json()
    if interface == "openai":
        assert body["error"]["code"] == "limit_exceeded"
        assert body["error"]["type"] == "invalid_request_error"
    elif interface == "anthropic":
        assert body["type"] == "error"
        assert body["error"]["type"] == "invalid_request_error"
    else:
        assert isinstance(body["detail"], str)
    text = reply.text
    assert "1024" in text


@pytest.mark.parametrize("interface", INTERFACES)
async def test_oversized_body_is_413_before_validation(interface: str) -> None:
    """The body is not JSON at all: had it been parsed or validated, the answer would be 400 or
    422, never 413."""
    async with _client(_app(body_bytes_max=1024)) as client:
        reply = await client.post(
            PATHS[interface],
            content=b"{" + b"x" * 4096,
            headers={"content-type": "application/json", "accept": "application/json"},
        )
    _assert_413(interface, reply)


@pytest.mark.parametrize("interface", INTERFACES)
async def test_oversized_streamed_body_with_no_length_is_413(interface: str) -> None:
    async def chunks() -> AsyncIterator[bytes]:
        yield b"{"
        for _ in range(8):
            yield b"x" * 512

    async with _client(_app(body_bytes_max=1024)) as client:
        reply = await client.post(
            PATHS[interface],
            content=chunks(),
            headers={"content-type": "application/json", "accept": "application/json"},
        )
    _assert_413(interface, reply)


async def test_a_body_under_the_cap_is_served() -> None:
    async with _client(_app(body_bytes_max=1024)) as client:
        reply = await client.post("/v1/run", json={"input": {"text": "hi"}})
    assert reply.status_code == 200


async def test_an_oversized_mcp_tool_call_is_refused() -> None:
    """A tool call over the cap is refused at `/v1/mcp` with 413; the client raises. Its
    in-process call to `/v1/run` would pass the same cap (it is smaller than the MCP request)."""
    app = _app(body_bytes_max=2048)
    async with app.router.lifespan_context(app), _mcp_client(app) as client:
        with pytest.raises(MCPError):
            await client.call_tool(AGENT, {"input": {"text": "y" * 4096}}, raise_on_error=False)


@pytest.mark.parametrize("interface", ["native", "openai", "anthropic"])
def test_413_is_declared_on_the_route(interface: str) -> None:
    spec = _app().openapi()
    assert "413" in spec["paths"][PATHS[interface]]["post"]["responses"]


# --- the messages cap --------------------------------------------------------------------------


def _messages(count: int) -> list[dict[str, str]]:
    roles = ["user", "assistant"]
    return [{"role": roles[(count - 1 - i) % 2], "content": "m"} for i in range(count)]


@pytest.mark.parametrize("interface", ["openai", "anthropic"])
async def test_messages_over_the_cap_are_refused(interface: str) -> None:
    async with _client(_app(messages_max=4)) as client:
        if interface == "openai":
            body: dict[str, Any] = {"model": AGENT, "messages": _messages(5)}
            reply = await client.post("/v1/chat/completions", json=body)
        else:
            body = {"model": AGENT, "max_tokens": 10, "messages": _messages(5)}
            reply = await client.post("/v1/messages", json=body)
    _assert_limit_refusal(interface, reply.status_code, reply.json(), "4")
    async with _client(_app(messages_max=4)) as client:
        body["messages"] = _messages(4)
        path = "/v1/chat/completions" if interface == "openai" else "/v1/messages"
        assert (await client.post(path, json=body)).status_code == 200


async def test_native_history_over_the_cap_is_refused() -> None:
    history = [{"role": "user", "text": "m"}] * 4
    async with _client(_app(messages_max=4)) as client:
        reply = await client.post(
            "/v1/run", json={"input": {"text": "hi", "data": {"history": history}}}
        )
    assert reply.status_code == 400
    assert "history" in reply.json()["detail"]
