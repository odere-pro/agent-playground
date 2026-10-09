"""The PoC-6b tests conftest, as a module mypy can check: `conftest.py` re-exports it, and mypy
skips that file, since a second top-level `conftest` module would clash with the chassis one.

Kind tests need the `kind-poc05` cluster (PoC-6 runs in PoC-5's cluster) and the PoC-6 engines on
it (`deploy/kind/poc06/run.sh up`). Every test marked `kind` here is also marked `network` and is
skipped unless `POC06_KIND=1` and the `kind-poc05` kubectl context exists. `make test` and `make
check` never set the variable, so they run only the offline tests. The static tests in
`test_poc06b_kind_static.py` carry no `kind` marker and always run.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from functools import cache
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
KIND_VARIABLE = "POC06_KIND"
KIND_CONTEXT = "kind-poc05"


@cache
def kind_context_exists() -> bool:
    """Whether kubectl knows the `kind-poc05` context. Asked once, only when the variable is set."""
    kubectl = shutil.which("kubectl")
    if kubectl is None:
        return False
    try:
        found = subprocess.run(
            [kubectl, "config", "get-contexts", "-o", "name"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return KIND_CONTEXT in found.stdout.split()


def kind_skip_reason() -> str | None:
    if os.environ.get(KIND_VARIABLE) != "1":
        return f"a kind test: set {KIND_VARIABLE}=1 (needs the {KIND_CONTEXT} context)"
    if not kind_context_exists():
        return f"a kind test: no kubectl context {KIND_CONTEXT!r} (make kind-poc06 ARGS=up)"
    return None


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if item.path.parent != HERE or item.get_closest_marker("kind") is None:
            continue
        item.add_marker(pytest.mark.network)
        reason = kind_skip_reason()
        if reason is not None:
            item.add_marker(pytest.mark.skip(reason=reason))
