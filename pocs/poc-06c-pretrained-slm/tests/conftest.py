"""Puts the PoC-2, PoC-4, PoC-5, and PoC-6a test folders on `sys.path`, so a test here can build on
their harnesses, `poc06_harness` included. Excluded from mypy (pyproject.toml), like the PoC-4 and
PoC-5 conftests: a second top-level `conftest` module would clash with the chassis one.
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
):
    if str(_tests) not in sys.path:
        sys.path.insert(0, str(_tests))
