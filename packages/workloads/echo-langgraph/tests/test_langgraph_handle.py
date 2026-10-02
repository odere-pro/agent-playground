"""`handle` against the fake model server and a FastMCP stub of the chassis tool endpoint, both
over `httpx2.ASGITransport` (the HTTP client line the openai client and the MCP SDK use). No
socket, no key, no chassis import.

Named `test_langgraph_handle.py`, not `test_handle.py`: the test folders have no `__init__.py`,
so two `test_handle.py` basenames in one pytest run collide with echo-python's.
"""

from __future__ import annotations

import asyncio
import importlib
import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx2
import jsonschema
import pytest
from echo_langgraph import PROMPT_VERSION, SYSTEM_PROMPT, handle
from fake_model_server import Script, create_app
from fastmcp import FastMCP

# `echo_langgraph.handle` the attribute is the function; the modules hold the transport hooks.
handle_module: Any = importlib.import_module("echo_langgraph.handle")
tools_module: Any = importlib.import_module("echo_langgraph.tools")

ROOT = Path(__file__).resolve().parents[4]
EXAMPLE_SCRIPT = ROOT / "packages/fake-model-server/scripts/example.yaml"
EVENTS_SCHEMA = json.loads((ROOT / "packages/chassis/schemas/events.v0.json").read_text())

TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
STUB_DEFINITION = "A small language model."
CTX: dict[str, Any] = {
    "request_id": "req-1",
    "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
    "idempotency_key": "idem-1",
    "agent": "echo",
    "agent_version": "0.0.1",
    "budget": {"max_tokens": 2000, "timeout_ms": 30000},
    "versions": {"chassis": "0.1.0"},
    "model_route": "big-default",
    "traceparent": TRACEPARENT,
}


class Recording(httpx2.AsyncBaseTransport):
    """Records every request, then hands it to the wrapped transport."""

    def __init__(self, inner: httpx2.AsyncBaseTransport) -> None:
        self.inner = inner
        self.requests: list[httpx2.Request] = []

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return await self.inner.handle_async_request(request)


def _stub_tools() -> FastMCP:
    stub: FastMCP = FastMCP(name="chassis-tools")

    @stub.tool
    def glossary_lookup(term: str) -> dict[str, str | None]:
        """Look up a platform term. Returns its short definition, or null if unknown."""
        return {"term": term, "definition": STUB_DEFINITION}

    return stub


@pytest.fixture
def model() -> Iterator[tuple[Any, Recording]]:
    app = create_app(Script.from_yaml(EXAMPLE_SCRIPT))
    recording = Recording(httpx2.ASGITransport(app=app))
    handle_module.model_transport = recording
    yield app, recording
    handle_module.model_transport = None


@asynccontextmanager
async def _serving(stub: FastMCP) -> AsyncIterator[Any]:
    """The stub's ASGI app with its lifespan running in a task of its own: pytest-asyncio may
    tear a fixture down in another task, and anyio refuses to exit a cancel scope there."""
    app = stub.http_app(path="/mcp", stateless_http=True)
    ready, done = asyncio.Event(), asyncio.Event()

    async def run() -> None:
        async with app.router.lifespan_context(app):
            ready.set()
            await done.wait()

    task = asyncio.create_task(run())
    await ready.wait()
    try:
        yield app
    finally:
        done.set()
        await task


@pytest.fixture
async def tools() -> AsyncIterator[Recording]:
    async with _serving(_stub_tools()) as app:
        recording = Recording(httpx2.ASGITransport(app=app))
        tools_module.transport = recording
        yield recording
        tools_module.transport = None


async def _run(text: str, ctx: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    events = [e async for e in handle({"text": text, "data": {}}, ctx or CTX)]
    for event in events:
        jsonschema.validate(event, EVENTS_SCHEMA)
    return events


def _text(events: list[dict[str, Any]]) -> str:
    return "".join(e["text"] for e in events if e["type"] == "delta")


async def test_streams_start_deltas_metrics_end(model: Any, tools: Recording) -> None:
    events = await _run("simplify: the quick brown fox")
    kinds = [e["type"] for e in events]
    assert kinds[0] == "start" and kinds[-2:] == ["metrics", "end"]
    assert kinds.count("delta") > 1, "the reply streams in pieces"
    assert all(e["schema_version"] == "0" for e in events)
    assert events[0]["request_id"] == "req-1"
    assert _text(events) == "Plain words. Short sentences. Same facts."
    metrics = events[-2]
    assert (metrics["input_tokens"], metrics["output_tokens"]) == (42, 9)
    assert metrics["model_route"] == "big-default" and metrics["attempt"] == 1
    assert events[-1]["status"] == "ok"


async def test_input_is_its_own_human_message_and_the_tool_is_offered(
    model: Any, tools: Recording
) -> None:
    app, _ = model
    await _run("simplify: secret facts here")
    body = app.state.calls[-1]
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    system, user = body["messages"]
    assert system["content"] == SYSTEM_PROMPT and "secret facts" not in system["content"]
    assert user["content"] == "simplify: secret facts here"
    assert body["model"] == "big-default" and body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}
    offered = [t["function"]["name"] for t in body["tools"]]
    assert offered == ["glossary_lookup"]
    assert body["tools"][0]["function"]["parameters"]["required"] == ["term"]
    assert PROMPT_VERSION == "simplifier-v1"


async def test_route_falls_back_to_big_default(model: Any, tools: Recording) -> None:
    app, _ = model
    await _run("simplify: x", {**CTX, "model_route": None})
    assert app.state.calls[-1]["model"] == "big-default"
    await _run("simplify: x", {**CTX, "model_route": "local-small"})
    assert app.state.calls[-1]["model"] == "local-small"


async def test_tool_loop_over_mcp(model: Any, tools: Recording) -> None:
    app, _ = model
    events = await _run("what does the glossary say?")
    kinds = [e["type"] for e in events]
    assert kinds[0] == "start" and kinds[-2:] == ["metrics", "end"]
    calls = [e for e in events if e["type"] == "tool_call"]
    assert len(calls) == 1
    call = calls[0]
    assert call["name"] == "glossary_lookup" and call["arguments"] == {"term": "SLM"}
    assert call["result"] == {"term": "SLM", "definition": STUB_DEFINITION}
    assert call["call_id"].startswith("call_")
    after = events[kinds.index("tool_call") + 1 :]
    assert _text(after) == "From the glossary: SLM means small language model."
    assert _text(events[: kinds.index("tool_call")]) == ""
    # The second model call carries the tool loop in the OpenAI shape (contract v1, item 3).
    second = app.state.calls[-1]["messages"]
    assert [m["role"] for m in second] == ["system", "user", "assistant", "tool"]
    assert second[2]["tool_calls"][0]["id"] == call["call_id"]
    assert second[3]["tool_call_id"] == call["call_id"]
    assert len(app.state.calls) == 2


async def test_every_model_and_mcp_request_carries_the_traceparent(
    model: Any, tools: Recording
) -> None:
    _, model_calls = model
    await _run("what does the glossary say?")
    assert len(model_calls.requests) == 2
    assert tools.requests, "the tools were listed and called over MCP"
    for request in [*model_calls.requests, *tools.requests]:
        assert request.headers.get("traceparent") == TRACEPARENT, request.url


async def test_no_traceparent_header_when_ctx_has_none(model: Any, tools: Recording) -> None:
    _, model_calls = model
    await _run("simplify: x", {**CTX, "traceparent": None})
    for request in [*model_calls.requests, *tools.requests]:
        assert "traceparent" not in request.headers


async def test_no_key_from_the_environment_reaches_any_request(
    model: Any, tools: Recording, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "planted")
    _, model_calls = model
    events = await _run("what does the glossary say?")
    assert events[-1]["type"] == "end"
    for request in [*model_calls.requests, *tools.requests]:
        assert all("planted" not in value for value in request.headers.values())
        assert request.headers.get("authorization") in (None, "Bearer not-a-key")
    for request in tools.requests:
        assert "authorization" not in request.headers


async def test_http_error_becomes_an_error_event(model: Any, tools: Recording) -> None:
    app, _ = model
    events = await _run("fail please")
    assert [e["type"] for e in events] == ["start", "error"]
    assert events[-1]["code"] == "http_500" and events[-1]["retryable"] is True
    assert "scripted failure" in events[-1]["message"]
    assert len(app.state.calls) == 1, "no retry inside the workload; the chassis decides"


async def test_model_connection_error_becomes_a_retryable_error(tools: Recording) -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    handle_module.model_transport = httpx2.MockTransport(refuse)
    try:
        events = await _run("x")
    finally:
        handle_module.model_transport = None
    assert [e["type"] for e in events] == ["start", "error"]
    assert events[-1]["code"] == "connect_error" and events[-1]["retryable"] is True


async def test_model_timeout_becomes_a_retryable_error(tools: Recording) -> None:
    def hang(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("slow model", request=request)

    handle_module.model_transport = httpx2.MockTransport(hang)
    try:
        events = await _run("x")
    finally:
        handle_module.model_transport = None
    assert [e["type"] for e in events] == ["start", "error"]
    assert events[-1]["code"] == "timeout" and events[-1]["retryable"] is True


async def test_tool_endpoint_down_runs_without_tools(model: Any) -> None:
    """As in echo-python: an unreachable tool endpoint is logged and counted, not fatal."""
    app, _ = model

    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    before = tools_module.list_failures
    tools_module.transport = httpx2.MockTransport(refuse)
    try:
        events = await _run("simplify: x")
    finally:
        tools_module.transport = None
    assert events[-1] == {"schema_version": "0", "type": "end", "status": "ok"}
    assert _text(events) == "Plain words. Short sentences. Same facts."
    assert "tools" not in app.state.calls[-1]
    assert tools_module.list_failures == before + 1


async def test_tool_error_result_becomes_a_tool_call_with_an_error(model: Any) -> None:
    stub: FastMCP = FastMCP(name="chassis-tools")

    @stub.tool
    def glossary_lookup(term: str) -> dict[str, str]:
        """Look up a platform term."""
        raise ValueError("glossary offline")

    async with _serving(stub) as app:
        tools_module.transport = httpx2.ASGITransport(app=app)
        try:
            events = await _run("what does the glossary say?")
        finally:
            tools_module.transport = None
    (call,) = [e for e in events if e["type"] == "tool_call"]
    assert call["name"] == "glossary_lookup" and "glossary offline" in call["result"]["error"]
    assert events[-1]["type"] == "end"


def test_any_other_failure_is_model_error_not_the_pipeline_code() -> None:
    """The catch-all is `model_error`, as in echo-pydanticai; `engine_error` is the pipeline's own
    code (`chassis.server.app`), so a workload must not use it."""
    from echo_langgraph.mapping import error_event

    error = error_event(ValueError("the graph fell over"))
    assert error["code"] == "model_error" and error["retryable"] is False
    assert error["message"] == "the graph fell over"
    grouped = error_event(BaseExceptionGroup("wrapped", [KeyError("k")]))
    assert grouped["code"] == "model_error"
