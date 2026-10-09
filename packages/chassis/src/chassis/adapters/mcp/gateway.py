"""`McpGatewayTools`: `ToolPort` over LiteLLM's MCP gateway (PoC-5, plan section 2.7).

An MCP client (FastMCP 4, streamable HTTP) that authenticates with the service's own LiteLLM
virtual key, `Authorization: Bearer <key>`. The gateway's per-key allow-list is the hard limit:
the adapter lists what the gateway shows this key and adds no allow-list of its own. The httpx
client ignores the environment (`trust_env=False`) and never follows a redirect, so no proxy
variable or redirect can carry the key elsewhere. The key is never logged, never in an error, and
never in `repr`.

`list_tools()` is sync in the Protocol, so the adapter keeps a cached list: `await refresh()`
fills it, and `start()` refreshes it every `refresh_s` in the background. A failed refresh keeps
the last list and counts `refresh_failures`; the background loop logs a refresh that raises and
keeps looping. Each call opens one short MCP session.

`call()`, in order:
- a write tool (`read_only: false`; a tool with no `readOnlyHint` counts as one) with no
  `idempotency_key` raises `idempotency_key_required` before any request;
- arguments that fail the listed schema (required fields, top-level types, extra fields when
  `additionalProperties` is false) raise `bad_arguments` before any request;
- the key goes as `_meta.idempotency_key`, and also as the `idempotency_key` argument when the
  tool's schema declares that property (the fallback for a gateway that drops `_meta`);
- the gateway's answer maps to: the call's own JSON-RPC error first (an unknown tool
  `unknown_tool`, a refusal text `tool_denied`, invalid params `bad_arguments`), then 401 or
  403 `tool_denied`, then a connect error, a timeout, or a 5xx `tool_unavailable` (retryable);
  the session-close `DELETE`'s status is never read; a tool error result the gateway phrases as
  a refusal maps the same way (an unknown tool `unknown_tool`, a tool off the key's allow-list
  `tool_denied`); a tool error that is not one of these stays `ToolResult(is_error=True)`.

A `ToolError.message` is fixed text, never an upstream body.

Known limit: the refusal texts are matched on any tool's error text, not only the gateway's, so an
upstream tool whose error starts with one picks its own code (`unknown_tool`, `tool_denied`,
`idempotency_key_required`, `bad_arguments`). It cannot pick the message, which stays fixed text,
so nothing the tool sends leaks through the error.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from typing import Any

import httpx2
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from mcp.shared.exceptions import MCPError
from mcp_types import CallToolResult, TextContent, Tool

from chassis.ports.tool import ToolDefinition, ToolError, ToolResult

URL_VAR = "LITELLM_MCP_URL"
API_KEY_VAR = "LITELLM_API_KEY"
AUTH_HEADER_VAR = "LITELLM_MCP_AUTH_HEADER"
"""Optional: the header that carries `Bearer <key>`. Default `Authorization`; the plan's
fallback, if the gateway reads the key elsewhere, is `x-litellm-api-key`."""

META_KEY = "idempotency_key"
# suggested: 30 s per call (plan section 2.7) and a refresh every 60 s.
DEFAULT_TIMEOUT_S = 30.0
TOOLS_REFRESH_S = 60.0

log = logging.getLogger(__name__)

ClientFactory = Callable[..., httpx2.AsyncClient]

# LiteLLM v1.103.0 says "Error: Tool '<name>' not found" for an unknown tool, and "Error: Tool
# '<name>' is not allowed for your key/team ..." or "User not allowed to call this tool." for a
# tool outside the key's allow-list (pocs/poc-05-sandboxed/notes/2026-10-02-bring-up.md, item 3).
_UNKNOWN_TOOL = re.compile(
    r"^\s*(unknown tool|tool not found)\b|^\s*(error:\s*)?tool '[^']*' not found\b", re.IGNORECASE
)
_DENIED = re.compile(
    r"^\s*(error:\s*)?tool '[^']*' is not allowed\b|^\s*user not allowed to call this tool\b",
    re.IGNORECASE,
)
_KEY_REQUIRED = re.compile(r"^\s*idempotency_key_required\b")
_BAD_ARGUMENTS = re.compile(
    r"\bvalidation errors? for\b|missing required argument|^\s*invalid arguments", re.IGNORECASE
)
_INVALID_PARAMS = -32602
_METHOD_NOT_FOUND = -32601

_JSON_TYPES: dict[str, Callable[[Any], bool]] = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, int | float) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "object": lambda v: isinstance(v, Mapping),
    "array": lambda v: isinstance(v, list | tuple),
    "null": lambda v: v is None,
}


def _matches(schema: Any, value: Any) -> bool:
    """A small JSON Schema type check: `type` (one or a list), else `anyOf` or `oneOf`."""
    if not isinstance(schema, Mapping):
        return True
    kind = schema.get("type")
    if isinstance(kind, str):
        check = _JSON_TYPES.get(kind)
        return check is None or check(value)
    if isinstance(kind, list):
        return any(_JSON_TYPES.get(k, lambda _: True)(value) for k in kind if isinstance(k, str))
    options = schema.get("anyOf") or schema.get("oneOf")
    if isinstance(options, list) and options:
        return any(_matches(option, value) for option in options)
    return True


def _argument_problem(schema: Mapping[str, Any], arguments: Mapping[str, Any]) -> str | None:
    """What is wrong with the arguments, by field name only, or None."""
    properties = schema.get("properties")
    properties = properties if isinstance(properties, Mapping) else {}
    for field in schema.get("required") or ():
        if field not in arguments:
            return f"missing required argument {field!r}"
    for field, value in arguments.items():
        if field not in properties:
            if schema.get("additionalProperties") is False:
                return f"unexpected argument {field!r}"
            continue
        if not _matches(properties[field], value):
            return f"argument {field!r} has the wrong type"
    return None


def _definition(tool: Tool) -> ToolDefinition:
    hint = tool.annotations.read_only_hint if tool.annotations is not None else None
    parameters = dict(tool.input_schema or {})
    parameters.setdefault("type", "object")
    return ToolDefinition(
        name=tool.name,
        description=tool.description or tool.title or tool.name,
        parameters=parameters,
        read_only=hint is True,  # no hint from a real server: a write tool (fail safe)
    )


def _content(result: CallToolResult) -> Any:
    if result.structured_content is not None:
        return result.structured_content
    blocks = result.content
    if len(blocks) == 1 and isinstance(blocks[0], TextContent):
        return blocks[0].text
    return [block.model_dump(mode="json", exclude_none=True) for block in blocks]


def _first_text(result: CallToolResult) -> str:
    for block in result.content:
        if isinstance(block, TextContent):
            return block.text
    return ""


def _leaves(exc: BaseException) -> Iterator[BaseException]:
    if isinstance(exc, BaseExceptionGroup):
        for inner in exc.exceptions:
            yield from _leaves(inner)
    else:
        yield exc


class _Statuses:
    """The HTTP error statuses one MCP session saw. FastMCP folds them all into one JSON-RPC
    error, so the adapter reads them from a response hook instead. The session-close `DELETE` is
    not counted: a gateway in stateless mode may refuse it (404, 405), and that says nothing about
    the call (review M4)."""

    def __init__(self) -> None:
        self.seen: list[int] = []

    async def hook(self, response: httpx2.Response) -> None:
        if response.status_code >= 400 and response.request.method != "DELETE":
            self.seen.append(response.status_code)


def _rpc_error(exc: BaseException | None, name: str) -> ToolError | None:
    """The `ToolError` for a JSON-RPC error of the call itself, or None."""
    for leaf in _leaves(exc) if exc is not None else ():
        if isinstance(leaf, MCPError):
            if leaf.error.code == _METHOD_NOT_FOUND or _UNKNOWN_TOOL.match(leaf.error.message):
                return ToolError("unknown_tool", f"no tool named {name!r}")
            if _DENIED.match(leaf.error.message):
                return ToolError("tool_denied", f"the gateway refused {name!r} for this key")
            if leaf.error.code == _INVALID_PARAMS:
                return ToolError("bad_arguments", f"the arguments for {name!r} were refused")
    return None


def _failure(exc: BaseException | None, statuses: Sequence[int], name: str) -> ToolError:
    """Map a failed session to a `ToolError`. The message is fixed text, never upstream text.
    The call's own JSON-RPC error (unknown tool, denial, invalid params) beats any HTTP status."""
    rpc = _rpc_error(exc, name)
    if rpc is not None:
        return rpc
    if any(s in (401, 403) for s in statuses):
        return ToolError("tool_denied", f"the gateway refused the call to {name!r}")
    if any(s >= 500 or s in (408, 429) for s in statuses):
        status = next(s for s in statuses if s >= 500 or s in (408, 429))
        return ToolError("tool_unavailable", f"the gateway answered HTTP {status}", retryable=True)
    if statuses:
        return ToolError("tool_unavailable", f"the gateway answered HTTP {statuses[0]}")
    kind = type(next(_leaves(exc))).__name__ if exc is not None else "no answer"
    return ToolError("tool_unavailable", f"the gateway did not answer ({kind})", retryable=True)


class McpGatewayTools:
    """`ToolPort` over an MCP gateway. Build it with `from_env()` in a deployment."""

    name = "mcp"

    def __init__(
        self,
        url: str,
        api_key: str,
        *,
        auth_header: str = "Authorization",
        timeout: float = DEFAULT_TIMEOUT_S,
        refresh_s: float = TOOLS_REFRESH_S,
        httpx_client_factory: ClientFactory | None = None,
    ) -> None:
        self.url = url
        self.timeout = timeout
        self.refresh_s = refresh_s
        self.refresh_failures = 0
        # Called once per failed refresh; the tool endpoint sets it to count
        # `chassis.tools.refresh_failed` on the telemetry port.
        self.on_refresh_failed: Callable[[], None] | None = None
        self._auth_header = auth_header
        self._api_key = api_key
        self._factory: ClientFactory = httpx_client_factory or httpx2.AsyncClient
        self._tools: dict[str, ToolDefinition] = {}
        self._task: asyncio.Task[None] | None = None

    def __repr__(self) -> str:
        key = "set" if self._api_key else "none"
        return f"McpGatewayTools(url={self.url!r}, tools={len(self._tools)}, api_key={key})"

    @classmethod
    def from_env(cls) -> McpGatewayTools:
        """Build from `LITELLM_MCP_URL` and `LITELLM_API_KEY` (both required) and the optional
        `LITELLM_MCP_AUTH_HEADER`. An unset variable is a `LookupError` that names it."""
        url = os.environ.get(URL_VAR)
        if not url:
            raise LookupError(f"tools: mcp needs {URL_VAR}")
        api_key = os.environ.get(API_KEY_VAR)
        if not api_key:
            raise LookupError(f"tools: mcp needs {API_KEY_VAR}")
        header = os.environ.get(AUTH_HEADER_VAR) or "Authorization"
        return cls(url, api_key, auth_header=header)

    # The session.

    def _client(self, statuses: _Statuses) -> Client[StreamableHttpTransport]:
        def factory(**kwargs: Any) -> httpx2.AsyncClient:
            kwargs["follow_redirects"] = False
            kwargs["trust_env"] = False
            kwargs["event_hooks"] = {"response": [statuses.hook]}
            return self._factory(**kwargs)

        transport = StreamableHttpTransport(
            self.url,
            headers={self._auth_header: f"Bearer {self._api_key}"},
            httpx_client_factory=factory,  # type: ignore[arg-type]
        )
        return Client(transport, timeout=self.timeout)

    # The tool list.

    def list_tools(self) -> Sequence[ToolDefinition]:
        return tuple(self._tools.values())

    async def refresh(self) -> bool:
        """Replace the cached list with what the gateway shows this key. On failure keep the
        last list, count it, and return False."""
        statuses = _Statuses()
        try:
            async with asyncio.timeout(self.timeout), self._client(statuses) as client:
                tools = await client.list_tools()
        except Exception as exc:  # every failure keeps the last list
            self.refresh_failures += 1
            error = _failure(exc, statuses.seen, "tools/list")
            log.warning("tool gateway: refresh failed: %s", error.message)
            self._refresh_failed()
            return False
        self._tools = {t.name: _definition(t) for t in tools}
        return True

    def _refresh_failed(self) -> None:
        """Call `on_refresh_failed`; a failing callback (telemetry) is logged, never raised."""
        if self.on_refresh_failed is None:
            return
        try:
            self.on_refresh_failed()
        except Exception as exc:
            log.warning("tool gateway: refresh-failed callback raised %s", type(exc).__name__)

    def start(self) -> None:
        """Refresh every `refresh_s` in the background until `aclose()`."""
        if self._task is None:
            self._task = asyncio.create_task(self._refresh_loop())

    async def _refresh_loop(self) -> None:
        """Refresh until cancelled. A refresh that raises is logged and counted; the loop goes on,
        so one bad round never ends the background refresh (review L2)."""
        while True:
            await asyncio.sleep(self.refresh_s)
            try:
                await self.refresh()
            except Exception as exc:
                self.refresh_failures += 1
                log.warning("tool gateway: refresh loop: a refresh raised %s", type(exc).__name__)
                self._refresh_failed()

    async def aclose(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    # The call.

    async def call(
        self, name: str, arguments: Mapping[str, Any], *, idempotency_key: str | None = None
    ) -> ToolResult:
        definition = self._tools.get(name)
        if definition is None:
            await self.refresh()  # the list may be stale; the gateway still decides
            definition = self._tools.get(name)
        args = dict(arguments)
        if definition is not None:
            if not definition.read_only and not idempotency_key:
                raise ToolError("idempotency_key_required", f"{name!r} is a write tool")
            problem = _argument_problem(definition.parameters, args)
            if problem is not None:
                raise ToolError("bad_arguments", f"{name!r}: {problem}")
            properties = definition.parameters.get("properties") or {}
            if idempotency_key and not definition.read_only and META_KEY in properties:
                args[META_KEY] = idempotency_key
        meta = {META_KEY: idempotency_key} if idempotency_key else None
        statuses = _Statuses()
        try:
            async with asyncio.timeout(self.timeout), self._client(statuses) as client:
                result = await client.call_tool_mcp(name, args, meta=meta)
        except Exception as exc:  # every failure maps to a code
            error = _failure(exc, statuses.seen, name)
            log.warning("tool gateway: call %r failed: %s %s", name, error.code, error.message)
            raise error from None
        if result.is_error:
            return self._tool_error(name, result)
        return ToolResult(content=_content(result))

    @staticmethod
    def _tool_error(name: str, result: CallToolResult) -> ToolResult:
        """A tool error result: a refusal the gateway or server phrases as one maps to its code;
        anything else is the tool's own failure and stays a result."""
        text = _first_text(result)
        if _UNKNOWN_TOOL.match(text):
            raise ToolError("unknown_tool", f"no tool named {name!r} for this key")
        if _DENIED.match(text):
            raise ToolError("tool_denied", f"the gateway refused {name!r} for this key")
        if _KEY_REQUIRED.match(text):
            raise ToolError("idempotency_key_required", f"{name!r} needs an idempotency key")
        if _BAD_ARGUMENTS.search(text):
            raise ToolError("bad_arguments", f"the arguments for {name!r} were refused")
        return ToolResult(content=_content(result), is_error=True)
