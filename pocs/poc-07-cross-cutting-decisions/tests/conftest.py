"""Puts the earlier PoC test folders on `sys.path`, so a PoC-7 test can build on their harnesses.
Excluded from mypy (pyproject.toml), like the other PoC conftests.
"""

from __future__ import annotations

import sys
from pathlib import Path

POCS = Path(__file__).resolve().parents[2]
for _tests in (
    POCS / "poc-02-two-engines-one-contract" / "tests",
    POCS / "poc-03-one-interface-every-client" / "tests",
    POCS / "poc-04-stateless-scalable" / "tests",
    POCS / "poc-05-sandboxed" / "tests",
    POCS / "poc-06a-bake-off-sidecar-lane" / "tests",
):
    if str(_tests) not in sys.path:
        sys.path.insert(0, str(_tests))
