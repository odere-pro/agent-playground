"""Puts the PoC-2, PoC-4, PoC-5, and PoC-6a test folders on `sys.path`, so a test here can build on
their harnesses, `poc06_harness` included. It also puts the chassis test folder there, for
`plain_a2a_stub`, the plain-A2A stub agent that the chassis tests and this PoC share. Excluded
from mypy (pyproject.toml), like the PoC-4 and PoC-5 conftests: a second top-level `conftest`
module would clash with the chassis one.
"""

from __future__ import annotations

import sys
from pathlib import Path

POCS = Path(__file__).resolve().parents[2]
for _tests in (
    POCS / "poc-02-two-engines-one-contract" / "tests",
    POCS / "poc-04-stateless-scalable" / "tests",
    POCS / "poc-05-sandboxed" / "tests",
    POCS / "poc-06a-bake-off-sidecar-lane" / "tests",
    POCS.parent / "packages" / "chassis" / "tests",
):
    if str(_tests) not in sys.path:
        sys.path.insert(0, str(_tests))
