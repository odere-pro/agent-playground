"""The MCP server: one write tool, the idempotency key, structured results (plan 2.8).

In-process FastMCP client, no socket. Each call starts at most one tiny child Python process.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest
from code_runner import isolation
from code_runner.runner import Limits, RunnerError, RunResult
from code_runner.server import TOOL_NAME, ResultCache, create_server
from fastmcp import Client
from fastmcp.exceptions import ToolError

RANDOM = "import os\nprint(os.urandom(8).hex())"


@pytest.fixture
def server(tmp_path: Path) -> Any:
    return create_server(limits=Limits(output_cap_bytes=1024), tmp_root=tmp_path)


async def _call(
    server: Any, args: dict[str, Any], key: str | None, *, raise_on_error: bool = True
) -> Any:
    meta = {"idempotency_key": key} if key is not None else None
    async with Client(server) as client:
        return await client.call_tool(TOOL_NAME, args, meta=meta, raise_on_error=raise_on_error)


async def test_code_runner_lists_one_write_tool(server: Any) -> None:
    async with Client(server) as client:
        tools = await client.list_tools()
    assert [t.name for t in tools] == ["run_python"]
    tool = tools[0]
    assert tool.annotations is not None
    assert tool.annotations.read_only_hint is False
    props = tool.input_schema["properties"]
    assert props["timeout_s"]["minimum"] == 1
    assert props["timeout_s"]["maximum"] == 10
    assert props["timeout_s"]["default"] == 5
    assert tool.output_schema is not None
    assert set(tool.output_schema["properties"]) == {
        "stdout",
        "stderr",
        "exit_code",
        "timed_out",
        "truncated",
    }


async def test_code_runner_call_returns_structured_stdout(server: Any) -> None:
    result = await _call(server, {"code": "print(6 * 7)"}, "k-1")
    assert result.structured_content == {
        "stdout": "42\n",
        "stderr": "",
        "exit_code": 0,
        "timed_out": False,
        "truncated": False,
    }


async def test_code_runner_timeout_sets_timed_out(server: Any) -> None:
    result = await _call(server, {"code": "while True:\n    pass", "timeout_s": 1}, "k-slow")
    assert result.structured_content["timed_out"] is True


async def test_code_runner_same_key_returns_the_same_result_without_a_second_run(
    server: Any,
) -> None:
    first = await _call(server, {"code": RANDOM}, "k-same")
    second = await _call(server, {"code": RANDOM}, "k-same")
    other = await _call(server, {"code": RANDOM}, "k-other")
    assert first.structured_content == second.structured_content
    assert other.structured_content["stdout"] != first.structured_content["stdout"]


async def test_code_runner_key_in_the_argument_is_the_fallback(server: Any) -> None:
    first = await _call(server, {"code": RANDOM, "idempotency_key": "k-arg"}, None)
    second = await _call(server, {"code": RANDOM}, "k-arg")
    assert first.structured_content == second.structured_content


async def test_code_runner_call_without_a_key_is_refused(server: Any) -> None:
    with pytest.raises(ToolError, match="idempotency_key_required"):
        await _call(server, {"code": "print(1)"}, None)


async def test_code_runner_meta_and_argument_keys_must_agree(server: Any) -> None:
    with pytest.raises(ToolError, match="bad_arguments"):
        await _call(server, {"code": "print(1)", "idempotency_key": "a"}, "b")


async def test_code_runner_key_reused_with_other_arguments_is_refused(server: Any) -> None:
    await _call(server, {"code": "print(1)"}, "k-reuse")
    with pytest.raises(ToolError, match="bad_arguments"):
        await _call(server, {"code": "print(2)"}, "k-reuse")


async def test_code_runner_oversized_code_is_bad_arguments(tmp_path: Path) -> None:
    small = create_server(limits=Limits(max_code_bytes=8), tmp_root=tmp_path)
    with pytest.raises(ToolError, match="bad_arguments"):
        await _call(small, {"code": "print('long enough')"}, "k-big")


async def test_code_runner_timeout_out_of_bounds_is_refused(server: Any) -> None:
    with pytest.raises(ToolError):
        await _call(server, {"code": "print(1)", "timeout_s": 60}, "k-bounds")


async def test_code_runner_forgets_the_oldest_key_past_the_cache_size(tmp_path: Path) -> None:
    small = create_server(limits=Limits(), tmp_root=tmp_path, cache_size=1)
    first = await _call(small, {"code": RANDOM}, "k-a")
    await _call(small, {"code": RANDOM}, "k-b")
    again = await _call(small, {"code": RANDOM}, "k-a")
    assert again.structured_content["stdout"] != first.structured_content["stdout"]


async def test_code_runner_health_route(server: Any) -> None:
    transport = httpx.ASGITransport(app=server.http_app(path="/mcp"))
    async with httpx.AsyncClient(transport=transport, base_url="http://code-runner") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


OK = RunResult(stdout="ok\n", stderr="", exit_code=0, timed_out=False, truncated=False)


async def test_code_runner_a_cancelled_first_caller_does_not_fail_the_waiters() -> None:
    cache = ResultCache(4)
    release = asyncio.Event()
    runs = 0

    async def run() -> RunResult:
        nonlocal runs
        runs += 1
        await release.wait()
        return OK

    first = asyncio.create_task(cache.get_or_run("k", "f", run))
    await asyncio.sleep(0)
    waiter = asyncio.create_task(cache.get_or_run("k", "f", run))
    await asyncio.sleep(0)
    first.cancel()
    await asyncio.sleep(0)
    release.set()
    assert await waiter == OK
    assert first.cancelled()
    assert runs == 1
    assert await cache.get_or_run("k", "f", run) == OK
    assert runs == 1


async def test_code_runner_a_run_with_no_caller_left_is_stopped() -> None:
    cache = ResultCache(4)
    stopped = asyncio.Event()

    async def run() -> RunResult:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            stopped.set()
            raise
        return OK

    first = asyncio.create_task(cache.get_or_run("k", "f", run))
    await asyncio.sleep(0)
    first.cancel()
    await asyncio.wait_for(stopped.wait(), 1)
    # The key is free again: a retry runs.
    assert await cache.get_or_run("k", "f", _ok) == OK


async def _ok() -> RunResult:
    return OK


async def test_code_runner_a_failed_run_fails_every_waiter_and_frees_the_key() -> None:
    cache = ResultCache(4)

    async def boom() -> RunResult:
        await asyncio.sleep(0)
        raise RunnerError("run_failed", "x")

    results = await asyncio.gather(
        cache.get_or_run("k", "f", boom), cache.get_or_run("k", "f", boom), return_exceptions=True
    )
    assert all(isinstance(r, RunnerError) and r.code == "run_failed" for r in results)
    assert await cache.get_or_run("k", "f", _ok) == OK


async def test_code_runner_health_fails_once_isolation_is_lost(
    server: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(isolation, "_lost", True)
    app = server.http_app(path="/mcp", stateless_http=True)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://code-runner") as client:
        response = await client.get("/health")
    assert response.status_code == 503
    assert response.json() == {"status": "isolation_lost"}
