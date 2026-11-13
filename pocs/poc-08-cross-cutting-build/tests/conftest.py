"""Keeps the stack drills out of collection unless their variable is set:
`test_poc08_stack_litellm.py` needs `COMPOSE_STACK=1` (`.github/workflows/poc08-litellm.yml`).
Excluded from mypy (pyproject.toml), like the other PoC conftests: a second top-level `conftest`
module would clash with the chassis one.

The stack drill (`test_poc08_stack_metrics.py`, `network`) is collected only with
`COMPOSE_STACK=1`, so the offline gate never sees it (`test_poc08_c1_ci.py` checks that this folder
has no `network` test under the gate). Named by path without the variable, it is skipped.
"""

from __future__ import annotations

from poc_harness.drills import gate_is_on

GATED_DRILLS = {
    "test_poc08_stack_metrics.py": "COMPOSE_STACK",
    "test_poc08_stack_litellm.py": "COMPOSE_STACK",
    "test_poc08_router_live.py": "ROUTER_DRILL",
}
"""A `network` drill against a running stack, to the variable that must be `1` to collect it."""
collect_ignore = [name for name, var in GATED_DRILLS.items() if not gate_is_on(var)]
