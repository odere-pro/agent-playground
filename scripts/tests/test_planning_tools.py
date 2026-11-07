"""docs/planning/tools: retire (wave WD) and park (wave WP) a backlog issue with its number kept.

Every test runs on a temporary tree or on hand-built rows, never on the real backlog files.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "docs" / "planning" / "tools"
LIVE = "W2 Chassis MVP"
DONE = "Done: delivered by the PoC track"
PARKED = "Parked: the SLM track (2026-11-06)"
OPEN = "- [ ] an open criterion"
EVIDENCE = "pocs/poc-01-x/tests/test_a.py"
TICK = f"- [x] a met criterion · Delivered in PoC-1: `{EVIDENCE}::test_it`"
NOTE = "docs/notes/n.md"
STORIES = ("H-1", "H-2", "H-15", "X-5")

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
def sync(monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    """The sync module, imported without running it."""
    yield _load("sync", monkeypatch)
    sys.modules.pop("planning_sync_under_test", None)


@pytest.fixture
def check(monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    """The check module, imported without running it."""
    yield _load("check", monkeypatch)
    sys.modules.pop("planning_check_under_test", None)


# --- sync.py -------------------------------------------------------------------------------------


def row(index: int, eid: str, wave: str, size: str = "M", deps: tuple[int, ...] = ()) -> Row:
    """One backlog row in the shape backlog.build() returns."""
    return {
        "index": index,
        "epic_id": eid,
        "title": f"title {eid}",
        "file": f"{index:03d}-{eid}-x.md",
        "wave": wave,
        "layers": ["platform"],
        "priority": "P1",
        "size": size,
        "deps": list(deps),
        "phase": "0",
    }


def rows(*items: Row) -> dict[int, Row]:
    return {r["index"]: r for r in items}


PLAN_TEMPLATE = (
    "done {{R:WD}} {{N_DONE}} {{DONE_DAYS}}\nparked {{R:WP}} {{N_PARKED}} {{PARKED_DAYS}}\n"
    "mvp {{MVP_PATH}} {{MVP_DAYS}}\nopen {{TOTAL_DAYS}}\n{{TABLE}}\n"
)


def test_critical_paths_counts_no_days_for_a_done_row(sync: ModuleType) -> None:
    """A WD row adds 0 days and no step to a chain."""
    m = rows(row(1, "H-1", "WD", "L"), row(2, "H-2", "W2", "S", deps=(1,)))

    best = sync.critical_paths(m)

    assert best[2] == (1, [2])


def test_critical_paths_counts_no_days_for_a_parked_row(sync: ModuleType) -> None:
    """A WP row adds 0 days and no step to a chain."""
    m = rows(row(1, "H-1", "W2", "S"), row(2, "H-2", "WP", "L", deps=(1,)))

    best = sync.critical_paths(m)

    assert best[2] == (0, [])


@pytest.mark.parametrize("wave", ["WD", "WP"])
def test_wave_range_is_a_dash_when_the_wave_is_empty(sync: ModuleType, wave: str) -> None:
    """An empty wave renders as a dash, so {{R:WD}} and {{R:WP}} work before any row moves."""
    m = rows(row(1, "H-1", "W2"))

    assert sync.wave_range(m, wave) == "—"


def test_plan_renders_the_done_and_parked_totals(sync: ModuleType) -> None:
    """The plan counts done and parked rows and their days, and leaves them out of the open days."""
    m = rows(
        row(1, "H-1", "WD", "L"),
        row(2, "H-2", "W2", "S"),
        row(3, "X-5", "WP", "M"),
        row(4, "H-15", "W5", "M", deps=(2,)),
    )

    text, total, _ = sync.plan_text(m, PLAN_TEMPLATE)

    assert "done 001 1 5\n" in text
    assert "parked 003 1 2.5\n" in text
    assert total == 3.5
    assert "open 3.5\n" in text


def test_plan_renders_empty_done_and_parked_waves(sync: ModuleType) -> None:
    """With no WD or WP row, the plan still renders, with a dash for both ranges."""
    m = rows(row(1, "H-1", "W2", "S"), row(2, "H-15", "W5", "M", deps=(1,)))

    text, _, _ = sync.plan_text(m, PLAN_TEMPLATE)

    assert "done — 0 0\n" in text
    assert "parked — 0 0\n" in text


def test_plan_anchors_the_mvp_path_on_h15(sync: ModuleType) -> None:
    """MVP_PATH and MVP_DAYS follow the chain to 059 H-15, the template GA."""
    m = rows(row(1, "H-1", "W2", "S"), row(2, "H-15", "W5", "M", deps=(1,)), row(3, "H-2", "W5"))

    text, _, _ = sync.plan_text(m, PLAN_TEMPLATE)

    assert "mvp 001 H-1 → 002 H-15 3.5\n" in text


def test_plan_lists_scattered_done_rows_in_one_table(sync: ModuleType) -> None:
    """The Done table lists every WD row in index order, even when other waves sit between them."""
    m = rows(row(1, "H-1", "WD"), row(2, "H-15", "W5"), row(3, "H-2", "WD"))

    text, _, _ = sync.plan_text(m, PLAN_TEMPLATE)
    done = text.split(f"### {DONE}\n", 1)[1].split("\n\n", 1)[0]
    ids = [line.split(" | ")[1] for line in done.splitlines() if line.startswith("| 0")]

    assert ids == ["H-1", "H-2"]


def test_summary_prints_done_parked_and_open_totals(sync: ModuleType) -> None:
    """sync's last line names the done, parked, and open totals."""
    m = rows(row(1, "H-1", "WD", "L"), row(2, "H-15", "W5", "S"), row(3, "H-2", "WP", "M"))

    line = sync.summary(m, 1, "002 H-15")

    assert "1 done (5 days), 1 parked (2.5 days)" in line
    assert "1 engineer-days open" in line


# --- check.py ------------------------------------------------------------------------------------


def issue(
    index: int,
    eid: str,
    boxes: tuple[str, ...] = (OPEN, OPEN, OPEN),
    milestone: str = LIVE,
    deps: tuple[str, ...] = (),
) -> tuple[str, str]:
    """One issue file: its name and its text, in the shape sync.py writes."""
    fm = (
        f'---\ntitle: "{eid}: t"\nlabels: ["story"]\nmilestone: "{milestone}"\nindex: {index}\n'
        f"epic_id: {eid}\ndepends_on: {json.dumps(list(deps))}\nblocks: []\nepic_refs: []\n---\n"
    )
    body = (
        "\n## Why\n\nw\n\n## What\n\nw\n\n## Out of scope\n\n- none\n\n## Acceptance criteria\n\n"
        + "\n".join(boxes)
        + "\n\n## Dependencies\n\n- Depends on: none\n\n## References\n\n- r\n"
    )
    return f"{index:03d}-{eid}-x.md", fm + body


@pytest.fixture
def tree(tmp_path: Path) -> Callable[..., Path]:
    """A repo with docs/planning: an epic with STORIES, its checksum, and the given issues."""

    def make(*issues: tuple[str, str]) -> Path:
        root = tmp_path / "docs" / "planning"
        (root / "issues").mkdir(parents=True)
        (root / "tools").mkdir()
        epic = "".join(f"{n}. **{s}** story\n" for n, s in enumerate(STORIES, 1)).encode()
        (root / "slm-agent-platform-epic-v3.md").write_bytes(epic)
        digest = hashlib.sha256(epic).hexdigest()
        (root / "tools" / "epic.sha256").write_text(f"{digest}  slm-agent-platform-epic-v3.md\n")
        for name, text in issues:
            (root / "issues" / name).write_text(text)
        (tmp_path / EVIDENCE).parent.mkdir(parents=True)
        (tmp_path / EVIDENCE).write_text("def test_it():\n    pass\n")
        (tmp_path / NOTE).parent.mkdir(parents=True)
        (tmp_path / NOTE).write_text('# Note\n\n<a id="r1"></a>\n\n## A result\n')
        return root

    return make


def clean_issues(*extra: tuple[str, str]) -> tuple[tuple[str, str], ...]:
    """Issues that cover every story in STORIES once, then the extra ones."""
    base = [issue(n, eid) for n, eid in enumerate(STORIES, 1)]
    return (*base, *extra)


def test_check_passes_a_clean_tree(check: ModuleType, tree: Callable[..., Path]) -> None:
    """The temporary tree the other tests change has no error."""
    root = tree(*clean_issues())

    errors, _ = check.run(root)

    assert errors == []


def test_check_counts_ticked_boxes_as_acceptance_criteria(
    check: ModuleType, tree: Callable[..., Path]
) -> None:
    """A fully ticked issue passes the box count."""
    issues = list(clean_issues())
    issues[0] = issue(1, "H-1", boxes=(TICK, TICK, TICK))
    root = tree(*issues)

    errors, _ = check.run(root)

    assert errors == []


def test_check_fails_an_issue_with_fewer_than_three_boxes(
    check: ModuleType, tree: Callable[..., Path]
) -> None:
    """Two boxes, open or ticked, are fewer than 3 acceptance criteria."""
    issues = list(clean_issues())
    issues[0] = issue(1, "H-1", boxes=(OPEN, TICK))
    root = tree(*issues)

    errors, _ = check.run(root)

    assert errors == ["001-H-1-x.md: fewer than 3 acceptance criteria"]


@pytest.mark.parametrize("with_b", [False, True])
def test_check_accepts_x5_with_or_without_its_b_half(
    check: ModuleType, tree: Callable[..., Path], with_b: bool
) -> None:
    """X-5 is covered once before X-5b exists and twice after."""
    extra = (issue(5, "X-5b"),) if with_b else ()
    root = tree(*clean_issues(*extra))

    errors, _ = check.run(root)

    assert errors == []


def test_check_fails_x5b_without_x5(check: ModuleType, tree: Callable[..., Path]) -> None:
    """X-5b alone covers X-5 once, but the split needs both halves."""
    issues = [*clean_issues()[:3], issue(4, "X-5b")]
    root = tree(*issues)

    errors, _ = check.run(root)

    assert errors == ["story X-5 covered 1 times, expected 2"]


def test_check_fails_a_live_issue_that_depends_on_a_parked_one(
    check: ModuleType, tree: Callable[..., Path]
) -> None:
    """A live issue on a WP row is an error."""
    issues = list(clean_issues())
    issues[0] = issue(1, "H-1", milestone=PARKED)
    issues[1] = issue(2, "H-2", deps=("001 H-1",))
    root = tree(*issues)

    errors, _ = check.run(root)

    assert errors == ["002-H-2-x.md: live issue depends on parked 001 H-1"]


@pytest.mark.parametrize("milestone", [PARKED, DONE])
def test_check_lets_an_off_plan_issue_depend_on_a_parked_one(
    check: ModuleType, tree: Callable[..., Path], milestone: str
) -> None:
    """A WP or WD issue may depend on a WP row."""
    boxes = (TICK, TICK, TICK, "Delivered by: PoC-1") if milestone == DONE else (OPEN,) * 3
    issues = list(clean_issues())
    issues[0] = issue(1, "H-1", milestone=PARKED)
    issues[1] = issue(2, "H-2", boxes=boxes, milestone=milestone, deps=("001 H-1",))
    root = tree(*issues)

    errors, _ = check.run(root, strict=True)

    assert errors == []


STRICT_FAILS = {
    "no evidence words": (
        (f"- [x] met: `{EVIDENCE}`", OPEN, OPEN),
        LIVE,
        "001-H-1-x.md: ticked line has no `Delivered in PoC-` or `Delivered by:`: "
        f"- [x] met: `{EVIDENCE}`",
    ),
    "no token or link": (
        ("- [x] met. Delivered in PoC-1, see the demo", OPEN, OPEN),
        LIVE,
        "001-H-1-x.md: ticked line has no backtick token or link: "
        "- [x] met. Delivered in PoC-1, see the demo",
    ),
    "missing path": (
        ("- [x] met. Delivered in PoC-1: `pocs/gone/test_b.py::test_b`", OPEN, OPEN),
        LIVE,
        "001-H-1-x.md: ticked line cites pocs/gone/test_b.py, which does not exist",
    ),
    "missing anchor": (
        (f"- [x] met · Delivered in PoC-1: `{NOTE}#gone`", OPEN, OPEN),
        LIVE,
        f"001-H-1-x.md: ticked line cites {NOTE}#gone, which has no such anchor",
    ),
    "missing path with an anchor": (
        ("- [x] met · Delivered in PoC-1: `docs/gone.md#a-result`", OPEN, OPEN),
        LIVE,
        "001-H-1-x.md: ticked line cites docs/gone.md, which does not exist",
    ),
    "done with an open box": (
        (TICK, TICK, OPEN, "Delivered by: PoC-1"),
        DONE,
        "001-H-1-x.md: done issue has an open box",
    ),
    "done with no delivered-by line": (
        (TICK, TICK, TICK),
        DONE,
        "001-H-1-x.md: done issue has no `Delivered by:` line under ## Acceptance criteria",
    ),
    "live with no open box": (
        (TICK, TICK, TICK),
        LIVE,
        "001-H-1-x.md: every box is ticked; retire it (wave WD)",
    ),
}


@pytest.mark.parametrize("case", STRICT_FAILS, ids=list(STRICT_FAILS))
def test_check_strict_fails_each_rule(
    check: ModuleType, tree: Callable[..., Path], case: str
) -> None:
    """Each --strict rule names the issue and what is wrong."""
    boxes, milestone, want = STRICT_FAILS[case]
    issues = list(clean_issues())
    issues[0] = issue(1, "H-1", boxes=boxes, milestone=milestone)
    root = tree(*issues)

    errors, _ = check.run(root, strict=True)

    assert errors == [want]


STRICT_PASSES = {
    "a ticked line with evidence": ((TICK, OPEN, OPEN), LIVE),
    "a ticked line with a link": (
        ("- [x] met · Delivered by: [the demo](https://example.com/demo)", OPEN, OPEN),
        LIVE,
    ),
    "a heading anchor": ((f"- [x] met · Delivered in PoC-1: `{NOTE}#a-result`", OPEN, OPEN), LIVE),
    "an a-id anchor": ((f"- [x] met · Delivered in PoC-1: `{NOTE}#r1`", OPEN, OPEN), LIVE),
    "a done issue": ((TICK, TICK, TICK, "Delivered by: PoC-1 and PoC-5"), DONE),
    "a parked issue with every box open": ((OPEN, OPEN, OPEN), PARKED),
}


@pytest.mark.parametrize("case", STRICT_PASSES, ids=list(STRICT_PASSES))
def test_check_strict_passes_a_well_formed_issue(
    check: ModuleType, tree: Callable[..., Path], case: str
) -> None:
    """An issue that follows the evidence form passes --strict."""
    boxes, milestone = STRICT_PASSES[case]
    issues = list(clean_issues())
    issues[0] = issue(1, "H-1", boxes=boxes, milestone=milestone)
    root = tree(*issues)

    errors, _ = check.run(root, strict=True)

    assert errors == []


@pytest.mark.parametrize("case", STRICT_FAILS, ids=list(STRICT_FAILS))
def test_check_skips_the_strict_rules_by_default(
    check: ModuleType, tree: Callable[..., Path], case: str
) -> None:
    """Without --strict, the evidence rules do not run."""
    boxes, milestone, _ = STRICT_FAILS[case]
    issues = list(clean_issues())
    issues[0] = issue(1, "H-1", boxes=boxes, milestone=milestone)
    root = tree(*issues)

    errors, _ = check.run(root)

    assert errors == []


@pytest.mark.parametrize(
    ("argv", "want"),
    [
        ([], (1, 10**6, False)),
        (["37", "67"], (37, 67, False)),
        (["--strict"], (1, 10**6, True)),
        (["--strict", "37", "67"], (37, 67, True)),
    ],
)
def test_check_parses_the_range_and_the_strict_flag(
    check: ModuleType, argv: list[str], want: tuple[int, int, bool]
) -> None:
    """`FIRST LAST` keeps working, with or without --strict."""
    assert check.parse_args(argv) == want
