"""A read-only root file system for one Python process (057 H-19), with the standard library only.

Two uses:

- The rules, imported by `agent_testkit.stateless`: which audit events write to the file system
  (`written`), and which paths are not the root (`counts`).
- A launcher, run as a script in a child process, so the same rules hold in the workload's and in
  the chassis's own environment (the chassis's has no `agent_testkit`):

  `python [-I] readonly_root.py --container NAME [--allow DIR ...] (-m MODULE | SCRIPT) [ARGS]`

  It adds a process-wide audit hook, then runs the module or the script as `__main__`. Each write
  outside an `--allow` folder is refused with `EROFS`, as a read-only root answers, and one line
  `MARKER {"container": ..., "event": ..., "path": ...}` goes to stderr (fd 2, no file opened).
  `refused_writes(log_text)` reads those lines back. `--allow` is a writable volume, such as the
  pod's `/tmp` emptyDir; the caller checks it is empty after each call.

Not seen: writes made by a non-Python child process (a CLI the workload starts) outside an
`--allow` folder.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import runpy
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LAUNCHER = Path(__file__)
"""This file: the script a child process runs to get a read-only root."""
MARKER = "agent-testkit: read-only root refused a write:"
NOT_THE_ROOT = frozenset({"/dev/null", "/dev/stdout", "/dev/stderr"})
"""Paths a container may open for writing on a read-only root."""
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
PATH_EVENTS: dict[str, tuple[tuple[int, int | None], ...]] = {
    "os.mkdir": ((0, 2),),
    "os.rmdir": ((0, 1),),
    "os.remove": ((0, 1),),
    "os.rename": ((0, 2), (1, 3)),
    "os.replace": ((0, 2), (1, 3)),
    "os.symlink": ((1, 2),),
    "os.link": ((1, 3),),
    "os.truncate": ((0, None),),
    "os.chmod": ((0, 2),),
    "os.chown": ((0, 3),),
    "os.utime": ((0, 3),),
    "sqlite3.connect": ((0, None),),
}
"""Audit events that change the file system: for each path argument, its position and the
position of its `dir_fd` (a relative path is relative to that folder, as in `shutil.rmtree`)."""


@dataclass(frozen=True)
class Write:
    """One refused write: the container, the audit event, and the path."""

    container: str
    event: str
    path: str

    def __str__(self) -> str:
        return f"container {self.container!r} wrote {self.path} ({self.event})"


def _fd_folder(fd: Any) -> str | None:
    """The folder an open `dir_fd` names; None for no fd (`None`, -1) or when it cannot be read."""
    if not isinstance(fd, int) or isinstance(fd, bool) or fd < 0:
        return None
    try:
        return str(Path(f"/proc/self/fd/{fd}").readlink())  # Linux
    except OSError:
        pass
    try:
        getpath = getattr(fcntl, "F_GETPATH", None)  # macOS
        if getpath is not None:
            raw = fcntl.fcntl(fd, getpath, b"\0" * 1024)
            return os.fsdecode(raw.split(b"\0", 1)[0])
    except OSError:
        pass
    return None


def _as_path(value: Any, dir_fd: Any = None) -> str | None:
    if isinstance(value, int) or value is None:
        return None  # a file descriptor: its open was audited already
    try:
        path = os.fsdecode(os.fspath(value))
    except TypeError:
        return None
    if path == ":memory:":
        return None
    folder = _fd_folder(dir_fd) if not Path(path).is_absolute() else None
    return str(Path(folder, path) if folder else Path(path).absolute())


def written(event: str, args: tuple[Any, ...]) -> list[str]:
    """The paths an audit event writes to; `[]` when it writes nothing."""
    if event == "open":
        flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
        path = _as_path(args[0]) if flags & WRITE_FLAGS else None
        return [path] if path else []
    paths = (
        _as_path(args[at], args[fd] if fd is not None and fd < len(args) else None)
        for at, fd in PATH_EVENTS.get(event, ())
        if at < len(args)
    )
    return [path for path in paths if path]


def _bytecode(path: Path) -> bool:
    """A `.pyc` directly in a `__pycache__` folder, or the `<name>.pyc.<n>` temp file Python
    writes it through (`importlib._bootstrap_external._write_atomic`). Nothing else there."""
    name = path.name
    is_pyc = name.endswith(".pyc") or (".pyc." in name and name.rsplit(".", 1)[1].isdigit())
    return path.parent.name == "__pycache__" and is_pyc


def counts(path: str) -> bool:
    """A write to `path` is a write to the root: not `/dev/null` and the like, nor bytecode."""
    return path not in NOT_THE_ROOT and not _bytecode(Path(path))


def refused_writes(log_text: str) -> list[Write]:
    """The writes a launched process refused, read from its stderr or log."""
    found: list[Write] = []
    for line in log_text.splitlines():
        _, marker, rest = line.partition(MARKER)
        if marker:
            item = json.loads(rest)
            found.append(Write(str(item["container"]), str(item["event"]), str(item["path"])))
    return found


def _under(path: str, folders: Sequence[str]) -> bool:
    return any(path == folder or path.startswith(folder.rstrip("/") + "/") for folder in folders)


def install(container: str, allow: Sequence[str] = ()) -> None:
    """Refuse every write outside `allow` from now on, in every thread of this process."""
    folders = [str(Path(folder).absolute()) for folder in allow]
    folders += [os.path.realpath(folder) for folder in folders]

    def audit(event: str, args: tuple[Any, ...]) -> None:
        if event != "open" and event not in PATH_EVENTS:
            return
        paths = [p for p in written(event, args) if counts(p) and not _under(p, folders)]
        for path in paths:
            line = {"container": container, "event": event, "path": path}
            os.write(2, f"{MARKER} {json.dumps(line)}\n".encode())
        if paths:
            message = f"Read-only file system (stateless check, container {container!r})"
            raise OSError(errno.EROFS, message, paths[0])

    sys.addaudithook(audit)


def _parse(argv: list[str]) -> tuple[str, list[str], list[str]]:
    container = "workload"
    allow: list[str] = []
    while argv and argv[0] in ("--container", "--allow"):
        if len(argv) < 2:
            raise SystemExit(f"readonly_root: {argv[0]} needs a value")
        flag, value, argv = argv[0], argv[1], argv[2:]
        if flag == "--container":
            container = value
        else:
            allow.append(value)
    if not argv:
        raise SystemExit("readonly_root: name a script or -m MODULE to run")
    return container, allow, argv


def main(argv: list[str]) -> None:
    container, allow, rest = _parse(argv)
    here = str(LAUNCHER.parent)
    if sys.path and sys.path[0] == here:
        del sys.path[0]  # never let the kit's own modules shadow the program's
    sys.dont_write_bytecode = True  # a read-only root cannot make a `__pycache__` folder either
    install(container, allow)
    if rest[0] == "-m":
        if len(rest) < 2:
            raise SystemExit("readonly_root: -m needs a module")
        sys.argv = [rest[1], *rest[2:]]
        runpy.run_module(rest[1], run_name="__main__", alter_sys=True)
        return
    sys.argv = rest
    runpy.run_path(rest[0], run_name="__main__")


if __name__ == "__main__":
    main(sys.argv[1:])
