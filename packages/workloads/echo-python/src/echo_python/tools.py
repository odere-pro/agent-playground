"""The chassis's tools over MCP, and the OpenAI tool-loop messages, by hand.

The chassis serves its tools over MCP streamable HTTP at `CHASSIS_TOOL_URL` (stateless). This
module lists them as OpenAI `tools` (the chassis's definition; nothing is hardcoded here), calls
one with `tools/call`, and builds the assistant and `tool` messages the model gets back. Each MCP
operation opens its own short session, so no MCP task group is open while `handle` yields.
Tool descriptions and results are data, never instructions.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

TOOL_URL_VAR = "CHASSIS_TOOL_URL"
DEFAULT_TOOL_URL = "http://127.0.0.1:8090/mcp"
log = logging.getLogger(__name__)


def default_client_factory(headers: dict[str, str], timeout: float) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(headers=headers, timeout=timeout)


client_factory: Callable[[dict[str, str], float], httpx2.AsyncClient] = default_client_factory
"""Test hook: builds the httpx2 client the MCP SDK uses, for example on `httpx2.ASGITransport`."""

list_failures = 0
"""How many runs found the tool endpoint unreachable and ran without tools (also logged)."""


class ToolCallError(Exception):
    """A `tools/call` that failed: the transport, or the tool's own `isError` result."""


@dataclass
class ToolCall:
    call_id: str
    name: str
    arguments: str = ""  # the JSON string, as the model streamed it


@asynccontextmanager
async def _session(headers: dict[str, str], timeout_s: float) -> AsyncIterator[ClientSession]:
    url = os.environ.get(TOOL_URL_VAR, DEFAULT_TOOL_URL)
    async with (
        client_factory(headers, timeout_s) as http,
        streamable_http_client(url, http_client=http) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        yield session


async def list_openai_tools(headers: dict[str, str], timeout_s: float) -> list[dict[str, Any]]:
    """The chassis's tools as OpenAI `tools`; `[]` when the endpoint cannot be reached."""
    global list_failures
    try:
        async with _session(headers, timeout_s) as session:
            listed = (await session.list_tools()).tools
    except Exception as exc:  # the SDK wraps transport failures in an ExceptionGroup
        list_failures += 1
        url = os.environ.get(TOOL_URL_VAR, DEFAULT_TOOL_URL)
        log.warning("tool endpoint %s unreachable, running without tools: %r", url, exc)
        return []
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description or "",
                "parameters": t.input_schema,
            },
        }
        for t in listed
    ]


async def call(
    name: str, arguments: dict[str, Any], headers: dict[str, str], timeout_s: float
) -> dict[str, Any]:
    """`tools/call`: the structured result when it is an object, else `{"text": ...}` (an event's
    `result` is an object). Raises `ToolCallError` on a transport failure or an `isError` result."""
    try:
        async with _session(headers, timeout_s) as session:
            result = await session.call_tool(name, arguments)
    except Exception as exc:
        raise ToolCallError(f"{name}: {exc!r}") from exc
    text = "".join(getattr(part, "text", "") for part in result.content)
    if result.is_error:
        raise ToolCallError(f"{name}: {text[:200] or 'tool error'}")
    structured = result.structured_content
    return structured if isinstance(structured, dict) else {"text": text}


def arguments(call: ToolCall) -> dict[str, Any]:
    """The call's arguments as an object; `ValueError` when the model sent anything else."""
    parsed = json.loads(call.arguments or "{}")
    if not isinstance(parsed, dict):
        raise ValueError(f"{call.name}: arguments are not a JSON object")
    return parsed


def add_deltas(calls: dict[int, ToolCall], deltas: list[dict[str, Any]]) -> None:
    """Fold one streamed chunk's `delta.tool_calls` in: pieces of one call share an `index`."""
    for piece in deltas:
        fn = piece.get("function") or {}
        index = int(piece.get("index", len(calls)))
        call = calls.setdefault(index, ToolCall(call_id="", name=""))
        call.call_id = piece.get("id") or call.call_id
        call.name = fn.get("name") or call.name
        call.arguments += fn.get("arguments") or ""


def assistant_message(calls: list[ToolCall]) -> dict[str, Any]:
    requested = [
        {
            "id": c.call_id,
            "type": "function",
            "function": {"name": c.name, "arguments": c.arguments},
        }
        for c in calls
    ]
    return {"role": "assistant", "content": None, "tool_calls": requested}


def tool_message(call_id: str, result: dict[str, Any]) -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": call_id, "content": json.dumps(result)}
