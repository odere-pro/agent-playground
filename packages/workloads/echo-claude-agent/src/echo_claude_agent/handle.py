"""Skeleton of the Claude Agent SDK simplifier (PoC-6, 6b). It does nothing yet.

`handle(input, ctx)` is the wire form of the contract (docs/contracts/contract-v0.md): plain dicts
in, event dicts out, `schema_version: "0"` on each. The skeleton yields `start`, then one `error`
with code `not_implemented`, so the workload registers, serves, and validates before the engine
exists. A later task replaces the body and keeps the signature and the test hooks.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx2

SCHEMA_VERSION = "0"

transport: httpx2.AsyncBaseTransport | None = None
"""Test-only hook: when set, model calls go through this `httpx2` transport, not a socket. The
MCP hook is `echo_claude_agent.tools.transport`."""


async def handle(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    """`start`, then `error` (`not_implemented`, not retryable). Never raises."""
    yield {
        "schema_version": SCHEMA_VERSION,
        "type": "start",
        "request_id": str(ctx.get("request_id", "")),
    }
    yield {
        "schema_version": SCHEMA_VERSION,
        "type": "error",
        "code": "not_implemented",
        "message": "echo-claude-agent is a PoC-6 skeleton: the engine is not built yet",
        "retryable": False,
    }
