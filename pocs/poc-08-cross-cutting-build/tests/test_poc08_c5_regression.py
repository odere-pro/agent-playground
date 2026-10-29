"""PoC-8 exit criterion 5: "A regression in business logic fails CI through the offline eval."
(docs/planning/poc/008-PoC-8-build-cross-cutting.md)

The regression is the planning doc's demo: business logic that drops facts (`bakeoff.golden.logic`,
`drop-facts`: every answer loses its first expected fact). The gate is `bakeoff.evalgate` against
the committed baseline `evals/gate/baseline.json`, called through its CLI entry (`main`), the way
CI and `make evals-regression` call it. Offline, no socket beyond the cells' Unix sockets.

- **Every baseline target, offline.** The reference scorer (`bakeoff.evalcheck.offline_report`)
  answers the 50 gate cases as each logic does. The gate exits 0 on `normal` and 1 on
  `drop-facts`, for every engine and lane the baseline lists.
- **Through every PoC-8 cell.** The cell's real engine, behind the chassis with every stage on,
  answers `THROUGH_CELL` gate cases with the business logic's reply. Its answers are scored with
  the same checks (`score_case`) and replace those rows in the report. The gate passes the normal
  logic and fails the drop-facts logic for that engine and lane. So the answer the engine and the
  chassis deliver is what the gate scores, and nothing on the way restores a dropped fact.
- **The CI job is the same command.** `poc08-evals.yml`'s `regression` job runs
  `make evals-regression`, whose recipe runs the drop-facts logic and fails unless the gate exits 1.

The full DeepEval run through each stack is CI's (`poc08-evals.yml`, job `evals`); it needs the
eval tool and is not offline.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
import yaml
from bakeoff.evalcheck import offline_report, score_case
from bakeoff.evalgate import BASELINE, load_baseline
from bakeoff.evalgate import main as evalgate
from bakeoff.evalrun import report_stem
from bakeoff.golden import Case, load_gate_cases
from bakeoff.golden.generate import judge_script, model_script, simulate
from bakeoff.golden.logic import LOGICS, apply_logic
from fake_model_server import Script
from poc08_harness import FETCH, GATE_MARK, LOOK_IT_UP, ROOT, cell_params, full_stack

THROUGH_CELL = 3
"""suggested: gate cases per cell through the real engine; the offline scorer gives the rest."""
DROP = "drop-facts"


def _targets() -> list[tuple[str, str]]:
    baseline = load_baseline(BASELINE)
    return [
        (engine, lane)
        for agent in baseline["agents"].values()
        for engine, lanes in agent["engines"].items()
        for lane in lanes
    ]


def _write(folder: Path, reports: Sequence[dict[str, Any]]) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    for report in reports:
        stem = report_stem(report["engine"], report["meta"]["lane"], report["meta"]["logic"])
        (folder / f"{stem}.json").write_text(json.dumps(report))
    return folder


@pytest.mark.parametrize("logic", ["normal", DROP])
def test_the_gate_fails_drop_facts_and_passes_normal_on_every_baseline_target(
    logic: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Criterion 5 (offline, every baseline target): `python -m bakeoff.evalgate` exits 0 on the
    normal logic and 1 on the logic that drops facts."""
    targets = _targets()
    assert len(targets) >= 15, targets
    reports = [offline_report(engine, lane, logic=logic) for engine, lane in targets]
    code = evalgate(["--report", str(_write(tmp_path / logic, reports))])
    out = capsys.readouterr().out
    assert code == (1 if logic == DROP else 0), out
    assert f"eval gate: {0 if logic == DROP else len(targets)} passed" in out, out


def _through_cell_cases() -> list[Case]:
    """Must-pass gate cases with no tool call whose first fact the drop-facts logic removes, and
    whose input names none of the harness's scripted triggers."""
    must_pass = set(load_baseline(BASELINE)["agents"]["simplifier"]["must_pass"])
    dropped = {c.id: c for c in apply_logic(load_gate_cases(), DROP)}
    picked = [
        case
        for case in load_gate_cases()
        if case.id in must_pass
        and not case.tool_calls
        and dropped[case.id].reference != case.reference
        and not any(m in case.input for m in (FETCH, GATE_MARK, LOOK_IT_UP))
    ]
    return picked[:THROUGH_CELL]


def _reply(case: Case, logic: str) -> str:
    variant = apply_logic([case], logic)[0]
    _, reply = simulate(variant, Script.model_validate(model_script([variant])))
    return reply


def _with_rows(report: dict[str, Any], rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    cases = [rows.get(c["id"], c) for c in report["cases"]]
    passed = sum(1 for c in cases if c["pass"])
    summary = {"total": len(cases), "passed": passed, "failed": len(cases) - passed}
    return {**report, "cases": cases, "summary": summary}


@pytest.mark.parametrize(("engine", "lane"), cell_params())
async def test_drop_facts_through_the_cell_fails_the_gate_and_normal_passes(
    engine: str,
    lane: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Criterion 5 (through every cell): the cell's engine delivers the business logic's
    answers; scored and gated, the normal logic passes and the drop-facts logic fails."""
    assert (engine, lane) in _targets(), f"{engine}/{lane} is not in the gate baseline"
    cases = _through_cell_cases()
    assert len(cases) == THROUGH_CELL
    judge = Script.model_validate(judge_script(load_gate_cases()))
    answers: dict[str, dict[str, str]] = {logic: {} for logic in LOGICS}
    with full_stack(engine, lane, monkeypatch) as stack:
        for logic in LOGICS:
            for case in cases:
                stack.model.reset()
                stack.model.answer = _reply(case, logic)
                response = await stack.run(case.input)
                body = response.json()
                assert body["status"] == "ok", body
                answers[logic][case.id] = body["output"]["text"]
    codes: dict[str, int] = {}
    for logic in LOGICS:
        for case in cases:
            assert answers[logic][case.id].strip() == _reply(case, logic).strip(), (
                f"{engine}/{lane} changed the {logic} answer of {case.id}"
            )
        rows = {c.id: score_case(c, answers[logic][c.id], [], judge) for c in cases}
        assert all(r["pass"] for r in rows.values()) == (logic == "normal"), rows
        report = _with_rows(offline_report(engine, lane, logic=logic), rows)
        folder = _write(tmp_path / logic, [report])
        codes[logic] = evalgate(["--report", str(folder), "--engine", engine, "--lane", lane])
    out = capsys.readouterr().out
    assert codes == {"normal": 0, DROP: 1}, out


def _recipe(makefile: str, target: str) -> list[str]:
    lines = makefile.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f"{target}:"))
    recipe: list[str] = []
    for line in lines[start + 1 :]:
        if not line.startswith("\t"):
            break
        recipe.append(line.strip())
    return recipe


def test_the_ci_regression_job_runs_the_same_drop_facts_gate() -> None:
    """Criterion 5 (CI): the `regression` job runs `make evals-regression`, which runs the
    drop-facts logic through `bakeoff.evalrun` and fails unless `bakeoff.evalgate` exits 1."""
    workflow = yaml.safe_load((ROOT / ".github/workflows/poc08-evals.yml").read_text())
    runs = [step.get("run", "") for step in workflow["jobs"]["regression"]["steps"]]
    assert any(run.startswith("make evals-regression") for run in runs), runs
    recipe = " ".join(_recipe((ROOT / "Makefile").read_text(), "evals-regression"))
    assert f"-m bakeoff.evalrun --logic {DROP}" in recipe
    assert "-m bakeoff.evalgate" in recipe
    assert "rc -eq 1" in recipe and "exit 1" in recipe, "only exit 1 from the gate is a pass"
    assert DROP in LOGICS
