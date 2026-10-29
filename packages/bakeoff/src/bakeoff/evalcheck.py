"""The offline reference scorer: the golden set's four checks, with no engine and no eval tool.

`offline_report(engine, lane, logic=...)` replays every case through the generated model script
(`bakeoff.golden.generate.simulate`, the fake model server's own `Script.pick` and the chassis's
fake tools), asks the scripted judge for the rubric verdict, and writes `eval-report.v0` with
`tool: offline`. It names the engine and lane only so the gate can look the report up: the script
is what every engine answers with, so the verdicts are the same for every engine.

What it is for: the offline tests of the gate (`make test`, no socket), and a cross-check that it
agrees with DeepEval case by case and assertion by assertion on the committed PoC-7 reports
(`test_bakeoff_evalgate.py`). CI runs DeepEval through the chassis on every engine
(`bakeoff.evalrun`); this scorer never replaces it (ADR-010 rejected our own runner, T5).

The checks mirror `evals/deepeval/test_golden.py`: `contains` and `banned` are case-insensitive
substring checks, `tools` compares names and arguments in order, `rubric` is the judge's `pass`.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from fake_model_server import Script

from bakeoff.evalreport import SCHEMA_ID, validate_report
from bakeoff.golden import Case, load_gate_cases
from bakeoff.golden.generate import judge_script, model_script, simulate
from bakeoff.golden.logic import apply_logic

__all__ = ["RUNNER", "offline_report", "score_case"]

RUNNER = "offline"


def _assertion(kind: str, value: str, problem: str) -> dict[str, Any]:
    return {"kind": kind, "value": value, "pass": not problem, "reason": problem or "ok"}


def score_case(
    case: Case, answer: str, calls: Sequence[dict[str, Any]], judge: Script
) -> dict[str, Any]:
    """One `eval-report.v0` case row for `answer` and the tool `calls` it made."""
    folded = answer.casefold()
    rows = [
        _assertion("contains", fact, "" if fact.casefold() in folded else f"output lacks {fact!r}")
        for fact in case.contains
    ]
    rows += [
        _assertion(
            "banned", word, f"output holds banned {word!r}" if word.casefold() in folded else ""
        )
        for word in case.banned
    ]
    expected = [c.as_dict() for c in case.tool_calls]
    got = list(calls)
    rows.append(
        _assertion("tools", "", "" if got == expected else f"tool calls {got}, expected {expected}")
    )
    prompt = f"<Output>\n{answer}\n</Output>\n<Rubric>{case.rubric}</Rubric>"
    verdict = json.loads(judge.pick([{"role": "user", "content": prompt}]).reply)
    rows.append(
        {"kind": "rubric", "value": "", "pass": bool(verdict["pass"]), "reason": verdict["reason"]}
    )
    return {
        "id": case.id,
        "pass": all(r["pass"] for r in rows),
        "trace_id": None,
        "assertions": rows,
    }


def offline_report(
    engine: str, lane: str, *, logic: str = "normal", cases: Sequence[Case] | None = None
) -> dict[str, Any]:
    """The golden cases (default: the 50 gate cases) as `logic` answers them, scored offline."""
    golden = list(cases) if cases is not None else load_gate_cases()
    answered = apply_logic(golden, logic)
    model = Script.model_validate(model_script(answered))
    judge = Script.model_validate(judge_script(golden))
    rows = []
    for case, variant in zip(golden, answered, strict=True):
        calls, reply = simulate(variant, model)
        rows.append(score_case(case, reply, calls, judge))
    rows.sort(key=lambda c: c["id"])
    passed = sum(1 for c in rows if c["pass"])
    report: dict[str, Any] = {
        "schema": SCHEMA_ID,
        "engine": engine,
        "tool": RUNNER,
        "cases": rows,
        "summary": {"total": len(rows), "passed": passed, "failed": len(rows) - passed},
        "meta": {"lane": lane, "logic": logic, "runner": RUNNER},
    }
    validate_report(report)
    return report
