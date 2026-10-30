"""PoC-9 exit criteria 3 and 8.

Criterion 3: "Every item in the Agent MVP done-when list in 000-plan.md is checked."
Criterion 8: "A short report lists what the backlog chassis waves (W2 and W5) should change, based
on the PoC."
"""

from __future__ import annotations

import re
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
PLAN = HERE.parents[1] / "docs/planning/poc/000-plan.md"


def test_c3_the_audit_has_one_row_per_done_when_item() -> None:
    plan = PLAN.read_text().split("## Agent MVP: done when")[1].split("\n## ")[0]
    items = re.findall(r"^- \[[ x]\] ", plan, flags=re.M)
    audit = (HERE / "notes/2026-10-30-done-when.md").read_text()
    rows = re.findall(r"^\| \d+ \|.*\| (met|\*\*flagged\*\*)", audit, flags=re.M)
    assert len(rows) == len(items) == 17


def test_c8_the_report_covers_both_waves() -> None:
    report = (HERE / "notes/2026-10-30-w2-w5-report.md").read_text()
    assert "## W2" in report and "## W5" in report
    assert (HERE / "notes/backlog-changes.md").is_file()
