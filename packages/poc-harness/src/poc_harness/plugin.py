"""The drill gate: the pytest marker `requires_env(variable, *, context=None, hint="")`.

Loaded by the `pytest11` entry point. A marked test needs a running stack, so it is always
`network`. It skips unless the variable is `"1"`. With the variable on and a `context` given, a
context kubectl lacks fails the test: the operator asked for the drill, and an all-skip run would
exit 0 having checked nothing. kubectl is asked once per context, only with the variable on.

Imports pytest and the standard library only: every pytest run in the workspace loads this module.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field

import pytest

MARKER = "requires_env"
KUBECTL_TIMEOUT_S = 10.0
_CONTEXTS = pytest.StashKey[dict[str, bool]]()


@dataclass(frozen=True)
class _Gate:
    """One `requires_env` mark. The fields are the marker's signature."""

    variable: str
    context: str | None = field(default=None, kw_only=True)
    hint: str = field(default="", kw_only=True)

    @property
    def is_on(self) -> bool:
        return os.environ.get(self.variable) == "1"

    @property
    def skip_reason(self) -> str:
        needs = f" (needs the {self.context} context)" if self.context else ""
        return f"a drill: set {self.variable}=1{needs}"

    @property
    def missing_context(self) -> str:
        hint = f": {self.hint}" if self.hint else ""
        return f"{self.variable}=1 but kubectl has no context {self.context!r}{hint}"


def _gates(item: pytest.Item) -> list[_Gate]:
    return [_Gate(*mark.args, **mark.kwargs) for mark in item.iter_markers(MARKER)]


def _kubectl_has(context: str) -> bool:
    kubectl = shutil.which("kubectl")
    if kubectl is None:
        return False
    try:
        found = subprocess.run(
            [kubectl, "config", "get-contexts", context, "-o", "name"],
            capture_output=True,
            text=True,
            timeout=KUBECTL_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return found.returncode == 0 and context in found.stdout.split()


def _context_exists(config: pytest.Config, context: str) -> bool:
    """`_kubectl_has`, asked once per context in a session."""
    known = config.stash.setdefault(_CONTEXTS, {})
    if context not in known:
        known[context] = _kubectl_has(context)
    return known[context]


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """First, so `network` is on the item when pytest applies `-m`, and this skip reason is the
    one reported when the sockets-off skip also applies."""
    for item in items:
        for gate in _gates(item):
            item.add_marker("network")
            if not gate.is_on:
                item.add_marker(pytest.mark.skip(reason=gate.skip_reason))


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    """Before any fixture, so a fixture that skips cannot hide the missing context."""
    for gate in _gates(item):
        if gate.is_on and gate.context and not _context_exists(item.config, gate.context):
            pytest.fail(gate.missing_context, pytrace=False)
