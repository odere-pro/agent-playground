"""The PoC-4 drill gate. A Compose drill (marker `stack`, or any test in `test_compose_scale.py`) is
skipped unless `POC04_STACK=1`; a kind drill (marker `kind`, or any test in `test_kind.py`) unless
`POC04_KIND=1`. Both are `network` tests; `make test-integration` sets neither.
"""

from pathlib import Path

from poc_harness.drills import Drill, drill_gate

pytest_collection_modifyitems = drill_gate(
    Path(__file__).resolve().parent,
    Drill("stack", "POC04_STACK", files="test_compose_scale.py", adds_marker=True),
    Drill("kind", "POC04_KIND", files="test_kind.py", adds_marker=True),
)
