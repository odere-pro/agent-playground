"""The MCP test hook of echo-claude-agent (PoC-6 skeleton)."""

from __future__ import annotations

import httpx2

transport: httpx2.AsyncBaseTransport | None = None
"""Test-only hook: when set, MCP requests go through this `httpx2` transport, not a socket."""
