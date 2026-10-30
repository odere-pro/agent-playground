"""The PoC-5 drill gate. Every test in a `test_poc05_kind_*.py` file, or marked `kind`, is a
`network` test, skipped unless `POC05_KIND=1` and the `kind-poc05` kubectl context exists. Only
`network` is added: `deploy/kind/poc06/run.sh` selects with `-m kind`, so the marker set stays.
"""

from pathlib import Path

from poc_harness.drills import Drill, drill_gate

pytest_collection_modifyitems = drill_gate(
    Path(__file__).resolve().parent,
    Drill(
        "kind",
        "POC05_KIND",
        context="kind-poc05",
        files="test_poc05_kind_*",
        label="a kind test",
        hint="make kind-poc05 ARGS=up",
    ),
)
