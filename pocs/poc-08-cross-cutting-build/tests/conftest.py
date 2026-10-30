"""Puts the earlier PoC test folders and the chassis test folder on `sys.path`, so a PoC-8 test can
build on their harnesses.
Excluded from mypy (pyproject.toml), like the other PoC conftests: a second top-level `conftest`
module would clash with the chassis one.

The stack drill (`test_poc08_stack_metrics.py`, `network`) is collected only with
`POC08_STACK=1`, so the offline gate never sees it (`test_poc08_c1_ci.py` checks that this folder
has no `network` test under the gate). Named by path without the variable, it is skipped.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

POCS = Path(__file__).resolve().parents[2]
for _tests in (
    POCS / "poc-02-two-engines-one-contract" / "tests",
    POCS / "poc-03-one-interface-every-client" / "tests",
    POCS / "poc-05-sandboxed" / "tests",
    POCS / "poc-06a-bake-off-sidecar-lane" / "tests",
    POCS / "poc-07-cross-cutting-decisions" / "tests",
    POCS.parent / "packages" / "chassis" / "tests",  # `promtext`, the Prometheus text reader
):
    if str(_tests) not in sys.path:
        sys.path.insert(0, str(_tests))

# The names are `poc08_stack.STACK_VAR` and `poc08_stack.DRILL_FILE`; the static test checks both.
collect_ignore = [] if os.environ.get("POC08_STACK") == "1" else ["test_poc08_stack_metrics.py"]
