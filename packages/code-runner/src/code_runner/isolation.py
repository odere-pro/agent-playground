"""Isolation between calls in one long-lived server (review findings H2 and F5, plan 2.8).

Every child runs as the server's own uid, with no capabilities and no PID namespace of its own,
so neither a per-call uid nor `unshare --pid` is available. What is left, Linux only:

- The server is a child subreaper (`PR_SET_CHILD_SUBREAPER`). A process that leaves the call's
  process group (`setsid()`) and loses its parent is reparented to the server, not to init, so
  it stays a descendant the server can find.
- After each call, `sweep` finds every new descendant through `/proc`, kills it, reaps it, and
  repeats until none is left. Only then is the work directory removed and the next call let in.
  If the sweep cannot finish, isolation is marked lost: every later call is refused and
  `/health` fails, so the pod is restarted.
- The server is not dumpable (`PR_SET_DUMPABLE=0`), so a child with the same uid cannot
  ptrace it or read its memory through `/proc` (the result cache holds other calls' output).

Off Linux (a developer's Mac) none of this exists: the process-group kill is all there is, and a
warning says so once. Mode 0700 directories with random names do not help here: every call
runs as the same uid, and a uid can always open its own directories.
"""

from __future__ import annotations

import contextlib
import ctypes
import logging
import os
import signal
import sys
import time
from collections.abc import Callable

log = logging.getLogger("code_runner")

_PR_SET_DUMPABLE = 4
_PR_SET_CHILD_SUBREAPER = 36

SWEEP_DEADLINE_S = 5.0  # suggested: then isolation is marked lost
NPROC_ALLOWANCE = 32  # suggested: tasks a call may add on top of the uid's count at its start

Process = tuple[int, str]  # (pid, start time in clock ticks): a reused pid is a new process

_reaper = False
_warned = False
_lost = False


def supported() -> bool:
    return sys.platform == "linux"


def _prctl(option: int, value: int) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(option, value, 0, 0, 0) != 0:
        errno = ctypes.get_errno()
        raise OSError(errno, os.strerror(errno))


def ensure_reaper() -> bool:
    """Make this process a child subreaper once. False off Linux, after one warning.

    On Linux a failure raises `OSError`: the caller refuses to run code without the sweep.
    """
    global _reaper, _warned
    if not supported():
        if not _warned:
            _warned = True
            log.warning(
                "isolation between calls is Linux only: off Linux a process that leaves "
                "the call's process group can outlive the call"
            )
        return False
    if not _reaper:
        _prctl(_PR_SET_CHILD_SUBREAPER, 1)
        _reaper = True
    return True


def harden_server() -> None:
    """For the server process at start: subreaper and not dumpable. A no-op off Linux."""
    if ensure_reaper():
        _prctl(_PR_SET_DUMPABLE, 0)


def lost() -> bool:
    return _lost


def mark_lost() -> None:
    global _lost
    _lost = True


def _read_stat(proc_root: str, name: str) -> tuple[int, str, str] | None:
    """(ppid, state, start time) from `/proc/<pid>/stat`, or None if the process is gone."""
    try:
        with open(os.path.join(proc_root, name, "stat"), encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return None
    # The command name is in parentheses and may hold spaces or ")": split after the last one.
    fields = text[text.rfind(")") + 2 :].split()
    if len(fields) < 20:
        return None
    return int(fields[1]), fields[0], fields[19]


def descendants(root: int, *, proc_root: str = "/proc") -> frozenset[Process]:
    """Every process below `root`, alive or a zombie, from one pass over `proc_root`."""
    children: dict[int, list[Process]] = {}
    try:
        names = os.listdir(proc_root)
    except OSError:
        return frozenset()
    for name in names:
        if not name.isdigit():
            continue
        stat = _read_stat(proc_root, name)
        if stat is not None:
            children.setdefault(stat[0], []).append((int(name), stat[2]))
    found: set[Process] = set()
    todo = [root]
    while todo:
        for child in children.get(todo.pop(), []):
            if child not in found:
                found.add(child)
                todo.append(child[0])
    return frozenset(found)


def _kill(pid: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.kill(pid, signal.SIGKILL)


def _reap(pid: int) -> None:
    # Only a direct child can be reaped; for the rest this is ChildProcessError.
    with contextlib.suppress(ChildProcessError):
        os.waitpid(pid, os.WNOHANG)


def sweep(
    root: int,
    before: frozenset[Process],
    *,
    proc_root: str = "/proc",
    kill: Callable[[int], None] = _kill,
    reap: Callable[[int], None] = _reap,
    deadline_s: float = SWEEP_DEADLINE_S,
) -> bool:
    """Kill and reap every descendant of `root` not in `before` until none is left.

    True when none is left, False at the deadline. A killed process cannot fork again, so a new
    one can only come from a process not yet seen; each pass finds it. Blocking on purpose: a
    cancel must not interrupt it.
    """
    end = time.monotonic() + deadline_s
    while True:
        left = descendants(root, proc_root=proc_root) - before
        if not left:
            return True
        for pid, _ in left:
            kill(pid)
        for pid, _ in left:
            reap(pid)
        if time.monotonic() >= end:
            return False
        time.sleep(0.005)


def nproc_limit(*, proc_root: str = "/proc", allowance: int = NPROC_ALLOWANCE) -> int:
    """The child's `RLIMIT_NPROC`: the uid's task count now, plus `allowance`. 0 off Linux.

    The kernel counts every task of the real uid, and the server shares the child's uid, so the
    server's own threads count too. A fixed small value would refuse the child's first fork.
    """
    if not supported():
        return 0
    uid = str(os.getuid())
    tasks = 0
    for name in os.listdir(proc_root):
        if not name.isdigit():
            continue
        try:
            with open(os.path.join(proc_root, name, "status"), encoding="utf-8") as f:
                lines = dict(line.split(":", 1) for line in f if ":" in line)
        except OSError:
            continue
        if lines.get("Uid", "").split()[:1] == [uid]:
            tasks += int(lines.get("Threads", "1").strip() or 1)
    return tasks + allowance
