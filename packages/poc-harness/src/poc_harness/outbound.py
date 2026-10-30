"""A workload's outbound HTTP, routed into an ASGI app and recorded, plus its MCP hooks.

Not for measurements: `forward` buffers each whole response before the workload reads it, so no
test here streams through the chassis proxy. Time-to-first-token and per-delta figures must not
come from this path.

A workload's outbound HTTP (its model client, its MCP client, any client built on httpx or
httpx2) is routed by patching the httpcore and httpcore2 connection pools, below the OpenTelemetry
httpx instrumentation, so any `traceparent` the workload adds is still on the request the chassis
sees. Each Python workload's MCP hook is pointed at the same route too, so a tool listing never
falls back to a socket. A Unix-socket pool is a real lane and is left alone.
"""

from __future__ import annotations

import importlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpcore
import httpcore2
import httpx
import httpx2
import pytest

from poc_harness.apps import proxy_for
from poc_harness.scenario import CHASSIS_PROXY_URL, MCP_PATH, MODEL_URL


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
    return sum(
        int(importlib.import_module(name).list_failures)
        for name in ("echo_python.tools", "echo_langgraph.tools")
    )


# --- Routing ---


Send = Callable[[httpx.Request], Awaitable[httpx.Response]]
"""Where `patch_outbound` sends one routed request; returns the response, read in full."""
Forward = Callable[[str, bytes, Any, bytes], Awaitable[httpx.Response]]
"""`(method, raw URL, raw headers, body)` in, the routed response out; records the call."""


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
    forward = _recording_forward(send, seen)
    _patch_pool(monkeypatch, httpcore.AsyncConnectionPool, httpcore.Response, forward)
    _patch_pool(monkeypatch, httpcore2.AsyncConnectionPool, httpcore2.Response, forward)
    point_tools_at(monkeypatch, _forward_transport(forward))
    monkeypatch.setenv("CHASSIS_MODEL_URL", MODEL_URL)
    return seen


def _recording_forward(send: Send, seen: list[OutboundCall]) -> Forward:
    async def forward(method: str, raw_url: bytes, raw_headers: Any, body: bytes) -> httpx.Response:
        headers = httpx.Headers([(k.decode(), v.decode()) for k, v in raw_headers])
        url = httpx.URL(raw_url.decode())
        seen.append(OutboundCall(method, url, headers, body))
        return await send(httpx.Request(method, url, headers=headers, content=body))

    return forward


def _patch_pool(
    monkeypatch: pytest.MonkeyPatch, pool_type: Any, response_type: Any, forward: Forward
) -> None:
    """Patch `pool_type.handle_async_request` (httpcore or httpcore2) to hand each request to
    `forward`, except on a Unix-socket pool, which is a real lane (the sidecar).
    """
    original = pool_type.handle_async_request

    async def handle(pool: Any, request: Any) -> Any:
        if getattr(pool, "_uds", None):
            return await original(pool, request)
        stream: Any = request.stream
        body = b"".join([chunk async for chunk in stream])
        response = await forward(request.method.decode(), bytes(request.url), request.headers, body)
        return response_type(
            response.status_code, headers=response.headers.raw, content=response.content
        )

    monkeypatch.setattr(pool_type, "handle_async_request", handle)


def _forward_transport(forward: Forward) -> TransportFactory:
    """An `httpx2` transport class (so a factory) that hands each request to `forward`."""

    class ForwardTransport(httpx2.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
            body = await request.aread()
            response = await forward(
                request.method, str(request.url).encode(), request.headers.raw, body
            )
            return httpx2.Response(
                response.status_code, headers=response.headers.raw, content=response.content
            )

    return ForwardTransport
