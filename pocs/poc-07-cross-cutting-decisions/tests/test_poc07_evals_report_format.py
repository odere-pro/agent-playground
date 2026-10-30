"""Exit criterion 8: one eval tool is picked for CI, and it produces the same report format for
every engine.

The pick is DeepEval (notes/2026-10-28-eval-tool-trial.md). Its committed reports for
`echo-python` and `echo-langgraph` (notes/evals/, written by `python -m bakeoff.evaltrial`) must be
`eval-report.v0` with the same keys at every level, one row per golden case id, and the same ids.
The tool's own raw reports must have the same keys too. The golden set has 20 labelled cases.
Reads committed files only: no socket, no tool install.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from bakeoff.evalreport import SCHEMA_ID, validate_report
from bakeoff.golden import GOLDEN_SIZE, load_cases

EVALS = Path(__file__).resolve().parents[1] / "notes" / "evals"
PICKED = "deepeval"
ENGINES = ("echo-python", "echo-langgraph")


def _load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((EVALS / name).read_text(encoding="utf-8"))
    return data


def _shape(value: Any) -> Any:
    """The keys at every level, with list items merged: the report's shape, not its values."""
    if isinstance(value, dict):
        return {k: _shape(v) for k, v in sorted(value.items()) if k != "meta"}
    if isinstance(value, list):
        merged: dict[str, Any] = {}
        kinds = set()
        for item in value:
            shape = _shape(item)
            if isinstance(shape, dict):
                merged |= shape
            else:
                kinds.add(str(shape))
        return [merged] if merged else sorted(kinds)
    return type(value).__name__


def test_the_golden_set_has_20_labelled_cases() -> None:
    """Evals scope: "each tried on the same 20 golden cases"; each case is labelled."""
    cases = load_cases()
    assert len(cases) == GOLDEN_SIZE == 20
    assert all(c.label in ("pass", "fail") for c in cases)
    assert {c.task for c in cases} == {"simplifier", "lookup"}
    assert any(c.label == "fail" for c in cases), "the tools need something to fail"


@pytest.mark.parametrize("engine", ENGINES)
def test_the_picked_tool_report_is_eval_report_v0_with_one_row_per_case(engine: str) -> None:
    """Criterion 8: "One eval tool is picked for CI"; its report is `eval-report.v0`."""
    report = _load(f"{PICKED}-{engine}.json")
    validate_report(report)
    assert report["schema"] == SCHEMA_ID
    assert (report["tool"], report["engine"]) == (PICKED, engine)
    ids = [c["id"] for c in report["cases"]]
    assert ids == sorted({c.id for c in load_cases()})
    assert report["summary"]["total"] == len(ids) == GOLDEN_SIZE
    assert report["summary"]["passed"] == sum(c["pass"] for c in report["cases"])


def test_both_engines_give_the_same_report_format() -> None:
    """Criterion 8: "it produces the same report format for every engine"."""
    first, second = (_load(f"{PICKED}-{engine}.json") for engine in ENGINES)
    assert _shape(first) == _shape(second)
    assert [c["id"] for c in first["cases"]] == [c["id"] for c in second["cases"]]
    for a, b in zip(first["cases"], second["cases"], strict=True):
        assert [(x["kind"], x["value"]) for x in a["assertions"]] == [
            (x["kind"], x["value"]) for x in b["assertions"]
        ], a["id"]


def test_the_raw_tool_reports_share_their_keys() -> None:
    """Criterion 8: "the same report format for every engine", for raw reports too."""
    first, second = (_load(f"{PICKED}-{engine}.raw.json") for engine in ENGINES)
    assert _shape(first) == _shape(second)


def test_same_cases_pass_on_both_engines_and_match_the_labels_but_one() -> None:
    """Criterion 8: "the same report format for every engine", and the same verdicts too.

    The scripted model answers the same way on both engines, so the verdicts agree; the
    scripted judge disagrees with the human label on g08 only (a false fail)."""
    cases = {c.id: c for c in load_cases()}
    verdicts = [{c["id"]: c["pass"] for c in _load(f"{PICKED}-{e}.json")["cases"]} for e in ENGINES]
    assert verdicts[0] == verdicts[1]
    disagree = sorted(i for i, ok in verdicts[0].items() if ok != (cases[i].label == "pass"))
    assert disagree == ["g08"]
