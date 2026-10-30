"""Harness self-tests: `route_outbound` catches both HTTP stacks a workload may use, the
TypeScript echo runs on a Unix socket, and `on_unix_socket` and `serve_tcp` serve an app.

Moved from the PoC-2 tests (they tested `poc02_harness`). Not exit criteria of their own. The
per-engine tool and trace tests (`test_tools.py`, `test_run_correlation.py` in PoC-2) rely on the
first: the MCP client (`mcp` 2.2) and pydantic-ai use `httpx2`, which has its own core
(`httpcore2`), so a harness that patches only `httpcore` would let their `/mcp` calls try a real
socket instead of reaching the chassis. The TypeScript engine and twin tests rely on the second.
"""

from __future__ import annotations

import asyncio
import shutil
import socket
import stat
import tempfile
from pathlib import Path
from typing import Any

import httpx
import httpx2
import pytest
from chassis.fakes.tool import default_tools
from poc_harness.apps import chassis_app, running
from poc_harness.outbound import route_outbound
from poc_harness.scenario import MCP_PATH, MODEL_URL, ROUTE, SIMPLIFIED, SIMPLIFY, TIMEOUT_S
from poc_harness.sockets import on_unix_socket, require_tcp, serve_tcp
from poc_harness.sse import sse_frames
from poc_harness.typescript import typescript_echo

TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "poc-02-harness", "version": "0.0.1"},
    },
}


async def test_httpx2_to_mcp_and_httpx_to_the_proxy_are_both_routed_and_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One `httpx2` request to `/mcp` reaches the chassis's MCP app in process and one `httpx`
    request reaches the model proxy; both are in `Outbound.calls` with their headers. No socket:
    `make test` refuses TCP, so an unrouted call would fail here.
    """
    app = chassis_app("chassis.core.handle:echo_wire", tools=default_tools())
    outbound = route_outbound(monkeypatch, app)
    async with asyncio.timeout(TIMEOUT_S), running(app):
        async with httpx2.AsyncClient(base_url="http://127.0.0.1:8090") as client2:
            mcp = await client2.post(
                MCP_PATH,
                json=INITIALIZE,
                headers={
                    "accept": "application/json, text/event-stream",
                    "traceparent": TRACEPARENT,
                },
            )
        async with httpx.AsyncClient(base_url=MODEL_URL) as client:
            model = await client.post(
                "/chat/completions",
                json={"model": ROUTE, "messages": [{"role": "user", "content": SIMPLIFY}]},
                headers={"traceparent": TRACEPARENT},
            )
    assert mcp.status_code == 200, mcp.text
    assert '"serverInfo"' in mcp.text, mcp.text
    assert model.status_code == 200, model.text
    assert model.json()["choices"][0]["message"]["content"] == SIMPLIFIED
    [mcp_call] = outbound.to(MCP_PATH)
    [model_call] = outbound.to("/v1/chat/completions")
    assert mcp_call.method == "POST" and model_call.method == "POST"
    assert mcp_call.headers["traceparent"] == TRACEPARENT
    assert model_call.headers["traceparent"] == TRACEPARENT
    assert b'"initialize"' in mcp_call.body


@pytest.mark.slow
def test_typescript_echo_replaces_a_stale_unix_socket_and_serves_its_card_there() -> None:
    """With `UDS` set, the TypeScript echo removes a stale socket file left at the path, listens
    there, and serves its agent card, which names loopback. A non-loopback `HOST` does not stop
    it: no TCP port is bound, so the loopback guard does not apply. No TCP.
    """
    folder = tempfile.mkdtemp(prefix="poc-")
    path = str(Path(folder, "ts.sock"))
    try:
        stale = socket.socket(socket.AF_UNIX)
        stale.bind(path)
        stale.close()  # leaves the socket file behind, as a killed server does
        assert stat.S_ISSOCK(Path(path).stat().st_mode)
        with (
            typescript_echo(uds=path, env={"HOST": "10.0.0.1"}) as url,
            httpx.Client(transport=httpx.HTTPTransport(uds=path), base_url=url) as client,
        ):
            card = client.get("/.well-known/agent-card.json").json()
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    urls = [i["url"] for i in card["supportedInterfaces"]]
    assert urls == ["http://127.0.0.1:9000/"], card


async def _hello(scope: dict[str, Any], receive: Any, send: Any) -> None:
    """A bare ASGI app: `hello` on HTTP, a clean lifespan."""
    if scope["type"] == "lifespan":
        while (await receive())["type"] != "lifespan.shutdown":
            await send({"type": "lifespan.startup.complete"})
        await send({"type": "lifespan.shutdown.complete"})
        return
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"hello"})


async def _no_start(scope: dict[str, Any], receive: Any, send: Any) -> None:
    """An ASGI app whose lifespan fails, so uvicorn never starts serving."""
    await receive()
    await send({"type": "lifespan.startup.failed", "message": "refused on purpose"})


def test_on_unix_socket_serves_the_app_and_removes_the_socket_after() -> None:
    """`on_unix_socket` serves the app on a fresh Unix socket, short enough for macOS, and removes
    the socket folder on exit. No TCP.
    """
    with on_unix_socket(_hello, "t.sock") as uds:
        assert len(uds.encode()) < 104, uds
        assert stat.S_ISSOCK(Path(uds).stat().st_mode)
        with httpx.Client(transport=httpx.HTTPTransport(uds=uds), base_url="http://t") as client:
            answer = client.get("/")
    assert answer.status_code == 200 and answer.text == "hello"
    assert not Path(uds).parent.exists()


def test_on_unix_socket_raises_when_the_app_does_not_start() -> None:
    """A failed lifespan is a `RuntimeError` that names the socket, not a hang."""
    with pytest.raises(RuntimeError, match="uvicorn did not start"), on_unix_socket(_no_start):
        pass


@pytest.mark.network
def test_serve_tcp_serves_the_app_at_the_url_it_yields(request: pytest.FixtureRequest) -> None:
    """`serve_tcp` builds the app for a free loopback URL and serves it there. TCP, so `network`."""
    require_tcp(request)
    built: list[str] = []

    def build(url: str) -> Any:
        built.append(url)
        return _hello

    with serve_tcp(build) as url:
        answer = httpx.get(url, trust_env=False)
    assert built == [url] and url.startswith("http://127.0.0.1:")
    assert answer.status_code == 200 and answer.text == "hello"


def test_sse_frames_reads_named_event_frames_in_order() -> None:
    """`sse_frames` turns `event:`/`data:` blocks into `(name, payload)` pairs, in order."""
    body = 'event: start\ndata: {"a": 1}\n\nevent: delta\ndata: {"text": "hi"}\n\n'
    assert sse_frames(body) == [("start", {"a": 1}), ("delta", {"text": "hi"})]
