"""Puts the earlier PoC test folders on `sys.path`, so a PoC-8 test can build on their harnesses.
Excluded from mypy (pyproject.toml), like the other PoC conftests: a second top-level `conftest`
module would clash with the chassis one.
"""

from __future__ import annotations

import sys
from pathlib import Path

POCS = Path(__file__).resolve().parents[2]
for _tests in (
    POCS / "poc-02-two-engines-one-contract" / "tests",
    POCS / "poc-03-one-interface-every-client" / "tests",
    POCS / "poc-05-sandboxed" / "tests",
    POCS / "poc-06a-bake-off-sidecar-lane" / "tests",
    POCS / "poc-07-cross-cutting-decisions" / "tests",
):
    if str(_tests) not in sys.path:
        sys.path.insert(0, str(_tests))
