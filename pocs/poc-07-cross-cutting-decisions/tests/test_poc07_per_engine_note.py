"""Exit criterion 6: the per-engine note's "Run" cells match what the scenario tests run.

`notes/2026-10-28-per-engine-mapping.md` has one table, eight engines by four concerns. A cell
that starts with **Run** claims a PoC-7 test ran that engine for that concern. The engine lists
here are the ones the scenario tests parametrize over, imported from the harness or from the test
module itself, never copied. The note is dated evidence and is not edited; a drift fails here.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import test_poc07_evals_gate
import test_poc07_fb_engines
import test_poc07_sec_marking
from poc_harness import security, trace_join

NOTE = Path(__file__).resolve().parents[1] / "notes/2026-10-28-per-engine-mapping.md"
CONCERNS = ("Observability", "Security", "Feedback", "Evals (gate)")
NOTE_ENGINES = 8
"""The note's own count: "eight engines by four concerns"."""

TESTED: dict[str, frozenset[str]] = {
    "Observability": frozenset(trace_join.ENGINES),
    "Security": frozenset(security.ENGINES) | frozenset(test_poc07_sec_marking.ENGINE_NAMES),
    "Feedback": frozenset(test_poc07_fb_engines.FEEDBACK_ENGINES),
    "Evals (gate)": frozenset(test_poc07_evals_gate.TWO_ENGINES),
}
"""The engines each concern's scenario tests run."""


def _cells(row: str) -> list[str]:
    return [cell.strip() for cell in row.strip().strip("|").split("|")]


def per_engine_table() -> dict[str, dict[str, str]]:
    """The note's "Per engine" table: engine name -> concern -> cell text."""
    lines = NOTE.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("| Engine |"))
    header = _cells(lines[start])
    table: dict[str, dict[str, str]] = {}
    for line in lines[start + 2 :]:
        if not line.startswith("|"):
            break
        row = dict(zip(header, _cells(line), strict=True))
        table[row["Engine"].split()[0]] = row
    return table


TABLE = per_engine_table()
RUN_CELLS = [
    (concern, engine)
    for engine, row in TABLE.items()
    for concern in CONCERNS
    if row[concern].startswith("**Run")
]
TESTED_CELLS = [(concern, engine) for concern in CONCERNS for engine in sorted(TESTED[concern])]


def test_the_note_table_has_eight_engines_by_four_concerns() -> None:
    """Criterion 6: "Anything that must live in the workload's event mapping is listed per
    engine": the table has the eight engines and a cell for each concern."""
    concerns_per_row = {tuple(c for c in CONCERNS if c in row) for row in TABLE.values()}

    assert (len(TABLE), concerns_per_row) == (NOTE_ENGINES, {CONCERNS})


@pytest.mark.parametrize(("concern", "engine"), RUN_CELLS)
def test_each_cell_marked_run_is_an_engine_a_scenario_test_runs(concern: str, engine: str) -> None:
    """Criterion 6: "Anything that must live in the workload's event mapping is listed per
    engine": each cell marked Run names an engine that concern's scenario tests run."""
    assert engine in TESTED[concern]


@pytest.mark.parametrize(("concern", "engine"), TESTED_CELLS)
def test_each_engine_a_scenario_test_runs_is_marked_run(concern: str, engine: str) -> None:
    """Criterion 6: "Anything that must live in the workload's event mapping is listed per
    engine": each engine a concern's scenario tests run is marked Run in the note."""
    assert TABLE[engine][concern].startswith("**Run")
