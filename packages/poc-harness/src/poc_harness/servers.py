"""Servers a scenario runs on Unix sockets: a workload's A2A template server, and the chassis
proxy app with `/mcp` over the fake `ToolPort`.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from chassis.adapters.a2a import build_agent_card
from chassis.adapters.a2a.inprocess import load_handle

from poc_harness.apps import chassis_app, proxy_for
from poc_harness.scenario import SIDECAR_URL
from poc_harness.sockets import on_unix_socket


class LifespanOf:
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
    with on_unix_socket(LifespanOf(proxy_for(public), public), "proxy.sock") as uds:
        yield uds
