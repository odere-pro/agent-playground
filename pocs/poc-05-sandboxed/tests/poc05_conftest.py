"""The PoC-5 tests conftest, as a module mypy can check: `conftest.py` re-exports it, and mypy
skips that file, since a second top-level `conftest` module would clash with the chassis one.

Puts the PoC-2, PoC-3, and PoC-4 test folders on `sys.path`, so a PoC-5 harness can build on
`poc02_harness`, `poc03_harness`, and `poc04_harness`.

Kind tests need the `kind-poc05` cluster and are `network` tests: every test in a file named
`test_poc05_kind_*.py`, or any test here marked `kind`, is marked `network` and skipped unless
`POC05_KIND=1` and the `kind-poc05` kubectl context exists. `make test` and `make check` never set
the variable, so they run only the offline tests (plan section 4).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from functools import cache
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
POCS = HERE.parents[1]
for _tests in (
    POCS / "poc-02-two-engines-one-contract" / "tests",
    POCS / "poc-03-one-interface-every-client" / "tests",
    POCS / "poc-04-stateless-scalable" / "tests",
):
    if str(_tests) not in sys.path:
        sys.path.insert(0, str(_tests))

KIND_VARIABLE = "POC05_KIND"
KIND_CONTEXT = "kind-poc05"
KIND_FILE_PREFIX = "test_poc05_kind_"


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
        return f"a kind test: no kubectl context {KIND_CONTEXT!r} (make kind-poc05 ARGS=up)"
    return None


def is_kind_test(item: pytest.Item) -> bool:
    return (
        item.path.name.startswith(KIND_FILE_PREFIX) or item.get_closest_marker("kind") is not None
    )


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if item.path.parent != HERE or not is_kind_test(item):
            continue
        item.add_marker(pytest.mark.network)
        reason = kind_skip_reason()
        if reason is not None:
            item.add_marker(pytest.mark.skip(reason=reason))
