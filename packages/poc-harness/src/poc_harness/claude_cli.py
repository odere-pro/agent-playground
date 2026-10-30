"""A stand-in for the `claude` CLI, for the `echo-claude-agent` cells.

The real CLI cannot start offline (it needs its own binary run and a provider route). PoC-6b
replaced `echo_claude_agent.handle.query_fn` with replayed SDK messages, which never reach the
chassis. This stand-in goes one step further, so the chassis stages see a real Claude run:

- It reads what the workload hands the CLI (`ClaudeAgentOptions`): `ANTHROPIC_BASE_URL`,
  `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_MODEL`, `ANTHROPIC_CUSTOM_HEADERS` (the `traceparent`), the
  system prompt, and the `chassis` MCP server's URL and headers.
- It lists the MCP tools, then calls `POST /v1/messages` on the chassis model proxy in a loop: a
  `tool_use` block is a call to the chassis `/mcp`, its result goes back as a `tool_result`.
- It yields the SDK message objects the real CLI would (`AssistantMessage`, `UserMessage` with
  `ToolResultBlock`, `ResultMessage`), so the workload's own mapping (`mapping.py`) runs as is.

All HTTP goes over the chassis proxy's Unix socket. No TCP, no key.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

import httpx
import httpx2
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

MCP_SERVER = "chassis"
MAX_TOKENS = 1024


def _headers(env: Mapping[str, str]) -> dict[str, str]:
    """What the CLI sends: the bearer and the custom headers (`name: value`)."""
    headers = {
        "authorization": f"Bearer {env['ANTHROPIC_AUTH_TOKEN']}",
        "anthropic-version": "2023-06-01",
    }
    name, _, value = env.get("ANTHROPIC_CUSTOM_HEADERS", "").partition(":")
    if name.strip() and value.strip():
        headers[name.strip().lower()] = value.strip()
    return headers


class ClaudeCliStandIn:
    """Callable like the SDK's `query(prompt=..., options=...)`. `uds` is the chassis proxy
    socket the run's model and MCP calls go to."""

    def __init__(self, uds: str) -> None:
        self.uds = uds

    def __call__(self, *, prompt: Any, options: Any = None) -> AsyncIterator[Any]:
        return self._run(str(prompt), options)

    @asynccontextmanager
    async def _session(self, server: Mapping[str, Any]) -> AsyncIterator[ClientSession]:
        transport = httpx2.AsyncHTTPTransport(uds=self.uds)
        async with (
            httpx2.AsyncClient(headers=dict(server.get("headers") or {}), transport=transport) as h,
            streamable_http_client(str(server["url"]), http_client=h) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            yield session

    async def _tools(self, server: Mapping[str, Any]) -> list[dict[str, Any]]:
        async with self._session(server) as session:
            listed = await session.list_tools()
        return [
            {
                "name": f"mcp__{MCP_SERVER}__{tool.name}",
                "description": tool.description or "",
                "input_schema": tool.input_schema,
            }
            for tool in listed.tools
        ]

    async def _call(
        self, server: Mapping[str, Any], name: str, arguments: dict[str, Any]
    ) -> tuple[str, bool]:
        bare = name.split("__", 2)[-1]
        async with self._session(server) as session:
            result = await session.call_tool(bare, arguments)
        if result.structured_content is not None and not result.is_error:
            return json.dumps(result.structured_content), False
        text = "".join(getattr(c, "text", "") for c in result.content)
        return text, bool(result.is_error)

    async def _call_all(
        self, server: Mapping[str, Any], uses: list[ToolUseBlock]
    ) -> tuple[list[Any], list[dict[str, Any]]]:
        """Each tool use's result, as the SDK block and as the wire form."""
        results: list[Any] = []
        wire: list[dict[str, Any]] = []
        for use in uses:
            text, is_error = await self._call(server, use.name, dict(use.input))
            results.append(ToolResultBlock(tool_use_id=use.id, content=text, is_error=is_error))
            wire.append(
                {
                    "type": "tool_result",
                    "tool_use_id": use.id,
                    "content": text,
                    "is_error": is_error,
                }
            )
        return results, wire

    async def _post(self, base_url: str, headers: dict[str, str], body: dict[str, Any]) -> Any:
        transport = httpx.AsyncHTTPTransport(uds=self.uds)
        async with httpx.AsyncClient(transport=transport, base_url=base_url, timeout=30) as http:
            return await http.post("/v1/messages", json=body, headers=headers)

    async def _run(self, prompt: str, options: Any) -> AsyncIterator[Any]:
        env: dict[str, str] = dict(options.env)
        server: Mapping[str, Any] = options.mcp_servers[MCP_SERVER]
        headers = _headers(env)
        tools = await self._tools(server)
        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        used = {"input_tokens": 0, "output_tokens": 0}
        for turn in range(1, int(options.max_turns) + 1):
            body = {
                "model": env["ANTHROPIC_MODEL"],
                "max_tokens": MAX_TOKENS,
                "system": options.system_prompt,
                "messages": messages,
                "tools": tools,
            }
            answer = await self._post(env["ANTHROPIC_BASE_URL"], headers, body)
            if answer.status_code != 200:
                yield _failed(turn, answer.status_code, answer.text)
                return
            reply = answer.json()
            for key in used:
                used[key] += int((reply.get("usage") or {}).get(key) or 0)
            blocks: list[Any] = [_block(b) for b in reply["content"]]
            yield AssistantMessage(content=blocks, model=str(reply.get("model") or ""))
            uses = [b for b in blocks if isinstance(b, ToolUseBlock)]
            if not uses:
                yield _done(turn, used)
                return
            results, wire = await self._call_all(server, uses)
            yield UserMessage(content=results)
            messages += [
                {"role": "assistant", "content": reply["content"]},
                {"role": "user", "content": wire},
            ]
        yield ResultMessage(
            subtype="error_max_turns",
            duration_ms=1,
            duration_api_ms=1,
            is_error=True,
            num_turns=int(options.max_turns),
            session_id="poc08",
        )


def _block(raw: Mapping[str, Any]) -> Any:
    if raw.get("type") == "tool_use":
        return ToolUseBlock(id=str(raw["id"]), name=str(raw["name"]), input=dict(raw["input"]))
    return TextBlock(text=str(raw.get("text") or ""))


def _done(turns: int, used: Mapping[str, int]) -> ResultMessage:
    return ResultMessage(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=turns,
        session_id="poc08",
        usage=dict(used),
    )


def _failed(turns: int, status: int, text: str) -> ResultMessage:
    return ResultMessage(
        subtype="error_during_execution",
        duration_ms=1,
        duration_api_ms=1,
        is_error=True,
        num_turns=turns,
        session_id="poc08",
        result=text[:200],
        api_error_status=status,
    )
