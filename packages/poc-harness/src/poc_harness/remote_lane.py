"""The `remote` lane in one process, for offline tests.

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

Who holds which token is in `poc_harness.lane_tokens`.
"""

from __future__ import annotations

import asyncio
import importlib
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
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

from poc_harness.apps import bundle_for, running
from poc_harness.lane_tokens import (
    PREVIOUS_TOKEN_ENV,
    REMOTE_PROXY_URL,
    REMOTE_URL,
    TOKEN_ENV,
    WORKLOAD_PREVIOUS_ENV,
    WORKLOAD_TOKEN_ENV,
    Tokens,
    new_trace_id,
    traceparent,
)
from poc_harness.outbound import point_tools_at
from poc_harness.scenario import ROUTE
from poc_harness.sockets import (
    on_unix_socket_on_this_loop,
    serve_on_this_loop,
    socket_path,
    uds_transport,
)

START_TIMEOUT_S = 10.0


@asynccontextmanager
async def workload_server(
    handle: str, *, token: bool = True, previous: bool = False
) -> AsyncIterator[str]:
    """The workload's template A2A server, built by its CLI, on a fresh Unix socket; yields the
    path. `token` adds `--require-token-env WORKLOAD_TOKEN_ENV` (the remote lane); `previous`
    adds `--previous-token-env WORKLOAD_PREVIOUS_ENV`. Without `token` it is the sidecar's server.
    """
    from workload_a2a.cli import build

    with socket_path("w.sock") as uds:
        argv = ["serve", "--handle", handle, "--uds", uds, "--log-level", "warning"]
        argv += ["--drain-timeout-s", "0", "--name", handle, "--version", "0.0.1"]
        if token:
            argv += ["--require-token-env", WORKLOAD_TOKEN_ENV]
        if previous:
            argv += ["--previous-token-env", WORKLOAD_PREVIOUS_ENV]
        async with serve_on_this_loop(build(argv)):
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
async def _remote_listener(
    app: Any, config: ChassisConfig, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[Any, str]]:
    """The chassis's remote proxy listener on a Unix socket, with the workload's model and MCP
    calls pointed at it. Yields the listener app and its socket path.
    """
    auth = config.spec.engine.auth
    assert auth is not None
    # The tokens come from the variables, the way `chassis serve` reads them.
    remote = create_remote_proxy_app(app, remote_tokens(auth.model_dump()))
    async with on_unix_socket_on_this_loop(remote, "r.sock") as remote_uds:
        inner = httpx.AsyncHTTPTransport(uds=remote_uds)
        echo: Any = importlib.import_module("echo_python.handle")
        monkeypatch.setattr(echo, "transport", _KeepOpen(inner))
        point_tools_at(monkeypatch, uds_transport(remote_uds))
        monkeypatch.setenv("CHASSIS_MODEL_URL", f"{REMOTE_PROXY_URL}/v1")
        monkeypatch.setenv("CHASSIS_TOOL_URL", f"{REMOTE_PROXY_URL}/mcp")
        try:
            yield remote, remote_uds
        finally:
            await inner.aclose()


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
    model, tools, telemetry = scripted_model(), GatedTools(), InMemoryTelemetry()
    connector = RemoteConnector()
    ports = PortBundle(
        model=model, engine=connector, config=InMemoryConfig(), telemetry=telemetry, tools=tools
    )
    previous = tokens.workload_previous is not None
    async with workload_server(handle, previous=previous) as workload_uds:
        config = remote_config(workload_uds, previous=tokens.chassis_previous is not None)
        app = create_app(config, ports)
        async with _remote_listener(app, config, monkeypatch) as (remote, remote_uds):
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
            async with running(app):
                yield lane


# --- Lane factories for `LaneContract` ---


@asynccontextmanager
async def inprocess_lane(path: str) -> AsyncIterator[EngineConnector]:
    connector = InProcessConnector()
    spec = {"connector": "inprocess", "handle": path}
    await connector.setup(spec, bundle_for(connector, model=ScriptedModel()))
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
        await connector.setup(spec, bundle_for(connector, model=ScriptedModel()))
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
        await connector.setup(spec, bundle_for(connector, model=ScriptedModel()))
        try:
            yield connector
        finally:
            await connector.close()
