"""The simplifier as a Claude Agent SDK agent, streamed back as chassis events.

`handle(input, ctx)` is the wire form of the contract (docs/contracts/contract-v0.md): plain dicts
in, event dicts out, `schema_version: "0"` on each. Same prompt and the same event sequence as
echo-pydanticai; the SDK mapping is in `mapping.py`.

Engine: one SDK `query()` per run, which is one `claude` CLI process (the binary in the SDK wheel;
no Node). The CLI is the model client and the MCP client. This workload runs shell and file tools,
so it is `untrusted` and runs in the `remote` lane only.

- Model: the CLI posts to `<ANTHROPIC_BASE_URL>/v1/messages`, the chassis model proxy. The base URL
  is `CHASSIS_MODEL_URL` with its trailing `/v1` removed. The route is `ctx["model_route"]`, for
  both the main and the small model.
- Tools: `Bash`, `Read`, `Write`, and the chassis MCP server at `CHASSIS_TOOL_URL` (server name
  `chassis`, so the tools are `mcp__chassis__*`). The CLI does not pass `traceparent` or the token
  to MCP, so they are set per server in `headers`. `ctx["run_secret"]`, when set, is the
  `x-chassis-run-secret` header on both: a second `ANTHROPIC_CUSTOM_HEADERS` line and in `headers`.
- Credentials: `CHASSIS_API_TOKEN`, when set and not empty, is `ANTHROPIC_AUTH_TOKEN` and the MCP
  bearer token; else the model gets a placeholder (the loopback proxy ignores it). No provider key
  is read or accepted: see the guard below.
- `HOME` is a fresh directory per run under `CLAUDE_AGENT_HOME_BASE` (default `/tmp`), removed
  after the run, once the CLI has exited. The work dir (`cwd`) is inside it. Closing the SDK's
  `query()` does not stop the CLI (it reads its inner generator with a plain `async for`), so
  `handle` builds the run's transport itself and awaits its `close()`: stdin EOF and 5 s to exit,
  then SIGTERM and 5 s, then SIGKILL. That runs whether the run is drained, closed early, or
  cancelled. A file the delete cannot remove is logged, never dropped in silence.
- The CLI's temp files go in `HOME` too: `TMPDIR`, `CLAUDE_CODE_TMPDIR`, and `XDG_RUNTIME_DIR`
  are one private (0700) folder, `HOME/tmp`. Else the CLI inherits this process's values (on
  Linux its temp root is `os.tmpdir()`, that is `TMPDIR`) and leaves `cc-socks` and
  `claude-<uid>/fswatch-probe-*` in the shared `/tmp` after the call.
- The environment guard: the SDK merges `os.environ` into the CLI's environment, so any
  `ANTHROPIC_*` or `CLAUDE_*` variable in this process would reach the CLI and the shell tool. If
  one is present that this run does not override, `handle` yields `start` then `env_not_clean`
  (the names only) and does not start the CLI.
- System prompt: replaced with the fixed `simplifier-v1` instruction, not appended to the Claude
  Code prompt. The input text is the prompt (a user turn), never part of the system prompt.
- Limits: `max_turns` 4 (three tool rounds and the answer). The deadline is `ctx.budget.timeout_ms`
  for the whole run (suggested: 30 s); it is checked at each wait for the next SDK message, and on
  expiry the SDK generator is closed and the CLI is stopped as above.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from collections import deque
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import aclosing, suppress
from pathlib import Path
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, Transport, query
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
from claude_agent_sdk.types import McpHttpServerConfig
from workload_a2a.events import DEFAULT_TIMEOUT_S as DEFAULT_TIMEOUT_S
from workload_a2a.events import MAX_MESSAGE, event
from workload_a2a.events import timeout_s as timeout_s

from echo_claude_agent.mapping import EventMapper, error_event

PROMPT_VERSION = "simplifier-v1"
SYSTEM_PROMPT = "Rewrite in plain words. Short sentences. Keep every fact."
DEFAULT_ROUTE = "big-default"
MODEL_URL_VAR = "CHASSIS_MODEL_URL"
DEFAULT_MODEL_URL = "http://127.0.0.1:8090/v1"
TOKEN_VAR = "CHASSIS_API_TOKEN"
# The run-scoped capability the chassis checks on its remote listener (contract v8).
RUN_SECRET_HEADER = "x-chassis-run-secret"  # pragma: allowlist secret (a header name, not a secret)
TOOL_URL_VAR = "CHASSIS_TOOL_URL"
DEFAULT_TOOL_URL = "http://127.0.0.1:8090/mcp"
HOME_BASE_VAR = "CLAUDE_AGENT_HOME_BASE"
DEFAULT_HOME_BASE = "/tmp"
# The run's temp folder, in HOME. Short on purpose: the CLI gives a child process its temp root
# only when `<root>/claude-<uid>` fits in 44 bytes (else it uses `/tmp/claude-<uid>`), and
# `/tmp/claude-run-XXXXXXXX/tmp/claude-10002` is 41.
TMP_DIR = "tmp"
TEMP_VARS = ("TMPDIR", "CLAUDE_CODE_TMPDIR", "XDG_RUNTIME_DIR")
MCP_SERVER = "chassis"
# suggested: 3 tool rounds and the answer.
MAX_TURNS = 4
# The CLI refuses to start without a credential. The loopback proxy ignores it; the remote lane
# sets CHASSIS_API_TOKEN instead. Not a secret.
PLACEHOLDER_TOKEN = "chassis-loopback-placeholder"  # pragma: allowlist secret  (not a credential)
ALLOWED_TOOLS = ["Bash", "Read", "Write", f"mcp__{MCP_SERVER}"]
DISABLE_FLAGS = {
    "DISABLE_TELEMETRY": "1",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "DISABLE_AUTOUPDATER": "1",
    "DISABLE_ERROR_REPORTING": "1",
}
# Every name the run sets itself (so it overrides an inherited value) plus this workload's own
# setting. Any other ANTHROPIC_* or CLAUDE_* name in `os.environ` fails the guard.
OVERRIDDEN = frozenset(
    {
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_MODEL",
        "ANTHROPIC_SMALL_FAST_MODEL",
        "ANTHROPIC_CUSTOM_HEADERS",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
        "CLAUDE_CODE_TMPDIR",
        HOME_BASE_VAR,
    }
)
GUARDED_PREFIXES = ("ANTHROPIC_", "CLAUDE_")

log = logging.getLogger(__name__)

_cleanups: set[asyncio.Task[None]] = set()
"""The running stop-and-remove tasks. `asyncio.shield` keeps no strong reference to its inner
task, so this set does, until each one is done."""

query_fn: Callable[..., AsyncIterator[Any]] = query
"""Test-only hook: the SDK's `query`. A test replaces it with a fake that yields SDK message
objects, so no CLI starts. (This replaces the skeleton's `transport` hooks: the CLI is the HTTP
client here, and this process makes no model or MCP request of its own.) It is called with
`prompt`, `options`, and `transport`."""

transport_fn: Callable[[str, ClaudeAgentOptions], Transport] = SubprocessCLITransport
"""Builds the run's CLI transport (default: the SDK's subprocess transport, which the pinned SDK
does not export by a public name). `handle` passes it to `query_fn` and awaits its `close()`
before it removes `HOME`, so a test replaces it with a fake CLI to check the order."""


def unclean_names() -> list[str]:
    """Names of `ANTHROPIC_*` and `CLAUDE_*` variables in this process that the run would not
    override. Names only, never values."""
    return sorted(
        name for name in os.environ if name.startswith(GUARDED_PREFIXES) and name not in OVERRIDDEN
    )


def base_url(model_url: str) -> str:
    """The CLI adds `/v1/messages`, so the chassis URL loses its trailing `/v1`."""
    url = model_url.rstrip("/")
    return url.removesuffix("/v1")


def build_options(
    ctx: dict[str, Any],
    route: str,
    token: str | None,
    home: Path,
    cwd: Path,
    stderr: Callable[[str], None],
) -> ClaudeAgentOptions:
    traceparent = ctx.get("traceparent")
    run_secret = ctx.get("run_secret")  # the chassis's per-run check; never logged
    lines = [f"traceparent: {traceparent}"] if traceparent else []
    if run_secret:
        lines.append(f"{RUN_SECRET_HEADER}: {run_secret}")
    env = {
        "ANTHROPIC_BASE_URL": base_url(os.environ.get(MODEL_URL_VAR, DEFAULT_MODEL_URL)),
        "ANTHROPIC_AUTH_TOKEN": token or PLACEHOLDER_TOKEN,
        "ANTHROPIC_MODEL": route,
        "ANTHROPIC_SMALL_FAST_MODEL": route,
        # Always set (empty without either header), so an inherited value can never get through.
        "ANTHROPIC_CUSTOM_HEADERS": "\n".join(lines),
        **DISABLE_FLAGS,
        "HOME": str(home),
        **dict.fromkeys(TEMP_VARS, str(home / TMP_DIR)),
    }
    headers: dict[str, str] = {}
    if traceparent:
        headers["traceparent"] = str(traceparent)
    if run_secret:
        headers[RUN_SECRET_HEADER] = str(run_secret)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    server: McpHttpServerConfig = {
        "type": "http",
        "url": os.environ.get(TOOL_URL_VAR, DEFAULT_TOOL_URL),
        "headers": headers,
    }
    return ClaudeAgentOptions(
        env=env,
        cwd=cwd,
        system_prompt=SYSTEM_PROMPT,
        tools=["Bash", "Read", "Write"],
        allowed_tools=list(ALLOWED_TOOLS),
        # No prompt can be answered here, so a tool outside `allowed_tools` is denied. The remote
        # sandbox is the boundary for the three that are allowed.
        permission_mode="dontAsk",
        mcp_servers={MCP_SERVER: server},
        strict_mcp_config=True,
        setting_sources=[],
        max_turns=MAX_TURNS,
        include_partial_messages=True,
        stderr=stderr,
    )


async def handle(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    """`start`, `delta`s, `tool_call`s, `metrics`, `end`; or `start` then one `error`. Never
    raises for a model, tool, CLI, or timeout failure.
    """
    yield event("start", request_id=str(ctx.get("request_id", "")))
    names = unclean_names()
    if names:
        yield event(
            "error",
            code="env_not_clean",
            message="refusing to start the CLI: these variables are set: " + ", ".join(names),
            retryable=False,
        )
        return
    token = os.environ.get(TOKEN_VAR) or None
    run_secret = str(ctx.get("run_secret") or "") or None
    # The shell tool can print the CLI's env, which holds the token and `ctx.run_secret`, so
    # either can be in any event, not just errors.
    async with aclosing(_scrubbed(_run(input, ctx, token), token, run_secret)) as events:
        async for out in events:
            yield out


async def _scrubbed(
    events: AsyncGenerator[dict[str, Any], None], *values: str | None
) -> AsyncGenerator[dict[str, Any], None]:
    """The events with each of `values` (the token, the run secret) removed. An `error` message
    is scrubbed on its full text, then cut to `MAX_MESSAGE`. Delta text is scrubbed across
    chunks: the last `len(longest) - 1` chars are held back and sent, scrubbed, before the next
    other event. The joined delta text and the order of events are kept; only where the text is
    split into deltas can change."""
    hidden = _hidden(values)
    keep = len(hidden[0]) - 1 if hidden else 0
    held = ""
    async with aclosing(events) as stream:
        async for out in stream:
            if out["type"] == "delta" and keep:
                text = _scrub(held + str(out["text"]), hidden)
                held = text[-keep:]
                if text[:-keep]:
                    yield {**out, "text": text[:-keep]}
                continue
            if held:
                yield event("delta", text=held)
                held = ""
            yield _final(_scrub(out, hidden))
    if held:
        yield event("delta", text=held)


def _final(out: dict[str, Any]) -> dict[str, Any]:
    """An `error` gets its (already scrubbed) message cut to `MAX_MESSAGE`; others pass."""
    if out["type"] != "error":
        return out
    return {**out, "message": str(out["message"])[:MAX_MESSAGE]}


async def _run(
    input: dict[str, Any], ctx: dict[str, Any], token: str | None
) -> AsyncGenerator[dict[str, Any], None]:
    """One CLI run in a fresh home: the mapped events, then at most one `error`."""
    route = str(ctx.get("model_route") or DEFAULT_ROUTE)
    mapper = EventMapper(route)
    stderr_tail: deque[str] = deque(maxlen=3)
    home: Path | None = None
    transport: Transport | None = None
    stream: AsyncIterator[Any] | None = None
    final: dict[str, Any] | None = None
    try:
        home = _make_home()
        options = _options(ctx, route, token, home, stderr_tail.append)
        prompt = str(input.get("text") or "")
        transport = transport_fn(prompt, options)
        stream = query_fn(prompt=prompt, options=options, transport=transport)
        async with aclosing(_mapped(stream, mapper, timeout_s(ctx))) as events:
            async for out in events:
                if out["type"] == "error":
                    final = out
                else:
                    yield out
    except Exception as exc:
        final = _failure(exc, stderr_tail, token, str(ctx.get("run_secret") or "") or None)
    finally:
        try:
            await _close(stream)
        finally:
            # Shielded: a second cancel must not skip the stop or leave `HOME` on disk. On a second
            # cancel the handle returns at once, and the stop and the removal finish after it.
            cleanup = asyncio.create_task(_end_run(transport, home))
            _cleanups.add(cleanup)
            cleanup.add_done_callback(_cleanups.discard)
            await asyncio.shield(cleanup)
    if final is not None:
        yield final
    elif not mapper.done:
        yield event(
            "error", code="model_error", message="the run ended with no result", retryable=False
        )


def _options(
    ctx: dict[str, Any],
    route: str,
    token: str | None,
    home: Path,
    stderr: Callable[[str], None],
) -> ClaudeAgentOptions:
    cwd = home / "work"
    cwd.mkdir()
    (home / TMP_DIR).mkdir(mode=0o700)  # the CLI wants a private runtime dir
    return build_options(ctx, route, token, home, cwd, stderr)


async def _mapped(
    stream: AsyncIterator[Any], mapper: EventMapper, seconds: float
) -> AsyncGenerator[dict[str, Any], None]:
    """The events for each SDK message until the stream ends or the deadline passes."""
    deadline = asyncio.get_running_loop().time() + seconds
    # Read to the end of the stream, also after the result: the CLI then exits by itself and
    # has written its last files before the run dir is removed.
    while True:
        try:
            async with asyncio.timeout_at(deadline):
                message = await anext(stream)
        except StopAsyncIteration:
            return
        except Exception:
            if mapper.done:  # the result is in; a late exit error adds nothing
                return
            raise
        for out in mapper.map(message):
            yield out


def _failure(exc: BaseException, stderr_tail: deque[str], *values: str | None) -> dict[str, Any]:
    """The `error` for an exception; a `cli_error` gets the last stderr lines. The log line has
    `values` (the token, the run secret) removed. The message is not cut here: `_scrubbed`
    removes them from the full text, then cuts it to `MAX_MESSAGE`."""
    error = error_event(exc)
    if error["code"] != "cli_error" or not stderr_tail:
        return error
    hidden = _hidden(values)
    tail = [_scrub(line, hidden) for line in stderr_tail]
    log.warning("the CLI failed; its last stderr lines: %s", " | ".join(tail))
    return {**error, "message": str(error["message"]) + " | " + " | ".join(stderr_tail)}


def _make_home() -> Path:
    base = Path(os.environ.get(HOME_BASE_VAR) or DEFAULT_HOME_BASE)
    base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="claude-run-", dir=base))


async def _close(stream: AsyncIterator[Any] | None) -> None:
    """Close the SDK generator, which stops the CLI. Never raises."""
    aclose = getattr(stream, "aclose", None)
    if aclose is not None:
        with suppress(Exception):
            await aclose()


async def _end_run(transport: Transport | None, home: Path | None) -> None:
    """Stop the CLI, wait for it to exit, then remove `HOME`. Never raises."""
    if transport is not None:
        try:
            # Bounded by the SDK (about 20 s): stdin EOF, then SIGTERM, then SIGKILL.
            await transport.close()
        except Exception as exc:
            log.warning("stopping the CLI failed: %s", type(exc).__name__)
    if home is not None:
        _remove_home(home)


def _remove_home(home: Path) -> None:
    """Delete `HOME`; log each path that cannot be removed. Never raises."""

    def report(_func: Callable[..., Any], path: str, exc: BaseException) -> None:
        log.warning("could not remove %s from the run home: %s", path, type(exc).__name__)

    shutil.rmtree(home, onexc=report)


def _hidden(values: tuple[str | None, ...]) -> tuple[str, ...]:
    """The values to scrub, empty ones dropped, longest first so none leaves part of another."""
    return tuple(sorted({v for v in values if v}, key=len, reverse=True))


def _scrub(value: Any, hidden: tuple[str, ...]) -> Any:
    """A copy of `value` with each of `hidden` replaced in every string, at any depth."""
    if not hidden:
        return value
    if isinstance(value, str):
        for item in hidden:
            value = value.replace(item, "[redacted]")
        return value
    if isinstance(value, dict):
        return {_scrub(key, hidden): _scrub(item, hidden) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_scrub(item, hidden) for item in value]
    return value
