"""PoC-5 `ToolPort` write mode (plan section 2.6): the write tool, the allow-list, and the key the
tool endpoint derives per call.

`TestWriteModeTools` binds the whole `ToolPortContract`, write cases included, to the fake set a
gateway key would see: `glossary_lookup` and `note_write` listed, `unlisted_probe` behind the port
but outside the allow-list. The endpoint tests reach `/mcp` on the proxy app in process
(`httpx2.ASGITransport`), as `test_tool_endpoint.py` does. No socket.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx2
import pytest
from chassis import CHASSIS_VERSION
from chassis.adapters.mcp.server import tool_key
from chassis.core.envelope import Budget, Context, Request, TaskInput, Versions
from chassis.core.handle import echo
from chassis.core.inbound import public_message
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.fakes.tool import (
    NOTE_WRITE,
    UNLISTED_MARKER,
    UNLISTED_PROBE,
    InMemoryTools,
    WriteModeTools,
    default_tools,
    write_mode_tools,
)
from chassis.ports.bundle import PortBundle
from chassis.ports.tool import ToolError
from chassis.server import ChassisConfig, create_app
from chassis.server.correlation import RunRecord, RunRegistry
from chassis.server.proxy_app import create_proxy_app
from chassis.server.results import key_hash
from chassis.server.tool_endpoint import run_key_of
from chassis_contracts.tool import KnownCall, ToolPortContract
from fastapi import FastAPI
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

CONFIG: dict[str, Any] = {
    "profile": "fake",
    "agent": {"name": "echo", "version": "0.0.1"},
    "spec": {"engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"}},
}
TRACE_A = "4bf92f3577b34da6a3ce929d0e0e4736"
TRACE_B = "5cf92f3577b34da6a3ce929d0e0e4737"


def _traceparent(trace: str) -> str:
    return f"00-{trace}-00f067aa0ba902b7-01"


class TestWriteModeTools(ToolPortContract):
    @pytest.fixture
    def tool_port(self) -> InMemoryTools:
        return write_mode_tools()

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
    def count_effects(self, tool_port: InMemoryTools) -> Callable[[], int]:
        return lambda: len(tool_port.effects)

    @pytest.fixture
    def make_unavailable(self, tool_port: InMemoryTools) -> Callable[[], None]:
        return lambda: tool_port.fail_next_call(
            ToolError("tool_unavailable", "scripted outage", retryable=True)
        )


class TestDefaultToolsStillPass(ToolPortContract):
    """The PoC-1 to PoC-4 set: read-only, no allow-list. The write cases skip."""

    @pytest.fixture
    def tool_port(self) -> InMemoryTools:
        return default_tools()

    @pytest.fixture
    def known_call(self) -> KnownCall:
        return KnownCall("glossary_lookup", {"term": "SLM"})


# The fake.


def test_default_tools_stay_read_only() -> None:
    assert all(d.read_only for d in default_tools().list_tools())


def test_write_mode_tools_list_the_allowed_ones_only() -> None:
    tools = write_mode_tools()
    assert [d.name for d in tools.list_tools()] == ["glossary_lookup", "note_write"]
    assert NOTE_WRITE.read_only is False
    assert UNLISTED_PROBE.name == "unlisted_probe"


async def test_the_allow_list_refuses_before_the_tool_runs() -> None:
    tools = write_mode_tools()
    with pytest.raises(ToolError) as info:
        await tools.call("unlisted_probe", {})
    assert info.value.code == "tool_denied"
    assert tools.effects == []


async def test_without_an_allow_list_every_tool_is_listed_and_callable() -> None:
    tools = write_mode_tools(allowed=None)
    assert "unlisted_probe" in [d.name for d in tools.list_tools()]
    result = await tools.call("unlisted_probe", {})
    assert result.content == UNLISTED_MARKER


async def test_a_repeated_key_returns_the_first_result_even_with_other_arguments() -> None:
    tools = write_mode_tools()
    first = await tools.call("note_write", {"text": "a"}, idempotency_key="k1")
    again = await tools.call("note_write", {"text": "b"}, idempotency_key="k1")
    assert again == first
    assert tools.notes == ["a"]


async def test_a_key_is_scoped_to_its_tool() -> None:
    """One key on two write tools is two effects: the dedup is per tool and key."""
    tools = write_mode_tools()
    tools.add(NOTE_WRITE.model_copy(update={"name": "note_write_2"}), tools.note_store)
    tools.allowed = None
    await tools.call("note_write", {"text": "a"}, idempotency_key="k1")
    await tools.call("note_write_2", {"text": "b"}, idempotency_key="k1")
    assert tools.notes == ["a", "b"]


async def test_bad_arguments_of_a_write_tool_have_no_effect() -> None:
    tools = write_mode_tools()
    with pytest.raises(ToolError) as info:
        await tools.call("note_write", {"txt": "a"}, idempotency_key="k1")
    assert info.value.code == "bad_arguments"
    result = await tools.call("note_write", {"text": "a"}, idempotency_key="k1")
    assert result.is_error is False and tools.notes == ["a"]


async def test_the_key_is_recorded_with_the_call() -> None:
    tools = write_mode_tools()
    await tools.call("note_write", {"text": "a"}, idempotency_key="k1")
    await tools.call("glossary_lookup", {"term": "SLM"})
    assert tools.idempotency_keys == ["k1", None]


# The key derivation.


def test_tool_key_is_versioned_and_short() -> None:
    key = tool_key("run", "note_write", {"text": "a"})
    assert key.startswith("tk1:") and len(key) == 4 + 40
    payload = "run|note_write|" + json.dumps({"text": "a"}, sort_keys=True, separators=(",", ":"))
    assert key == "tk1:" + hashlib.sha256(f"{payload}|".encode()).hexdigest()[:40]


def test_tool_key_ignores_argument_order() -> None:
    a = tool_key("run", "t", {"x": 1, "y": [1, 2]})
    b = tool_key("run", "t", {"y": [1, 2], "x": 1})
    assert a == b


@pytest.mark.parametrize(
    ("other"),
    [
        ("run-2", "t", {"x": 1}, None),
        ("run", "u", {"x": 1}, None),
        ("run", "t", {"x": 2}, None),
        ("run", "t", {"x": 1}, "n1"),
    ],
    ids=["run", "tool", "arguments", "nonce"],
)
def test_tool_key_changes_with_each_part(
    other: tuple[str, str, dict[str, Any], str | None],
) -> None:
    base = tool_key("run", "t", {"x": 1})
    run_key, name, arguments, nonce = other
    assert tool_key(run_key, name, arguments, nonce) != base


def test_the_nonce_is_never_the_key() -> None:
    assert "n1" not in tool_key("run", "t", {}, "n1")


def _record(
    request_id: str, trace: str, key: str, *, agent: str = "echo", text: str = "x"
) -> RunRecord:
    request, ctx = _run(request_id, trace, key, agent=agent, text=text)
    return RunRegistry().open(request, ctx)


def test_a_replay_of_the_same_client_key_derives_the_same_tool_key() -> None:
    """A PoC-4 takeover replays the client's key and input under a new `request_id`: the writes
    dedup."""
    first = _record("req-a", TRACE_A, "client-key-1")
    replay = _record("req-b", TRACE_B, "client-key-1")
    assert run_key_of(first, idempotency=True) == run_key_of(replay, idempotency=True)
    args = {"text": "a"}
    assert tool_key(run_key_of(first, idempotency=True), "note_write", args) == tool_key(
        run_key_of(replay, idempotency=True), "note_write", args
    )


def test_two_client_keys_derive_different_tool_keys() -> None:
    a = _record("req-a", TRACE_A, "client-key-1")
    b = _record("req-a", TRACE_B, "client-key-2")
    assert run_key_of(a, idempotency=True) != run_key_of(b, idempotency=True)


def test_two_agents_with_the_same_client_key_never_share_a_run_key() -> None:
    """Review M1 (a): agents A and B behind one gateway, one client key `order-42`."""
    a = _record("req-a", TRACE_A, "order-42", agent="agent-a")
    b = _record("req-b", TRACE_B, "order-42", agent="agent-b")
    assert run_key_of(a, idempotency=True) != run_key_of(b, idempotency=True)


def test_a_reused_client_key_with_other_input_is_another_run() -> None:
    """Review M1 (b): the PoC-4 entry expired and the client reuses its key for a new request."""
    first = _record("req-a", TRACE_A, "order-42", text="first order")
    later = _record("req-b", TRACE_B, "order-42", text="second order")
    assert run_key_of(first, idempotency=True) != run_key_of(later, idempotency=True)


def test_with_idempotency_off_the_run_key_is_the_request_id() -> None:
    """Review M1 (b): with `spec.idempotency.enabled: false` nothing replays, so a reused client
    key is a new run and its writes are new effects."""
    a = _record("req-a", TRACE_A, "order-42")
    b = _record("req-b", TRACE_B, "order-42")
    assert run_key_of(a, idempotency=False) == "req-a"
    assert run_key_of(b, idempotency=False) == "req-b"


def test_the_run_key_is_not_the_bare_hash_of_the_client_key() -> None:
    record = _record("req-a", TRACE_A, "order-42")
    assert run_key_of(record, idempotency=True) != key_hash("order-42")


def test_the_raw_client_key_is_not_on_the_record() -> None:
    record = _record("req-a", TRACE_A, "client-key-1")
    assert "client-key-1" not in repr(record)


# The tool endpoint.


def _ports(tools: InMemoryTools | None = None) -> PortBundle:
    return PortBundle(
        model=ScriptedModel(),
        engine=FakeEngine(handle=echo),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
        tools=tools if tools is not None else write_mode_tools(),
    )


def _run(
    request_id: str,
    trace: str,
    key: str | None = None,
    *,
    agent: str = "echo",
    text: str = "x",
) -> tuple[Request, Context]:
    request = Request(
        request_id=request_id,
        trace_id=trace,
        idempotency_key=f"idem-{request_id}" if key is None else key,
        agent=agent,
        agent_version="0.0.1",
        input=TaskInput(text=text),
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
    return request, ctx


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
async def _serving(
    ports: PortBundle, config: dict[str, Any] | None = None
) -> AsyncIterator[FastAPI]:
    app = create_app(ChassisConfig.model_validate(config or CONFIG), ports)
    proxy = create_proxy_app(app)
    async with app.router.lifespan_context(app):
        yield proxy


def _tools(ports: PortBundle) -> WriteModeTools:
    assert isinstance(ports.tools, WriteModeTools)
    return ports.tools


async def test_mcp_lists_the_write_tool_with_read_only_hint_false() -> None:
    async with _serving(_ports()) as app, _mcp_client(app) as client:
        listed = {t.name: t for t in await client.list_tools()}
    assert sorted(listed) == ["glossary_lookup", "note_write"]
    hints = listed["note_write"].annotations
    assert hints is not None and hints.read_only_hint is False


@pytest.mark.parametrize(
    "headers",
    [None, {"traceparent": _traceparent(TRACE_B)}],
    ids=["no-traceparent", "no-run-in-flight"],
)
async def test_a_write_with_no_run_is_idempotency_key_required(
    headers: dict[str, str] | None,
) -> None:
    ports = _ports()
    async with _serving(ports) as app, _mcp_client(app, headers) as client:
        result = await client.call_tool("note_write", {"text": "a"}, raise_on_error=False)
    assert result.is_error is True
    assert result.structured_content is not None
    assert result.structured_content["code"] == "idempotency_key_required"
    assert result.structured_content["retryable"] is False
    assert _tools(ports).calls == [], "refused before the port was called"


async def test_a_write_in_a_run_gets_the_derived_key() -> None:
    ports = _ports()
    request, ctx = _run("req-a", TRACE_A)
    async with (
        _serving(ports) as app,
        app.state.runs.register(request, ctx),
        _mcp_client(app, {"traceparent": _traceparent(TRACE_A)}) as client,
    ):
        result = await client.call_tool("note_write", {"text": "a"})
    assert result.is_error is False
    tools = _tools(ports)
    run_key = run_key_of(RunRegistry().open(request, ctx), idempotency=True)
    assert tools.idempotency_keys == [tool_key(run_key, "note_write", {"text": "a"})]


async def test_the_same_write_twice_in_one_run_is_one_effect() -> None:
    ports = _ports()
    request, ctx = _run("req-a", TRACE_A)
    async with (
        _serving(ports) as app,
        app.state.runs.register(request, ctx),
        _mcp_client(app, {"traceparent": _traceparent(TRACE_A)}) as client,
    ):
        first = await client.call_tool("note_write", {"text": "a"})
        second = await client.call_tool("note_write", {"text": "a"})
    assert first.structured_content == second.structured_content
    assert _tools(ports).notes == ["a"]


async def test_two_runs_never_share_a_key() -> None:
    ports = _ports()
    req_a, ctx_a = _run("req-a", TRACE_A)
    req_b, ctx_b = _run("req-b", TRACE_B)
    async with (
        _serving(ports) as app,
        app.state.runs.register(req_a, ctx_a),
        app.state.runs.register(req_b, ctx_b),
        _mcp_client(app, {"traceparent": _traceparent(TRACE_A)}) as client_a,
        _mcp_client(app, {"traceparent": _traceparent(TRACE_B)}) as client_b,
    ):
        await client_a.call_tool("note_write", {"text": "a"})
        await client_b.call_tool("note_write", {"text": "a"})
    tools = _tools(ports)
    assert tools.notes == ["a", "a"]
    assert len(set(tools.idempotency_keys)) == 2


async def test_two_agents_with_the_same_client_key_are_two_effects() -> None:
    """Review M1 (a) end to end: two runs of two agents, one client key, one write each."""
    ports = _ports()
    req_a, ctx_a = _run("req-a", TRACE_A, "order-42", agent="agent-a")
    req_b, ctx_b = _run("req-b", TRACE_B, "order-42", agent="agent-b")
    async with (
        _serving(ports) as app,
        app.state.runs.register(req_a, ctx_a),
        app.state.runs.register(req_b, ctx_b),
        _mcp_client(app, {"traceparent": _traceparent(TRACE_A)}) as client_a,
        _mcp_client(app, {"traceparent": _traceparent(TRACE_B)}) as client_b,
    ):
        first = await client_a.call_tool("note_write", {"text": "x"})
        second = await client_b.call_tool("note_write", {"text": "x"})
    assert _tools(ports).notes == ["x", "x"]
    assert first.structured_content != second.structured_content


async def test_with_idempotency_off_a_reused_client_key_is_a_new_effect() -> None:
    ports = _ports()
    config = {**CONFIG, "spec": {**CONFIG["spec"], "idempotency": {"enabled": False}}}
    req_a, ctx_a = _run("req-a", TRACE_A, "order-42")
    req_b, ctx_b = _run("req-b", TRACE_B, "order-42")
    async with (
        _serving(ports, config) as app,
        _mcp_client(app, {"traceparent": _traceparent(TRACE_A)}) as client_a,
        _mcp_client(app, {"traceparent": _traceparent(TRACE_B)}) as client_b,
    ):
        async with app.state.runs.register(req_a, ctx_a):
            await client_a.call_tool("note_write", {"text": "x"})
        async with app.state.runs.register(req_b, ctx_b):
            await client_b.call_tool("note_write", {"text": "x"})
    assert _tools(ports).notes == ["x", "x"]


async def test_a_workload_nonce_makes_a_second_intended_write() -> None:
    ports = _ports()
    request, ctx = _run("req-a", TRACE_A)
    async with (
        _serving(ports) as app,
        app.state.runs.register(request, ctx),
        _mcp_client(app, {"traceparent": _traceparent(TRACE_A)}) as client,
    ):
        await client.call_tool("note_write", {"text": "a"}, meta={"idempotency_key": "one"})
        await client.call_tool("note_write", {"text": "a"}, meta={"idempotency_key": "two"})
        await client.call_tool("note_write", {"text": "a"}, meta={"idempotency_key": "one"})
    tools = _tools(ports)
    assert tools.notes == ["a", "a"]
    run_key = run_key_of(RunRegistry().open(request, ctx), idempotency=True)
    assert tools.idempotency_keys[0] == tool_key(run_key, "note_write", {"text": "a"}, "one")
    assert all("one" not in (k or "") for k in tools.idempotency_keys), "mixed in, never raw"


@pytest.mark.parametrize("nonce", [7, "x" * 257], ids=["not-a-string", "too-long"])
async def test_a_bad_nonce_is_bad_arguments(nonce: Any) -> None:
    ports = _ports()
    request, ctx = _run("req-a", TRACE_A)
    async with (
        _serving(ports) as app,
        app.state.runs.register(request, ctx),
        _mcp_client(app, {"traceparent": _traceparent(TRACE_A)}) as client,
    ):
        result = await client.call_tool(
            "note_write", {"text": "a"}, meta={"idempotency_key": nonce}, raise_on_error=False
        )
    assert result.is_error is True
    assert result.structured_content is not None
    assert result.structured_content["code"] == "bad_arguments"
    assert _tools(ports).calls == []


async def test_a_read_only_call_in_a_run_sends_no_key() -> None:
    ports = _ports()
    request, ctx = _run("req-a", TRACE_A)
    async with (
        _serving(ports) as app,
        app.state.runs.register(request, ctx),
        _mcp_client(app, {"traceparent": _traceparent(TRACE_A)}) as client,
    ):
        await client.call_tool("glossary_lookup", {"term": "SLM"})
    assert _tools(ports).idempotency_keys == [None]


async def test_an_unlisted_tool_is_not_served_over_mcp() -> None:
    ports = _ports()
    async with _serving(ports) as app, _mcp_client(app) as client:
        result = await client.call_tool("unlisted_probe", {}, raise_on_error=False)
    assert result.is_error is True
    assert UNLISTED_MARKER not in json.dumps(result.structured_content or {})
    assert _tools(ports).calls == []


@pytest.mark.parametrize(
    "code",
    [
        "unknown_tool",
        "bad_arguments",
        "idempotency_key_required",
        "tool_denied",
        "tool_unavailable",
    ],
)
def test_every_tool_code_has_a_fixed_public_message(code: str) -> None:
    assert public_message(code) != public_message("no_such_code_ever")


async def test_an_upstream_message_never_reaches_the_workload() -> None:
    """The MCP error carries the code, `retryable`, and `public_message(code)`; never the
    `ToolError` message, which an adapter may fill from an upstream body.
    """
    tools = write_mode_tools()
    leak = "upstream said: http://10.0.0.7:4000 key=sk-secret"
    tools.fail_next_call(ToolError("tool_unavailable", leak, retryable=True))
    ports = _ports(tools)
    async with _serving(ports) as app, _mcp_client(app) as client:
        result = await client.call_tool("glossary_lookup", {"term": "SLM"}, raise_on_error=False)
    assert result.is_error is True
    assert result.structured_content == {
        "code": "tool_unavailable",
        "message": public_message("tool_unavailable"),
        "retryable": True,
    }
    text = "".join(getattr(part, "text", "") for part in result.content)
    assert "sk-secret" not in text and "10.0.0.7" not in text
    assert public_message("tool_unavailable") in text
