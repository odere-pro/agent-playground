"""Run one piece of Python in a bounded child process (plan section 2.8).

These limits are the tool's own hygiene, so one call stays small and predictable. They are
not the security boundary: the sandbox pod is (gVisor, no egress, the PID and memory limits,
the `/tmp` size cap). Never run this outside such a pod in a deployed lane.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import signal
import stat
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from code_runner import isolation

# Sets the rlimits, then runs main.py from the fresh working directory. Passed with `-c`, so
# the child needs no file of ours. Off Linux (a developer's Mac) a limit the kernel refuses is
# skipped; on Linux it stops the run with exit code 125.
_BOOTSTRAP = """\
import resource, sys
def _limit(name, soft, hard):
    try:
        resource.setrlimit(getattr(resource, name), (soft, hard))
    except (ValueError, OSError, AttributeError):
        if sys.platform == "linux":
            sys.stderr.write("code-runner: cannot set " + name + "\\n")
            sys.exit(125)
_cpu, _mem, _nofile, _fsize, _nproc = (int(a) for a in sys.argv[1:6])
_limit("RLIMIT_CPU", _cpu, _cpu + 1)
_limit("RLIMIT_AS", _mem, _mem)
_limit("RLIMIT_NOFILE", _nofile, _nofile)
_limit("RLIMIT_FSIZE", _fsize, _fsize)
if _nproc > 0:
    _limit("RLIMIT_NPROC", _nproc, _nproc)
with open("main.py", encoding="utf-8") as _f:
    _src = _f.read()
sys.argv = ["main.py"]
del resource, _limit, _cpu, _mem, _nofile, _fsize, _nproc, _f
exec(compile(_src, "main.py", "exec"), {"__name__": "__main__", "__builtins__": __builtins__})
"""

# After the child exits or is killed, how long to wait for the pipes to close.
_DRAIN_S = 2.0  # suggested
_READ_CHUNK = 65536  # suggested


@dataclass(frozen=True)
class Limits:
    """Per-call limits. Values are suggested in plan section 2.8 unless noted."""

    max_code_bytes: int = 64 * 1024
    max_timeout_s: float = 10
    output_cap_bytes: int = 64 * 1024  # per stream
    address_space_bytes: int = 256 * 1024 * 1024
    open_files: int = 64  # suggested: not in the plan
    file_size_bytes: int = 8 * 1024 * 1024  # suggested: not in the plan


DEFAULT_LIMITS = Limits()

log = logging.getLogger("code_runner")


@dataclass(frozen=True)
class RunResult:
    """What one run produced. `exit_code` is negative when a signal ended the child."""

    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool
    truncated: bool


class RunnerError(Exception):
    """A call the runner refused or could not start. `code` is a stable error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def child_env(workdir: str) -> dict[str, str]:
    """The whole environment the child gets. Nothing comes from the server's own env."""
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": workdir,
        "TMPDIR": workdir,
        "LANG": "C.UTF-8",
    }


class _CappedReader:
    """Keeps the first `cap` bytes of a stream and drains the rest, so the child never blocks."""

    def __init__(self, cap: int) -> None:
        self.cap = cap
        self.data = bytearray()
        self.cut = False

    async def read(self, stream: asyncio.StreamReader) -> None:
        while chunk := await stream.read(_READ_CHUNK):
            room = self.cap - len(self.data)
            if room > 0:
                self.data += chunk[:room]
            if len(chunk) > room:
                self.cut = True

    def text(self) -> str:
        return bytes(self.data).decode("utf-8", errors="replace")


def _kill_group(pgid: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pgid, signal.SIGKILL)


def _remove_tree(path: str) -> None:
    def force(func: Callable[..., Any], target: str, _exc: BaseException) -> None:
        # The code may have removed write or search permission; restore it and retry.
        with contextlib.suppress(OSError):
            os.chmod(os.path.dirname(target), stat.S_IRWXU)
            os.chmod(target, stat.S_IRWXU)
            func(target)

    shutil.rmtree(path, onexc=force)


def _make_workdir(code: str, tmp_root: str | Path | None) -> str:
    workdir = tempfile.mkdtemp(prefix="run-", dir=tmp_root)
    try:
        Path(workdir, "main.py").write_text(code, encoding="utf-8")
    except BaseException:
        _remove_tree(workdir)
        raise
    return workdir


def _validate(code: str, timeout_s: float, limits: Limits) -> None:
    if len(code.encode("utf-8")) > limits.max_code_bytes:
        raise RunnerError("bad_arguments", f"code is over {limits.max_code_bytes} bytes")
    if not 0 < timeout_s <= limits.max_timeout_s:
        raise RunnerError("bad_arguments", f"timeout_s must be in (0, {limits.max_timeout_s}]")


async def run_python(
    code: str,
    timeout_s: float,
    *,
    limits: Limits = DEFAULT_LIMITS,
    tmp_root: str | Path | None = None,
    python: str = sys.executable,
) -> RunResult:
    """Run `code` with `python -I -S` in a fresh directory under `tmp_root`, then remove it.

    No shell; stdin is empty; the child is a new session, killed with its group at
    `timeout_s` and again after it exits. On Linux every other process the call started is
    then swept (`isolation.sweep`) before the directory is removed, so none outlives the call.
    Off Linux only the group kill applies. One call at a time per process: the sweep stops
    every process started since the call began.
    """
    global _active
    _validate(code, timeout_s, limits)
    if isolation.lost():
        raise RunnerError("run_failed", "isolation between calls was lost; restart the server")
    if _active:
        raise RunnerError("run_failed", "another call is running in this process")
    try:
        sweeping = isolation.ensure_reaper()
    except OSError as exc:
        raise RunnerError("run_failed", f"cannot isolate the call ({exc.strerror})") from exc
    _active = True
    try:
        return await _run_isolated(code, timeout_s, limits, tmp_root, python, sweeping)
    finally:
        _active = False


_active = False


async def _run_isolated(
    code: str,
    timeout_s: float,
    limits: Limits,
    tmp_root: str | Path | None,
    python: str,
    sweeping: bool,
) -> RunResult:
    me = os.getpid()
    before = isolation.descendants(me) if sweeping else frozenset()
    workdir = _make_workdir(code, tmp_root)
    clean = True
    try:
        cpu_s = int(timeout_s) + 1
        args = [
            str(cpu_s),
            str(limits.address_space_bytes),
            str(limits.open_files),
            str(limits.file_size_bytes),
            str(isolation.nproc_limit() if sweeping else 0),
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                python,
                "-I",
                "-S",
                "-c",
                _BOOTSTRAP,
                *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workdir,
                env=child_env(workdir),
                start_new_session=True,
                close_fds=True,
            )
        except OSError as exc:
            raise RunnerError(
                "run_failed", f"cannot start the interpreter ({exc.strerror})"
            ) from exc
        result = await _supervise(proc, timeout_s, limits)
    finally:
        # Also on error or cancel: nothing from this call may see the next call's directory.
        if sweeping and not isolation.sweep(me, before):
            clean = False
            isolation.mark_lost()
            log.error("a process from the call could not be stopped; refusing further calls")
        _remove_tree(workdir)
    if not clean:
        raise RunnerError("run_failed", "a process from the call could not be stopped")
    return result


async def _supervise(
    proc: asyncio.subprocess.Process, timeout_s: float, limits: Limits
) -> RunResult:
    assert proc.stdout is not None and proc.stderr is not None
    out = _CappedReader(limits.output_cap_bytes)
    err = _CappedReader(limits.output_cap_bytes)
    readers = asyncio.gather(out.read(proc.stdout), err.read(proc.stderr))
    timed_out = False
    try:
        try:
            await asyncio.wait_for(proc.wait(), timeout_s)
        except TimeoutError:
            timed_out = True
    except BaseException:
        readers.cancel()
        raise
    finally:
        # Also on cancel: the group dies with the call.
        _kill_group(proc.pid)
        await proc.wait()
    drained = True
    try:
        await asyncio.wait_for(readers, _DRAIN_S)
    except TimeoutError:
        drained = False
    exit_code = proc.returncode if proc.returncode is not None else -signal.SIGKILL
    return RunResult(
        stdout=out.text(),
        stderr=err.text(),
        exit_code=exit_code,
        timed_out=timed_out,
        truncated=out.cut or err.cut or not drained,
    )
