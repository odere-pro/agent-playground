"""The MCP server: one write tool, `run_python` (plan section 2.8).

No `from __future__ import annotations` here: the tool's argument bounds come from the
`Limits` given to `create_server`, and FastMCP reads them from the live annotations.
"""

import asyncio
import hashlib
import logging
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated, Any, Protocol

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_context
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from code_runner import isolation
from code_runner.runner import DEFAULT_LIMITS, Limits, RunnerError, RunResult, run_python

TOOL_NAME = "run_python"
DEFAULT_TIMEOUT_S = 5  # suggested (plan 2.8)
MIN_TIMEOUT_S = 1  # suggested (plan 2.8)
CACHE_SIZE = 256  # suggested: results kept by idempotency key, in memory
MAX_KEY_CHARS = 256  # suggested
# One run at a time keeps memory predictable in a 256Mi pod, and the isolation sweep needs it:
# it stops every process started since the call began. Do not raise it.
MAX_CONCURRENT = 1

log = logging.getLogger("code_runner")


class Runner(Protocol):
    def __call__(
        self, code: str, timeout_s: float, *, limits: Limits, tmp_root: str | Path | None
    ) -> Awaitable[RunResult]: ...


def fingerprint(code: str, timeout_s: int) -> str:
    return hashlib.sha256(f"{timeout_s}\0{code}".encode()).hexdigest()


class _Running:
    def __init__(self, fingerprint: str, task: "asyncio.Task[RunResult]") -> None:
        self.fingerprint = fingerprint
        self.task = task
        self.callers = 0


class ResultCache:
    """The last `size` results by idempotency key; a running call is shared, never run twice.

    In memory: a restart forgets every key (a recorded limit). A key bound to other
    arguments is refused with `bad_arguments`, so a reused key cannot return a result for
    different code. The run is a task of its own: a caller that is cancelled leaves it to the
    other callers of the same key. When the last caller is gone, the run is cancelled (its
    child is killed) and the key is free, so a retry runs again.
    """

    def __init__(self, size: int) -> None:
        self.size = size
        self._done: OrderedDict[str, tuple[str, RunResult]] = OrderedDict()
        self._running: dict[str, _Running] = {}

    async def get_or_run(
        self, key: str, fingerprint: str, run: Callable[[], Awaitable[RunResult]]
    ) -> RunResult:
        if key in self._done:
            seen, result = self._done[key]
            self._check(seen, fingerprint)
            self._done.move_to_end(key)
            return result
        entry = self._running.get(key)
        if entry is None:
            entry = _Running(fingerprint, asyncio.create_task(self._run(key, fingerprint, run)))
            self._running[key] = entry
        else:
            self._check(entry.fingerprint, fingerprint)
        entry.callers += 1
        try:
            return await asyncio.shield(entry.task)
        except asyncio.CancelledError:
            if entry.task.cancelled() or entry.callers > 1:
                raise
            # The last caller left: stop the run and free the key for a retry.
            if self._running.get(key) is entry:
                del self._running[key]
            entry.task.cancel()
            raise
        finally:
            entry.callers -= 1

    async def _run(
        self, key: str, fingerprint: str, run: Callable[[], Awaitable[RunResult]]
    ) -> RunResult:
        try:
            result = await run()
        except BaseException:
            self._forget(key)
            raise
        self._forget(key)
        if self.size > 0:
            self._done[key] = (fingerprint, result)
            while len(self._done) > self.size:
                self._done.popitem(last=False)
        return result

    def _forget(self, key: str) -> None:
        entry = self._running.get(key)
        if entry is not None and entry.task is asyncio.current_task():
            del self._running[key]

    @staticmethod
    def _check(seen: str, fingerprint: str) -> None:
        if seen != fingerprint:
            raise RunnerError("bad_arguments", "idempotency key reused with other arguments")


def _meta_key() -> str | None:
    context = get_context()
    request = context.request_context
    meta: dict[str, Any] | None = request.meta if request is not None else None
    if not meta:
        return None
    value = meta.get("idempotency_key")
    if value is None:
        return None
    if not isinstance(value, str):
        raise RunnerError("bad_arguments", "_meta.idempotency_key must be a string")
    return value


def resolve_key(argument: str | None) -> str:
    """`_meta.idempotency_key` first; the `idempotency_key` argument is the fallback (2.7)."""
    meta = _meta_key()
    if meta is not None and argument is not None and meta != argument:
        raise RunnerError("bad_arguments", "_meta and argument idempotency keys differ")
    key = meta if meta is not None else argument
    if not key:
        raise RunnerError("idempotency_key_required", f"{TOOL_NAME} is a write tool")
    if len(key) > MAX_KEY_CHARS:
        raise RunnerError("bad_arguments", f"idempotency key is over {MAX_KEY_CHARS} characters")
    return key


def create_server(
    *,
    limits: Limits = DEFAULT_LIMITS,
    tmp_root: str | Path | None = None,
    cache_size: int = CACHE_SIZE,
    runner: Runner = run_python,
) -> FastMCP:
    """Build the server. `tmp_root` is where each call's fresh directory goes (default: TMPDIR)."""
    mcp = FastMCP(
        "code-runner",
        instructions=(
            f"{TOOL_NAME} runs Python in a sandbox with no network. "
            "Print what you need: the result has stdout, stderr, exit_code, timed_out, truncated."
        ),
    )
    cache = ResultCache(cache_size)
    slots = asyncio.Semaphore(MAX_CONCURRENT)
    max_timeout = int(limits.max_timeout_s)

    @mcp.tool(
        name=TOOL_NAME,
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=False,
        ),
    )
    async def run_python_tool(
        code: Annotated[str, Field(description="Python source, run as __main__.")],
        timeout_s: Annotated[
            int,
            Field(ge=MIN_TIMEOUT_S, le=max_timeout, description="Wall-clock limit in seconds."),
        ] = DEFAULT_TIMEOUT_S,
        idempotency_key: Annotated[
            str | None,
            Field(description="Fallback when the client cannot send _meta.idempotency_key."),
        ] = None,
    ) -> RunResult:
        """Run Python 3.12 (standard library only) with no network and no files kept.

        Each call runs in a fresh empty directory that is removed afterwards. stdout and
        stderr are each cut at a fixed size; `truncated` says so. A write tool: the same
        idempotency key returns the first result without running the code again.
        """
        try:
            key = resolve_key(idempotency_key)

            async def run() -> RunResult:
                async with slots:
                    started = time.monotonic()
                    result = await runner(code, timeout_s, limits=limits, tmp_root=tmp_root)
                log.info(
                    "run code_bytes=%d exit_code=%d timed_out=%s truncated=%s seconds=%.2f",
                    len(code.encode("utf-8")),
                    result.exit_code,
                    result.timed_out,
                    result.truncated,
                    time.monotonic() - started,
                )
                return result

            return await cache.get_or_run(key, fingerprint(code, timeout_s), run)
        except RunnerError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_request: Request) -> JSONResponse:
        # A process from a call that could not be stopped: let the pod be restarted.
        if isolation.lost():
            return JSONResponse({"status": "isolation_lost"}, status_code=503)
        return JSONResponse({"status": "ok"})

    return mcp
