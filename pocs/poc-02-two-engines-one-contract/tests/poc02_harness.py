"""Shared harness for the PoC-2 scenario tests. Not a test module; the test files import it.

Not for measurements: `forward` in `route_outbound` buffers each whole response before the
workload reads it, so no test here streams through the chassis proxy. Time-to-first-token and
per-delta figures must not come from this path; `demo/measure.py` measures the lanes directly.

Everything here runs offline. A workload's outbound HTTP (its model client, its MCP client, any
client built on httpx or httpx2) is routed into the chassis app through `httpx.ASGITransport` by
patching the httpcore and httpcore2 connection pools, below the OpenTelemetry httpx
instrumentation, so any `traceparent` the workload adds is still on the request the chassis sees.
Each Python workload's MCP hook is pointed at the same route too, so a tool listing never falls
back to a socket. The `sidecar` lane, the TypeScript echo, and the servers the TypeScript echo
calls run on Unix sockets, which `make test` allows (`--disable-socket --allow-unix-socket`).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping, Sequence
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpcore
import httpcore2
import httpx
import httpx2
import pytest
from chassis.adapters.a2a import InProcessConnector, build_agent_card
from chassis.adapters.a2a.inprocess import load_handle
from chassis.adapters.litellm import LiteLLMModel
from chassis.core.envelope import Context, Request
from chassis.fakes import InMemoryConfig, InMemoryTelemetry
from chassis.ports.bundle import PortBundle
from chassis.ports.engine import EngineConnector
from chassis.ports.model import (
    ModelChunk,
    ModelMessage,
    ModelPort,
    ModelResult,
    ToolCallRequest,
    ToolSpec,
    Usage,
)
from chassis.server import ChassisConfig, create_app
from chassis.server.proxy_app import create_proxy_app
from chassis_contracts.helpers import make_context, make_request
from fake_model_server import Script
from fake_model_server import create_app as create_fake_model_app

ROOT = Path(__file__).resolve().parents[3]
POC = ROOT / "pocs/poc-02-two-engines-one-contract"
EXAMPLE_SCRIPT = ROOT / "packages/fake-model-server/scripts/example.yaml"
TYPESCRIPT_ECHO = ROOT / "packages/workloads/echo-typescript"

SIMPLIFIED = "Plain words. Short sentences. Same facts."
"""What the fake model server answers to `simplify: ...` (usage 42 in, 9 out)."""
SIMPLIFY = "simplify: the quick brown fox"
TOOL_PROMPT = "simplify: what does SLM mean? look it up"
AFTER_TOOL = "SLM means a small language model."
"""What `ToolLoopModel` answers once it sees a tool result."""
NO_TOOL = "no glossary_lookup tool was offered"
ROUTE = "big-default"
MODEL_URL = "http://127.0.0.1:8080/v1"
MCP_PATH = "/mcp"
SIDECAR_URL = "http://127.0.0.1:9100"
"""The URL a sidecar's agent card names. The connector reaches it over `uds`; no TCP."""
TYPESCRIPT_URL = "http://127.0.0.1:9000"
"""What the TypeScript echo's agent card names by default; over `uds` it only fills `Host`."""
CHASSIS_PROXY_URL = "http://127.0.0.1:8090"
"""Where a workload's model and MCP calls go (`CHASSIS_MODEL_URL`, `CHASSIS_TOOL_URL`)."""
TIMEOUT_S = 30.0
"""Every async scenario runs under this deadline, so a half-built feature fails, not hangs."""

PYTHON_ENGINES = ("echo_python:handle", "echo_pydanticai:handle", "echo_langgraph:handle")


def engine_params(extra: Mapping[str, str] | None = None) -> list[Any]:
    """The three Python engines as parameters. An engine named in `extra` is strict xfail with
    that reason (for example, a client that drops a header, with the fix).
    """
    reasons = dict(extra or {})
    return [
        pytest.param(
            handle,
            id=handle.split(":")[0],
            marks=[pytest.mark.xfail(strict=True, reason=reasons[handle])]
            if handle in reasons
            else [],
        )
        for handle in PYTHON_ENGINES
    ]


# --- The chassis app ---


def fake_model_over_litellm() -> LiteLLMModel:
    """The chassis's real model adapter, pointed at the fake model server over ASGI."""
    app = create_fake_model_app(Script.from_yaml(EXAMPLE_SCRIPT))
    return LiteLLMModel("http://fake/v1", agent="poc-02", transport=httpx.ASGITransport(app=app))


def chassis_config(engine: Mapping[str, Any]) -> ChassisConfig:
    return ChassisConfig.model_validate(
        {
            "version": "cfg-poc2",
            "profile": "fake",
            "agent": {"name": "simplifier", "version": "0.0.1"},
            "spec": {
                "adapters": {"model": "fake"},
                "engine": dict(engine),
                "model": {"route": ROUTE},
                "prompt": {"version": "simplifier-v1"},
            },
        }
    )


def chassis_app(
    handle: str | None = None,
    *,
    model: ModelPort | None = None,
    tools: Any = None,
    connector: EngineConnector | None = None,
    engine: Mapping[str, Any] | None = None,
) -> Any:
    """The chassis app with injected ports. Default lane: `inprocess` serving `handle`; default
    model: LiteLLM over the fake model server. `tools` goes to `PortBundle.tools` when given.
    """
    spec = dict(engine or {"connector": "inprocess", "handle": handle})
    extra: dict[str, Any] = {} if tools is None else {"tools": tools}
    ports = PortBundle(
        model=model or fake_model_over_litellm(),
        engine=connector or InProcessConnector(),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
        **extra,
    )
    return create_app(chassis_config(spec), ports)


def proxy_for(app: Any) -> Any:
    """The localhost-only proxy app over `app`: what a workload's model and MCP calls reach. No
    fallback to the public app: the proxies moving back to the public port must fail a test. Call
    it once per app, before the app starts (it hooks the MCP lifespan into the public app's).
    """
    return create_proxy_app(app)


@asynccontextmanager
async def running(*apps: Any) -> AsyncIterator[None]:
    """Run the lifespan of each distinct app, in order, in a task of its own, so a fixture can
    hold it across its `yield` (FastMCP's lifespan opens a task group, which must exit in the
    task it entered).
    """
    distinct = list({id(app): app for app in apps}.values())
    ready = asyncio.Event()
    stop = asyncio.Event()

    async def hold() -> None:
        async with AsyncExitStack() as stack:
            for app in distinct:
                await stack.enter_async_context(app.router.lifespan_context(app))
            ready.set()
            await stop.wait()

    task = asyncio.create_task(hold())
    waiter = asyncio.create_task(ready.wait())
    done, _ = await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
    if task in done:
        waiter.cancel()
        task.result()
        raise RuntimeError("the lifespan ended before it was ready")
    try:
        yield
    finally:
        stop.set()
        await task


def client_for(app: Any, base_url: str = "http://chassis") -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=base_url, follow_redirects=True
    )


def sse_frames(text: str) -> list[tuple[str, dict[str, Any]]]:
    """`/v1/run` streamed: `(event name, payload)` per frame."""
    out: list[tuple[str, dict[str, Any]]] = []
    for block in text.split("\n\n"):
        if block.strip():
            event, data = block.splitlines()[:2]
            out.append((event.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return out


# --- Outbound HTTP from a workload, routed into an ASGI app ---


@dataclass
class OutboundCall:
    method: str
    url: httpx.URL
    headers: httpx.Headers
    body: bytes

    @property
    def path(self) -> str:
        return self.url.path


@dataclass
class Outbound:
    """Where a workload's calls land: the proxy app `app` (model pass-through and `/mcp`).
    `public` is the chassis's public app, whose lifespan runs the ports.
    """

    public: Any
    app: Any
    calls: list[OutboundCall] = field(default_factory=list)

    def to(self, prefix: str) -> list[OutboundCall]:
        return [c for c in self.calls if c.path.startswith(prefix)]


# --- The workloads' MCP hooks ---


TransportFactory = Callable[[], httpx2.AsyncBaseTransport]


class _FreshTransport(httpx2.AsyncBaseTransport):
    """An `httpx2` transport that builds a new inner transport per request and closes it after.
    The workloads keep their MCP transport in a module global and close the client after each
    session, and the sidecar lane runs the workload on another event loop, so a shared pool would
    be closed or cross loops. The response is read in full before it returns.
    """

    def __init__(self, build: TransportFactory) -> None:
        self._build = build

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        inner = self._build()
        try:
            response = await inner.handle_async_request(request)
            body = await response.aread()
        finally:
            await inner.aclose()
        return httpx2.Response(response.status_code, headers=response.headers.raw, content=body)


def point_tools_at(monkeypatch: pytest.MonkeyPatch, build: TransportFactory) -> None:
    """Point the MCP hook of each Python workload at `build()`: `echo_python.tools.client_factory`,
    `echo_pydanticai.handle.tool_transport`, `echo_langgraph.tools.transport`. Without it a tool
    listing tries a real socket, and the workload swallows the failure and runs without tools.
    """
    import importlib

    transport = _FreshTransport(build)
    py_tools: Any = importlib.import_module("echo_python.tools")

    def client_factory(headers: dict[str, str], timeout: float) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(headers=headers, timeout=timeout, transport=transport)

    monkeypatch.setattr(py_tools, "client_factory", client_factory)
    monkeypatch.setattr(
        importlib.import_module("echo_pydanticai.handle"), "tool_transport", transport
    )
    monkeypatch.setattr(importlib.import_module("echo_langgraph.tools"), "transport", transport)
    monkeypatch.setenv("CHASSIS_TOOL_URL", f"{CHASSIS_PROXY_URL}{MCP_PATH}")


def tool_list_failures() -> int:
    """The runs that found the MCP endpoint unreachable and ran without tools, summed over the
    workloads that count them (`echo_python`, `echo_langgraph`; `echo_pydanticai` fails the run
    instead). A tool scenario asserts it does not change.
    """
    import importlib

    return sum(
        int(importlib.import_module(name).list_failures)
        for name in ("echo_python.tools", "echo_langgraph.tools")
    )


Send = Callable[[httpx.Request], Awaitable[httpx.Response]]
"""Where `patch_outbound` sends one routed request; returns the response, read in full."""


def route_outbound(monkeypatch: pytest.MonkeyPatch, public: Any) -> Outbound:
    """Send every non-Unix-socket request in this test into the chassis proxy app over ASGI and
    record it, and point each workload's MCP hook at the same route (`patch_outbound`).
    """
    proxy = proxy_for(public)
    outbound = Outbound(public, proxy)
    to_proxy = httpx.ASGITransport(app=proxy)

    async def send(request: httpx.Request) -> httpx.Response:
        response = await to_proxy.handle_async_request(request)
        await response.aread()
        return response

    patch_outbound(monkeypatch, send, outbound.calls)
    return outbound


def patch_outbound(
    monkeypatch: pytest.MonkeyPatch, send: Send, calls: list[OutboundCall] | None = None
) -> list[OutboundCall]:
    """Hand every non-Unix-socket request to `send` and record it in `calls`, and point each
    workload's MCP hook at the same route (`point_tools_at`). `route_outbound` sends into one
    proxy app over ASGI; a caller with several chassis can pick the target per request.

    Two HTTP stacks are patched the same way. `httpx` (echo_python, the chassis's own clients)
    routes through `httpcore.AsyncConnectionPool`. `httpx2`, the httpx 2 line in its own package,
    which the MCP client (`mcp` 2.2), pydantic-ai, and `openai` use, routes through its own core,
    `httpcore2.AsyncConnectionPool` (`httpx2.AsyncHTTPTransport.handle_async_request` builds an
    `httpcore2.Request` and awaits `self._pool.handle_async_request`). Both land in `calls`.
    """
    seen: list[OutboundCall] = [] if calls is None else calls

    async def forward(method: str, raw_url: bytes, raw_headers: Any, body: bytes) -> httpx.Response:
        headers = httpx.Headers([(k.decode(), v.decode()) for k, v in raw_headers])
        url = httpx.URL(raw_url.decode())
        seen.append(OutboundCall(method, url, headers, body))
        return await send(httpx.Request(method, url, headers=headers, content=body))

    original = httpcore.AsyncConnectionPool.handle_async_request

    async def handle(pool: httpcore.AsyncConnectionPool, request: httpcore.Request) -> Any:
        if getattr(pool, "_uds", None):  # a Unix socket is a real lane (the sidecar)
            return await original(pool, request)
        stream: Any = request.stream
        body = b"".join([chunk async for chunk in stream])
        response = await forward(request.method.decode(), bytes(request.url), request.headers, body)
        return httpcore.Response(
            response.status_code, headers=response.headers.raw, content=response.content
        )

    monkeypatch.setattr(httpcore.AsyncConnectionPool, "handle_async_request", handle)

    original2 = httpcore2.AsyncConnectionPool.handle_async_request

    async def handle2(pool: httpcore2.AsyncConnectionPool, request: httpcore2.Request) -> Any:
        if getattr(pool, "_uds", None):
            return await original2(pool, request)
        stream: Any = request.stream
        body = b"".join([chunk async for chunk in stream])
        response = await forward(request.method.decode(), bytes(request.url), request.headers, body)
        return httpcore2.Response(
            response.status_code, headers=response.headers.raw, content=response.content
        )

    monkeypatch.setattr(httpcore2.AsyncConnectionPool, "handle_async_request", handle2)

    class Forward(httpx2.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
            body = await request.aread()
            response = await forward(
                request.method, str(request.url).encode(), request.headers.raw, body
            )
            return httpx2.Response(
                response.status_code, headers=response.headers.raw, content=response.content
            )

    point_tools_at(monkeypatch, Forward)
    monkeypatch.setenv("CHASSIS_MODEL_URL", MODEL_URL)
    return seen


# --- A model that calls the tool once, then answers ---


class ToolLoopModel:
    """A `ModelPort` for the tool scenario: offered `glossary_lookup` and no tool result yet, it
    calls the tool; given a tool result, it answers `AFTER_TOOL`. Records what it saw.
    """

    name = "tool-loop"

    def __init__(self) -> None:
        self.calls: list[tuple[list[ModelMessage], list[str]]] = []

    def _answer(
        self, messages: Sequence[ModelMessage], tools: Sequence[ToolSpec] | None
    ) -> ModelResult:
        offered = [t.name for t in tools or []]
        self.calls.append((list(messages), offered))
        usage = Usage(input_tokens=10, output_tokens=5)
        if any(m.role == "tool" for m in messages):
            return ModelResult(text=AFTER_TOOL, usage=usage, model=ROUTE)
        if "glossary_lookup" in offered:
            call = ToolCallRequest(
                call_id="call_1", name="glossary_lookup", arguments={"term": "SLM"}
            )
            return ModelResult(text="", tool_calls=[call], usage=usage, model=ROUTE)
        return ModelResult(text=NO_TOOL, usage=usage, model=ROUTE)

    def tool_results(self) -> list[str]:
        return [m.content or "" for msgs, _ in self.calls for m in msgs if m.role == "tool"]

    async def complete(
        self,
        messages: Sequence[ModelMessage],
        *,
        route: str,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ModelResult:
        return self._answer(messages, tools)

    async def stream(
        self,
        messages: Sequence[ModelMessage],
        *,
        route: str,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AsyncIterator[ModelChunk]:
        result = self._answer(messages, tools)
        if result.text:
            yield ModelChunk(text=result.text)
        for call in result.tool_calls:
            yield ModelChunk(tool_call=call)
        yield ModelChunk(usage=result.usage, finish=True)


# --- Running a handle or a connector ---


def request_and_context(text: str, request_id: str = "req-poc2") -> tuple[Request, Context]:
    request = make_request(text=text, request_id=request_id)
    return request, make_context(request, model_route=ROUTE)


async def run_handle(path: str, text: str) -> list[dict[str, Any]]:
    """Call a workload's wire-form `handle` directly and collect its events as dicts."""
    request, ctx = request_and_context(text)
    handle = load_handle(path)
    out: list[dict[str, Any]] = []
    async for raw in handle(request.input.model_dump(mode="json"), ctx.model_dump(mode="json")):
        dump = getattr(raw, "model_dump", None)
        out.append(dump(mode="json") if callable(dump) else dict(raw))
    return out


def bundle_for(connector: EngineConnector) -> PortBundle:
    return PortBundle(
        model=fake_model_over_litellm(),
        engine=connector,
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
    )


# --- Servers on Unix sockets (the offline gate allows them) ---


class _LifespanOf:
    """ASGI: the lifespan of `lifespan_app`, every other scope to `app`. The chassis proxy app has
    no lifespan of its own; the public app's lifespan builds the ports and the MCP server.
    """

    def __init__(self, app: Any, lifespan_app: Any) -> None:
        self.app = app
        self.lifespan_app = lifespan_app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        target = self.lifespan_app if scope["type"] == "lifespan" else self.app
        await target(scope, receive, send)


@contextmanager
def on_unix_socket(app: Any, name: str = "app.sock") -> Iterator[str]:
    """Serve `app` with uvicorn on a fresh Unix socket in a thread (its own event loop, lifespan
    on); yields the socket path. The folder is short on purpose: macOS caps the path at 104 bytes.
    """
    import uvicorn

    folder = tempfile.mkdtemp(prefix="poc02-")
    uds = str(Path(folder, name))
    server = uvicorn.Server(uvicorn.Config(app, uds=uds, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            if time.monotonic() > deadline or not thread.is_alive():
                raise RuntimeError(f"uvicorn did not start on {uds}")
            time.sleep(0.05)
        yield uds
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        shutil.rmtree(folder, ignore_errors=True)


@contextmanager
def a2a_on_unix_socket(handle_path: str) -> Iterator[tuple[str, str]]:
    """Serve `handle_path` with the workload's own template server (`workload_a2a.server`) on a
    Unix socket in a thread. Yields `(url, uds)` for `SidecarConnector.setup`.
    """
    from workload_a2a.server import build_app

    card = build_agent_card(name=handle_path, version="0.0.1", url=SIDECAR_URL)
    with on_unix_socket(build_app(load_handle(handle_path), card), "a2a.sock") as uds:
        yield SIDECAR_URL, uds


@contextmanager
def chassis_tools_on_unix_socket() -> Iterator[str]:
    """The chassis proxy app (with `/mcp` over the fake `ToolPort`) on a Unix socket; yields the
    path. For a scenario that runs a workload without `route_outbound`, with `point_tools_at`.
    """
    from chassis.fakes.tool import default_tools

    public = chassis_app("chassis.core.handle:echo_wire", tools=default_tools())
    with on_unix_socket(_LifespanOf(proxy_for(public), public), "proxy.sock") as uds:
        yield uds


def uds_transport(uds: str) -> TransportFactory:
    """For `point_tools_at`: a new `httpx2` transport over `uds` per request."""
    return lambda: httpx2.AsyncHTTPTransport(uds=uds)


def require_tcp(request: pytest.FixtureRequest) -> None:
    """Skip the test when the offline gate has disabled sockets."""
    if request.config.getoption("--disable-socket", default=False):
        pytest.skip("sockets are disabled; run `uv run pytest -m network -p no:socket`")


@contextmanager
def serve_tcp(build: Callable[[str], Any]) -> Iterator[str]:
    """Serve `build(url)` on uvicorn at a free loopback port in a thread; yields the URL. TCP, so
    only for `network` tests.
    """
    import uvicorn

    port = free_port()
    url = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(
        uvicorn.Config(build(url), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            if time.monotonic() > deadline or not thread.is_alive():
                raise RuntimeError(f"uvicorn did not start on {url}")
            time.sleep(0.05)
        yield url
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def free_port() -> int:
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


# --- The TypeScript echo ---


def typescript_node() -> str:
    """The `node` binary, once `dist` is built and no older than `src`. Skips, pointing at
    `npm ci`, when the workload's `node_modules` is absent (CI does not install it yet).
    """
    if not (TYPESCRIPT_ECHO / "node_modules").is_dir():
        pytest.skip(
            f"{TYPESCRIPT_ECHO.relative_to(ROOT)}/node_modules is absent; run `npm ci` there"
        )
    node, npm = shutil.which("node"), shutil.which("npm")
    if node is None or npm is None:
        pytest.skip("node_modules is there but Node is not on PATH; install Node 24, then `npm ci`")
    main = TYPESCRIPT_ECHO / "dist/src/main.js"
    newest = max(path.stat().st_mtime for path in (TYPESCRIPT_ECHO / "src").glob("*.ts"))
    if not main.exists() or main.stat().st_mtime < newest:
        subprocess.run([npm, "run", "build"], cwd=TYPESCRIPT_ECHO, check=True, capture_output=True)
    return node


@contextmanager
def typescript_echo(
    *,
    uds: str | None = None,
    port: int | None = None,
    env: Mapping[str, str] | None = None,
) -> Iterator[str]:
    """`node dist/src/main.js` in packages/workloads/echo-typescript; yields its base URL once
    the agent card answers. With `uds` it listens on that Unix socket (`UDS`), no TCP, and the URL
    only names the card's host (`TYPESCRIPT_URL`); with `port` it binds TCP on loopback, so only
    a `network` test passes it. `env` adds variables, for example `CHASSIS_MODEL_UDS`.
    """
    if (uds is None) == (port is None):
        raise ValueError("pass exactly one of uds and port")
    node = typescript_node()
    child_env = {**os.environ, **(env or {})}
    if uds is not None:
        base_url = TYPESCRIPT_URL
        child_env["UDS"] = uds
        probe = httpx.Client(transport=httpx.HTTPTransport(uds=uds), base_url=base_url)
    else:
        base_url = f"http://127.0.0.1:{port}"
        child_env.update(HOST="127.0.0.1", PORT=str(port))
        probe = httpx.Client(base_url=base_url, trust_env=False)
    proc = subprocess.Popen(
        [node, "dist/src/main.js"],
        cwd=TYPESCRIPT_ECHO,
        env=child_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                if probe.get("/.well-known/agent-card.json", timeout=1.0).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline or proc.poll() is not None:
                proc.kill()
                out = proc.communicate(timeout=5)[0]
                raise RuntimeError(f"echo-typescript did not serve its agent card:\n{out}")
            time.sleep(0.1)
        yield base_url
    finally:
        probe.close()
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


@contextmanager
def typescript_echo_on_unix_sockets(fake_model: Any) -> Iterator[tuple[str, str]]:
    """The TypeScript echo on a Unix socket, its model calls going over another Unix socket to
    `fake_model` (`CHASSIS_MODEL_UDS`). No TCP anywhere. Yields `(url, uds)` for
    `SidecarConnector.setup`.
    """
    with on_unix_socket(fake_model, "model.sock") as model_uds:
        folder = tempfile.mkdtemp(prefix="poc02-")
        uds = str(Path(folder, "ts.sock"))
        env = {"CHASSIS_MODEL_URL": f"{CHASSIS_PROXY_URL}/v1", "CHASSIS_MODEL_UDS": model_uds}
        try:
            with typescript_echo(uds=uds, env=env) as url:
                yield url, uds
        finally:
            shutil.rmtree(folder, ignore_errors=True)


# --- MCP over the chassis app, as plain JSON-RPC ---


async def mcp_session(client: httpx.AsyncClient) -> str | None:
    """`initialize` and `notifications/initialized`; returns the session id, if any."""
    response, message = await mcp_request(
        client,
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "poc-02-tests", "version": "0.0.1"},
        },
    )
    assert "result" in message, message
    session: str | None = response.headers.get("mcp-session-id")
    await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers=_mcp_headers(session),
    )
    return session


def _mcp_headers(session: str | None) -> dict[str, str]:
    headers = {
        "accept": "application/json, text/event-stream",
        "content-type": "application/json",
        "mcp-protocol-version": "2025-06-18",
    }
    if session:
        headers["mcp-session-id"] = session
    return headers


async def mcp_request(
    client: httpx.AsyncClient,
    method: str,
    params: dict[str, Any],
    session: str | None = None,
    request_id: int = 1,
) -> tuple[httpx.Response, dict[str, Any]]:
    """One JSON-RPC request to `/mcp`; the answer comes as JSON or as one SSE `data:` line."""
    response = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
        headers=_mcp_headers(session),
    )
    assert response.status_code == 200, response.text
    if response.headers.get("content-type", "").startswith("text/event-stream"):
        for line in response.text.splitlines():
            if line.startswith("data:"):
                message = json.loads(line[5:])
                if message.get("id") == request_id:
                    return response, dict(message)
        raise AssertionError(f"no answer to {method} in {response.text!r}")
    return response, dict(response.json())


async def glossary_over_mcp(app: Any, term: str = "SLM") -> tuple[dict[str, Any], str]:
    """List the tools on `app`'s `/mcp`, then call `glossary_lookup`. Returns the tool's listing
    and the text of its answer.
    """
    async with client_for(app) as client:
        session = await mcp_session(client)
        _, listed = await mcp_request(client, "tools/list", {}, session, request_id=2)
        tools = {t["name"]: t for t in listed["result"]["tools"]}
        assert "glossary_lookup" in tools, sorted(tools)
        _, called = await mcp_request(
            client,
            "tools/call",
            {"name": "glossary_lookup", "arguments": {"term": term}},
            session,
            request_id=3,
        )
    result = called["result"]
    assert not result.get("isError"), result
    text = "".join(part.get("text", "") for part in result["content"])
    return tools["glossary_lookup"], text
