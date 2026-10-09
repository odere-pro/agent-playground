"""`McpGatewayTools` (PoC-5, plan section 2.7): the real `ToolPort` adapter, an MCP client to
LiteLLM's MCP gateway, bound to the whole `ToolPortContract` (write mode included) over the fake
MCP server in process (`httpx2.ASGITransport`). No socket, no key.

The fake server's per-token allow-list stands in for the gateway's per-key list: the service's
key sees `glossary_lookup` and `note_write`; `unlisted_probe` exists behind the gateway but is
outside the list. The adapter's own cases follow: the error mapping, where the idempotency key
goes, and the key kept out of logs, errors, and `repr`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx2
import pytest
from chassis.adapters.mcp.gateway import (
    API_KEY_VAR,
    URL_VAR,
    McpGatewayTools,
    _failure,
)
from chassis.ports.tool import ToolError
from chassis_contracts.tool import KnownCall, ToolPortContract
from fake_mcp_server import PROBE_MARKER, FakeMcpState, create_app
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError as FastMcpToolError
from mcp.shared.exceptions import MCPError
from starlette.applications import Starlette

KEY = "sk-test-virtual-key-0123456789abcdef"
URL = "http://gateway/mcp/"
ALLOWED = frozenset({"glossary_lookup", "note_write"})


class Gateway:
    """The fake MCP server in process, plus switches that make requests fail: every request,
    or with `calls_only` only `tools/call`, so the session opens and the call itself fails."""

    def __init__(self, app: Starlette) -> None:
        self.app = app
        self.inner = httpx2.ASGITransport(app=app)
        self.fail_with: int | type[Exception] | None = None
        self.calls_only = False
        self.list_fails_with: int | None = None
        self.delete_status: int | None = None
        self.requests = 0
        self.client_kwargs: list[dict[str, Any]] = []

    def factory(self, **kwargs: Any) -> httpx2.AsyncClient:
        self.client_kwargs.append(kwargs)
        return httpx2.AsyncClient(transport=_Transport(self), **kwargs)


class _Transport(httpx2.AsyncBaseTransport):
    def __init__(self, gate: Gateway) -> None:
        self.gate = gate

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        self.gate.requests += 1
        if request.method == "DELETE" and self.gate.delete_status is not None:
            # A gateway in stateless mode refuses the session-close `DELETE`.
            return httpx2.Response(self.gate.delete_status, request=request)
        if self.gate.list_fails_with is not None and b'"tools/list"' in request.content:
            return httpx2.Response(self.gate.list_fails_with, request=request)
        fail = self.gate.fail_with
        if self.gate.calls_only and b'"tools/call"' not in request.content:
            fail = None
        if isinstance(fail, int):
            # An upstream body that quotes the key: it must never reach a `ToolError`.
            return httpx2.Response(fail, text=f"upstream said no to {KEY}", request=request)
        if fail is not None:
            raise fail("injected")
        return await self.gate.inner.handle_async_request(request)


async def _open(
    gate: Gateway, key: str = KEY, call_timeout_s: float = 5.0
) -> AsyncIterator[McpGatewayTools]:
    """The server's lifespan runs in a task of its own: a fixture's setup and teardown may run
    in different tasks, and anyio's cancel scopes must exit where they entered."""
    started, stop = asyncio.Event(), asyncio.Event()

    async def serve() -> None:
        async with gate.app.router.lifespan_context(gate.app):
            started.set()
            await stop.wait()

    server = asyncio.create_task(serve())
    await started.wait()
    port = McpGatewayTools(URL, key, timeout=call_timeout_s, httpx_client_factory=gate.factory)
    await port.refresh()
    try:
        yield port
    finally:
        await port.aclose()
        stop.set()
        await server


@pytest.fixture
def fake_state() -> FakeMcpState:
    return FakeMcpState(allow={KEY: ALLOWED})


@pytest.fixture
def gate(fake_state: FakeMcpState) -> Gateway:
    return Gateway(create_app(fake_state, expose_calls=True))


@pytest.fixture
async def gateway_tools(gate: Gateway) -> AsyncIterator[McpGatewayTools]:
    async for port in _open(gate):
        yield port


class TestMcpGatewayTools(ToolPortContract):
    @pytest.fixture
    def tool_port(self, gateway_tools: McpGatewayTools) -> McpGatewayTools:
        return gateway_tools

    @pytest.fixture
    def known_call(self) -> KnownCall:
        return KnownCall("glossary_lookup", {"term": "SLM"})

    @pytest.fixture
    def write_call(self) -> KnownCall:
        return KnownCall("note_write", {"text": "hello"})

    @pytest.fixture
    def other_write_arguments(self) -> dict[str, Any]:
        return {"text": "another note"}

    @pytest.fixture
    def denied_name(self) -> str:
        return "unlisted_probe"

    @pytest.fixture
    def count_effects(self, fake_state: FakeMcpState) -> Callable[[], int]:
        return lambda: fake_state.executions

    @pytest.fixture
    def make_unavailable(self, gate: Gateway) -> Callable[[], None]:
        def fail() -> None:
            gate.fail_with = 503

        return fail


# The adapter's own cases.


def test_lists_what_the_gateway_shows_this_key_with_write_mode(
    gateway_tools: McpGatewayTools,
) -> None:
    tools = {d.name: d for d in gateway_tools.list_tools()}
    assert set(tools) == ALLOWED
    assert tools["glossary_lookup"].read_only is True
    assert tools["note_write"].read_only is False
    assert tools["glossary_lookup"].parameters["required"] == ["term"]


async def test_a_tool_with_no_read_only_hint_is_a_write_tool() -> None:
    mcp = FastMCP("hintless")

    @mcp.tool
    def touch(path: str) -> str:
        """Touch a path."""
        return path

    gate = Gateway(mcp.http_app(path="/mcp/"))
    async for port in _open(gate):
        (definition,) = port.list_tools()
        assert definition.read_only is False
        with pytest.raises(ToolError) as info:
            await port.call("touch", {"path": "a"})
        assert info.value.code == "idempotency_key_required"


async def test_write_without_key_is_refused_before_any_request(
    gateway_tools: McpGatewayTools, gate: Gateway, fake_state: FakeMcpState
) -> None:
    before = gate.requests
    with pytest.raises(ToolError) as info:
        await gateway_tools.call("note_write", {"text": "x"})
    assert info.value.code == "idempotency_key_required"
    assert gate.requests == before
    assert fake_state.calls == []


async def test_write_without_key_reaches_the_server_and_is_refused_with_no_effect(
    gateway_tools: McpGatewayTools, gate: Gateway, fake_state: FakeMcpState
) -> None:
    """Review L6: a tool missing from the cache skips the local check, so the server's own refusal
    is what holds. Here the list fails, the cache is empty, and the call goes out with no key."""
    gateway_tools._tools = {}
    gate.list_fails_with = 503
    before = gate.requests
    with pytest.raises(ToolError) as info:
        await gateway_tools.call("note_write", {"text": "x"})
    assert info.value.code == "idempotency_key_required"
    assert gate.requests > before, "the call reached the server"
    assert fake_state.executions == 0


@pytest.mark.parametrize("delete_status", [404, 405])
async def test_an_unknown_tool_stays_unknown_when_the_session_close_is_refused(
    gateway_tools: McpGatewayTools, gate: Gateway, delete_status: int
) -> None:
    """Review M4: the close `DELETE`'s status must not override the call's own error."""
    gate.delete_status = delete_status
    ok = await gateway_tools.call("glossary_lookup", {"term": "SLM"})  # the paired control
    assert ok.is_error is False
    with pytest.raises(ToolError) as info:
        await gateway_tools.call("no_such_tool", {})
    assert info.value.code == "unknown_tool"


@pytest.mark.parametrize(
    ("code", "expected"), [(-32601, "unknown_tool"), (-32602, "bad_arguments")]
)
@pytest.mark.parametrize("status", [404, 405])
def test_a_json_rpc_error_wins_over_a_status_from_another_request(
    code: int, expected: str, status: int
) -> None:
    error = MCPError(code=code, message="refused")
    assert _failure(ExceptionGroup("session", [error]), [status], "t").code == expected


@pytest.mark.parametrize(
    "message",
    [
        "Error: Tool 'unlisted_probe' is not allowed for your key/team on server 'fake_tools'.",
        "User not allowed to call this tool.",
    ],
)
def test_a_json_rpc_error_phrased_as_a_denial_is_tool_denied(message: str) -> None:
    """Review L1: a denial sent as a JSON-RPC error is final, not a retryable outage."""
    error = MCPError(code=-32603, message=message)
    failure = _failure(ExceptionGroup("session", [error]), [], "t")
    assert failure.code == "tool_denied"
    assert failure.retryable is False
    assert message not in str(failure)


async def test_a_failing_refresh_callback_is_contained(gate: Gateway) -> None:
    """Review L2: the callback runs inside `refresh()`'s failure path; it must not escape."""
    async for port in _open(gate):

        def broken() -> None:
            raise RuntimeError("telemetry down")

        port.on_refresh_failed = broken
        gate.fail_with = 503
        assert await port.refresh() is False
        assert port.refresh_failures == 1


async def test_the_refresh_loop_logs_a_failure_and_keeps_looping(
    gate: Gateway, caplog: pytest.LogCaptureFixture
) -> None:
    """Review L2: an exception in one refresh is logged and counted; the next one still runs."""
    caplog.set_level(logging.WARNING)
    rounds: list[int] = []
    third = asyncio.Event()

    async def raising() -> bool:
        rounds.append(1)
        if len(rounds) >= 3:
            third.set()
        raise RuntimeError("boom")

    async for port in _open(gate):
        port.refresh = raising  # type: ignore[method-assign]
        port.refresh_s = 0.01
        port.start()
        async with asyncio.timeout(2):
            await third.wait()
        assert port._task is not None and not port._task.done()
        assert port.refresh_failures >= 2
    assert any("refresh loop" in r.getMessage() for r in caplog.records)


async def test_bad_arguments_are_refused_before_any_request(
    gateway_tools: McpGatewayTools, gate: Gateway
) -> None:
    before = gate.requests
    for arguments in ({}, {"term": 3}, {"term": "SLM", "extra": 1}):
        with pytest.raises(ToolError) as info:
            await gateway_tools.call("glossary_lookup", arguments)
        assert info.value.code == "bad_arguments"
        assert info.value.retryable is False
    assert gate.requests == before


async def test_the_key_goes_in_meta_and_as_the_argument_the_schema_declares(
    gateway_tools: McpGatewayTools, fake_state: FakeMcpState
) -> None:
    await gateway_tools.call("note_write", {"text": "x"}, idempotency_key="tk1:abc")
    (call,) = fake_state.calls
    assert call["idempotency_key"] == "tk1:abc"


async def test_the_key_is_not_added_as_an_argument_the_schema_does_not_declare() -> None:
    seen: list[dict[str, Any]] = []
    mcp = FastMCP("strict")

    @mcp.tool(annotations={"readOnlyHint": False})
    def stamp(text: str) -> dict[str, str]:
        """Stamp a text. Its schema refuses any other argument."""
        seen.append({"text": text})
        return {"stamped": text}

    gate = Gateway(mcp.http_app(path="/mcp/"))
    async for port in _open(gate):
        result = await port.call("stamp", {"text": "a"}, idempotency_key="tk1:abc")
    assert result.is_error is False
    assert result.content == {"stamped": "a"}
    assert seen == [{"text": "a"}]


async def test_a_tool_error_the_tool_reports_is_a_result_not_an_exception() -> None:
    mcp = FastMCP("failing")

    @mcp.tool(annotations={"readOnlyHint": True})
    def flaky(term: str) -> str:
        """Always fails as a tool."""
        raise ValueError("no luck")

    gate = Gateway(mcp.http_app(path="/mcp/"))
    async for port in _open(gate):
        result = await port.call("flaky", {"term": "a"})
    assert result.is_error is True


# LiteLLM v1.103.0's own texts, from the kind cluster (bring-up note, items 3 and "Requests").
@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("Error: Tool 'no_such_tool_ever' not found", "unknown_tool"),
        (
            "Error: Tool 'unlisted_probe' is not allowed for your key/team on server "
            "'fake_tools'. Contact proxy admin for access.",
            "tool_denied",
        ),
        ("User not allowed to call this tool.", "tool_denied"),
    ],
)
async def test_the_gateway_s_refusal_texts_map_to_their_codes(text: str, code: str) -> None:
    mcp = FastMCP("litellm-like")

    @mcp.tool(annotations={"readOnlyHint": True})
    def refused() -> str:
        """Answers the way the gateway refuses a call."""
        raise FastMcpToolError(text)

    gate = Gateway(mcp.http_app(path="/mcp/"))
    async for port in _open(gate):
        with pytest.raises(ToolError) as info:
            await port.call("refused", {})
    assert info.value.code == code
    assert info.value.retryable is False
    assert text not in str(info.value)  # fixed text, never the upstream body
    assert "fake_tools" not in str(info.value)


async def test_the_unlisted_probe_is_refused_and_its_marker_never_seen(
    gateway_tools: McpGatewayTools, fake_state: FakeMcpState
) -> None:
    ok = await gateway_tools.call("glossary_lookup", {"term": "SLM"})  # the paired control
    assert ok.is_error is False
    with pytest.raises(ToolError) as info:
        await gateway_tools.call("unlisted_probe", {})
    assert info.value.code in {"unknown_tool", "tool_denied"}
    assert PROBE_MARKER not in repr(fake_state.calls)


@pytest.mark.parametrize("calls_only", [False, True])
@pytest.mark.parametrize("status", [401, 403])
async def test_gateway_refusal_is_tool_denied(
    gateway_tools: McpGatewayTools, gate: Gateway, status: int, calls_only: bool
) -> None:
    gate.fail_with, gate.calls_only = status, calls_only
    with pytest.raises(ToolError) as info:
        await gateway_tools.call("glossary_lookup", {"term": "SLM"})
    assert info.value.code == "tool_denied"
    assert info.value.retryable is False


@pytest.mark.parametrize("failure", [500, 502, 503, 504, httpx2.ConnectError, httpx2.ReadTimeout])
@pytest.mark.parametrize("calls_only", [False, True])
async def test_transport_failure_and_5xx_are_retryable_unavailable(
    gateway_tools: McpGatewayTools,
    gate: Gateway,
    failure: int | type[Exception],
    calls_only: bool,
) -> None:
    gate.fail_with, gate.calls_only = failure, calls_only
    with pytest.raises(ToolError) as info:
        await gateway_tools.call("glossary_lookup", {"term": "SLM"})
    assert info.value.code == "tool_unavailable"
    assert info.value.retryable is True
    assert "upstream said no" not in info.value.message
    assert KEY not in str(info.value)


async def test_a_call_over_the_timeout_is_retryable_unavailable() -> None:
    mcp = FastMCP("slow")

    @mcp.tool(annotations={"readOnlyHint": True})
    async def slow(term: str) -> str:
        """Answers after a long time."""
        await asyncio.sleep(30)
        return term

    async for port in _open(Gateway(mcp.http_app(path="/mcp/")), call_timeout_s=0.5):
        with pytest.raises(ToolError) as info:
            await port.call("slow", {"term": "a"})
    assert info.value.code == "tool_unavailable"
    assert info.value.retryable is True


async def test_a_failed_refresh_keeps_the_last_list(
    gateway_tools: McpGatewayTools, gate: Gateway
) -> None:
    before = list(gateway_tools.list_tools())
    gate.fail_with = 503
    assert await gateway_tools.refresh() is False
    assert list(gateway_tools.list_tools()) == before
    assert gateway_tools.refresh_failures == 1


async def test_a_wrong_key_lists_nothing_and_cannot_call(gate: Gateway) -> None:
    async for port in _open(gate, key="sk-not-a-real-key-000000000"):
        assert list(port.list_tools()) == []
        with pytest.raises(ToolError) as info:
            await port.call("glossary_lookup", {"term": "SLM"})
        assert info.value.code in {"unknown_tool", "tool_denied"}


async def test_the_client_ignores_the_environment_and_never_follows_redirects(
    gateway_tools: McpGatewayTools, gate: Gateway
) -> None:
    await gateway_tools.call("glossary_lookup", {"term": "SLM"})
    assert gate.client_kwargs
    for kwargs in gate.client_kwargs:
        assert kwargs["trust_env"] is False
        assert kwargs["follow_redirects"] is False


async def test_the_key_never_reaches_a_log_an_error_or_repr(
    gate: Gateway, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    errors: list[str] = []
    async for port in _open(gate):
        assert KEY not in repr(port)
        await port.call("glossary_lookup", {"term": "SLM"})
        await port.call("note_write", {"text": "x"}, idempotency_key="k1")
        for failure in (401, 503, httpx2.ConnectError):
            gate.fail_with = failure
            try:
                await port.call("glossary_lookup", {"term": "SLM"})
            except ToolError as exc:
                errors.append(f"{exc!s} {exc.message} {exc!r}")
            await port.refresh()
        gate.fail_with = None
        for bad in ({}, {"term": 1}):
            try:
                await port.call("glossary_lookup", bad)
            except ToolError as exc:
                errors.append(f"{exc!s} {exc.message} {exc!r}")
    assert len(errors) == 5
    assert caplog.records, "the adapter should log its failures"
    text = "\n".join(r.getMessage() for r in caplog.records) + "\n" + "\n".join(errors)
    assert KEY not in text
    assert "upstream said no" not in text


def test_from_env_reads_url_and_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(URL_VAR, URL)
    monkeypatch.setenv(API_KEY_VAR, KEY)
    port = McpGatewayTools.from_env()
    assert port.url == URL
    assert KEY not in repr(port)


@pytest.mark.parametrize("missing", [URL_VAR, API_KEY_VAR])
def test_from_env_names_the_unset_variable(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    monkeypatch.setenv(URL_VAR, URL)
    monkeypatch.setenv(API_KEY_VAR, KEY)
    monkeypatch.delenv(missing)
    with pytest.raises(LookupError, match=missing):
        McpGatewayTools.from_env()


def test_names_are_the_plan_s() -> None:
    assert (URL_VAR, API_KEY_VAR) == ("LITELLM_MCP_URL", "LITELLM_API_KEY")
