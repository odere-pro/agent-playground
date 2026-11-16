"""The per-run `HOME` is gone when `handle` returns, however the run ends (057 H-19).

The SDK's public `query()` wraps its inner generator in a plain `async for`, so closing it does
not stop the CLI: the process keeps writing its transcript, `.claude.json`, and MCP logs into
`HOME`. The fake here does the same. Its "CLI" is a task that writes into `HOME` until the
transport's `close()` stops it, or for a moment on its own. No CLI starts, no socket opens.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import aclosing
from pathlib import Path
from typing import Any, cast

import pytest
from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock
from echo_claude_agent import handle

handle_module: Any = importlib.import_module("echo_claude_agent.handle")

CTX: dict[str, Any] = {
    "request_id": "req-1",
    "budget": {"max_tokens": 2000, "timeout_ms": 30000},
    "model_route": "big-default",
}
WRITE_EVERY_S = 0.005
MOMENT_S = 0.3  # how long the fake CLI writes on its own when nothing stops it


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    for name in list(os.environ):
        if name.startswith(("ANTHROPIC_", "CLAUDE", "CHASSIS_")):
            monkeypatch.delenv(name)
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    base = tmp_path / "base"
    monkeypatch.setenv("CLAUDE_AGENT_HOME_BASE", str(base))
    # As the stateless lane check and the pod do: the process's temp folder is the scratch base.
    monkeypatch.setenv("TMPDIR", str(base))
    return base


def cli_env(options: ClaudeAgentOptions) -> dict[str, str]:
    """The CLI's environment as the SDK builds it: `os.environ`, then `options.env` on top."""
    return {**os.environ, **options.env}


class WritingCLI:
    """A fake CLI process: it writes the files the real CLI writes into `HOME`, recreating
    folders as the CLI does, until `close()` stops it or `MOMENT_S` passes. Like the Linux CLI,
    it also writes its temp files: `cc-socks` under `XDG_RUNTIME_DIR`, else its temp root, and
    `claude-<uid>/fswatch-probe-*` under its temp root (`CLAUDE_CODE_TMPDIR`, else `TMPDIR`)."""

    def __init__(self, options: ClaudeAgentOptions) -> None:
        env = cli_env(options)
        self.home = Path(env["HOME"])
        self.tmp = Path(env.get("CLAUDE_CODE_TMPDIR") or env["TMPDIR"])
        self.runtime = Path(env.get("XDG_RUNTIME_DIR") or self.tmp)
        self.stop = asyncio.Event()
        self.task = asyncio.create_task(self._write())

    async def _write(self) -> None:
        loop = asyncio.get_running_loop()
        until = loop.time() + MOMENT_S
        n = 0
        while not self.stop.is_set() and loop.time() < until:
            self._files(n)
            n += 1
            await asyncio.sleep(WRITE_EVERY_S)
        self._files(n)  # the last flush before exit

    def _files(self, n: int) -> None:
        projects = self.home / ".claude" / "projects" / "work"
        logs = self.home / ".cache" / "claude-cli" / "mcp-logs-chassis"
        projects.mkdir(parents=True, exist_ok=True)
        logs.mkdir(parents=True, exist_ok=True)
        (projects / "s-1.jsonl").write_text(f"turn {n}\n")
        (logs / "log.txt").write_text(f"line {n}\n")
        (self.home / ".claude.json").write_text("{}")
        socks = self.runtime / "cc-socks"
        probe = self.tmp / f"claude-{os.getuid()}" / "fswatch-probe-aBc123"
        socks.mkdir(parents=True, exist_ok=True)
        probe.mkdir(parents=True, exist_ok=True)
        (socks / "4242.sock").write_text("")
        (probe / "probe").write_text(f"probe {n}\n")


class FakeTransport:
    """The `Transport` `handle` builds per run: `close()` returns once the CLI has exited."""

    def __init__(self, prompt: Any, options: ClaudeAgentOptions) -> None:
        self.cli = WritingCLI(options)
        self.closed = False

    async def close(self) -> None:
        self.cli.stop.set()
        await self.cli.task
        self.closed = True


def fake_query(*, hang: bool) -> Callable[..., AsyncIterator[Any]]:
    """Stands in for the SDK's `query`: its close does not reach the CLI, like the SDK's."""

    def query(*, prompt: Any, options: ClaudeAgentOptions, transport: Any = None) -> Any:
        if transport is None:  # no transport given: the run owns a CLI no one can stop
            WritingCLI(options)
        return messages(hang)

    return query


async def messages(hang: bool) -> AsyncIterator[Any]:
    yield AssistantMessage(content=[TextBlock("Hello.")], model="big-default")
    if hang:
        await asyncio.sleep(3600)
    yield ResultMessage(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=1,
        session_id="s-1",
        usage={"input_tokens": 1, "output_tokens": 1},
    )


def install(monkeypatch: pytest.MonkeyPatch, *, hang: bool = False) -> None:
    monkeypatch.setattr(handle_module, "query_fn", fake_query(hang=hang))
    monkeypatch.setattr(handle_module, "transport_fn", FakeTransport, raising=False)


async def after_the_moment() -> None:
    """Give a CLI no one stopped the time to finish writing, so a leak shows on disk."""
    await asyncio.sleep(MOMENT_S + 0.1)


def call() -> AsyncGenerator[dict[str, Any], None]:
    """`handle` as the async generator it is (its annotation says `AsyncIterator`)."""
    stream: Any = handle({"text": "simplify: Hello.", "data": {}}, CTX)
    return cast(AsyncGenerator[dict[str, Any], None], stream)


def mode_of(path: Path) -> int:
    return path.stat().st_mode & 0o777


def left_on_disk(base: Path) -> list[Path]:
    return sorted(base.rglob("*"))


async def test_handle_leaves_no_home_file_when_the_generator_is_closed_after_the_first_event(
    monkeypatch: pytest.MonkeyPatch, clean_env: Path
) -> None:
    """The A2A server closes the generator early: the CLI has exited and `HOME` is gone."""
    install(monkeypatch)

    stream = call()
    first = await anext(stream)
    second = await anext(stream)  # the CLI has started and is writing
    await stream.aclose()
    await after_the_moment()

    assert (first["type"], second["type"]) == ("start", "delta")
    assert left_on_disk(clean_env) == []


async def test_handle_leaves_no_home_file_when_the_generator_is_drained(
    monkeypatch: pytest.MonkeyPatch, clean_env: Path
) -> None:
    """A run read to its `end` stops the CLI before `HOME` goes."""
    install(monkeypatch)

    events = [e async for e in handle({"text": "simplify: Hello.", "data": {}}, CTX)]
    await after_the_moment()

    assert events[-1]["type"] == "end"
    assert left_on_disk(clean_env) == []


async def test_handle_leaves_no_home_file_when_the_task_is_cancelled_mid_run(
    monkeypatch: pytest.MonkeyPatch, clean_env: Path
) -> None:
    """A cancelled call, waiting on the CLI, still stops it before `HOME` goes."""
    install(monkeypatch, hang=True)
    started = asyncio.Event()

    async def consume() -> None:
        async with aclosing(call()) as stream:
            async for out in stream:
                if out["type"] == "delta":
                    started.set()

    task = asyncio.create_task(consume())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await after_the_moment()

    assert left_on_disk(clean_env) == []


async def test_handle_leaves_no_cli_temp_file_when_tmpdir_and_xdg_runtime_dir_are_inherited(
    monkeypatch: pytest.MonkeyPatch, clean_env: Path
) -> None:
    """The process's `TMPDIR` and `XDG_RUNTIME_DIR` are the scratch base (as in the lane check
    on Linux): the CLI's `cc-socks` and `claude-<uid>/fswatch-probe-*` still go with `HOME`."""
    runtime = clean_env / "run-user"
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    install(monkeypatch)

    events = [e async for e in call()]
    await after_the_moment()

    assert events[-1]["type"] == "end"
    assert left_on_disk(clean_env) == []


async def test_handle_points_every_cli_temp_root_into_the_run_home_when_it_builds_the_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`TMPDIR`, `CLAUDE_CODE_TMPDIR`, and `XDG_RUNTIME_DIR` are one private folder in `HOME`."""
    seen: list[ClaudeAgentOptions] = []

    def query(*, prompt: Any, options: ClaudeAgentOptions, transport: Any = None) -> Any:
        seen.append(options)
        return messages(hang=False)

    monkeypatch.setattr(handle_module, "query_fn", query)
    monkeypatch.setattr(handle_module, "transport_fn", FakeTransport)

    stream = call()
    await anext(stream)
    await anext(stream)  # the run is live, its folders exist
    env = seen[0].env
    tmp = Path(env["TMPDIR"])
    mode = mode_of(tmp)
    await stream.aclose()

    assert tmp.parent == Path(env["HOME"])
    assert env["CLAUDE_CODE_TMPDIR"] == env["XDG_RUNTIME_DIR"] == str(tmp)
    assert mode == 0o700


class SlowTransport(FakeTransport):
    """A transport whose `close()` waits for the test to let the CLI exit."""

    closing = asyncio.Event()
    release = asyncio.Event()

    async def close(self) -> None:
        type(self).closing.set()
        await type(self).release.wait()
        await super().close()


async def test_handle_leaves_no_home_file_when_cancelled_again_while_the_cli_stops(
    monkeypatch: pytest.MonkeyPatch, clean_env: Path
) -> None:
    """A second cancel during `close()` returns at once; the held cleanup task still removes
    `HOME` once the CLI exits."""
    install(monkeypatch, hang=True)
    monkeypatch.setattr(SlowTransport, "closing", asyncio.Event())
    monkeypatch.setattr(SlowTransport, "release", asyncio.Event())
    monkeypatch.setattr(handle_module, "transport_fn", SlowTransport)
    started = asyncio.Event()

    async def consume() -> None:
        async with aclosing(call()) as stream:
            async for out in stream:
                if out["type"] == "delta":
                    started.set()

    task = asyncio.create_task(consume())
    await started.wait()
    task.cancel()
    await SlowTransport.closing.wait()
    task.cancel()  # the second cancel, while `close()` waits
    with pytest.raises(asyncio.CancelledError):
        await task
    pending = set(handle_module._cleanups)
    SlowTransport.release.set()
    await asyncio.gather(*pending)

    assert len(pending) == 1, "the handle holds its cleanup task until it is done"
    assert handle_module._cleanups == set()
    assert left_on_disk(clean_env) == []


async def test_handle_logs_a_warning_when_the_home_cannot_be_removed(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A failed delete is logged with its path, never dropped in silence."""
    install(monkeypatch)

    def failing_rmtree(path: Any, *args: Any, onexc: Any = None, **kw: Any) -> None:
        assert onexc is not None, "rmtree must report each failure"
        onexc(os.unlink, str(Path(path) / ".claude.json"), PermissionError("denied"))

    monkeypatch.setattr(handle_module.shutil, "rmtree", failing_rmtree)

    with caplog.at_level(logging.WARNING, logger=handle_module.log.name):
        events = [e async for e in handle({"text": "simplify: Hello.", "data": {}}, CTX)]

    assert events[-1]["type"] == "end"
    assert any(".claude.json" in r.getMessage() for r in caplog.records)


async def test_transport_fn_is_the_sdk_subprocess_transport_when_no_test_replaces_it() -> None:
    """The default transport starts the CLI in the SDK wheel; `handle` closes it per run."""
    from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

    hook = handle_module.transport_fn

    assert hook is SubprocessCLITransport
