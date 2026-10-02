"""Shared harness for the PoC-5 offline remote-lane tests. Not a test module; the tests import it.

The whole `remote` lane in one process, with no TCP socket (`make test` allows Unix sockets):

    chassis public app (ASGI, in the test)
      -> `RemoteConnector` (`spec.engine.connector: remote`), `Authorization: Bearer` on every
         request, over a Unix socket
      -> the workload's template A2A server, built by its own CLI (`workload-a2a serve --uds ...
         --require-token-env ...`), so the bearer check is the one a remote pod runs
      -> `echo_python:handle`, whose model and MCP calls carry `CHASSIS_API_TOKEN` as a bearer
      -> the chassis's remote proxy listener (`create_remote_proxy_app`: `BearerAuth`, then
         `RequireRun`), served on its own Unix socket
      -> the model port (a scripted fake) and the tool port (the fake `glossary_lookup`)
      -> events back over A2A to the chassis.

Every server runs on the test's own event loop, so the chassis's run registry, the model fake,
and the telemetry fake are touched from one loop only.

Tokens are random per test (`new_token`); no file holds a value. The variable names are fixed
(`TOKEN_ENV` and the rest); a test sets them with `monkeypatch`.
"""

from __future__ import annotations

import asyncio
import importlib
import secrets
import shutil
import tempfile
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from chassis.adapters.a2a import InProcessConnector
from chassis.adapters.a2a.remote import RemoteConnector
from chassis.adapters.a2a.sidecar import SidecarConnector
from chassis.fakes import InMemoryConfig, InMemoryTelemetry, ScriptedModel, default_tools
from chassis.fakes.model import ScriptRule
from chassis.ports.bundle import PortBundle
from chassis.ports.engine import EngineConnector
from chassis.ports.model import ToolCallRequest
from chassis.ports.tool import ToolDefinition, ToolResult
from chassis.server import ChassisConfig, create_app
from chassis.server.remote_auth import create_remote_proxy_app, remote_tokens
from poc02_harness import ROUTE, point_tools_at, running, uds_transport

__all__ = [
    "HOLD",
    "PREVIOUS_TOKEN_ENV",
    "REMOTE_PROXY_URL",
    "REMOTE_URL",
    "REPLY",
    "ROUTE",
    "SENDS_ENV",
    "TOKEN_ENV",
    "WORKLOAD_PREVIOUS_ENV",
    "WORKLOAD_TOKEN_ENV",
    "GatedTools",
    "HeldRun",
    "RemoteLane",
    "Tokens",
    "inprocess_lane",
    "new_token",
    "new_trace_id",
    "remote_config",
    "remote_lane",
    "remote_lane_connector",
    "scripted_model",
    "sidecar_lane",
    "traceparent",
    "workload_server",
]

TOKEN_ENV = "POC05_REMOTE_TOKEN"
"""`spec.engine.auth.token_env`: the connector sends it; the remote listener accepts it."""
PREVIOUS_TOKEN_ENV = "POC05_REMOTE_TOKEN_PREVIOUS"
"""`spec.engine.auth.previous_token_env`: the remote listener also accepts it during a rotation."""
WORKLOAD_TOKEN_ENV = "POC05_WORKLOAD_TOKEN"
"""The workload server's `--require-token-env`."""
WORKLOAD_PREVIOUS_ENV = "POC05_WORKLOAD_TOKEN_PREVIOUS"
"""The workload server's `--previous-token-env`."""
SENDS_ENV = "CHASSIS_API_TOKEN"
"""What `echo_python` sends as its bearer on every model and MCP call."""

REMOTE_URL = "http://echo-remote:9000"
"""`spec.engine.url`. Over `uds` it only fills the `Host` header and the path."""
REMOTE_PROXY_URL = "http://chassis-remote:8091"
"""Where the workload's model and MCP calls go. Over a Unix socket it only fills `Host`."""
START_TIMEOUT_S = 10.0


def new_token() -> str:
    """A random per-test token. The prefix marks it as a test value, not a key."""
    return f"poc05-test-{secrets.token_hex(16)}"


def new_trace_id() -> str:
    """A W3C trace id (32 hex) for one run, so a test can name the run in a `traceparent`."""
    return secrets.token_hex(16)


def traceparent(trace_id: str) -> str:
    return f"00-{trace_id}-{secrets.token_hex(8)}-01"


@dataclass(frozen=True)
class Tokens:
    """Who holds which token. In steady state all five are one value (`Tokens.one()`); during a
    rotation the current and previous slots differ (plan section 2.2).
    """

    chassis: str
    """`TOKEN_ENV`: the connector sends it to the workload; the remote listener accepts it."""
    workload: str
    """`WORKLOAD_TOKEN_ENV`: the workload server requires it."""
    sends: str
    """`SENDS_ENV`: the workload sends it to the remote listener."""
    chassis_previous: str | None = None
    workload_previous: str | None = None

    @classmethod
    def one(cls) -> Tokens:
        token = new_token()
        return cls(chassis=token, workload=token, sends=token)

    def values(self) -> set[str]:
        return {
            t
            for t in (
                self.chassis,
                self.workload,
                self.sends,
                self.chassis_previous,
                self.workload_previous,
            )
            if t
        }

    def apply(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Set the variables; an unused previous slot is removed, not left from a former test."""
        monkeypatch.setenv(TOKEN_ENV, self.chassis)
        monkeypatch.setenv(WORKLOAD_TOKEN_ENV, self.workload)
        monkeypatch.setenv(SENDS_ENV, self.sends)
        for name, value in (
            (PREVIOUS_TOKEN_ENV, self.chassis_previous),
            (WORKLOAD_PREVIOUS_ENV, self.workload_previous),
        ):
            if value:
                monkeypatch.setenv(name, value)
            else:
                monkeypatch.delenv(name, raising=False)


# --- Servers on Unix sockets, on the test's event loop ---


@asynccontextmanager
async def _serving(server: uvicorn.Server) -> AsyncIterator[None]:
    task = asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(START_TIMEOUT_S):
            while not server.started:
                if task.done():
                    task.result()
                    raise RuntimeError("uvicorn exited before it started")
                await asyncio.sleep(0.01)
        yield
    finally:
        server.should_exit = True
        server.force_exit = True
        await task


@asynccontextmanager
async def _socket_folder() -> AsyncIterator[Path]:
    """A short folder: macOS caps a Unix socket path at 104 bytes."""
    folder = Path(tempfile.mkdtemp(prefix="p5-"))
    try:
        yield folder
    finally:
        shutil.rmtree(folder, ignore_errors=True)


@asynccontextmanager
async def workload_server(
    handle: str, *, token: bool = True, previous: bool = False
) -> AsyncIterator[str]:
    """The workload's template A2A server, built by its CLI, on a fresh Unix socket; yields the
    path. `token` adds `--require-token-env WORKLOAD_TOKEN_ENV` (the remote lane); `previous`
    adds `--previous-token-env WORKLOAD_PREVIOUS_ENV`. Without `token` it is the sidecar's server.
    """
    from workload_a2a.cli import build

    async with _socket_folder() as folder:
        uds = str(folder / "w.sock")
        argv = ["serve", "--handle", handle, "--uds", uds, "--log-level", "warning"]
        argv += ["--drain-timeout-s", "0", "--name", handle, "--version", "0.0.1"]
        if token:
            argv += ["--require-token-env", WORKLOAD_TOKEN_ENV]
        if previous:
            argv += ["--previous-token-env", WORKLOAD_PREVIOUS_ENV]
        async with _serving(build(argv)):
            yield uds


@asynccontextmanager
async def app_on_socket(app: Any, name: str) -> AsyncIterator[str]:
    """`app` on uvicorn on a fresh Unix socket, lifespan off; yields the path."""
    async with _socket_folder() as folder:
        uds = str(folder / name)
        config = uvicorn.Config(app, uds=uds, lifespan="off", log_level="warning")
        async with _serving(uvicorn.Server(config)):
            yield uds


def uds_client(uds: str, base_url: str, token: str | None = None) -> httpx.AsyncClient:
    """A client over `uds`; `token` sets `Authorization: Bearer`. Never TCP."""
    headers = {} if token is None else {"Authorization": f"Bearer {token}"}
    return httpx.AsyncClient(
        transport=httpx.AsyncHTTPTransport(uds=uds),
        base_url=base_url,
        headers=headers,
        trust_env=False,
    )


# --- Ports ---


HOLD = "hold"
"""A prompt with this word makes the scripted model call `glossary_lookup` first."""
REPLY = "plain words"
"""What the scripted model answers, at once or after the tool result."""


def scripted_model() -> ScriptedModel:
    """A prompt with `HOLD` calls `glossary_lookup` once, then answers `REPLY`; any other prompt
    answers `REPLY` at once. Each call uses 10 input and 5 output tokens.
    """
    call = ToolCallRequest(call_id="call_1", name="glossary_lookup", arguments={"term": "SLM"})
    return ScriptedModel(
        [ScriptRule(match=HOLD, tool_call=call), ScriptRule(after_tool=True, reply=REPLY)],
        default_reply=REPLY,
    )


class GatedTools:
    """A `ToolPort` over the fake `glossary_lookup`. After `hold()`, the next `call` sets
    `entered` and waits for `release`, so a test can act while a run is in flight. The run's
    first model call has settled by then, so no model reservation holds its budget.
    """

    name = "gated"

    def __init__(self) -> None:
        self.inner = default_tools()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self._armed = False

    def hold(self) -> None:
        self._armed = True
        self.entered.clear()
        self.release.clear()

    def list_tools(self) -> Sequence[ToolDefinition]:
        return self.inner.list_tools()

    async def call(
        self, name: str, arguments: Mapping[str, Any], *, idempotency_key: str | None = None
    ) -> ToolResult:
        if self._armed:
            self._armed = False
            self.entered.set()
            await self.release.wait()
        return await self.inner.call(name, arguments, idempotency_key=idempotency_key)


def remote_config(uds: str, *, previous: bool = False) -> ChassisConfig:
    """`spec.engine.connector: remote` over `uds`, with the bearer by variable name, and
    `spec.trust: untrusted` (only the remote lane may run it).
    """
    auth: dict[str, Any] = {"scheme": "bearer", "token_env": TOKEN_ENV}
    if previous:
        auth["previous_token_env"] = PREVIOUS_TOKEN_ENV
    return ChassisConfig.model_validate(
        {
            "version": "cfg-poc5",
            "profile": "fake",
            "agent": {"name": "simplifier", "version": "0.0.1"},
            "spec": {
                "adapters": {"model": "fake"},
                "trust": "untrusted",
                "engine": {"connector": "remote", "url": REMOTE_URL, "auth": auth, "uds": uds},
                "model": {"route": ROUTE},
                "prompt": {"version": "simplifier-v1"},
            },
        }
    )


class _KeepOpen(httpx.AsyncBaseTransport):
    """`echo_python` closes its client, and so its transport, after each run. This one keeps the
    inner transport open; the harness closes it at the end.
    """

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self._inner = inner

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        return None


@dataclass
class RemoteLane:
    """One running remote lane: the chassis app, its remote listener, and the workload."""

    app: Any
    remote_proxy: Any
    telemetry: InMemoryTelemetry
    model: ScriptedModel
    tools: GatedTools
    connector: RemoteConnector
    tokens: Tokens
    workload_uds: str
    remote_proxy_uds: str

    def client(self) -> httpx.AsyncClient:
        """The chassis's public app, over ASGI."""
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://chassis", timeout=30.0
        )

    def remote_proxy_client(self, token: str | None) -> httpx.AsyncClient:
        """A caller of the remote listener, the way a remote pod calls it."""
        return uds_client(self.remote_proxy_uds, REMOTE_PROXY_URL, token)

    def workload_client(self, token: str | None) -> httpx.AsyncClient:
        """A caller of the workload's A2A server, the way the chassis or anything else calls it."""
        return uds_client(self.workload_uds, REMOTE_URL, token)

    def counter_total(self, name: str) -> int:
        return sum(v for (n, _), v in self.telemetry.counters.items() if n == name)

    @asynccontextmanager
    async def held_run(self, text: str = f"simplify: {HOLD}") -> AsyncIterator[HeldRun]:
        """One `/v1/run` through the lane, held in flight at its tool call (after one model call
        that asked for the tool) until the block exits. Yields the run's trace id; after the
        block, `HeldRun.response` is the run's answer.
        """
        held = HeldRun(new_trace_id())
        self.tools.hold()
        body = {"trace_id": held.trace_id, "input": {"text": text}}
        async with self.client() as client:
            task = asyncio.create_task(client.post("/v1/run", json=body))
            entered = asyncio.create_task(self.tools.entered.wait())
            try:
                async with asyncio.timeout(START_TIMEOUT_S):
                    await asyncio.wait({task, entered}, return_when=asyncio.FIRST_COMPLETED)
                if not entered.done():
                    raise AssertionError(f"the run ended before its tool call: {task.result()}")
                yield held
            finally:
                entered.cancel()
                self.tools.release.set()
                held.response = await task


@dataclass
class HeldRun:
    trace_id: str
    response: httpx.Response | None = None

    @property
    def traceparent(self) -> str:
        return traceparent(self.trace_id)


@asynccontextmanager
async def remote_lane(
    monkeypatch: pytest.MonkeyPatch,
    *,
    tokens: Tokens | None = None,
    handle: str = "echo_python:handle",
) -> AsyncIterator[RemoteLane]:
    """The whole remote lane, running (see the module docstring). The workload's server accepts
    a previous token when `tokens.workload_previous` is set; the remote listener when
    `tokens.chassis_previous` is set.
    """
    tokens = tokens or Tokens.one()
    tokens.apply(monkeypatch)
    model = scripted_model()
    tools = GatedTools()
    telemetry = InMemoryTelemetry()
    connector = RemoteConnector()
    async with (
        workload_server(handle, previous=tokens.workload_previous is not None) as workload_uds,
    ):
        config = remote_config(workload_uds, previous=tokens.chassis_previous is not None)
        ports = PortBundle(
            model=model,
            engine=connector,
            config=InMemoryConfig(),
            telemetry=telemetry,
            tools=tools,
        )
        app = create_app(config, ports)
        auth = config.spec.engine.auth
        assert auth is not None
        # The tokens come from the variables, the way `chassis serve` reads them.
        remote = create_remote_proxy_app(app, remote_tokens(auth.model_dump()))
        async with app_on_socket(remote, "r.sock") as remote_uds:
            inner = httpx.AsyncHTTPTransport(uds=remote_uds)
            echo: Any = importlib.import_module("echo_python.handle")
            monkeypatch.setattr(echo, "transport", _KeepOpen(inner))
            point_tools_at(monkeypatch, uds_transport(remote_uds))
            monkeypatch.setenv("CHASSIS_MODEL_URL", f"{REMOTE_PROXY_URL}/v1")
            monkeypatch.setenv("CHASSIS_TOOL_URL", f"{REMOTE_PROXY_URL}/mcp")
            lane = RemoteLane(
                app=app,
                remote_proxy=remote,
                telemetry=telemetry,
                model=model,
                tools=tools,
                connector=connector,
                tokens=tokens,
                workload_uds=workload_uds,
                remote_proxy_uds=remote_uds,
            )
            try:
                async with running(app):
                    yield lane
            finally:
                await inner.aclose()


# --- Lane factories for `LaneContract` ---


def _bundle(engine: EngineConnector) -> PortBundle:
    return PortBundle(
        model=ScriptedModel(), engine=engine, config=InMemoryConfig(), telemetry=InMemoryTelemetry()
    )


@asynccontextmanager
async def inprocess_lane(path: str) -> AsyncIterator[EngineConnector]:
    connector = InProcessConnector()
    await connector.setup({"connector": "inprocess", "handle": path}, _bundle(connector))
    try:
        yield connector
    finally:
        await connector.close()


@asynccontextmanager
async def sidecar_lane(path: str) -> AsyncIterator[EngineConnector]:
    """The workload's template server with no token, on a Unix socket, behind `SidecarConnector`."""
    async with workload_server(path, token=False) as uds:
        connector = SidecarConnector()
        spec = {"connector": "sidecar", "url": "http://127.0.0.1:9100", "uds": uds}
        await connector.setup(spec, _bundle(connector))
        try:
            yield connector
        finally:
            await connector.close()


@asynccontextmanager
async def remote_lane_connector(path: str) -> AsyncIterator[EngineConnector]:
    """The same template server with `--require-token-env` on, behind `RemoteConnector` with the
    token from `TOKEN_ENV`. The binder sets both variables per test (`Tokens.apply`).
    """
    async with workload_server(path) as uds:
        connector = RemoteConnector()
        spec = remote_config(uds).spec.engine.as_mapping()
        await connector.setup(spec, _bundle(connector))
        try:
            yield connector
        finally:
            await connector.close()
