"""`eval-report.v0` from each tool's own report (the committed trial reports as fixtures)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from bakeoff.evalreport import KINDS, normalize, trim_raw, validate_report
from bakeoff.golden import load_cases
from bakeoff.registry import ROOT

EVALS = ROOT / "pocs/poc-07-cross-cutting-decisions/notes/evals"


def _raw(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((EVALS / name).read_text(encoding="utf-8"))
    return data


@pytest.mark.parametrize("tool", ["promptfoo", "deepeval"])
def test_bakeoff_evalreport_both_tools_normalize_to_the_same_rows(tool: str) -> None:
    report = normalize(tool, "echo-python", _raw(f"{tool}-echo-python.raw.json"))
    assert [c["id"] for c in report["cases"]] == sorted(c.id for c in load_cases())
    kinds = {a["kind"] for c in report["cases"] for a in c["assertions"]}
    assert kinds == set(KINDS)
    assert report["summary"] == {"total": 20, "passed": 13, "failed": 7}


def test_bakeoff_evalreport_tools_agree_case_by_case() -> None:
    a, b = (
        normalize(t, "echo-python", _raw(f"{t}-echo-python.raw.json"))
        for t in ("promptfoo", "deepeval")
    )
    rows = [
        [
            (c["id"], c["pass"], [(x["kind"], x["value"], x["pass"]) for x in c["assertions"]])
            for c in r["cases"]
        ]
        for r in (a, b)
    ]
    assert rows[0] == rows[1]


def test_bakeoff_evalreport_rejects_a_bad_report_and_tool() -> None:
    with pytest.raises(ValueError, match=r"eval-report\.v0"):
        validate_report({"schema": "eval-report.v0", "engine": "x", "tool": "x"})
    with pytest.raises(ValueError, match="unknown eval tool"):
        normalize("ragas", "x", {})


def test_bakeoff_evalreport_trim_drops_host_paths() -> None:
    raw = {"testFile": f"{ROOT}/evals/deepeval/test_golden.py", "testCases": []}
    assert trim_raw("deepeval", raw, str(ROOT))["testFile"] == "evals/deepeval/test_golden.py"
    for name in EVALS.glob("*.json"):
        text = Path(name).read_text(encoding="utf-8")
        assert str(ROOT) not in text and "/tmp/" not in text, name
