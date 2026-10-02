"""PoC-2 harness self-tests: `route_outbound` catches both HTTP stacks a workload may use, and
the TypeScript echo runs on a Unix socket.

Not exit criteria of their own. The per-engine tool and trace tests (`test_tools.py`,
`test_run_correlation.py`) rely on the first: the MCP client (`mcp` 2.2) and pydantic-ai use
`httpx2`, which has its own core (`httpcore2`), so a harness that patches only `httpcore` would let
their `/mcp` calls try a real socket instead of reaching the chassis. The TypeScript engine and
twin tests rely on the second.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import stat
import tempfile

import httpx
import httpx2
import pytest
from chassis.fakes.tool import default_tools
from poc02_harness import (
    MCP_PATH,
    MODEL_URL,
    ROUTE,
    SIMPLIFIED,
    SIMPLIFY,
    TIMEOUT_S,
    chassis_app,
    route_outbound,
    running,
    typescript_echo,
)

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
    folder = tempfile.mkdtemp(prefix="poc02-")
    path = os.path.join(folder, "ts.sock")
    try:
        stale = socket.socket(socket.AF_UNIX)
        stale.bind(path)
        stale.close()  # leaves the socket file behind, as a killed server does
        assert stat.S_ISSOCK(os.stat(path).st_mode)
        with (
            typescript_echo(uds=path, env={"HOST": "10.0.0.1"}) as url,
            httpx.Client(transport=httpx.HTTPTransport(uds=path), base_url=url) as client,
        ):
            card = client.get("/.well-known/agent-card.json").json()
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    urls = [i["url"] for i in card["supportedInterfaces"]]
    assert urls == ["http://127.0.0.1:9000/"], card
