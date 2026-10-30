"""A plain JSON-RPC MCP client over an `httpx.AsyncClient`: no MCP SDK, so a test sees the wire."""

from __future__ import annotations

import json
from typing import Any

import httpx

from poc_harness.apps import client_for

PROTOCOL_VERSION = "2025-06-18"


async def mcp_session(client: httpx.AsyncClient) -> str | None:
    """`initialize` and `notifications/initialized`; returns the session id, if any."""
    response, message = await mcp_request(
        client,
        "initialize",
        {
            "protocolVersion": PROTOCOL_VERSION,
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
        "mcp-protocol-version": PROTOCOL_VERSION,
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
