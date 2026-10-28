"""The fake MCP server (PoC-5, plan section 2.7): four harmless tools, no auth of its own.

`FakeMcpState` is the whole memory of one server: the notes, the count of real executions, the
calls received, and the optional allow-list that imitates a gateway's per-key tool list.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import Tool
from mcp.types import CallToolRequestParams, ListToolsRequest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

TOOL_NAMES = ("glossary_lookup", "acronym_expand", "note_write", "unlisted_probe")
PROBE_MARKER = "UNLISTED-PROBE-MARKER-7f3a"
ANY_CALLER = "*"
MCP_PATH = "/mcp/"

GLOSSARY: dict[str, str] = {
    "SLM": "A small language model: a model small enough to run cheaply on one GPU or a CPU.",
    "A2A": "Agent-to-Agent, the protocol the chassis uses to send a task to a workload.",
    "MCP": "Model Context Protocol, the protocol the chassis uses to serve tools.",
    "chassis": "The service around a workload: it holds keys, calls models and tools, and traces.",
    "sidecar": "A lane where the workload runs in its own container next to the chassis.",
    "LiteLLM": "The model router the chassis calls; it holds routes, keys, and budgets.",
    "workload": "The agent code that runs behind the chassis and answers one request.",
    "lane": "How the chassis reaches a workload: inprocess, sidecar, or remote.",
    "port": "A typed interface the chassis uses for one outside dependency.",
    "fake": "An in-memory stand-in for a port, used in tests and the fake profile.",
    "traceparent": "The W3C header that carries the trace id from one call to the next.",
}

ACRONYMS: dict[str, str] = {
    "RAG": "retrieval-augmented generation",
    "SLM": "small language model",
    "LLM": "large language model",
    "MCP": "Model Context Protocol",
    "A2A": "Agent-to-Agent",
    "TTFT": "time to first token",
}


@dataclass
class FakeMcpState:
    """`allow` maps a bearer token to its tool names; `"*"` covers any caller, even with no
    token. `None` shows every tool to everyone. With a map, a caller with no entry sees none."""

    allow: Mapping[str, frozenset[str]] | None = None
    notes: list[dict[str, Any]] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    _by_key: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def executions(self) -> int:
        """Real writes done. A repeated key does not add one."""
        return len(self.notes)

    def allowed_for(self, token: str | None) -> frozenset[str] | None:
        if self.allow is None:
            return None
        if token is not None and token in self.allow:
            return self.allow[token]
        return self.allow.get(ANY_CALLER, frozenset())


def _bearer() -> str | None:
    value = get_http_headers(include={"authorization"}).get("authorization", "")
    scheme, _, token = value.partition(" ")
    return token.strip() or None if scheme.lower() == "bearer" else None


class _AllowList(Middleware):
    """Hide tools outside the caller's list and refuse a call to one as an unknown tool."""

    def __init__(self, state: FakeMcpState) -> None:
        self._state = state

    async def on_list_tools(
        self,
        context: MiddlewareContext[ListToolsRequest],
        call_next: CallNext[ListToolsRequest, Sequence[Tool]],
    ) -> Sequence[Tool]:
        tools = await call_next(context)
        allowed = self._state.allowed_for(_bearer())
        return tools if allowed is None else [t for t in tools if t.name in allowed]

    async def on_call_tool(
        self, context: MiddlewareContext[CallToolRequestParams], call_next: CallNext[Any, Any]
    ) -> Any:
        name = context.message.name
        allowed = self._state.allowed_for(_bearer())
        if allowed is not None and name not in allowed:
            raise ToolError(f"unknown tool: {name}")
        return await call_next(context)


def _meta_key(ctx: Context) -> str | None:
    """The key a client sends as `_meta.idempotency_key` of the tools/call request."""
    rc = ctx.request_context
    meta = getattr(rc, "meta", None) if rc is not None else None
    value = meta.get("idempotency_key") if isinstance(meta, Mapping) else None
    if value is None and meta is not None:
        value = getattr(meta, "idempotency_key", None)  # pydantic model with extra fields
    return value if isinstance(value, str) and value else None


def create_server(state: FakeMcpState) -> FastMCP:
    mcp = FastMCP("fake-mcp-server", middleware=[_AllowList(state)])

    @mcp.tool(annotations={"readOnlyHint": True})
    def glossary_lookup(term: str) -> dict[str, str | None]:
        """Look up a platform term. Returns its short definition, or null if unknown."""
        state.calls.append({"tool": "glossary_lookup", "arguments": {"term": term}})
        definition = GLOSSARY.get(term)
        if definition is None:
            definition = {k.casefold(): v for k, v in GLOSSARY.items()}.get(term.casefold())
        return {"term": term, "definition": definition}

    @mcp.tool(annotations={"readOnlyHint": True})
    def acronym_expand(acronym: str) -> dict[str, str | None]:
        """Expand an acronym. Returns its expansion, or null if unknown."""
        state.calls.append({"tool": "acronym_expand", "arguments": {"acronym": acronym}})
        expansion = ACRONYMS.get(acronym)
        if expansion is None:
            expansion = {k.casefold(): v for k, v in ACRONYMS.items()}.get(acronym.casefold())
        return {"acronym": acronym, "expansion": expansion}

    @mcp.tool(annotations={"readOnlyHint": False})
    def note_write(text: str, ctx: Context, idempotency_key: str | None = None) -> dict[str, Any]:
        """Store a note. One note per idempotency key; a repeated key returns the first result.

        The key is `_meta.idempotency_key`; the `idempotency_key` argument is the fallback.
        """
        key = _meta_key(ctx) or idempotency_key
        state.calls.append(
            {"tool": "note_write", "arguments": {"text": text}, "idempotency_key": key}
        )
        if not key:
            raise ToolError("idempotency_key_required: note_write needs an idempotency key")
        done = state._by_key.get(key)
        if done is not None:
            return done
        digest = hashlib.sha256(f"{key}|{text}".encode()).hexdigest()[:12]
        result = {"note_id": f"note-{len(state.notes) + 1}-{digest}", "text": text}
        state.notes.append({"key": key, **result})
        state._by_key[key] = result
        return result

    @mcp.tool(annotations={"readOnlyHint": True})
    def unlisted_probe() -> str:
        """Return a fixed marker. It is on no allow-list; seeing the marker means a leak."""
        state.calls.append({"tool": "unlisted_probe", "arguments": {}})
        return PROBE_MARKER

    return mcp


def create_app(state: FakeMcpState | None = None, *, expose_calls: bool = False) -> Starlette:
    """The streamable HTTP app at `/mcp/`. `GET /calls` (off by default) is for offline tests."""
    state = state if state is not None else FakeMcpState()
    mcp = create_server(state)

    if expose_calls:

        @mcp.custom_route("/calls", methods=["GET"])
        async def calls(_: Request) -> Response:
            return JSONResponse(
                json.loads(json.dumps({"executions": state.executions, "calls": state.calls}))
            )

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> Response:
        return JSONResponse({"status": "ok"})

    return mcp.http_app(path=MCP_PATH)
