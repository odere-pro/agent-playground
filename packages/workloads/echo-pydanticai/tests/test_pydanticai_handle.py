"""`handle` against the fake model server and a FastMCP stub of the chassis tool endpoint, both
over `httpx2.ASGITransport` (the openai SDK and the MCP client both run on `httpx2`). No socket, no
key, no chassis import: the event schema is read from its file by path.
"""

from __future__ import annotations

import asyncio
import importlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx2
import jsonschema
import pytest
from echo_pydanticai import PROMPT_VERSION, SYSTEM_PROMPT, handle
from fake_model_server import Script, create_app
from fastmcp import FastMCP

# `echo_pydanticai.handle` the attribute is the function; the module holds the test hooks.
handle_module: Any = importlib.import_module("echo_pydanticai.handle")

ROOT = Path(__file__).resolve().parents[4]
EXAMPLE_SCRIPT = ROOT / "packages/fake-model-server/scripts/example.yaml"
EVENTS_SCHEMA = json.loads((ROOT / "packages/chassis/schemas/events.v0.json").read_text())
TRACEPARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
PLANTED = "planted"
STUB_DEFINITION = "A small language model (stub)."

CTX: dict[str, Any] = {
    "request_id": "req-1",
    "trace_id": "0af7651916cd43dd8448eb211c80319c",
    "idempotency_key": "idem-1",
    "agent": "echo",
    "agent_version": "0.0.1",
    "budget": {"max_tokens": 2000, "timeout_ms": 30000},
    "versions": {"chassis": "0.1.0"},
    "model_route": "big-default",
    "traceparent": TRACEPARENT,
}


class Recording(httpx2.AsyncBaseTransport):
    """Delegates to an inner transport and keeps every request it saw, headers included."""

    def __init__(self, inner: httpx2.AsyncBaseTransport) -> None:
        self.inner = inner
        self.requests: list[httpx2.Request] = []

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return await self.inner.handle_async_request(request)


@dataclass
class Stubs:
    model_app: Any
    model: Recording
    tools: Recording
    tool_calls: list[str] = field(default_factory=list)

    def all_requests(self) -> list[httpx2.Request]:
        return [*self.model.requests, *self.tools.requests]


def _tool_stub(calls: list[str]) -> FastMCP:
    server: FastMCP = FastMCP(name="chassis-tools-stub")

    @server.tool
    def glossary_lookup(term: str) -> dict[str, str]:
        """Look up a platform term."""
        calls.append(term)
        return {"term": term, "definition": STUB_DEFINITION}

    return server


@pytest.fixture
async def stubs(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Stubs]:
    monkeypatch.setenv("OPENAI_API_KEY", PLANTED)
    model_app = create_app(Script.from_yaml(EXAMPLE_SCRIPT))
    calls: list[str] = []
    mcp_app = _tool_stub(calls).http_app(path="/mcp", stateless_http=True)
    model = Recording(httpx2.ASGITransport(app=model_app))
    tools = Recording(httpx2.ASGITransport(app=mcp_app))
    monkeypatch.setattr(handle_module, "model_transport", model)
    monkeypatch.setattr(handle_module, "tool_transport", tools)
    async with _lifespan(mcp_app):
        yield Stubs(model_app=model_app, model=model, tools=tools, tool_calls=calls)


@asynccontextmanager
async def _lifespan(app: Any) -> AsyncIterator[None]:
    """Run the app's lifespan in one task of its own: pytest-asyncio may tear a fixture down in
    another task than set it up, and anyio's cancel scopes must exit where they entered.
    """
    ready, stop = asyncio.Event(), asyncio.Event()

    async def hold() -> None:
        async with app.router.lifespan_context(app):
            ready.set()
            await stop.wait()

    task = asyncio.create_task(hold())
    waiter = asyncio.create_task(ready.wait())
    await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
    if task.done():
        waiter.cancel()
        task.result()
    try:
        yield
    finally:
        stop.set()
        await task


async def _run(text: str, ctx: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return [e async for e in handle({"text": text, "data": {}}, ctx or CTX)]


def _check_shape(events: list[dict[str, Any]]) -> None:
    for event in events:
        jsonschema.validate(event, EVENTS_SCHEMA)
        assert event["schema_version"] == "0"
    kinds = [e["type"] for e in events]
    assert kinds[0] == "start"
    assert kinds[-1] in ("end", "error")
    assert kinds.count("start") == 1 and kinds.count("end") + kinds.count("error") == 1


def _text(events: list[dict[str, Any]]) -> str:
    return "".join(e["text"] for e in events if e["type"] == "delta")


async def test_streams_start_deltas_metrics_end(stubs: Stubs) -> None:
    events = await _run("simplify: the quick brown fox")
    _check_shape(events)
    kinds = [e["type"] for e in events]
    assert kinds[:2] == ["start", "delta"] and kinds[-2:] == ["metrics", "end"]
    assert events[0]["request_id"] == "req-1"
    assert kinds.count("delta") > 1, "the reply streams in more than one delta"
    assert _text(events) == "Plain words. Short sentences. Same facts."
    metrics = events[-2]
    assert (metrics["input_tokens"], metrics["output_tokens"]) == (42, 9)
    assert metrics["model_route"] == "big-default" and metrics["attempt"] == 1
    assert events[-1]["status"] == "ok"
    assert "tool_call" not in kinds


async def test_system_prompt_and_input_are_separate_messages(stubs: Stubs) -> None:
    await _run("simplify: secret facts here")
    body = stubs.model_app.state.calls[-1]
    messages = body["messages"]
    assert [m["role"] for m in messages] == ["system", "user"]
    assert messages[0]["content"] == SYSTEM_PROMPT
    assert "secret facts" not in messages[0]["content"]
    assert messages[1]["content"] == "simplify: secret facts here"
    assert body["model"] == "big-default" and body["stream"] is True
    assert PROMPT_VERSION == "simplifier-v1"


async def test_the_model_is_offered_the_chassis_tool(stubs: Stubs) -> None:
    await _run("simplify: x")
    tools = stubs.model_app.state.calls[-1].get("tools") or []
    assert [t["function"]["name"] for t in tools] == ["glossary_lookup"]


async def test_route_comes_from_ctx(stubs: Stubs) -> None:
    await _run("simplify: x", {**CTX, "model_route": "local-small"})
    assert stubs.model_app.state.calls[-1]["model"] == "local-small"
    await _run("simplify: x", {**CTX, "model_route": None})
    assert stubs.model_app.state.calls[-1]["model"] == "big-default"


async def test_glossary_input_runs_the_tool_loop_over_mcp(stubs: Stubs) -> None:
    events = await _run("glossary: what is SLM?")
    _check_shape(events)
    calls = [e for e in events if e["type"] == "tool_call"]
    assert len(calls) == 1
    call = calls[0]
    assert call["name"] == "glossary_lookup" and call["arguments"] == {"term": "SLM"}
    assert call["result"] == {"term": "SLM", "definition": STUB_DEFINITION}
    assert call["call_id"].startswith("call_")
    assert stubs.tool_calls == ["SLM"], "the tool ran on the MCP stub, once"
    kinds = [e["type"] for e in events]
    assert kinds.index("tool_call") < kinds.index("delta")
    assert _text(events) == "From the glossary: SLM means small language model."
    assert kinds[-2:] == ["metrics", "end"]
    # Two model calls at the fake server's default usage (10 in, 5 out) each.
    assert (events[-2]["input_tokens"], events[-2]["output_tokens"]) == (20, 10)
    second = stubs.model_app.state.calls[-1]["messages"]
    assert [m["role"] for m in second] == ["system", "user", "assistant", "tool"]
    assert second[-1]["tool_call_id"] == call["call_id"]


async def test_every_model_and_mcp_request_carries_the_traceparent(stubs: Stubs) -> None:
    await _run("glossary: SLM")
    assert len(stubs.model.requests) == 2
    assert stubs.tools.requests, "the MCP stub saw no request"
    for request in stubs.all_requests():
        assert request.headers.get("traceparent") == TRACEPARENT, request.url


async def test_no_traceparent_header_when_ctx_has_none(stubs: Stubs) -> None:
    await _run("simplify: x", {k: v for k, v in CTX.items() if k != "traceparent"})
    assert stubs.all_requests()
    assert all("traceparent" not in r.headers for r in stubs.all_requests())


async def test_no_key_from_the_environment_reaches_any_request(stubs: Stubs) -> None:
    await _run("glossary: SLM")
    requests = stubs.all_requests()
    assert requests
    for request in requests:
        assert "authorization" not in request.headers, request.url
        assert all(PLANTED not in value for value in request.headers.values()), request.url


async def test_http_500_becomes_one_retryable_error_event(stubs: Stubs) -> None:
    events = await _run("fail please")
    _check_shape(events)
    assert [e["type"] for e in events] == ["start", "error"]
    assert events[-1]["code"] == "http_500" and events[-1]["retryable"] is True
    assert "scripted failure" in events[-1]["message"]
    assert len(stubs.model.requests) == 1, "the workload does not retry; the chassis does"


async def test_connect_error_becomes_a_retryable_error(
    stubs: Stubs, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    monkeypatch.setattr(handle_module, "model_transport", httpx2.MockTransport(refuse))
    events = await _run("simplify: x")
    _check_shape(events)
    assert [e["type"] for e in events] == ["start", "error"]
    assert events[-1]["code"] == "connect_error" and events[-1]["retryable"] is True


async def test_timeout_becomes_a_retryable_error(
    stubs: Stubs, monkeypatch: pytest.MonkeyPatch
) -> None:
    def hang(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("slow model", request=request)

    monkeypatch.setattr(handle_module, "model_transport", httpx2.MockTransport(hang))
    events = await _run("simplify: x")
    _check_shape(events)
    assert [e["type"] for e in events] == ["start", "error"]
    assert events[-1]["code"] == "timeout" and events[-1]["retryable"] is True


async def test_tool_endpoint_down_is_an_error_event_not_an_exception(
    stubs: Stubs, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    monkeypatch.setattr(handle_module, "tool_transport", httpx2.MockTransport(refuse))
    events = await _run("simplify: x")
    _check_shape(events)
    assert [e["type"] for e in events] == ["start", "error"]
    assert events[-1]["code"] == "connect_error" and events[-1]["retryable"] is True


def test_budget_timeout_is_used() -> None:
    assert handle_module.timeout_s({"budget": {"timeout_ms": 1500}}) == 1.5
    assert handle_module.timeout_s({}) == handle_module.DEFAULT_TIMEOUT_S


class Stalling(httpx2.AsyncBaseTransport):
    """A model that never answers, honoring the per-call read timeout the client sets on each
    request the way a real socket transport does: it waits that long, then raises `ReadTimeout`.
    `ASGITransport` and `MockTransport` apply no timeout, so this stands in for one."""

    def __init__(self) -> None:
        self.timeouts: list[float] = []

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        read = float(request.extensions["timeout"]["read"])
        self.timeouts.append(read)
        await asyncio.sleep(read)
        raise httpx2.ReadTimeout("no answer within the per-call timeout", request=request)


async def test_a_model_that_stalls_past_the_per_call_timeout_is_a_timeout_error(
    stubs: Stubs, monkeypatch: pytest.MonkeyPatch
) -> None:
    stalling = Stalling()
    monkeypatch.setattr(handle_module, "model_transport", stalling)
    ctx = {**CTX, "budget": {"max_tokens": 2000, "timeout_ms": 200}}
    async with asyncio.timeout(5):
        events = await _run("simplify: x", ctx)
    _check_shape(events)
    assert [e["type"] for e in events] == ["start", "error"]
    assert events[-1]["code"] == "timeout" and events[-1]["retryable"] is True
    assert stalling.timeouts == [0.2], "the per-call timeout is the run budget"


async def test_a_slow_consumer_is_not_cancelled_by_the_budget(stubs: Stubs) -> None:
    """The handle holds no whole-run deadline across its `yield`s: a consumer that is slower
    than `budget.timeout_ms` between events is not cancelled from inside the generator (the
    connector owns the run deadline). Each model and MCP call still has its own timeout."""
    ctx = {**CTX, "budget": {"max_tokens": 2000, "timeout_ms": 300}}
    events: list[dict[str, Any]] = []
    async for ev in handle({"text": "simplify: the quick brown fox", "data": {}}, ctx):
        events.append(ev)
        if ev["type"] == "delta" and len(events) == 2:
            await asyncio.sleep(0.5)
    _check_shape(events)
    assert events[-1]["type"] == "end", events[-1]
