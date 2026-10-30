"""Test support for the model proxy (proxy port): the chat route and the Anthropic proxy route.

Shared fakes and helpers: the port bundle, an in-process client, a run to correlate with, SSE
frame parsing, a recording model, a held model, a failing model, and a client that leaves a stream.
Not a test module; the `test_model_proxy*.py` files, `test_uncorrelated_cap.py`, and
`test_server.py` import it.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, MutableMapping, Sequence
from typing import Any

import httpx
from chassis import CHASSIS_VERSION
from chassis.adapters.anthropic_compat.types import RawMessageStreamEvent
from chassis.core.envelope import Budget, Context, Request, TaskInput, Versions
from chassis.core.handle import echo
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel, ScriptRule
from chassis.ports.bundle import PortBundle
from chassis.ports.model import (
    ModelChunk,
    ModelError,
    ModelMessage,
    ModelResult,
    ToolCallRequest,
    ToolSpec,
    Usage,
)
from chassis.server import ChassisConfig, create_app
from chassis.server.correlation import RunRecord
from chassis.server.proxy_app import create_proxy_app
from chassis_contracts.inbound import sse
from pydantic import TypeAdapter

TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"  # pragma: allowlist secret (a trace id in a test)
TRACEPARENT = f"00-{TRACE}-00f067aa0ba902b7-01"
"""A `traceparent` that names the run `run_request` opens."""

PROXY_CONFIG: dict[str, Any] = {
    "profile": "fake",
    "agent": {"name": "echo", "version": "0.0.1"},
    "spec": {"engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"}},
}

ANTHROPIC_BODY: dict[str, Any] = {
    "model": "big-default",
    "max_tokens": 64,
    "messages": [{"role": "user", "content": "simplify: the quick brown fox"}],
}
"""A minimal Anthropic Messages body. The scripted model reports 15 tokens for it."""

PLAIN_WORDS = [ScriptRule(match="simplify", reply="Plain words.")]

GLOSSARY_RULES = [
    ScriptRule(
        match="lookup",
        tool_call=ToolCallRequest(
            call_id="call_1", name="mcp__glossary__glossary_lookup", arguments={"term": "SLM"}
        ),
        usage=Usage(input_tokens=812, output_tokens=31),
    ),
    ScriptRule(after_tool=True, reply="SLM is a small language model."),
    ScriptRule(match="simplify", reply="Plain words. Short sentences."),
]
"""A glossary tool call for `lookup`, its answer after the tool, and a plain reply."""

STREAM_EVENT: TypeAdapter[Any] = TypeAdapter(RawMessageStreamEvent)

Frame = tuple[str, dict[str, Any]]


def proxy_ports(model: Any, engine: FakeEngine | None = None) -> PortBundle:
    """The fake port bundle around `model`, with the echo engine unless one is given."""
    return PortBundle(
        model=model,
        engine=engine or FakeEngine(handle=echo),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
    )


def telemetry_of(ports: PortBundle) -> InMemoryTelemetry:
    assert isinstance(ports.telemetry, InMemoryTelemetry)
    return ports.telemetry


def asgi_client(app: Any, *, raise_app_exceptions: bool = True) -> httpx.AsyncClient:
    """An httpx client served by `app` in process, with no socket."""
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions)
    return httpx.AsyncClient(transport=transport, base_url="http://chassis")


def run_request(max_tokens: int) -> tuple[Request, Context]:
    """A run with a token budget, traced by `TRACE`, for `app.state.runs.register`."""
    request = Request(
        request_id="req-1",
        trace_id=TRACE,
        idempotency_key="idem-1",
        agent="echo",
        agent_version="0.0.1",
        input=TaskInput(text="x"),
        budget=Budget(max_tokens=max_tokens),
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


def sse_frames(text: str) -> list[Frame]:
    """Parse a named-event SSE body; every frame must name its event and carry `data:`. A thin
    wrapper over the one SSE parser, `chassis_contracts.inbound.sse`.
    """
    out: list[Frame] = []
    for event in sse([text]):
        assert event.event is not None and event.data, event
        out.append((event.event, json.loads(event.data)))
    return out


def frame_names(frames: list[Frame]) -> list[str]:
    return [name for name, _ in frames]


def validate_stream_events(frames: list[Frame]) -> None:
    """Every frame is a valid Anthropic stream event (`ping` is outside the SDK's union)."""
    for name, data in frames:
        if name != "ping":
            STREAM_EVENT.validate_python(data)


class RecordingModel(ScriptedModel):
    """A `ScriptedModel` that records tools, `max_tokens`, and temperature per call. With a
    `gate`, a stream sets `streaming` and then waits for the gate before its first chunk.
    """

    def __init__(
        self, rules: Sequence[ScriptRule] | None = None, *, gate: asyncio.Event | None = None
    ) -> None:
        super().__init__(rules)
        self.tools: list[Sequence[ToolSpec] | None] = []
        self.max_tokens: list[int | None] = []
        self.temperatures: list[float] = []
        self.gate = gate
        self.streaming = asyncio.Event()

    def _note(self, tools: Sequence[ToolSpec] | None, max_tokens: int | None, temp: float) -> None:
        self.tools.append(tools)
        self.max_tokens.append(max_tokens)
        self.temperatures.append(temp)

    async def complete(
        self,
        messages: Sequence[ModelMessage],
        *,
        route: str,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ModelResult:
        self._note(tools, max_tokens, temperature)
        return await super().complete(
            messages, route=route, tools=tools, temperature=temperature, max_tokens=max_tokens
        )

    async def stream(
        self,
        messages: Sequence[ModelMessage],
        *,
        route: str,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AsyncIterator[ModelChunk]:
        self._note(tools, max_tokens, temperature)
        self.streaming.set()
        if self.gate is not None:
            await asyncio.wait_for(self.gate.wait(), timeout=5)
        async for chunk in super().stream(
            messages, route=route, tools=tools, temperature=temperature, max_tokens=max_tokens
        ):
            yield chunk


class FailingModel:
    """Raises `error` on every call, before any chunk."""

    name = "failing"

    def __init__(self, error: ModelError) -> None:
        self.error = error

    async def complete(self, messages: Sequence[ModelMessage], **_: Any) -> ModelResult:
        raise self.error

    async def stream(self, messages: Sequence[ModelMessage], **_: Any) -> AsyncIterator[ModelChunk]:
        raise self.error
        yield ModelChunk()


class GatedModel:
    """A model held open until `gate` is set.

    A stream sends `first` (with `first_usage`, unless `silent`), waits, then finishes; `closed`
    turns true when the stream ends or is closed. A complete call waits, then answers or fails.
    Final usage is `PROMPT` input tokens plus the full `max_tokens` as output: the worst case an
    upstream that honors `max_tokens` can report.
    """

    name = "gated"
    PROMPT = 10

    def __init__(
        self, *, first_usage: Usage | None = None, silent: bool = False, fail: bool = False
    ) -> None:
        self.gate = asyncio.Event()
        self.first_usage = first_usage
        self.silent = silent
        self.fail = fail
        self.max_tokens: list[int | None] = []
        self.closed = False

    async def complete(
        self, messages: Sequence[ModelMessage], *, max_tokens: int | None = None, **_: Any
    ) -> ModelResult:
        self.max_tokens.append(max_tokens)
        await self.gate.wait()
        if self.fail:
            raise ModelError("upstream_error", "the upstream failed", retryable=True)
        return ModelResult(text="ok", usage=self._usage(max_tokens))

    async def stream(
        self, messages: Sequence[ModelMessage], *, max_tokens: int | None = None, **_: Any
    ) -> AsyncIterator[ModelChunk]:
        self.max_tokens.append(max_tokens)
        try:
            if not self.silent:
                yield ModelChunk(text="first", usage=self.first_usage)
            await self.gate.wait()
            yield ModelChunk(usage=self._usage(max_tokens), finish=True)
        finally:
            self.closed = True

    def _usage(self, max_tokens: int | None) -> Usage:
        return Usage(input_tokens=self.PROMPT, output_tokens=max_tokens or 0)


async def stream_and_leave(
    proxy: Any, path: str, body: dict[str, Any], marker: bytes, headers: dict[str, str]
) -> list[Any]:
    """POST over raw ASGI; the client leaves once a sent body chunk holds `marker`."""
    raw = json.dumps(body).encode()
    gone = asyncio.Event()
    sent: list[Any] = []
    first = True

    async def receive() -> dict[str, Any]:
        nonlocal first
        if first:
            first = False
            return {"type": "http.request", "body": raw, "more_body": False}
        await gone.wait()
        return {"type": "http.disconnect"}

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(message)
        if marker in message.get("body", b""):
            gone.set()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(raw)).encode()),
            *[(k.encode(), v.encode()) for k, v in headers.items()],
        ],
        "client": ("127.0.0.1", 1),
        "server": ("chassis", 80),
        "app": proxy,
    }
    await asyncio.wait_for(proxy(scope, receive, send), 5)
    return sent


async def abandon_stream(
    model: GatedModel, path: str, body: dict[str, Any], marker: bytes
) -> RunRecord:
    """Stream `body` to `path` in a run of 1000 tokens, leave at `marker`, then release the model.

    Returns the run's record once the route has settled the call.
    """
    app = create_app(ChassisConfig.model_validate(PROXY_CONFIG), proxy_ports(model))
    proxy = create_proxy_app(app)
    request, ctx = run_request(max_tokens=1000)
    async with (
        app.router.lifespan_context(app),
        app.state.runs.register(request, ctx) as record,
    ):
        try:
            sent = await stream_and_leave(proxy, path, body, marker, {"traceparent": TRACEPARENT})
            assert sent[0]["status"] == 200
        finally:
            model.gate.set()
        await asyncio.sleep(0)
        assert isinstance(record, RunRecord)
        return record
