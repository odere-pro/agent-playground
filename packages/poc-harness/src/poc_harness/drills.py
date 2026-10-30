"""One drill gate for the PoC conftests: a table of drills in, a `pytest_collection_modifyitems`
hook out.

A drill needs a running stack (Compose or a kind cluster), so it is a `network` test and is
skipped unless its variable is `1` and, for a kind drill, the kubectl context exists. `make test`
and `make check` never set the variables. Only tests directly in the conftest's folder count.

    pytest_collection_modifyitems = drill_gate(Path(__file__).parent, Drill(...), ...)
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from fnmatch import fnmatch
from functools import cache
from pathlib import Path

import pytest

KUBECTL_TIMEOUT_S = 10

Hook = Callable[[list[pytest.Item]], None]


@dataclass(frozen=True)
class Drill:
    """One row of a drill table."""

    marker: str
    """The marker that names a drill test, for example `kind`."""
    variable: str
    """The variable that turns the drill on when it is `1`, for example `POC05_KIND`."""
    context: str | None = None
    """A kubectl context the drill also needs, for example `kind-poc05`."""
    files: str | None = None
    """A file name pattern (`fnmatch`) whose every test is this drill."""
    adds_marker: bool = False
    """Whether a test the file pattern names also gets `marker` (else only `network`)."""
    label: str = ""
    """How a skip reason names the test; default `a <marker> drill`."""
    hint: str = ""
    """What the skip reason says to run when the context is missing."""

    def names(self, item: pytest.Item) -> bool:
        """Whether `item` is this drill, by its file name or its marker."""
        by_file = self.files is not None and fnmatch(item.path.name, self.files)
        return by_file or item.get_closest_marker(self.marker) is not None

    def skip_reason(self) -> str | None:
        """Why the drill cannot run here, or `None` when it can."""
        label = self.label or f"a {self.marker} drill"
        if self.context is None:
            return None if self.is_on() else f"{label}: set {self.variable}=1"
        if not self.is_on():
            return f"{label}: set {self.variable}=1 (needs the {self.context} context)"
        if not kubectl_context_exists(self.context):
            return f"{label}: no kubectl context {self.context!r} ({self.hint})"
        return None

    def is_on(self) -> bool:
        return os.environ.get(self.variable) == "1"


@cache
def kubectl_context_exists(context: str) -> bool:
    """Whether kubectl knows `context`. Asked once per context, only when a variable is set."""
    kubectl = shutil.which("kubectl")
    if kubectl is None:
        return False
    try:
        found = subprocess.run(
            [kubectl, "config", "get-contexts", "-o", "name"],
            capture_output=True,
            text=True,
            timeout=KUBECTL_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return context in found.stdout.split()


def gate_item(item: pytest.Item, drill: Drill) -> None:
    """Mark one collected test for one drill: the marker, `network`, and the skip if it is off."""
    if not drill.names(item):
        return
    if drill.adds_marker and item.get_closest_marker(drill.marker) is None:
        item.add_marker(getattr(pytest.mark, drill.marker))
    item.add_marker(pytest.mark.network)
    reason = drill.skip_reason()
    if reason is not None:
        item.add_marker(pytest.mark.skip(reason=reason))


def drill_gate(here: Path, *table: Drill) -> Hook:
    """The `pytest_collection_modifyitems` hook for the tests directly in `here`."""

    @pytest.hookimpl(tryfirst=True)
    def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
        for item in items:
            if item.path.parent != here:
                continue
            for drill in table:
                gate_item(item, drill)

    return pytest_collection_modifyitems
