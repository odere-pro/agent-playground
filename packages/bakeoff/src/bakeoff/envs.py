"""The environment of every process the kit spawns, built from scratch.

A child never gets `os.environ`. This container may run a live agent session whose `ANTHROPIC_*`
and `CLAUDE_*` variables must never reach a workload or the chassis. The only parent variable read
is `PATH`; everything else is named by the caller.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

__all__ = ["FORBIDDEN_PREFIXES", "build_env", "is_secret_name"]

FORBIDDEN_PREFIXES = ("ANTHROPIC_", "CLAUDE_")
"""A child env never names a variable that starts with one of these."""
FALLBACK_PATH = "/usr/local/bin:/usr/bin:/bin"


def is_secret_name(name: str) -> bool:
    """True for a variable whose value must never be printed."""
    return name.startswith(FORBIDDEN_PREFIXES) or name.endswith(("_TOKEN", "_KEY"))


def build_env(home: Path, extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """`PATH`, a temp `HOME`, and `extra`. Raises `ValueError` (naming the variable only) when
    `extra` holds a `FORBIDDEN_PREFIXES` name."""
    env = {"PATH": os.environ.get("PATH") or FALLBACK_PATH, "HOME": str(home)}
    for name, value in (extra or {}).items():
        if name.startswith(FORBIDDEN_PREFIXES):
            raise ValueError(f"refusing to pass {name} to a child process")
        env[name] = value
    return env
