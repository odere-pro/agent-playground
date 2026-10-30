"""One call to a running replica, through any interface, as a `Reply` a test can compare."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx2
from chassis_contracts.interface import BASE_URL, Chassis

from poc_harness.interfaces import AGENT
from poc_harness.sse import sse_events

CALL_TIMEOUT_S = 30.0


@dataclass(frozen=True)
class Reply:
    """One HTTP answer: status, headers (lowercase names), and the body as JSON or SSE events."""

    status: int
    headers: dict[str, str]
    body: Any

    @property
    def replayed(self) -> bool:
        return self.headers.get("idempotent-replayed") == "true"


def without_created(value: Any) -> Any:
    """`value` with every `created` key dropped: a replayed chat format gets a fresh one."""
    if isinstance(value, dict):
        return {k: without_created(v) for k, v in value.items() if k != "created"}
    if isinstance(value, list | tuple):
        return [without_created(v) for v in value]
    return value


PATHS = {"native": "/v1/run", "openai": "/v1/chat/completions", "anthropic": "/v1/messages"}


def _body(interface: str, text: str, stream: bool, timeout_ms: int) -> dict[str, Any]:
    if interface == "native":
        return {
            "input": {"text": text, "data": {}},
            "stream": stream,
            "budget": {"timeout_ms": timeout_ms},
        }
    message = {"role": "user", "content": text}
    if interface == "openai":
        return {"model": AGENT, "messages": [message], "stream": stream}
    return {"model": AGENT, "max_tokens": 256, "messages": [message], "stream": stream}


async def call(
    chassis: Chassis,
    interface: str,
    text: str,
    *,
    key: str | None,
    stream: bool = False,
    timeout_ms: int = 10_000,
) -> Reply:
    """One call through `interface` (`native`, `openai`, `anthropic`, or `mcp`) with
    `Idempotency-Key: key` when `key` is set. MCP is complete only; its body is the envelope."""
    headers = {"idempotency-key": key} if key is not None else {}
    async with asyncio.timeout(CALL_TIMEOUT_S):
        if interface == "mcp":
            from chassis_contracts.interface import mcp_client

            arguments = _body("native", text, False, timeout_ms)
            async with mcp_client(chassis.public_uds, headers) as client:
                result = await client.call_tool(chassis.agent, arguments, raise_on_error=False)
            return Reply(200 if not result.is_error else 500, {}, result.structured_content)
        transport = httpx2.AsyncHTTPTransport(uds=chassis.public_uds)
        async with httpx2.AsyncClient(
            transport=transport, base_url=BASE_URL, timeout=CALL_TIMEOUT_S
        ) as http:
            response = await http.post(
                PATHS[interface],
                json=_body(interface, text, stream, timeout_ms),
                headers=headers,
            )
    lowered = {k.lower(): v for k, v in response.headers.items()}
    if stream and response.status_code == 200:
        return Reply(response.status_code, lowered, sse_events(response.text))
    try:
        parsed: Any = response.json()
    except ValueError:
        parsed = response.text
    return Reply(response.status_code, lowered, parsed)
