"""An engine's test-only model and MCP hooks, pointed at the chassis proxy socket."""

from __future__ import annotations

import importlib
import os
from pathlib import Path

import httpx
import httpx2
import pytest

from poc_harness.bakeoff_tasks import ENGINES
from poc_harness.claude_cli import ClaudeCliStandIn

PROXY_URL = "http://chassis-proxy"
"""Where a workload's model and MCP calls go. Over the Unix socket it only fills `Host`."""


class _FreshHttpx(httpx.AsyncBaseTransport):
    """A new connection per request: the lanes run the workload on other event loops."""

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


class _FreshSync(httpx2.BaseTransport):
    """The sync form, for smolagents, which closes its client (and transport) after each run."""

    def __init__(self, uds: str) -> None:
        self._uds = uds

    def handle_request(self, request: httpx2.Request) -> httpx2.Response:
        inner = httpx2.HTTPTransport(uds=self._uds)
        try:
            response = inner.handle_request(request)
            body = response.read()
        finally:
            inner.close()
        return httpx2.Response(response.status_code, headers=response.headers.raw, content=body)


def _set(monkeypatch: pytest.MonkeyPatch, dotted: str, value: object) -> None:
    module, _, attr = dotted.rpartition(".")
    monkeypatch.setattr(importlib.import_module(module), attr, value)


def wire_engine(monkeypatch: pytest.MonkeyPatch, engine: str, uds: str, home: Path) -> None:
    """Point the engine's test-only model and MCP hooks at `uds`, and set the two URLs. Test
    wiring: each engine names its hooks differently (`poc_harness.bakeoff_tasks.ENGINES`)."""
    monkeypatch.setenv("CHASSIS_MODEL_URL", f"{PROXY_URL}/v1")
    monkeypatch.setenv("CHASSIS_TOOL_URL", f"{PROXY_URL}/mcp")
    spec = ENGINES[engine]
    if engine == "echo-claude-agent":
        for name in list(os.environ):  # the workload's own guard refuses these
            if name.startswith(("ANTHROPIC_", "CLAUDE")):
                monkeypatch.delenv(name)
        monkeypatch.setenv("CLAUDE_AGENT_HOME_BASE", str(home))
        _set(monkeypatch, "echo_claude_agent.handle.query_fn", ClaudeCliStandIn(uds))
        return
    assert spec.model_hook is not None and spec.tool_hook is not None
    model: object = _FreshHttpx2(uds)
    if engine == "echo-python":
        model = _FreshHttpx(uds)
    elif engine == "echo-smolagents":
        model = _FreshSync(uds)
    tools = _FreshHttpx2(uds)
    tool_hook: object = tools
    if spec.tool_hook.endswith("client_factory"):

        def client_factory(headers: dict[str, str], timeout: float) -> httpx2.AsyncClient:
            return httpx2.AsyncClient(headers=headers, timeout=timeout, transport=tools)

        tool_hook = client_factory
    _set(monkeypatch, spec.model_hook, model)
    _set(monkeypatch, spec.tool_hook, tool_hook)
