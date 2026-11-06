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

A call to a name the cached list does not hold refreshes the list first, at most once per
`unknown_refresh_cooldown_s` (suggested: 10 s); inside the cooldown the call goes to the gateway
on the cached list, and the gateway still decides. A name longer than `MAX_TOOL_NAME` (128, the
MCP limit) is `unknown_tool` before any request, so it never reaches the gateway or the log.

`call()`, in order:
- a write tool (`read_only: false`; a tool with no `readOnlyHint` counts as one) with no
  `idempotency_key` raises `idempotency_key_required` before any request;
- arguments that fail the listed schema raise `bad_arguments` before any request. The check is
  `chassis.ports.tool_arguments.check_arguments`, the one the fake tools run (R5b-08): types,
  `enum`, `anyOf`/`oneOf`, and nested `properties`, `required`, `additionalProperties: false`.
  The message names fields and paths, never a value;
- the key goes as `_meta.idempotency_key`, and also as the `idempotency_key` argument when the
  tool's schema declares that property (the fallback for a gateway that drops `_meta`);
- the gateway's answer maps to: the call's own JSON-RPC error first (an unknown tool
  `unknown_tool`, a refusal text `tool_denied`, invalid params `bad_arguments`), then 401 or
  403 `tool_denied`, then a connect error, a timeout, or a 5xx `tool_unavailable` (retryable);
  the session-close `DELETE`'s status is never read; a tool error result the gateway phrases as
  a refusal maps the same way (an unknown tool `unknown_tool`, a tool off the key's allow-list
  `tool_denied`); a tool error that is not one of these stays `ToolResult(is_error=True)`.

A `ToolError.message` is fixed text, never an upstream body.

Result size (R5a-24): a result whose serialized form is over `MAX_RESULT_BYTES` (1 MiB, suggested)
is `tool_unavailable` (not retryable) with a fixed message that names the cap, never the content.
The MCP client library reads a whole response, then parses it, before the adapter sees the
result; so the adapter also wraps each response body as it streams in and stops reading past
`MAX_RESPONSE_BYTES` (twice the cap plus framing: FastMCP sends a result as text and again as
structured content). The count is of decoded bytes: the client asks for `Accept-Encoding:
identity`, and a body the gateway encodes anyway (gzip, deflate) is decoded in the wrapper, in
pieces of at most 1 MiB, so a small compressed body cannot grow past the cap. The chassis
therefore holds at most about `MAX_RESPONSE_BYTES` plus one decoded piece of one response, and
relays at most `MAX_RESULT_BYTES`.

Known limit: the refusal texts are matched on any tool's error text, not only the gateway's, so an
upstream tool whose error starts with one picks its own code (`unknown_tool`, `tool_denied`,
`idempotency_key_required`, `bad_arguments`). It cannot pick the message, which stays fixed text,
so nothing the tool sends leaks through the error.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import time
import zlib
from collections.abc import AsyncIterator, Callable, Iterator, Mapping, Sequence
from typing import Any

import httpx2
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from mcp.shared.exceptions import MCPError
from mcp_types import CallToolResult, TextContent, Tool

from chassis.ports.tool import ToolDefinition, ToolError, ToolResult
from chassis.ports.tool_arguments import check_arguments

URL_VAR = "LITELLM_MCP_URL"
API_KEY_VAR = "LITELLM_API_KEY"
AUTH_HEADER_VAR = "LITELLM_MCP_AUTH_HEADER"
"""Optional: the header that carries `Bearer <key>`. Default `Authorization`; the plan's
fallback, if the gateway reads the key elsewhere, is `x-litellm-api-key`."""

META_KEY = "idempotency_key"
# suggested: 30 s per call (plan section 2.7) and a refresh every 60 s.
DEFAULT_TIMEOUT_S = 30.0
TOOLS_REFRESH_S = 60.0
UNKNOWN_REFRESH_COOLDOWN_S = 10.0
"""suggested: the shortest time between two refreshes caused by an unlisted name, as `OidcAuth`
does for an unknown `kid`, so a flood of made-up names cannot multiply the gateway's load."""
MAX_TOOL_NAME = 128
"""The longest tool name `call()` accepts (the MCP spec's limit for tool names)."""
MAX_RESULT_BYTES = 1024 * 1024
"""suggested: the largest serialized tool result the chassis relays (1 MiB), from the gateway
here and from any port in `adapters/mcp/server.py`. Over it: `tool_unavailable`."""
MAX_RESPONSE_BYTES = 2 * MAX_RESULT_BYTES + 64 * 1024
"""The most body bytes the adapter reads from one gateway response: a result travels as text and
again as structured content, plus JSON-RPC and SSE framing."""

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


def over_cap(name: str) -> ToolError:
    """The fixed refusal of a result over `MAX_RESULT_BYTES`: the cap, never the content."""
    return ToolError("tool_unavailable", f"the result of {name!r} is over {MAX_RESULT_BYTES} bytes")


def serialized_size(content: Any) -> int:
    """The bytes `content` takes as the chassis relays it: a string as UTF-8, else as JSON."""
    text = content if isinstance(content, str) else json.dumps(content, default=str)
    return len(text.encode("utf-8"))


def _content(result: CallToolResult, name: str) -> Any:
    """The result's content, refused with `over_cap` when its serialized form is too large."""
    content: Any
    if result.structured_content is not None:
        content = result.structured_content
    elif len(result.content) == 1 and isinstance(result.content[0], TextContent):
        content = result.content[0].text
    else:
        content = [block.model_dump(mode="json", exclude_none=True) for block in result.content]
    if serialized_size(content) > MAX_RESULT_BYTES:
        raise over_cap(name)
    return content


class _ResponseTooLarge(Exception):
    """A gateway response body passed `MAX_RESPONSE_BYTES`; reading stopped there."""


_DECODE_PIECE = 64 * 1024
"""The most decoded data `_Inflate` hands on at once, so the cap is checked before more comes."""


class _UnknownEncoding(Exception):
    """A `Content-Encoding` the capped stream cannot decode in bounded pieces; refused."""


class _Inflate:
    """A gzip or deflate body, decoded with the stdlib in pieces of at most `_DECODE_PIECE`."""

    def __init__(self, encoding: str) -> None:
        gzip = encoding in ("gzip", "x-gzip")
        self._z = zlib.decompressobj(16 + zlib.MAX_WBITS if gzip else zlib.MAX_WBITS)

    def decode(self, data: bytes) -> Iterator[bytes]:
        if self._z.eof and data:
            # zlib would keep bytes after the end of the stream in `unused_data`, uncounted.
            raise _ResponseTooLarge("bytes after the end of the compressed body")
        while True:
            piece = self._z.decompress(data, _DECODE_PIECE)
            if self._z.unused_data:
                raise _ResponseTooLarge("bytes after the end of the compressed body")
            data = self._z.unconsumed_tail
            if piece:
                yield piece
            if not data and len(piece) < _DECODE_PIECE:
                return

    def flush(self) -> Iterator[bytes]:
        yield from self.decode(b"")
        rest = self._z.flush()
        if rest:
            yield rest


class _CappedStream(httpx2.AsyncByteStream):
    """A response body that raises once more than `MAX_RESPONSE_BYTES` of decoded data came in,
    so the rest is never read into the chassis. With a `decoder`, it decodes the body itself, in
    bounded pieces, and counts what comes out; the hook drops `Content-Encoding` so httpx does
    not decode it again. It records the overrun on `statuses`, because the MCP client may wrap
    or swallow the exception."""

    def __init__(self, inner: Any, statuses: _Statuses, decoder: _Inflate | None = None) -> None:
        self._inner = inner
        self._statuses = statuses
        self._decoder = decoder
        self._read = 0

    def _count(self, piece: bytes) -> bytes:
        self._read += len(piece)
        if self._read > MAX_RESPONSE_BYTES:
            self._statuses.over_cap = True
            raise _ResponseTooLarge(f"over {MAX_RESPONSE_BYTES} bytes")
        return piece

    async def __aiter__(self) -> AsyncIterator[bytes]:
        decoder = self._decoder
        raw = 0
        try:
            async for chunk in self._inner:
                raw += len(chunk)
                if raw > MAX_RESPONSE_BYTES:
                    raise _ResponseTooLarge(f"over {MAX_RESPONSE_BYTES} raw bytes")
                for piece in decoder.decode(chunk) if decoder is not None else (chunk,):
                    yield self._count(piece)
            if decoder is not None:
                for piece in decoder.flush():
                    yield self._count(piece)
        except _ResponseTooLarge:
            self._statuses.over_cap = True
            raise

    async def aclose(self) -> None:
        await self._inner.aclose()


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
        self.over_cap = False

    async def hook(self, response: httpx2.Response) -> None:
        if response.status_code >= 400 and response.request.method != "DELETE":
            self.seen.append(response.status_code)
        try:
            decoder = _take_decoder(response)
        except _UnknownEncoding:
            # Fail closed: a body whose decoded size cannot be bounded is treated as over the cap.
            self.over_cap = True
            raise _ResponseTooLarge("an encoding the gateway adapter cannot bound") from None
        response.stream = _CappedStream(response.stream, self, decoder)


def _take_decoder(response: httpx2.Response) -> _Inflate | None:
    """The decoder for the body's `Content-Encoding`, or None for a plain body. The header is
    dropped, so the capped stream decodes and counts the body, and httpx passes it on as is.
    Only one gzip or deflate layer is decoded; anything else raises `_UnknownEncoding`."""
    encodings = [
        value.strip().lower()
        for value in response.headers.get_list("content-encoding", split_commas=True)
    ]
    encodings = [value for value in encodings if value and value != "identity"]
    if not encodings:
        return None
    if len(encodings) > 1 or encodings[0] not in ("gzip", "x-gzip", "deflate"):
        raise _UnknownEncoding(", ".join(encodings))
    del response.headers["content-encoding"]
    return _Inflate(encodings[0])


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


def _iter_leaves(exc: BaseException | None) -> Iterator[BaseException]:
    """The leaves of `exc` and of its `__cause__` and `__context__` chain."""
    seen: set[int] = set()
    todo = [exc] if exc is not None else []
    while todo:
        current = todo.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        for leaf in _leaves(current):
            yield leaf
            todo.extend(e for e in (leaf.__cause__, leaf.__context__) if e is not None)


def _failure(
    exc: BaseException | None, statuses: Sequence[int], name: str, *, over: bool = False
) -> ToolError:
    """Map a failed session to a `ToolError`. The message is fixed text, never upstream text.
    The call's own JSON-RPC error (unknown tool, denial, invalid params) beats any HTTP status;
    a response over `MAX_RESPONSE_BYTES` beats both."""
    if over or any(isinstance(leaf, _ResponseTooLarge) for leaf in _iter_leaves(exc)):
        return over_cap(name)
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
        unknown_refresh_cooldown_s: float = UNKNOWN_REFRESH_COOLDOWN_S,
        httpx_client_factory: ClientFactory | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.url = url
        self.timeout = timeout
        self.refresh_s = refresh_s
        self.unknown_refresh_cooldown_s = unknown_refresh_cooldown_s
        self.clock = clock
        self._unknown_refreshed_at: float | None = None
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
            # identity: the cap counts decoded bytes; a gateway that encodes anyway is decoded
            # by `_CappedStream`.
            headers={self._auth_header: f"Bearer {self._api_key}", "Accept-Encoding": "identity"},
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
            error = _failure(exc, statuses.seen, "tools/list", over=statuses.over_cap)
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
        if len(name) > MAX_TOOL_NAME:
            raise ToolError("unknown_tool", f"no tool has a name over {MAX_TOOL_NAME} characters")
        definition = self._tools.get(name)
        if definition is None and self._may_refresh_for_unknown():
            await self.refresh()  # the list may be stale; the gateway still decides
            definition = self._tools.get(name)
        args = dict(arguments)
        if definition is not None:
            if not definition.read_only and not idempotency_key:
                raise ToolError("idempotency_key_required", f"{name!r} is a write tool")
            check_arguments(definition, args)
            properties = definition.parameters.get("properties") or {}
            if idempotency_key and not definition.read_only and META_KEY in properties:
                args[META_KEY] = idempotency_key
        meta = {META_KEY: idempotency_key} if idempotency_key else None
        statuses = _Statuses()
        try:
            async with asyncio.timeout(self.timeout), self._client(statuses) as client:
                result = await client.call_tool_mcp(name, args, meta=meta)
        except Exception as exc:  # every failure maps to a code
            error = _failure(exc, statuses.seen, name, over=statuses.over_cap)
            log.warning("tool gateway: call %r failed: %s %s", name, error.code, error.message)
            raise error from None
        if result.is_error:
            return self._tool_error(name, result)
        return ToolResult(content=_content(result, name))

    def _may_refresh_for_unknown(self) -> bool:
        """True at most once per `unknown_refresh_cooldown_s`; records the refresh it allows."""
        now = self.clock()
        last = self._unknown_refreshed_at
        if last is not None and now - last < self.unknown_refresh_cooldown_s:
            return False
        self._unknown_refreshed_at = now
        return True

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
        return ToolResult(content=_content(result, name), is_error=True)
