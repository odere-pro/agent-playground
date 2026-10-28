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
  to MCP, so they are set per server in `headers`.
- Credentials: `CHASSIS_API_TOKEN`, when set and not empty, is `ANTHROPIC_AUTH_TOKEN` and the MCP
  bearer token; else the model gets a placeholder (the loopback proxy ignores it). No provider key
  is read or accepted: see the guard below.
- `HOME` is a fresh directory per run under `CLAUDE_AGENT_HOME_BASE` (default `/tmp`), removed
  after the run. The work dir (`cwd`) is inside it.
- The environment guard: the SDK merges `os.environ` into the CLI's environment, so any
  `ANTHROPIC_*` or `CLAUDE_*` variable in this process would reach the CLI and the shell tool. If
  one is present that this run does not override, `handle` yields `start` then `env_not_clean`
  (the names only) and does not start the CLI.
- System prompt: replaced with the fixed `simplifier-v1` instruction, not appended to the Claude
  Code prompt. The input text is the prompt (a user turn), never part of the system prompt.
- Limits: `max_turns` 4 (three tool rounds and the answer). The deadline is `ctx.budget.timeout_ms`
  for the whole run (suggested: 30 s); it is checked at each wait for the next SDK message, and on
  expiry the SDK generator is closed, which stops the CLI.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from collections import deque
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, query
from claude_agent_sdk.types import McpHttpServerConfig

from echo_claude_agent.mapping import EventMapper, error_event, event

PROMPT_VERSION = "simplifier-v1"
SYSTEM_PROMPT = "Rewrite in plain words. Short sentences. Keep every fact."
DEFAULT_ROUTE = "big-default"
MODEL_URL_VAR = "CHASSIS_MODEL_URL"
DEFAULT_MODEL_URL = "http://127.0.0.1:8090/v1"
TOKEN_VAR = "CHASSIS_API_TOKEN"
TOOL_URL_VAR = "CHASSIS_TOOL_URL"
DEFAULT_TOOL_URL = "http://127.0.0.1:8090/mcp"
HOME_BASE_VAR = "CLAUDE_AGENT_HOME_BASE"
DEFAULT_HOME_BASE = "/tmp"
MCP_SERVER = "chassis"
# suggested: 30 s when `ctx.budget.timeout_ms` is not set; the epic gives none.
DEFAULT_TIMEOUT_S = 30.0
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
        HOME_BASE_VAR,
    }
)
GUARDED_PREFIXES = ("ANTHROPIC_", "CLAUDE_")

query_fn: Callable[..., AsyncIterator[Any]] = query
"""Test-only hook: the SDK's `query`. A test replaces it with a fake that yields SDK message
objects, so no CLI starts. (This replaces the skeleton's `transport` hooks: the CLI is the HTTP
client here, and this process makes no model or MCP request of its own.)"""


def timeout_s(ctx: dict[str, Any]) -> float:
    budget = ctx.get("budget")
    if isinstance(budget, dict) and budget.get("timeout_ms"):
        return float(budget["timeout_ms"]) / 1000
    return DEFAULT_TIMEOUT_S


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
    env = {
        "ANTHROPIC_BASE_URL": base_url(os.environ.get(MODEL_URL_VAR, DEFAULT_MODEL_URL)),
        "ANTHROPIC_AUTH_TOKEN": token or PLACEHOLDER_TOKEN,
        "ANTHROPIC_MODEL": route,
        "ANTHROPIC_SMALL_FAST_MODEL": route,
        # Always set (empty without a traceparent), so an inherited value can never get through.
        "ANTHROPIC_CUSTOM_HEADERS": f"traceparent: {traceparent}" if traceparent else "",
        **DISABLE_FLAGS,
        "HOME": str(home),
    }
    headers: dict[str, str] = {}
    if traceparent:
        headers["traceparent"] = str(traceparent)
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
    route = str(ctx.get("model_route") or DEFAULT_ROUTE)
    token = os.environ.get(TOKEN_VAR) or None
    mapper = EventMapper(route)
    stderr_tail: deque[str] = deque(maxlen=3)
    home: Path | None = None
    stream: AsyncIterator[Any] | None = None
    final: dict[str, Any] | None = None
    try:
        home = _make_home()
        cwd = home / "work"
        cwd.mkdir()
        options = build_options(ctx, route, token, home, cwd, stderr_tail.append)
        stream = query_fn(prompt=str(input.get("text") or ""), options=options)
        deadline = asyncio.get_running_loop().time() + timeout_s(ctx)
        # Read to the end of the stream, also after the result: the CLI then exits by itself and
        # has written its last files before the run dir is removed.
        while True:
            try:
                async with asyncio.timeout_at(deadline):
                    message = await anext(stream)
            except StopAsyncIteration:
                break
            except Exception:
                if mapper.done:  # the result is in; a late exit error adds nothing
                    break
                raise
            for out in mapper.map(message):
                if out["type"] == "error":
                    final = out
                else:
                    yield out
    except Exception as exc:
        final = error_event(exc)
        if final["code"] == "cli_error" and stderr_tail:
            final["message"] += " | " + " | ".join(stderr_tail)
    finally:
        await _close(stream)
        if home is not None:
            shutil.rmtree(home, ignore_errors=True)
    if final is not None:
        yield _scrub(final, token)
    elif not mapper.done:
        yield event(
            "error", code="model_error", message="the run ended with no result", retryable=False
        )


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


def _scrub(error: dict[str, Any], token: str | None) -> dict[str, Any]:
    if token:
        error["message"] = str(error["message"]).replace(token, "[redacted]")
    return error
