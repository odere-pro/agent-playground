"""The `chassis` console script. Its first act, before `chassis.server` is imported, is to delete
a stale ready marker (ADR-012, H14).

The chassis is Running while it imports, seconds before it binds. A container restart keeps the
emptyDir, so a marker the previous container wrote would pass the chart's startup probe for that
whole time, while a workload that took the public port answers the readiness probe. So the
marker goes here, with only the standard library loaded. `Drain.serve` deletes it again and
writes it once every listener has bound. A relative path is left to the parser, which refuses it.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

FLAG = "--ready-marker"


def marker_path(argv: Sequence[str]) -> Path | None:
    """The absolute `--ready-marker` path in `argv` (the last one wins, as in argparse)."""
    found: Path | None = None
    for i, arg in enumerate(argv):
        if arg == FLAG and i + 1 < len(argv):
            found = Path(argv[i + 1])
        elif arg.startswith(FLAG + "="):
            found = Path(arg.removeprefix(FLAG + "="))
    return found if found is not None and found.is_absolute() else None


def clear_stale_marker(argv: Sequence[str]) -> None:
    path = marker_path(argv)
    if path is not None:
        path.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    clear_stale_marker(args)
    from chassis.server.cli import main as serve_main

    serve_main(args)


if __name__ == "__main__":
    main()
