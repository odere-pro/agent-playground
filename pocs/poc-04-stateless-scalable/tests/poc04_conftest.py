"""The PoC-4 tests conftest, as a module mypy can check: `conftest.py` re-exports it, and mypy
skips that file, since a second top-level `conftest` module would clash with the chassis one.

Puts the PoC-2 and PoC-3 test folders on `sys.path`, so `poc04_harness` builds on
`poc02_harness` and `poc03_harness`.

Drills need a running stack and are `network` tests: a Compose drill (marker `stack`, or any test
in `test_compose_scale.py`) is skipped unless `POC04_STACK=1`; a kind drill (marker `kind`, or any
test in `test_kind.py`) unless `POC04_KIND=1`. `make test-integration` does not set either.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
POCS = HERE.parents[1]
for _tests in (
    POCS / "poc-02-two-engines-one-contract" / "tests",
    POCS / "poc-03-one-interface-every-client" / "tests",
):
    if str(_tests) not in sys.path:
        sys.path.insert(0, str(_tests))

DRILLS = {
    "stack": ("POC04_STACK", "test_compose_scale.py"),
    "kind": ("POC04_KIND", "test_kind.py"),
}
"""marker -> (the variable that turns the drill on, the file whose every test is that drill)."""


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if item.path.parent != HERE:
            continue
        for marker, (variable, filename) in DRILLS.items():
            if item.path.name == filename and item.get_closest_marker(marker) is None:
                item.add_marker(getattr(pytest.mark, marker))
            if item.get_closest_marker(marker) is None:
                continue
            item.add_marker(pytest.mark.network)
            if os.environ.get(variable) != "1":
                item.add_marker(pytest.mark.skip(reason=f"a {marker} drill: set {variable}=1"))
