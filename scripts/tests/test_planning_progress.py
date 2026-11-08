"""docs/planning/tools: the product plan's phase per issue, and the progress report on it.

Every test runs on hand-built rows and a temporary issues folder, never on the real backlog files.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "docs" / "planning" / "tools"
LIVE, DONE, PARKED = "W2", "WD", "WP"

Row = dict[str, Any]


def _load(name: str, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(TOOLS))
    spec = importlib.util.spec_from_file_location(
        f"planning_{name}_under_test", TOOLS / f"{name}.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def progress(monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    """The progress module, imported without running it."""
    yield _load("progress", monkeypatch)
    sys.modules.pop("planning_progress_under_test", None)


@pytest.fixture
def check(monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    """The check module, imported without running it."""
    yield _load("check", monkeypatch)
    sys.modules.pop("planning_check_under_test", None)


def row(index: int, eid: str, wave: str = LIVE, size: str = "M", deps: tuple[int, ...] = ()) -> Row:
    """One backlog row in the shape backlog.build() returns."""
    return {
        "index": index,
        "epic_id": eid,
        "file": f"{index:03d}-{eid}-x.md",
        "wave": wave,
        "size": size,
        "deps": list(deps),
    }


def rows(*items: Row) -> dict[int, Row]:
    return {r["index"]: r for r in items}


def body(ticked: int, open_: int) -> str:
    """An issue body with `ticked` met and `open_` open acceptance boxes."""
    met = "".join("- [x] met · Delivered by: `x`\n" for _ in range(ticked))
    todo = "".join("- [ ] open\n" for _ in range(open_))
    head = "## Why\n\n- [ ] not a box that counts\n\n## Acceptance criteria\n\n"
    return f"{head}{met}{todo}\n## Dependencies\n"


# --- phases ---------------------------------------------------------------------------------------


def test_a_live_issue_the_phase_table_does_not_name_is_later(progress: ModuleType) -> None:
    assert progress.phase_of(row(1, "H-1"), {}) == "later"


def test_a_done_issue_keeps_its_phase_so_its_work_still_counts(progress: ModuleType) -> None:
    assert progress.phase_of(row(1, "H-1", DONE), {"H-1": "A"}) == "A"
    assert progress.phase_of(row(2, "H-2", DONE), {}) == "done"


def test_a_parked_issue_is_parked_whatever_the_table_says(progress: ModuleType) -> None:
    assert progress.phase_of(row(1, "G-3", PARKED), {"G-3": "B"}) == "parked"


def test_boxes_count_only_the_acceptance_criteria(progress: ModuleType) -> None:
    assert progress.boxes(body(2, 3)) == (2, 5)


def test_an_issue_with_no_acceptance_section_has_no_boxes(progress: ModuleType) -> None:
    assert progress.boxes("## Why\n\n- [ ] a list item\n") == (0, 0)


# --- the critical path ----------------------------------------------------------------------------


def test_the_critical_path_is_the_longest_chain_of_open_issues(progress: ModuleType) -> None:
    m = rows(row(1, "H-1"), row(2, "H-2", deps=(1,)), row(3, "H-3", deps=(2,)), row(4, "H-4"))

    steps, _, path = progress.chains(m)[3]

    assert (steps, path) == (3, [1, 2, 3])


def test_a_done_dependency_starts_the_chain_again(progress: ModuleType) -> None:
    m = rows(row(1, "H-1"), row(2, "H-2", DONE, deps=(1,)), row(3, "H-3", deps=(2,)))

    steps, _, path = progress.chains(m)[3]

    assert (steps, path) == (1, [3])


def test_a_tie_in_length_goes_to_the_more_complex_chain(progress: ModuleType) -> None:
    m = rows(row(1, "H-1", size="S"), row(2, "H-2", size="L"), row(3, "H-3", deps=(1, 2)))

    _, rank, path = progress.chains(m)[3]

    assert path == [2, 3]
    assert rank == progress.RANK["L"] + progress.RANK["M"]


# --- what a phase waits on ------------------------------------------------------------------------


def test_a_phase_waits_on_open_issues_outside_it(progress: ModuleType) -> None:
    m = rows(
        row(1, "H-1"),
        row(2, "H-2", DONE),
        row(3, "H-3"),
        row(4, "H-4", deps=(1, 2, 3)),
    )
    phases = {"H-1": "B", "H-3": "A", "H-4": "A"}

    assert progress.waits_on(m, [3, 4], phases) == {"B": {1}}


# --- the report -----------------------------------------------------------------------------------


def test_the_report_shows_boxes_complexity_and_the_critical_path(
    progress: ModuleType, tmp_path: Path
) -> None:
    m = rows(
        row(1, "H-1", size="L"),
        row(2, "H-2", size="S", deps=(1,)),
        row(3, "H-3"),
        row(4, "H-4", DONE, size="L"),
    )
    for r, (t, o) in zip(m.values(), [(1, 1), (0, 2), (3, 1), (3, 0)], strict=True):
        (tmp_path / r["file"]).write_text(body(t, o))
    phases = {"H-1": "A", "H-2": "A", "H-3": "B", "H-4": "A"}
    names = {"A": "First", "B": "Second"}

    text = progress.report(m, progress.read_boxes(m, tmp_path), phases, names)

    assert "| A First | 1/3 | 4/7 (57%) | 1 L, 1 S |" in text
    assert "| B Second | 0/1 | 3/4 (75%) | 1 M |" in text
    assert "001 H-1 (L, A) → 002 H-2 (S, A)" in text


def test_a_done_issue_in_a_phase_adds_nothing_to_what_the_phase_waits_on(
    progress: ModuleType, tmp_path: Path
) -> None:
    m = rows(row(1, "H-1"), row(2, "H-2", DONE, deps=(1,)), row(3, "H-3"))
    for r in m.values():
        (tmp_path / r["file"]).write_text(body(1, 2))

    text = progress.report(
        m, progress.read_boxes(m, tmp_path), {"H-2": "A", "H-3": "A"}, {"A": "X"}
    )

    assert "| A X | 1/2 | 2/6 (33%) | 1 M | — |" in text


def test_the_report_has_no_time_estimate(progress: ModuleType, tmp_path: Path) -> None:
    m = rows(row(1, "H-1"))
    (tmp_path / m[1]["file"]).write_text(body(0, 3))

    text = progress.report(m, progress.read_boxes(m, tmp_path), {"H-1": "A"}, {"A": "First"})

    assert "day" not in text.lower()
    assert "week" not in text.lower()


def test_the_phase_detail_lists_each_issue_and_its_open_dependencies(
    progress: ModuleType, tmp_path: Path
) -> None:
    m = rows(row(1, "H-1"), row(2, "H-2", size="S", deps=(1,)))
    for r in m.values():
        (tmp_path / r["file"]).write_text(body(1, 2))

    text = progress.detail(m, progress.read_boxes(m, tmp_path), "A", {"H-2": "A"})

    assert "| 002 H-2 | 1/3 | S | 001 H-1 (later) |" in text


# --- check.py holds the phase table and the plan together -----------------------------------------


def fm_issue(eid: str, wave: str) -> tuple[Path, str, dict[str, Any]]:
    """One (path, text, frontmatter) triple, as check.read_issues returns it."""
    return Path(f"001-{eid}-x.md"), "", {"epic_id": eid, "milestone": wave}


def test_check_refuses_a_phase_for_an_issue_the_backlog_does_not_have(check: ModuleType) -> None:
    errors = check.check_product_phases([fm_issue("H-1", check.WAVES["W2"])], {"H-9": "A"})

    assert errors == ["product_phases.py: H-9 is not a backlog issue"]


def test_check_refuses_a_phase_for_a_parked_issue(check: ModuleType) -> None:
    errors = check.check_product_phases([fm_issue("G-3", check.PARKED)], {"G-3": "B"})

    assert errors == ["product_phases.py: G-3 is parked; a parked issue has no phase"]


def test_check_lets_a_done_issue_keep_its_phase(check: ModuleType) -> None:
    assert check.check_product_phases([fm_issue("H-1", check.DONE)], {"H-1": "A"}) == []


def test_check_refuses_a_phase_outside_a_to_g(check: ModuleType) -> None:
    errors = check.check_product_phases([fm_issue("H-1", check.WAVES["W2"])], {"H-1": "Z"})

    assert errors == ["product_phases.py: H-1 has phase 'Z', not one of A to G"]


PLAN = """# Plan

### The issues in each phase

| Phase | Issues |
| ----- | ------ |
| A | [001 H-1](../x.md), [002 H-2](../y.md) |
| B | [003 H-3](../z.md); the rings of 004 H-4 (counted elsewhere) |

## Next
"""


def test_check_passes_a_plan_table_that_matches(check: ModuleType) -> None:
    phases = {"H-1": "A", "H-2": "A", "H-3": "B"}

    assert check.check_plan_table(PLAN, phases) == []


def test_check_flags_each_issue_the_plan_puts_in_another_phase(check: ModuleType) -> None:
    phases = {"H-1": "A", "H-2": "B", "H-3": "B"}

    errors = check.check_plan_table(PLAN, phases)

    assert errors == [
        "product plan: phase A lists H-2; product_phases.py puts it in B",
        "product plan: phase B does not list H-2, which product_phases.py puts there",
    ]
