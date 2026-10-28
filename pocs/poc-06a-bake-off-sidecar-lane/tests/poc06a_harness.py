"""Shared harness for the PoC-6a lane bindings. Not a test module; the test files import it.

It builds on `poc06_harness` (the task spec, the pass checks, the engine registry) and the PoC-2
harness (the chassis app, the template A2A server on a Unix socket, the TypeScript runner). What it
adds:

- **Two fake backends on Unix sockets.** The fake model server on `scripts/bakeoff.yaml` and the
  two-tool fake MCP server each run under uvicorn on their own socket, behind a recorder that
  keeps the method, path, and headers of every request (so a test can read the `traceparent` an
  engine sent). One pair serves a whole module; `Backends.reset` clears it between tests.
- **Engine wiring from the registry.** Each Python engine's test-only model and tool hooks (named
  in `ENGINES`) are pointed at those sockets. A new engine in the registry is wired with no change
  here. The TypeScript agent reaches the model through `CHASSIS_MODEL_UDS` and its tools through a
  `--import` preload (`poc06a_tool_uds.mjs`), because its MCP client has no socket option.
- **A connected lane.** `connected(engine, lane)` yields an `EngineConnector` for the engine in
  memory (`inprocess`) or over A2A on a Unix socket (`sidecar`), and `run_task` runs one benchmark
  task through it and returns the events as dicts for the pass checks.

No TCP anywhere, no key.
"""

from __future__ import annotations

import importlib
import shutil
import tempfile
from collections.abc import AsyncIterator, Iterator
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import httpx2
import pytest
from chassis.adapters.a2a import InProcessConnector
from chassis.adapters.a2a.sidecar import SidecarConnector
from chassis.core.envelope import Context, Request
from chassis.ports.engine import EngineConnector
from chassis_contracts.helpers import make_context, make_request
from fake_mcp_server.server import FakeMcpState
from fake_mcp_server.server import create_app as create_tool_app
from fake_model_server import Script
from fake_model_server import create_app as create_model_app
from poc02_harness import (
    TYPESCRIPT_ECHO,
    a2a_on_unix_socket,
    bundle_for,
    on_unix_socket,
    typescript_echo,
)
from poc06_harness import (
    BAKEOFF_SCRIPT,
    CHECKS,
    ENGINES,
    MODEL_URL,
    ROUTE,
    TOOL_URL,
    Engine,
    Task,
    Verdict,
)

LANES = ("inprocess", "sidecar")
TOOL_HOST = "tools.invalid"
"""The host in `TOOL_URL`; the TypeScript preload sends requests to it over the tool socket."""
PRELOAD = Path(__file__).resolve().parent / "poc06a_tool_uds.mjs"

# --- Recording ASGI wrapper ---------------------------------------------------------------------


@dataclass(frozen=True)
class Seen:
    """One HTTP request a backend received."""

    method: str
    path: str
    headers: dict[str, str]

    @property
    def traceparent(self) -> str | None:
        return self.headers.get("traceparent")


class Recorder:
    """Pure ASGI wrapper: records every HTTP request, passes every scope (lifespan too) on."""

    def __init__(self, app: Any, seen: list[Seen]) -> None:
        self.app = app
        self.seen = seen

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
            self.seen.append(Seen(scope["method"], scope["path"], headers))
        await self.app(scope, receive, send)


@dataclass
class Backends:
    """The fake model server and the two-tool fake MCP server, each on a Unix socket."""

    model_uds: str
    tool_uds: str
    model_app: Any
    tool_state: FakeMcpState
    model_seen: list[Seen] = field(default_factory=list)
    tool_seen: list[Seen] = field(default_factory=list)

    def reset(self) -> None:
        self.model_seen.clear()
        self.tool_seen.clear()
        self.model_app.state.calls.clear()
        self.tool_state.calls.clear()

    def model_calls(self) -> list[Seen]:
        """The chat-completion requests."""
        return [s for s in self.model_seen if s.path.endswith("/chat/completions")]

    def tool_requests(self) -> list[Seen]:
        """Every request to the MCP endpoint."""
        return [s for s in self.tool_seen if s.path.startswith("/mcp")]

    @property
    def tool_calls(self) -> list[tuple[str, dict[str, Any]]]:
        """The tool calls the MCP server ran: (tool, arguments)."""
        return [(str(c["tool"]), dict(c["arguments"])) for c in self.tool_state.calls]


@contextmanager
def serve_backends() -> Iterator[Backends]:
    """Start both fakes, each on its own Unix socket with its lifespan running."""
    model_app = create_model_app(Script.from_yaml(BAKEOFF_SCRIPT))
    state = FakeMcpState()
    tool_app = create_tool_app(state)
    model_seen: list[Seen] = []
    tool_seen: list[Seen] = []
    with (
        on_unix_socket(Recorder(model_app, model_seen), "model.sock") as model_uds,
        on_unix_socket(Recorder(tool_app, tool_seen), "tools.sock") as tool_uds,
    ):
        yield Backends(model_uds, tool_uds, model_app, state, model_seen, tool_seen)


# --- Wiring a Python engine's hooks -------------------------------------------------------------


class _FreshHttpx(httpx.AsyncBaseTransport):
    """An `httpx` transport over a Unix socket that opens a new connection per request. A workload
    keeps its hook in a module global and the lanes run it on different event loops, so a pooled
    connection could cross loops. The response is read in full (no timing is measured here)."""

    def __init__(self, uds: str) -> None:
        self._uds = uds

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        inner = httpx.AsyncHTTPTransport(uds=self._uds)
        try:
            response = await inner.handle_async_request(request)
            body = await response.aread()
        finally:
            await inner.aclose()
        return httpx.Response(response.status_code, headers=response.headers.raw, content=body)


class _FreshHttpx2(httpx2.AsyncBaseTransport):
    """The same for the `httpx2` line (the MCP client, pydantic-ai, `openai`, langchain)."""

    def __init__(self, uds: str) -> None:
        self._uds = uds

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        inner = httpx2.AsyncHTTPTransport(uds=self._uds)
        try:
            response = await inner.handle_async_request(request)
            body = await response.aread()
        finally:
            await inner.aclose()
        return httpx2.Response(response.status_code, headers=response.headers.raw, content=body)


def _set_hook(monkeypatch: pytest.MonkeyPatch, dotted: str, value: object) -> None:
    module, _, attr = dotted.rpartition(".")
    monkeypatch.setattr(importlib.import_module(module), attr, value)


def wire_python_engine(monkeypatch: pytest.MonkeyPatch, engine: Engine, backends: Backends) -> None:
    """Point a Python engine's model and tool hooks at the backends' sockets and set the two URLs.
    The hooks are the dotted names in the registry; echo-python's model hook takes an `httpx`
    transport and its tool hook is a client factory, the others take `httpx2` transports."""
    if engine.model_hook is None or engine.tool_hook is None:
        raise ValueError(f"{engine.name} has no module hooks (it is not a Python engine)")
    model: object = (
        _FreshHttpx(backends.model_uds)
        if engine.name == "echo-python"
        else _FreshHttpx2(backends.model_uds)
    )
    tools = _FreshHttpx2(backends.tool_uds)
    tool_hook: object = tools
    if engine.tool_hook.endswith("client_factory"):

        def client_factory(headers: dict[str, str], timeout: float) -> httpx2.AsyncClient:
            return httpx2.AsyncClient(headers=headers, timeout=timeout, transport=tools)

        tool_hook = client_factory
    _set_hook(monkeypatch, engine.model_hook, model)
    _set_hook(monkeypatch, engine.tool_hook, tool_hook)
    monkeypatch.setenv("CHASSIS_MODEL_URL", MODEL_URL)
    monkeypatch.setenv("CHASSIS_TOOL_URL", TOOL_URL)


# --- The TypeScript agent -----------------------------------------------------------------------


@dataclass(frozen=True)
class SidecarAddress:
    """Where a sidecar listens: the URL its card names and its Unix socket."""

    url: str
    uds: str


@contextmanager
def typescript_agent(backends: Backends) -> Iterator[SidecarAddress]:
    """`node dist/src/main.js` on a Unix socket. Its model call goes to `backends.model_uds`
    (`CHASSIS_MODEL_UDS`) and its MCP calls to `backends.tool_uds` (the preload). Skips, through
    `typescript_echo`, when `node_modules` is absent."""
    folder = tempfile.mkdtemp(prefix="poc06a-")
    uds = str(Path(folder, "ts.sock"))
    env = {
        "CHASSIS_MODEL_URL": MODEL_URL,
        "CHASSIS_MODEL_UDS": backends.model_uds,
        "CHASSIS_TOOL_URL": TOOL_URL,
        "POC06A_TOOL_UDS": backends.tool_uds,
        "POC06A_TOOL_HOST": TOOL_HOST,
        "POC06A_UDS_FETCH_MODULE": (TYPESCRIPT_ECHO / "dist/src/handle.js").as_uri(),
        "NODE_OPTIONS": f"--import={PRELOAD.as_uri()}",
    }
    try:
        with typescript_echo(uds=uds, env=env) as url:
            yield SidecarAddress(url, uds)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


# --- A connected lane ---------------------------------------------------------------------------


def sidecar_spec(address: SidecarAddress) -> dict[str, Any]:
    return {"connector": "sidecar", "url": address.url, "uds": address.uds}


@asynccontextmanager
async def sidecar_connector(address: SidecarAddress) -> AsyncIterator[EngineConnector]:
    """A set-up `SidecarConnector` to a sidecar on a Unix socket; closed on exit."""
    connector = SidecarConnector()
    await connector.setup(sidecar_spec(address), bundle_for(connector))
    try:
        yield connector
    finally:
        await connector.close()


@asynccontextmanager
async def inprocess_connector(handle: str) -> AsyncIterator[EngineConnector]:
    """A set-up `InProcessConnector` serving `handle`; closed on exit."""
    connector = InProcessConnector()
    await connector.setup({"connector": "inprocess", "handle": handle}, bundle_for(connector))
    try:
        yield connector
    finally:
        await connector.close()


def has_lane(engine: Engine, lane: str) -> bool:
    """A Python engine runs in both lanes; the TypeScript agent is a Node process, so only as a
    sidecar."""
    return lane == "sidecar" or engine.language == "python"


@asynccontextmanager
async def connected(
    engine: Engine, lane: str, typescript: SidecarAddress | None = None
) -> AsyncIterator[EngineConnector]:
    """The engine behind the chassis connector of `lane`. `typescript` is the running Node agent
    (see `typescript_agent`), needed for the TypeScript engine."""
    if not has_lane(engine, lane):
        raise ValueError(f"{engine.name} has no {lane} lane")
    async with AsyncExitStack() as stack:
        if engine.language == "typescript":
            if typescript is None:
                raise ValueError("pass the running TypeScript agent")
            yield await stack.enter_async_context(sidecar_connector(typescript))
        elif lane == "inprocess":
            yield await stack.enter_async_context(inprocess_connector(engine.handle))
        else:
            url, uds = stack.enter_context(a2a_on_unix_socket(engine.handle))
            yield await stack.enter_async_context(sidecar_connector(SidecarAddress(url, uds)))


def request_for(text: str, request_id: str = "req-poc06a") -> tuple[Request, Context]:
    request = make_request(text=text, request_id=request_id)
    return request, make_context(request, model_route=ROUTE)


async def run_events(
    connector: EngineConnector, text: str, request_id: str = "req-poc06a"
) -> tuple[list[dict[str, Any]], Context]:
    """Run `text` through the connector; the events as wire dicts, and the run's context."""
    request, ctx = request_for(text, request_id)
    events = [
        e.model_dump(mode="json", exclude_none=True) async for e in connector.run(request, ctx)
    ]
    return events, ctx


async def run_task(
    engine: Engine, lane: str, task: Task, typescript: SidecarAddress | None = None
) -> tuple[list[dict[str, Any]], Verdict, Context]:
    """One benchmark task, one engine, one lane: the events, the verdict of its pass check, and
    the run's context."""
    async with connected(engine, lane, typescript) as connector:
        events, ctx = await run_events(connector, task.text, f"req-{engine.name}-{task.name}")
    return events, CHECKS[task.name](events), ctx


def sidecar_engines() -> dict[str, Engine]:
    """The registry's trusted engines: every `sidecar`-lane engine."""
    return {n: e for n, e in ENGINES.items() if e.lane == "sidecar"}


def python_engines() -> dict[str, Engine]:
    return {n: e for n, e in sidecar_engines().items() if e.language == "python"}


def lane_cells() -> list[tuple[str, str]]:
    """(engine, lane) for every trusted engine and lane, the TypeScript in-memory cell included
    (a test marks it skipped, so the matrix shows the gap)."""
    return [(n, lane) for n in sidecar_engines() for lane in LANES]


def cell_params() -> list[Any]:
    """`pytest.param(engine, lane)` per cell; the TypeScript in-memory cell is skipped."""
    out: list[Any] = []
    for name, lane in lane_cells():
        marks = []
        if not has_lane(ENGINES[name], lane):
            marks.append(pytest.mark.skip(reason=f"{name} is a Node process: no in-memory lane"))
        out.append(pytest.param(name, lane, id=f"{name}-{lane}", marks=marks))
    return out
