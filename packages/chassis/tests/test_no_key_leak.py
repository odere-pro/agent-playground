"""A client's key never goes past the public interfaces (ADR-001 hard requirement 1).

Every call sends a fresh canary as `Authorization: Bearer sk-canary-<random>` and `x-api-key`
(through MCP, as the MCP client's own headers). The canary must be absent from the logs (the
telemetry port's and stdlib `logging`), the spans and their attributes, the counters and their
labels, what the workload's `handle` received (`ctx` and `input`), and every reply body and
header: complete, streamed, and error replies. Two engines: a `FakeEngine` probe that records
`ctx` and `input` as objects, and the real in-process A2A lane with the contract suite's probe,
which echoes them into the reply.
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import AsyncIterator
from typing import Any

import httpx
import httpx2
import pytest
from chassis.core.envelope import Context, TaskInput
from chassis.core.events import Delta, End, Error, Event, Start
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.ports.bundle import PortBundle
from chassis.server import ChassisConfig, create_app
from chassis.server.interfaces.mcp import MCP_PATH
from fastapi import FastAPI
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

AGENT = "echo"
CONFIG: dict[str, Any] = {
    "version": "cfg-1",
    "profile": "fake",
    "agent": {"name": AGENT, "version": "0.0.1"},
    "spec": {"engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"}},
}
LANE_CONFIG: dict[str, Any] = {
    **CONFIG,
    "spec": {
        "engine": {"connector": "inprocess", "handle": "chassis_contracts.interface:probe_handle"}
    },
}


class Probe:
    """A `handle` that records what it was given."""

    def __init__(self, *, fail: bool = False) -> None:
        self.seen: list[tuple[TaskInput, Context]] = []
        self.fail = fail

    async def __call__(self, input: TaskInput, ctx: Context) -> AsyncIterator[Event]:
        self.seen.append((input, ctx))
        yield Start(request_id=ctx.request_id)
        if self.fail:
            yield Error(code="engine_error", message="probe failed", retryable=False)
            return
        yield Delta(text="ok ")
        yield Delta(text="done")
        yield End(status="ok", output={"text": "ok done"})


def _canary() -> str:
    return f"sk-canary-{secrets.token_hex(12)}"


def _headers(canary: str) -> dict[str, str]:
    return {"authorization": f"Bearer {canary}", "x-api-key": canary}


def _app(probe: Probe | None) -> tuple[FastAPI, InMemoryTelemetry]:
    telemetry = InMemoryTelemetry()
    if probe is None:
        app = create_app(ChassisConfig.model_validate(LANE_CONFIG))
        return app, telemetry  # the lane's own telemetry is read from app.state after start
    ports = PortBundle(
        model=ScriptedModel(),
        engine=FakeEngine(handle=probe),
        config=InMemoryConfig(),
        telemetry=telemetry,
    )
    return create_app(ChassisConfig.model_validate(CONFIG), ports), telemetry


def _calls() -> list[tuple[str, dict[str, Any]]]:
    """`(path, body)` for every interface, complete and streamed, and refused bodies."""
    user = [{"role": "user", "content": "hello"}]
    return [
        ("/v1/run", {"input": {"text": "hello"}}),
        ("/v1/run", {"input": {"text": "hello"}, "stream": True}),
        ("/v1/run", {"input": {"text": "hello"}, "agent": "other"}),  # 400
        ("/v1/run", {"input": {"text": 5}}),  # 422
        ("/v1/run", {"input": {"text": "hello"}, "budget": {"max_tokens": 10**9}}),  # 400 limit
        ("/v1/chat/completions", {"model": AGENT, "messages": user}),
        ("/v1/chat/completions", {"model": AGENT, "messages": user, "stream": True}),
        ("/v1/chat/completions", {"model": "other", "messages": user}),  # 404
        ("/v1/chat/completions", {"model": AGENT, "messages": "x"}),  # 400
        ("/v1/messages", {"model": AGENT, "max_tokens": 50, "messages": user}),
        ("/v1/messages", {"model": AGENT, "max_tokens": 50, "messages": user, "stream": True}),
        ("/v1/messages", {"model": "other", "max_tokens": 50, "messages": user}),  # 404
        ("/v1/messages", {"model": AGENT, "max_tokens": 10**9, "messages": user}),  # 400 limit
    ]


def _mcp_client(app: FastAPI, headers: dict[str, str]) -> Client[Any]:
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


async def _exercise(app: FastAPI, canary: str) -> list[str]:
    """Every call with the canary; the text of every reply, headers and body."""
    replies: list[str] = []
    headers = _headers(canary)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://chassis") as client:
        for path, body in _calls():
            reply = await client.post(path, json=body, headers=headers)
            replies.append(f"{path} {reply.status_code} {dict(reply.headers)!r} {reply.text}")
        oversized = await client.post(
            "/v1/run",
            content=b"{" + b"x" * 2_000_000,
            headers={**headers, "content-type": "application/json"},
        )
        replies.append(f"413 {oversized.status_code} {dict(oversized.headers)!r} {oversized.text}")
        assert oversized.status_code == 413
    async with _mcp_client(app, headers) as mcp:
        tools = await mcp.list_tools()
        replies.append(repr(tools))
        result = await mcp.call_tool(AGENT, {"input": {"text": "hello"}}, raise_on_error=False)
        replies.append(repr(result))
        refused = await mcp.call_tool(
            AGENT, {"input": {"text": "hello"}, "agent": "other"}, raise_on_error=False
        )
        assert refused.is_error is True
        replies.append(repr(refused))
    return replies


def _assert_absent(canary: str, where: str, value: Any) -> None:
    assert canary not in repr(value), f"the canary leaked into {where}"


@pytest.mark.parametrize("fail", [False, True], ids=["ok", "engine-error"])
async def test_a_client_key_never_leaks_from_any_interface(
    fail: bool, caplog: pytest.LogCaptureFixture
) -> None:
    canary = _canary()
    probe = Probe(fail=fail)
    app, telemetry = _app(probe)
    caplog.set_level(logging.DEBUG)
    async with app.router.lifespan_context(app):
        replies = await _exercise(app, canary)
    assert probe.seen, "the probe never ran"
    for input, ctx in probe.seen:
        _assert_absent(canary, "the handle's input", input.model_dump())
        _assert_absent(canary, "the handle's ctx", ctx.model_dump())
    _assert_absent(canary, "a reply", replies)
    _assert_absent(canary, "the telemetry logs", telemetry.logs)
    _assert_absent(canary, "the spans", [(s.name, s.attributes) for s in telemetry.spans])
    _assert_absent(canary, "the counters or their labels", telemetry.counters)
    logged = [f"{r.getMessage()} {r.__dict__!r}" for r in caplog.records]
    _assert_absent(canary, "the stdlib logs", logged)
    assert any(" 200 " in reply for reply in replies)
    assert any(" 400 " in reply for reply in replies)


async def test_a_client_key_never_reaches_the_workload_over_the_a2a_lane(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The real in-process lane: the contract suite's probe echoes its `input` and `ctx` into
    the answer, so a key in either would show in a reply."""
    canary = _canary()
    app, _ = _app(None)
    caplog.set_level(logging.DEBUG)
    async with app.router.lifespan_context(app):
        replies = await _exercise(app, canary)
        telemetry = app.state.ports.telemetry
    assert any(r"\"ctx\"" in reply for reply in replies), "the probe never echoed"
    _assert_absent(canary, "a reply (so the handle's input or ctx)", replies)
    assert isinstance(telemetry, InMemoryTelemetry), "the fake profile's telemetry changed"
    _assert_absent(canary, "the telemetry logs", telemetry.logs)
    _assert_absent(canary, "the spans", [(s.name, s.attributes) for s in telemetry.spans])
    _assert_absent(canary, "the counters or their labels", telemetry.counters)
    logged = [f"{r.getMessage()} {r.__dict__!r}" for r in caplog.records]
    _assert_absent(canary, "the stdlib logs", logged)
