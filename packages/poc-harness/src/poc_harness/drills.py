"""The named drill marks: put one in a test file's `pytestmark`. See `poc_harness.plugin`.

A kind drill runs with `KIND_DRILLS=1`, a Compose stack drill with `COMPOSE_STACK=1`.
"""

from __future__ import annotations

import os

import pytest

from poc_harness.plugin import gate_is_on

__all__ = [
    "COMPOSE_STACK",
    "KIND_BAKEOFF",
    "KIND_SANDBOX",
    "KIND_SCALE",
    "gate_is_on",
    "required_env",
]

COMPOSE_STACK = pytest.mark.stack
"""A Compose stack is up. The test file's docstring names which one."""
KIND_SCALE = pytest.mark.kind
"""The scale cluster of `deploy/kind/run.sh` is up. The script pins its own context."""
KIND_SANDBOX = pytest.mark.kind(context="kind-poc05", hint="make kind-poc05 ARGS=up")
"""The sandbox cluster of `deploy/kind/poc05` is up."""
KIND_BAKEOFF = pytest.mark.kind(context="kind-poc05", hint="make kind-poc06 ARGS=up")
"""The bake-off is installed on the sandbox cluster (`deploy/kind/poc06`)."""


def required_env(*names: str) -> list[str]:
    """The value of each variable. A missing one fails the test: a drill was asked for, and an
    all-skip run would exit 0 having checked nothing.
    """
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        pytest.fail(f"a drill needs {', '.join(missing)}", pytrace=False)
    return [os.environ[name] for name in names]
