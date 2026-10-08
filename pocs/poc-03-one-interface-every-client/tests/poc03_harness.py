"""Shared harness for the PoC-3 interface suite binding. Not a test module; the binding imports it.

It reuses the PoC-2 harness by import (`poc02_harness`: the workload template server on a Unix
socket, the TypeScript echo, the outbound patch) and adds what PoC-3 needs on top:

- **A real chassis on Unix sockets.** `chassis_on_unix_sockets(target, lane, ...)` serves the
  public app and the proxy app with uvicorn, each on its own Unix socket, both on one event loop
  (`chassis_contracts.interface.serve_on_unix_sockets`). The public app runs its lifespan; the
  proxy app shares its state. No TCP anywhere.
- **Both lanes for every Python engine.** `inprocess` loads the handle by path; `sidecar` serves
  it with `workload_a2a` on a Unix socket (`poc02_harness.a2a_on_unix_socket`). That covers the
  framework workloads too, which PoC-2 ran in the `sidecar` lane only in Compose.
- **The TypeScript echo** in the `sidecar` lane only, on a Unix socket, its model calls going to
  the chassis's proxy socket (`CHASSIS_MODEL_UDS`).
- **Outbound routing by run.** A Python workload's model and MCP calls (any non-Unix-socket
  `httpx` or `httpx2` request) are sent over the proxy socket of the chassis whose run the
  request's `traceparent` names (`OutboundRouter`). Several chassis can then run at once (the
  suite keeps one per engine and lane), and a call that names no run fails loudly instead of
  landing on the wrong chassis. Each response is read in full before the workload sees it
  (`poc02_harness.patch_outbound`), which is fine for these model calls; no timing here.
- **Model calls from cassettes.** Each engine's chassis gets the real `LiteLLMModel` over a
  `CassetteTransport` (`cassettes`); replay never reaches a model.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from chassis.adapters.a2a import InProcessConnector
from chassis.adapters.a2a.sidecar import SidecarConnector
from chassis.core.trace import parse_traceparent
from chassis.fakes import InMemoryConfig, InMemoryTelemetry, default_tools
from chassis.ports.bundle import PortBundle
from chassis.ports.engine import EngineConnector
from chassis.ports.model import ModelPort
from chassis.server import ChassisConfig, create_app
from chassis.server.proxy_app import create_proxy_app
from chassis_contracts.interface import Chassis, UnixApp, serve_on_unix_sockets

_POC02_TESTS = Path(__file__).resolve().parents[2] / "poc-02-two-engines-one-contract" / "tests"
if str(_POC02_TESTS) not in sys.path:
    sys.path.insert(0, str(_POC02_TESTS))

from poc02_harness import (  # noqa: E402  (the PoC-2 tests folder is put on the path above)
    CHASSIS_PROXY_URL,
    EXAMPLE_SCRIPT,
    ROOT,
    ROUTE,
    SIMPLIFIED,
    SIMPLIFY,
    a2a_on_unix_socket,
    patch_outbound,
    tool_list_failures,
    typescript_echo,
)

__all__ = [
    "AGENT",
    "CASSETTES",
    "ENGINES",
    "EXAMPLE_SCRIPT",
    "LANES",
    "MODEL_BASE_URL",
    "PYTHON_ENGINES",
    "SIMPLIFIED",
    "SIMPLIFY",
    "TEST_KEY",
    "TYPESCRIPT",
    "OutboundRouter",
    "chassis_on_unix_sockets",
    "patch_outbound",
    "tool_list_failures",
]

POC = ROOT / "pocs/poc-03-one-interface-every-client"
CASSETTES = POC / "tests/cassettes/interfaces"
MODEL_BASE_URL = "http://model.invalid/v1"
"""The model URL the cassettes hold; `.invalid` never resolves (open note, section 10)."""
TEST_KEY = "cassette-key-not-real"
"""suggested: obviously not a key; `test_cassettes_hold_no_key` proves it never reaches a file."""
AGENT = "simplifier"
LANES = ("inprocess", "sidecar")
TYPESCRIPT = "echo-typescript"
ENGINES: dict[str, str | None] = {
    "echo_python": "echo_python:handle",
    "echo_pydanticai": "echo_pydanticai:handle",
    "echo_langgraph": "echo_langgraph:handle",
    TYPESCRIPT: None,
}
"""Engine label -> its Python handle path; the TypeScript echo has none (sidecar only)."""
PYTHON_ENGINES = tuple(label for label, path in ENGINES.items() if path is not None)


def chassis_config(engine: dict[str, Any]) -> ChassisConfig:
    """The served agent: every interface on (the default), the simplifier's versions."""
    return ChassisConfig.model_validate(
        {
            "version": "cfg-poc3",
            "profile": "fake",
            "agent": {"name": AGENT, "version": "0.0.1"},
            "spec": {
                "adapters": {"model": "fake"},
                "engine": engine,
                "model": {"route": ROUTE},
                "prompt": {"version": "simplifier-v1"},
            },
        }
    )


@dataclass
class OutboundRouter:
    """Sends a workload's outbound request over the proxy socket of the chassis whose in-flight
    run its `traceparent` names. A trace id is remembered once routed, so a call after the run
    ended (an MCP session closing, say) still reaches the same chassis. A request with no
    `traceparent`, or one that names no run, is a connect error, and is kept in `unrouted`.
    """

    chassis: dict[int, tuple[Any, str]] = field(default_factory=dict)
    known: dict[str, str] = field(default_factory=dict)
    unrouted: list[str] = field(default_factory=list)

    def add(self, public: Any, proxy_uds: str) -> None:
        self.chassis[id(public)] = (public, proxy_uds)

    def remove(self, public: Any) -> None:
        self.chassis.pop(id(public), None)

    def _proxy_for(self, trace_id: str | None) -> str | None:
        if trace_id is None:
            return None
        if trace_id not in self.known:
            for public, proxy_uds in self.chassis.values():
                if public.state.runs.lookup(trace_id) is not None:
                    self.known[trace_id] = proxy_uds
                    break
        return self.known.get(trace_id)

    async def send(self, request: httpx.Request) -> httpx.Response:
        uds = self._proxy_for(parse_traceparent(request.headers.get("traceparent")))
        if uds is None:
            traceparent = request.headers.get("traceparent")
            where = f"{request.method} {request.url} (traceparent {traceparent!r})"
            self.unrouted.append(where)
            raise httpx.ConnectError(f"no chassis holds the run of {where}", request=request)
        transport = httpx.AsyncHTTPTransport(uds=uds)  # fresh: the caller may be on any loop
        try:
            response = await transport.handle_async_request(request)
            content = await response.aread()
        finally:
            await transport.aclose()
        return httpx.Response(response.status_code, headers=response.headers, content=content)


@contextmanager
def chassis_on_unix_sockets(
    target: str, lane: str, *, model: ModelPort, router: OutboundRouter
) -> Iterator[Chassis]:
    """A running chassis for `target` (an engine label or a handle path) in `lane`: the public app
    and the proxy app on Unix sockets, `model` as its model port, the fake glossary tool as its
    tools, and in-memory telemetry. Registered with `router` while it runs.
    """
    folder = tempfile.mkdtemp(prefix="poc03-")
    public_uds = os.path.join(folder, "public.sock")
    proxy_uds = os.path.join(folder, "proxy.sock")
    with ExitStack() as stack:
        stack.callback(shutil.rmtree, folder, ignore_errors=True)
        connector: EngineConnector
        if target == TYPESCRIPT:
            if lane != "sidecar":
                raise ValueError(f"{TYPESCRIPT} runs in the sidecar lane only, not {lane!r}")
            ts_uds = os.path.join(folder, "ts.sock")
            env = {"CHASSIS_MODEL_URL": f"{CHASSIS_PROXY_URL}/v1", "CHASSIS_MODEL_UDS": proxy_uds}
            url = stack.enter_context(typescript_echo(uds=ts_uds, env=env))
            spec: dict[str, Any] = {"connector": "sidecar", "url": url, "uds": ts_uds}
            connector = SidecarConnector()
        else:
            handle = ENGINES.get(target) or target
            if lane == "inprocess":
                spec, connector = {"connector": "inprocess", "handle": handle}, InProcessConnector()
            elif lane == "sidecar":
                url, uds = stack.enter_context(a2a_on_unix_socket(handle))
                spec, connector = (
                    {"connector": "sidecar", "url": url, "uds": uds},
                    SidecarConnector(),
                )
            else:
                raise ValueError(f"unknown lane {lane!r}; one of {LANES}")
        config = chassis_config(spec)
        telemetry = InMemoryTelemetry()
        ports = PortBundle(
            model=model,
            engine=connector,
            config=InMemoryConfig(),
            telemetry=telemetry,
            tools=default_tools(),
        )
        public = create_app(config, ports)
        proxy = create_proxy_app(public)  # before the public app starts: it hooks /mcp's lifespan
        router.add(public, proxy_uds)
        stack.callback(router.remove, public)
        stack.enter_context(
            serve_on_unix_sockets(
                [UnixApp(public, public_uds), UnixApp(proxy, proxy_uds, lifespan=False)]
            )
        )
        yield Chassis(public_uds, telemetry, config)
