"""The PoC-8 offline eval gate (`bakeoff.evalgate`) and the offline reference scorer
(`bakeoff.evalcheck`). Offline: no process, no socket, no eval tool install.

The headline cases are the planning doc's demo, "a change to the business logic that drops facts
fails the eval step in CI": the committed baseline passes the `normal` logic on every engine and
lane it lists, and fails the `drop-facts` logic on each of them.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from bakeoff.evalcheck import offline_report
from bakeoff.evalgate import (
    BASELINE,
    GateError,
    check,
    load_baseline,
    load_reports,
    main,
    record,
)
from bakeoff.evalreport import validate_report
from bakeoff.golden import load_cases, load_gate_cases
from bakeoff.registry import ROOT

POC07_REPORTS = ROOT / "pocs/poc-07-cross-cutting-decisions/notes/evals"


def _targets() -> list[tuple[str, str]]:
    baseline = load_baseline(BASELINE)
    return [
        (engine, lane)
        for agent in baseline["agents"].values()
        for engine, lanes in agent["engines"].items()
        for lane in lanes
    ]


def _reports(logic: str) -> list[dict[str, Any]]:
    return [offline_report(engine, lane, logic=logic) for engine, lane in _targets()]


def test_bakeoff_evalgate_offline_report_is_eval_report_v0_with_one_row_per_gate_case() -> None:
    report = offline_report("echo-python", "sidecar")
    validate_report(report)
    assert [c["id"] for c in report["cases"]] == [c.id for c in load_gate_cases()]
    assert report["meta"] == {"lane": "sidecar", "logic": "normal", "runner": "offline"}
    assert report["summary"] == {"total": 50, "passed": 39, "failed": 11}


@pytest.mark.parametrize("engine", ["echo-python", "echo-langgraph"])
def test_bakeoff_evalgate_offline_scorer_agrees_with_deepeval_on_the_poc07_cases(
    engine: str,
) -> None:
    """The scorer's verdict on every case and every assertion equals the committed DeepEval run."""
    deepeval = json.loads((POC07_REPORTS / f"deepeval-{engine}.json").read_text())
    ours = offline_report(engine, "sidecar", cases=load_cases())

    def verdicts(report: dict[str, Any]) -> dict[str, Any]:
        return {
            c["id"]: (
                c["pass"],
                sorted((a["kind"], a["value"], a["pass"]) for a in c["assertions"]),
            )
            for c in report["cases"]
        }

    assert verdicts(ours) == verdicts(deepeval)


def test_bakeoff_evalgate_committed_baseline_covers_every_offline_engine_and_lane() -> None:
    baseline = load_baseline(BASELINE)
    assert baseline["cases"] == 50
    targets = set(_targets())
    for engine in ("echo-python", "echo-pydanticai", "echo-langgraph", "echo-openai-agents"):
        assert {(engine, lane) for lane in ("inprocess", "sidecar", "remote")} <= targets, engine
    for engine, reason in baseline["skipped"].items():
        assert reason.strip(), engine
        assert all(e != engine for e, _ in targets), f"{engine} is both run and skipped"


def test_bakeoff_evalgate_passes_the_normal_logic_on_every_baseline_target() -> None:
    results = check(load_baseline(BASELINE), _reports("normal"))
    assert results and all(r.ok for r in results), [r.line() for r in results if not r.ok]


def test_bakeoff_evalgate_fails_the_drop_facts_logic_on_every_baseline_target() -> None:
    results = check(load_baseline(BASELINE), _reports("drop-facts"))
    assert results and not any(r.ok for r in results)
    for result in results:
        assert any("must-pass" in p for p in result.problems), result.line()
        assert any("pass rate" in p for p in result.problems), result.line()


def _one_report(**changes: Any) -> dict[str, Any]:
    report = copy.deepcopy(offline_report("echo-python", "sidecar"))
    report.update(changes)
    return report


def _small_baseline() -> dict[str, Any]:
    return record([offline_report("echo-python", "sidecar")], agent="simplifier", skipped={})


def test_bakeoff_evalgate_record_writes_what_the_reports_show() -> None:
    baseline = _small_baseline()
    agent = baseline["agents"]["simplifier"]
    assert agent["engines"] == {"echo-python": {"sidecar": 0.78}}
    assert "g01" in agent["must_pass"] and "g08" not in agent["must_pass"]
    assert agent["must_fail"] == [
        "g09",
        "g10",
        "g11",
        "g12",
        "g19",
        "g20",
        "g39",
        "g40",
        "g41",
        "g50",
    ]
    assert check(baseline, [offline_report("echo-python", "sidecar")])[0].ok


def test_bakeoff_evalgate_record_refuses_a_known_bad_answer_that_passes() -> None:
    report = _one_report()
    g09 = next(c for c in report["cases"] if c["id"] == "g09")
    g09["pass"] = True
    with pytest.raises(GateError, match="g09"):
        record([report], agent="simplifier", skipped={})


def test_bakeoff_evalgate_fails_when_a_known_bad_answer_starts_to_pass() -> None:
    """The six PoC-7 wrong answers and four more are canaries: if one passes, the eval lost its
    teeth (a check stopped checking), and the gate says so even though the pass rate went up."""
    report = _one_report()
    for case in report["cases"]:
        if case["id"] == "g10":
            case["pass"] = True
    [result] = check(_small_baseline(), [report])
    assert not result.ok
    assert any("must-fail" in p and "g10" in p for p in result.problems)


def test_bakeoff_evalgate_fails_a_missing_report_an_unknown_engine_and_a_short_report() -> None:
    baseline = _small_baseline()
    [missing] = check(baseline, [])
    assert not missing.ok and "no report" in missing.problems[0]
    stranger = _one_report(engine="echo-unknown")
    results = check(baseline, [stranger, offline_report("echo-python", "sidecar")])
    assert [r.ok for r in results] == [True, False]
    assert "no baseline" in results[1].problems[0]
    short = _one_report()
    short["cases"] = short["cases"][:40]
    [result] = check(baseline, [short])
    assert not result.ok and any("cases" in p for p in result.problems)


def test_bakeoff_evalgate_engine_filter_checks_only_those_targets() -> None:
    baseline = load_baseline(BASELINE)
    reports = [offline_report("echo-python", lane) for lane in ("inprocess", "sidecar", "remote")]
    results = check(baseline, reports, engines={"echo-python"})
    assert len(results) == 3 and all(r.ok for r in results)


def _write(folder: Path, reports: list[dict[str, Any]]) -> None:
    folder.mkdir()
    for report in reports:
        name = f"x-{report['engine']}-{report['meta']['lane']}.json"
        (folder / name).write_text(json.dumps(report))
    (folder / "x.raw.json").write_text("{}")  # a tool's own report is not read


def test_bakeoff_evalgate_cli_exit_codes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    good, bad = tmp_path / "good", tmp_path / "bad"
    lanes = ("inprocess", "sidecar", "remote")
    _write(good, [offline_report("echo-python", lane) for lane in lanes])
    _write(bad, [offline_report("echo-python", lane, logic="drop-facts") for lane in lanes])
    args = ["--baseline", str(BASELINE), "--engine", "echo-python", "--report"]
    assert main([*args, str(good)]) == 0
    assert main([*args, str(bad)]) == 1
    out = capsys.readouterr().out
    assert "PASS simplifier echo-python sidecar 39/50" in out
    assert "FAIL simplifier echo-python sidecar" in out
    assert main(["--baseline", str(tmp_path / "none.json"), "--report", str(good)]) == 2


def test_bakeoff_evalgate_load_reports_reads_files_and_folders(tmp_path: Path) -> None:
    folder = tmp_path / "r"
    _write(folder, [offline_report("echo-python", "sidecar")])
    single = folder / "x-echo-python-sidecar.json"
    assert len(load_reports([folder])) == 1 and len(load_reports([single])) == 1
    with pytest.raises(GateError, match="no eval-report"):
        load_reports([tmp_path / "empty"])
