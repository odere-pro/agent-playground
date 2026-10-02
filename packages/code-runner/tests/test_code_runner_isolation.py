"""Isolation between calls: no process from a call outlives it (review finding H2, plan 2.8).

The subreaper sweep is Linux only; those tests skip elsewhere. The process-group path and the
`/proc` sweep logic (over a fake `/proc` tree) run everywhere. Each test is bounded to a few
seconds and starts at most one tiny child Python process.
"""

from __future__ import annotations

import asyncio
import logging
import os
import resource
import subprocess
import sys
import time
from pathlib import Path

import pytest
from code_runner import isolation
from code_runner.runner import Limits, RunnerError, run_python

FAST = Limits(output_cap_bytes=1024)
LINUX_ONLY = pytest.mark.skipif(
    sys.platform != "linux", reason="the subreaper sweep is Linux only (prctl, /proc)"
)
NOT_LINUX = pytest.mark.skipif(sys.platform == "linux", reason="checks the non-Linux fallback")


def _daemon_code(pidfile: Path, marker: Path, *, setsid: bool) -> str:
    """Fork; the child optionally leaves the session and forks a grandchild that lingers.

    The grandchild writes its pid, closes the pipes, sleeps, then writes `marker`. The code
    returns only after the pid file exists, so the call ends while the grandchild sleeps.
    """
    return (
        "import os, time\n"
        "if os.fork() == 0:\n"
        f"    {'os.setsid()' if setsid else 'pass'}\n"
        "    if os.fork() == 0:\n"
        "        null = os.open(os.devnull, os.O_RDWR)\n"
        "        for fd in (0, 1, 2):\n"
        "            os.dup2(null, fd)\n"
        f"        open({str(pidfile)!r}, 'w').write(str(os.getpid()))\n"
        "        time.sleep(0.6)\n"
        f"        open({str(marker)!r}, 'w').write('late')\n"
        "        os._exit(0)\n"
        "    os._exit(0)\n"
        "os.wait()\n"
        "for _ in range(200):\n"
        f"    if os.path.exists({str(pidfile)!r}):\n"
        "        break\n"
        "    time.sleep(0.01)\n"
        "print('parent done')\n"
    )


def _listing(path: Path) -> list[Path]:
    return list(path.iterdir())


def _alive(pid: int, grace_s: float) -> bool:
    """True if `pid` still exists after `grace_s` (a killed orphan stays a zombie until reaped)."""
    end = time.monotonic() + grace_s
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        if time.monotonic() >= end:
            return True
        time.sleep(0.01)


async def _run_and_check_no_survivor(tmp_path: Path, *, setsid: bool, grace_s: float) -> None:
    pidfile, marker = tmp_path / "pid", tmp_path / "marker"
    work = tmp_path / "work"
    work.mkdir()
    code = _daemon_code(pidfile, marker, setsid=setsid)
    result = await run_python(code, 3, limits=FAST, tmp_root=work)
    assert result.stdout == "parent done\n", result.stderr
    pid = int(pidfile.read_text())
    assert not _alive(pid, grace_s), "a process from the call is alive after the call returned"
    await asyncio.sleep(1.0)
    assert not marker.exists(), "a process started by the call wrote after the call returned"
    assert _listing(work) == []


async def test_code_runner_background_process_in_the_group_dies_with_the_call(
    tmp_path: Path,
) -> None:
    # Portable: the process-group kill is the whole guarantee off Linux. Off Linux the killed
    # orphan is reaped by launchd or init, not by us, so allow it a moment.
    grace_s = 0.0 if sys.platform == "linux" else 1.0
    await _run_and_check_no_survivor(tmp_path, setsid=False, grace_s=grace_s)


@LINUX_ONLY
async def test_code_runner_setsid_daemon_dies_with_the_call(tmp_path: Path) -> None:
    # The review's H2 reproduction: the grandchild leaves the process group with setsid().
    # The sweep reaps what it kills, so the process is gone when the call returns.
    await _run_and_check_no_survivor(tmp_path, setsid=True, grace_s=0.0)


@LINUX_ONLY
async def test_code_runner_child_has_an_nproc_limit(tmp_path: Path) -> None:
    code = "import resource as r\nprint(r.getrlimit(r.RLIMIT_NPROC)[0])"
    result = await run_python(code, 2, limits=FAST, tmp_root=tmp_path)
    assert result.exit_code == 0, result.stderr
    assert int(result.stdout) != resource.RLIM_INFINITY


@LINUX_ONLY
def test_code_runner_hardened_server_is_a_subreaper_and_not_dumpable() -> None:
    # In a subprocess: the flags are process-wide and must not stick to the test process.
    code = (
        "import ctypes\n"
        "from code_runner.isolation import harden_server\n"
        "harden_server()\n"
        "libc = ctypes.CDLL(None)\n"
        "flag = ctypes.c_int(0)\n"
        "libc.prctl(37, ctypes.byref(flag), 0, 0, 0)\n"  # PR_GET_CHILD_SUBREAPER
        "print(flag.value, libc.prctl(3, 0, 0, 0, 0))\n"  # PR_GET_DUMPABLE
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=10, check=True
    )
    assert out.stdout.split() == ["1", "0"]


@NOT_LINUX
async def test_code_runner_off_linux_warns_once_and_keeps_the_group_kill(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(isolation, "_warned", False)
    with caplog.at_level(logging.WARNING, logger="code_runner"):
        await run_python("print(1)", 2, limits=FAST, tmp_root=tmp_path)
        await run_python("print(2)", 2, limits=FAST, tmp_root=tmp_path)
    warnings = [r for r in caplog.records if "Linux only" in r.getMessage()]
    assert len(warnings) == 1


async def test_code_runner_refuses_every_call_once_isolation_is_lost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(isolation, "_lost", True)
    with pytest.raises(RunnerError) as info:
        await run_python("print(1)", 2, limits=FAST, tmp_root=tmp_path)
    assert info.value.code == "run_failed"
    assert _listing(tmp_path) == []


# The sweep over a fake /proc tree: portable, no real process is touched.


def _stat(proc: Path, pid: int, ppid: int, *, state: str = "S", start: int = 100) -> None:
    (proc / str(pid)).mkdir(exist_ok=True)
    rest = [state, str(ppid)] + ["0"] * 17 + [str(start)] + ["0"] * 5
    (proc / str(pid) / "stat").write_text(f"{pid} (py (x) y) " + " ".join(rest) + "\n")


def _tree(proc: Path, entries: dict[int, int]) -> None:
    for pid, ppid in entries.items():
        _stat(proc, pid, ppid)


def test_code_runner_descendants_follow_the_parent_links(tmp_path: Path) -> None:
    _tree(tmp_path, {1: 0, 10: 1, 11: 10, 12: 11, 20: 1, 21: 20})
    (tmp_path / "self").mkdir()  # not a pid: ignored
    found = isolation.descendants(10, proc_root=str(tmp_path))
    assert {pid for pid, _ in found} == {11, 12}


def test_code_runner_sweep_kills_new_descendants_and_spares_old_ones(tmp_path: Path) -> None:
    _tree(tmp_path, {10: 1, 11: 10})  # 11 existed before the call
    before = isolation.descendants(10, proc_root=str(tmp_path))
    _tree(tmp_path, {12: 10, 13: 12})  # the call's leftovers
    killed: list[int] = []

    def kill(pid: int) -> None:
        killed.append(pid)
        # Dying: a killed process is gone; its children reparent to the subreaper.
        for child in tmp_path.iterdir():
            stat = child / "stat"
            if stat.exists() and stat.read_text().rsplit(") ", 1)[1].split()[1] == str(pid):
                _stat(tmp_path, int(child.name), 10)
        for path in (tmp_path / str(pid)).iterdir():
            path.unlink()
        (tmp_path / str(pid)).rmdir()

    ok = isolation.sweep(
        10, before, proc_root=str(tmp_path), kill=kill, reap=lambda _pid: None, deadline_s=1.0
    )
    assert ok is True
    assert sorted(killed) == [12, 13]
    assert (tmp_path / "11").exists()


def test_code_runner_sweep_reports_failure_when_a_process_will_not_go(tmp_path: Path) -> None:
    _tree(tmp_path, {10: 1})
    before = isolation.descendants(10, proc_root=str(tmp_path))
    _tree(tmp_path, {12: 10})
    ok = isolation.sweep(
        10,
        before,
        proc_root=str(tmp_path),
        kill=lambda _pid: None,
        reap=lambda _pid: None,
        deadline_s=0.05,
    )
    assert ok is False


def test_code_runner_a_reused_pid_is_a_new_process(tmp_path: Path) -> None:
    _stat(tmp_path, 10, 1)
    _stat(tmp_path, 11, 10, start=100)
    before = isolation.descendants(10, proc_root=str(tmp_path))
    _stat(tmp_path, 11, 10, start=200)  # pid 11 died and was reused during the call
    after = isolation.descendants(10, proc_root=str(tmp_path))
    assert after - before == {(11, "200")}
