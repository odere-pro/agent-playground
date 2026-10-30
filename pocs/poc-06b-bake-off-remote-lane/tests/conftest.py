"""The PoC-6b drill gate. A test marked `kind` needs the `kind-poc05` cluster with the PoC-6
engines on it, so it is a `network` test, skipped unless `POC06_KIND=1` and the context exists.
The static tests in `test_poc06b_kind_static.py` carry no `kind` marker and always run.
"""

from pathlib import Path

from poc_harness.drills import Drill, drill_gate

pytest_collection_modifyitems = drill_gate(
    Path(__file__).resolve().parent,
    Drill(
        "kind",
        "POC06_KIND",
        context="kind-poc05",
        label="a kind test",
        hint="make kind-poc06 ARGS=up",
    ),
)
