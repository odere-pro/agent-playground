"""Progress of the product plan, per phase. Read-only: it writes nothing.

Usage: python3 tools/progress.py [PHASE]   (from docs/planning; `make progress` from the repo root)
With PHASE (A to G), it also lists that phase's issues.

For each phase it shows four things:
- Boxes: the acceptance criteria ticked with evidence, which `check.py --strict` verifies.
- Issues done: issues retired to wave WD, out of the phase's issues.
- Open complexity: the sizes S, M, and L of the issues still open. A size ranks how hard an issue
  is to build right. It is not a time estimate.
- Waits on: the open issues in other phases that the phase depends on.
- The critical path: the longest chain of open issues by dependency, counted in issues. A tie goes
  to the more complex chain.

The phase of each issue comes from `product_phases.py`. The boxes come from the issue files.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import backlog
from product_phases import PHASES, PRODUCT_PHASE

TOOLS = Path(__file__).resolve().parent
ISSUES = TOOLS.parent / "issues"
DONE, PARKED = "WD", "WP"
RANK = {"S": 1, "M": 2, "L": 3}
BOX = re.compile(r"^- \[([ x])\]", re.M)
OTHER = ("later",)

Row = dict[str, Any]
Rows = dict[int, Row]
Boxes = dict[int, tuple[int, int]]
Chain = tuple[int, int, list[int]]


def phase_of(row: Row, phases: dict[str, str] = PRODUCT_PHASE) -> str:
    """A row's product phase. A parked row is `parked`. Otherwise the table's phase, which a done
    row keeps so its work still counts; a row the table does not name is `done` or `later`."""
    if row["wave"] == PARKED:
        return "parked"
    return phases.get(row["epic_id"], "done" if row["wave"] == DONE else "later")


def is_open(row: Row) -> bool:
    return row["wave"] not in (DONE, PARKED)


def boxes(text: str) -> tuple[int, int]:
    """(ticked, total) acceptance boxes. Only the `## Acceptance criteria` section counts."""
    if "## Acceptance criteria" not in text:
        return 0, 0
    section = text.split("## Acceptance criteria", 1)[1].split("## Dependencies", 1)[0]
    marks = BOX.findall(section)
    return marks.count("x"), len(marks)


def read_boxes(rows: Rows, issues: Path = ISSUES) -> Boxes:
    """The boxes of every row whose issue file exists; (0, 0) for one that does not."""
    out: Boxes = {}
    for i, r in rows.items():
        path = issues / r["file"]
        out[i] = boxes(path.read_text()) if path.exists() else (0, 0)
    return out


def chains(rows: Rows) -> dict[int, Chain]:
    """The longest open chain that ends at each row, as (issues, complexity, indexes).

    A done or parked row ends a chain: what it waited on no longer blocks anything after it.
    Dependencies always have a lower index, so one pass in index order is enough.
    """
    best: dict[int, Chain] = {}
    for i in sorted(rows):
        r = rows[i]
        if not is_open(r):
            best[i] = (0, 0, [])
            continue
        prev = max(r["deps"], key=lambda d: best[d][:2], default=None)
        steps, rank, path = best[prev] if prev is not None else (0, 0, [])
        best[i] = (steps + 1, rank + RANK[r["size"]], [*path, i])
    return best


def waits_on(rows: Rows, members: list[int], phases: dict[str, str]) -> dict[str, set[int]]:
    """The open dependencies of a phase's issues that sit outside the phase, by their phase."""
    inside = set(members)
    out: dict[str, set[int]] = {}
    for i in members:
        for d in rows[i]["deps"]:
            if d in inside or not is_open(rows[d]):
                continue
            out.setdefault(phase_of(rows[d], phases), set()).add(d)
    return out


def name(rows: Rows, i: int) -> str:
    return f"{i:03d} {rows[i]['epic_id']}"


def complexity(rows: Rows, members: list[int]) -> str:
    """`1 L, 2 M, 3 S`, largest first, sizes with no issue left out."""
    count = Counter(rows[i]["size"] for i in members)
    return ", ".join(f"{count[s]} {s}" for s in ("L", "M", "S") if count[s]) or "—"


def ticked(box: Boxes, members: list[int]) -> str:
    done = sum(box[i][0] for i in members)
    total = sum(box[i][1] for i in members)
    share = f" ({100 * done // total}%)" if total else ""
    return f"{done}/{total}{share}"


def waits_text(rows: Rows, waits: dict[str, set[int]], order: list[str]) -> str:
    parts = [
        f"{ph}: " + ", ".join(name(rows, d) for d in sorted(waits[ph]))
        for ph in order
        if ph in waits
    ]
    return "; ".join(parts) or "—"


def path_text(rows: Rows, path: list[int], phases: dict[str, str]) -> str:
    return " → ".join(
        f"{name(rows, i)} ({rows[i]['size']}, {phase_of(rows[i], phases)})" for i in path
    )


def open_only(rows: Rows, members: list[int]) -> list[int]:
    return [i for i in members if is_open(rows[i])]


def done_text(rows: Rows, members: list[int]) -> str:
    return f"{len(members) - len(open_only(rows, members))}/{len(members)}"


def members_of(rows: Rows, phase: str, phases: dict[str, str]) -> list[int]:
    return [i for i in sorted(rows) if phase_of(rows[i], phases) == phase]


def report(
    rows: Rows,
    box: Boxes,
    phases: dict[str, str] = PRODUCT_PHASE,
    names: dict[str, str] = PHASES,
) -> str:
    """The progress table, the critical path per phase, and the critical path to the whole plan."""
    order = [*names, *OTHER]
    best = chains(rows)
    lines = [
        "# Product plan progress",
        "",
        "Boxes: acceptance criteria ticked with evidence. Open complexity: sizes S, M, L of the",
        "issues still open. Waits on: open issues in other phases this phase depends on.",
        "",
        "| Phase | Issues done | Boxes ticked | Open complexity | Waits on |",
        "| ----- | ----------- | ------------ | --------------- | -------- |",
    ]
    plan: list[int] = []
    for ph, title in names.items():
        members = members_of(rows, ph, phases)
        plan += members
        waits = waits_on(rows, open_only(rows, members), phases)
        lines.append(
            f"| {ph} {title} | {done_text(rows, members)} | {ticked(box, members)} | "
            f"{complexity(rows, open_only(rows, members))} | {waits_text(rows, waits, order)} |"
        )
    lines.append(
        f"| All phases | {done_text(rows, plan)} | {ticked(box, plan)} | "
        f"{complexity(rows, open_only(rows, plan))} | |"
    )
    lines += ["", "## Critical path per phase", ""]
    for ph in names:
        members = open_only(rows, members_of(rows, ph, phases))
        if not members:
            lines.append(f"- **{ph}**: no open issue")
            continue
        steps, _, path = max((best[i] for i in members), key=lambda c: c[:2])
        lines.append(f"- **{ph}** ({steps} issues): {path_text(rows, path, phases)}")
    if open_only(rows, plan):
        steps, _, path = max((best[i] for i in open_only(rows, plan)), key=lambda c: c[:2])
        lines += ["", "## Critical path to the whole plan", ""]
        lines.append(f"{steps} issues: {path_text(rows, path, phases)}")
    return "\n".join(lines)


def detail(rows: Rows, box: Boxes, phase: str, phases: dict[str, str] = PRODUCT_PHASE) -> str:
    """One phase's issues: boxes, complexity, and the open dependencies of each."""
    lines = [
        f"## Phase {phase}: the issues",
        "",
        "| Issue | Boxes | Complexity | Open dependencies |",
        "| ----- | ----- | ---------- | ----------------- |",
    ]
    for i in members_of(rows, phase, phases):
        deps = [d for d in rows[i]["deps"] if is_open(rows[d])] if is_open(rows[i]) else []
        dep_text = ", ".join(f"{name(rows, d)} ({phase_of(rows[d], phases)})" for d in deps) or "—"
        state = rows[i]["size"] if is_open(rows[i]) else f"{rows[i]['size']}, done"
        lines.append(f"| {name(rows, i)} | {box[i][0]}/{box[i][1]} | {state} | {dep_text} |")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if len(argv) > 1 or (argv and argv[0] not in PHASES):
        print(f"usage: progress.py [PHASE], with PHASE one of {', '.join(PHASES)}", file=sys.stderr)
        return 2
    rows = backlog.build()
    box = read_boxes(rows)
    print(report(rows, box))
    if argv:
        print()
        print(detail(rows, box, argv[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
