"""The ready marker's content and check (ADR-012, H14). Standard library only: `chassis.entry`
reads it before the server imports, and the chart's startup probe runs it every 2 s.

The marker is `<pid> <start>\\n`: the writer's pid and its start time, field 22 of
`/proc/<pid>/stat` (clock ticks since boot). `is_live` passes only while a process with that pid
has that start time. So a marker a killed chassis (SIGKILL, OOM) left in the kept emptyDir fails
as soon as the restarted container's pid 1 exists, not only once `chassis.entry` deletes it. An
empty marker (the old format), one written where `/proc` was unreadable (`-`), or any other
content fails closed.

`python -m chassis.ready_marker PATH` exits 0 when the marker is live, 1 when it is not, 2 on a
missing or relative path.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from pathlib import Path

PROC = Path("/proc")
NO_START = "-"
MAX_DIGITS = 10
"""A pid is at most 2^22 on Linux (`pid_max`); `int()` on a huge string would raise."""
START_FIELD = 22
"""`man 5 proc`: field 22 of `/proc/<pid>/stat` is `starttime`."""
_FIRST_FIELD_AFTER_COMM = 3
"""Fields 1 and 2 (`pid`, `(comm)`) come before the `)` the parser splits on; `state` is field 3."""


def process_start(pid: int, proc: Path = PROC) -> str | None:
    """The start time of `pid`, or None when `/proc/<pid>/stat` cannot be read or parsed."""
    try:
        text = (proc / str(pid) / "stat").read_text(encoding="ascii", errors="replace")
    except OSError:
        return None
    _, sep, rest = text.rpartition(")")  # `comm` may hold spaces and parentheses
    if not sep:
        return None
    fields = rest.split()
    index = START_FIELD - _FIRST_FIELD_AFTER_COMM  # fields[0] is field 3, `state`
    return fields[index] if len(fields) > index and fields[index].isdigit() else None


def stamp(pid: int, proc: Path = PROC) -> str:
    start = process_start(pid, proc)
    return f"{pid} {NO_START if start is None else start}\n"


def write(path: Path, pid: int | None = None, proc: Path = PROC) -> None:
    """Write the marker for `pid` (this process by default). Written beside the path and moved
    into place, so a probe never reads a half-written or empty file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(stamp(os.getpid() if pid is None else pid, proc), encoding="ascii")
    tmp.replace(path)


def is_live(path: Path, proc: Path = PROC) -> bool:
    """True only when the marker names a process that still runs with that start time."""
    try:
        parts = path.read_text(encoding="ascii", errors="replace").split()
    except OSError:
        return False
    if len(parts) != 2:
        return False
    pid, start = parts
    if not (0 < len(pid) <= MAX_DIGITS and pid.isdigit() and start.isdigit()):
        return False
    return process_start(int(pid), proc) == start


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or not Path(args[0]).is_absolute():
        sys.stderr.write("usage: python -m chassis.ready_marker /absolute/path\n")
        return 2
    return 0 if is_live(Path(args[0])) else 1


if __name__ == "__main__":
    sys.exit(main())
