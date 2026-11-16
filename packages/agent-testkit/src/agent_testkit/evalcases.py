"""The agent's golden cases, judged: the `eval_kit` case schema, the scripted judge, `score_case`.

`evals/cases.yaml` in an agent repo is in the platform's golden-set schema
(`eval_kit.golden.CASES_SCHEMA`): `version`, and per case `id`, `task`, `input`, `expect`,
`rubric`, `reference`, `label`. `suites.EvalCases` runs each case through the chassis with a fake
model scripted per case (`model_script`: the case's input gets its reference as the answer), asks
the scripted judge for the rubric verdict, and writes `eval-report.v0`.

The kit cannot import `eval_kit`: it pulls in `bakeoff` and `chassis`, which a workload's
environment never holds (ADR-001). So `CASES_SCHEMA`, `Case`, `parse_cases`, `judge_script`, and
`score_case` are copies of `eval_kit.golden`, `eval_kit.golden.generate`, and
`eval_kit.evalcheck`. Change those first, then copy; `tests/test_agent_testkit_evalcases.py`
fails when they drift.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import jsonschema
from fake_model_server import Script

__all__ = [
    "CASES_SCHEMA",
    "JUDGE_DEFAULT",
    "REPORT_SCHEMA_ID",
    "RUNNER",
    "Case",
    "ToolCallSpec",
    "answer_of",
    "ask_judge",
    "judge_run",
    "judge_script",
    "model_script",
    "parse_cases",
    "report",
    "score_case",
    "status_problem",
]

REPORT_SCHEMA_ID = "eval-report.v0"
RUNNER = "agent-testkit"
TOOL = "offline"
"""`eval-report.v0`'s name for a scripted, offline scorer (`eval_kit.evalreport`)."""
JUDGE_DEFAULT = {"reason": "The judge has no verdict for this answer.", "pass": False, "score": 0}

Task = Literal["simplifier", "lookup"]
Label = Literal["pass", "fail"]

_TOOL_CALL = {
    "type": "object",
    "required": ["name", "arguments"],
    "additionalProperties": False,
    "properties": {
        "name": {"type": "string", "minLength": 1},
        "arguments": {"type": "object", "additionalProperties": {"type": "string"}},
    },
}
_STRINGS = {"type": "array", "items": {"type": "string", "minLength": 1}}

CASES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["version", "cases"],
    "additionalProperties": False,
    "properties": {
        "version": {"const": 1},
        "cases": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "task", "input", "expect", "rubric", "reference", "label"],
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string", "pattern": "^g[0-9]{2}$"},
                    "task": {"enum": ["simplifier", "lookup"]},
                    "input": {"type": "string", "pattern": "^(simplify|lookup): .+"},
                    "expect": {
                        "type": "object",
                        "required": ["contains", "tool_calls"],
                        "additionalProperties": False,
                        "properties": {
                            "contains": {**_STRINGS, "minItems": 1},
                            "banned": _STRINGS,
                            "tool_calls": {"type": "array", "items": _TOOL_CALL},
                        },
                    },
                    "model": {
                        "type": "object",
                        "required": ["calls"],
                        "additionalProperties": False,
                        "properties": {"calls": {"type": "array", "items": _TOOL_CALL}},
                    },
                    "rubric": {"type": "string", "minLength": 1},
                    "reference": {"type": "string", "minLength": 1, "pattern": '^[^"\\\\\n]+$'},
                    "label": {"enum": ["pass", "fail"]},
                    "note": {"type": "string"},
                    "judge": {
                        "type": "object",
                        "required": ["verdict", "reason"],
                        "additionalProperties": False,
                        "properties": {
                            "verdict": {"enum": ["pass", "fail"]},
                            "reason": {"type": "string", "minLength": 1},
                        },
                    },
                },
            },
        },
    },
}
"""A copy of `eval_kit.golden.CASES_SCHEMA`."""


@dataclass(frozen=True)
class ToolCallSpec:
    name: str
    arguments: Mapping[str, str]

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "arguments": dict(self.arguments)}


@dataclass(frozen=True)
class Case:
    """A copy of `eval_kit.golden.Case`."""

    id: str
    task: Task
    input: str
    contains: tuple[str, ...]
    banned: tuple[str, ...]
    tool_calls: tuple[ToolCallSpec, ...]
    rubric: str
    reference: str
    label: Label
    model_calls: tuple[ToolCallSpec, ...]
    judge_verdict: Label
    judge_reason: str
    note: str = ""


def _calls(rows: Sequence[Mapping[str, Any]]) -> tuple[ToolCallSpec, ...]:
    return tuple(ToolCallSpec(str(r["name"]), dict(r["arguments"])) for r in rows)


def _case(row: Mapping[str, Any]) -> Case:
    expect = row["expect"]
    calls = _calls(expect["tool_calls"])
    model = row.get("model")
    judge = row.get("judge")
    label: Label = row["label"]
    default_reason = (
        "The answer meets the rubric." if label == "pass" else "The answer misses the rubric."
    )
    return Case(
        id=row["id"],
        task=row["task"],
        input=row["input"],
        contains=tuple(expect["contains"]),
        banned=tuple(expect.get("banned") or ()),
        tool_calls=calls,
        rubric=row["rubric"],
        reference=row["reference"],
        label=label,
        model_calls=_calls(model["calls"]) if model else calls,
        judge_verdict=judge["verdict"] if judge else label,
        judge_reason=judge["reason"] if judge else default_reason,
        note=str(row.get("note") or ""),
    )


def parse_cases(data: Any) -> list[Case]:
    """Validate `data` (the parsed YAML) and return the cases: `eval_kit.golden.parse_cases` with
    no case count. Raises `ValueError`."""
    try:
        jsonschema.validate(data, CASES_SCHEMA)
    except jsonschema.ValidationError as exc:
        where = "/".join(str(p) for p in exc.absolute_path) or "(root)"
        raise ValueError(f"golden set: {where}: {exc.message}") from exc
    cases = [_case(row) for row in data["cases"]]
    for attr in ("id", "input", "reference"):
        values = [getattr(c, attr) for c in cases]
        dupes = sorted({v for v in values if values.count(v) > 1})
        if dupes:
            raise ValueError(f"golden set: duplicate {attr}: {dupes}")
    for case in cases:
        if case.task == "simplifier" and case.tool_calls:
            raise ValueError(f"golden set: {case.id}: a simplifier case calls no tool")
        if case.task == "lookup" and not case.tool_calls:
            raise ValueError(f"golden set: {case.id}: a lookup case expects a tool call")
    return cases


def judge_script(cases: Sequence[Case]) -> dict[str, Any]:
    """The scripted judge: a copy of `eval_kit.golden.generate.judge_script`. Each case's verdict
    is given when the judge's prompt holds that case's reference; any other answer gets
    `JUDGE_DEFAULT`, a fail."""
    rules = [
        {
            "match": case.reference,
            "reply": json.dumps(
                {
                    "reason": case.judge_reason,
                    "pass": case.judge_verdict == "pass",
                    "score": 1 if case.judge_verdict == "pass" else 0,
                }
            ),
        }
        for case in sorted(cases, key=lambda c: -len(c.reference))
    ]
    return {"model": "fake-judge", "rules": rules, "default_reply": json.dumps(JUDGE_DEFAULT)}


def _reference(case: Case) -> str:
    return case.reference


def model_script(
    cases: Sequence[Case], reply: Callable[[Case], str] = _reference
) -> list[dict[str, Any]]:
    """The chassis's fake model, per case: the case's input gets `reply(case)`, by default its
    reference. A case that scripts tool calls is refused: the agent's suite does not script the
    chassis's fake tools yet (the platform's gate does, `eval_kit.golden.generate.model_script`)."""
    for case in cases:
        if case.model_calls:
            raise ValueError(f"{case.id}: the agent's eval does not script tool calls yet")
    return [{"match": case.input, "reply": reply(case)} for case in cases]


def ask_judge(case: Case, answer: str, judge: Script) -> dict[str, Any]:
    """The judge's verdict on `answer`: `{"reason", "pass", "score"}`."""
    prompt = f"<Output>\n{answer}\n</Output>\n<Rubric>{case.rubric}</Rubric>"
    verdict: dict[str, Any] = json.loads(judge.pick([{"role": "user", "content": prompt}]).reply)
    return verdict


def _assertion(kind: str, value: str, problem: str) -> dict[str, Any]:
    return {"kind": kind, "value": value, "pass": not problem, "reason": problem or "ok"}


def score_case(
    case: Case, answer: str, calls: Sequence[dict[str, Any]], judge: Script
) -> dict[str, Any]:
    """One `eval-report.v0` case row: a copy of `eval_kit.evalcheck.score_case`."""
    return _row(case, answer, calls, ask_judge(case, answer, judge))


def status_problem(envelope: Mapping[str, Any], statuses: Sequence[str]) -> str:
    """Why the run's status fails the case, or `""` when it is one of `statuses`."""
    status = envelope.get("status")
    if status in statuses:
        return ""
    return f"run ended {status!r}, expected one of {tuple(statuses)!r}"


def judge_run(
    case: Case, envelope: Mapping[str, Any], judge: Script, statuses: Sequence[str]
) -> tuple[dict[str, Any], int]:
    """The case row for a `/v1/run` envelope, and the judge's score, with one judge call.

    A run whose status is not in `statuses` fails the case even when its text passes every
    check: a refused or blocked run must not pass. `eval-report.v0` has no status assertion, so
    the reason is `status_problem`, which the suite puts in `meta.status`."""
    answer, calls = answer_of(envelope)
    verdict = ask_judge(case, answer, judge)
    row = _row(case, answer, calls, verdict)
    if status_problem(envelope, statuses):
        row = {**row, "pass": False}
    return row, int(verdict["score"])


def _row(
    case: Case, answer: str, calls: Sequence[dict[str, Any]], verdict: Mapping[str, Any]
) -> dict[str, Any]:
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
    rows.append(
        {"kind": "rubric", "value": "", "pass": bool(verdict["pass"]), "reason": verdict["reason"]}
    )
    return {
        "id": case.id,
        "pass": all(r["pass"] for r in rows),
        "trace_id": None,
        "assertions": rows,
    }


def answer_of(envelope: Mapping[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    """The answer text and the tool calls (`name`, `arguments`) of a `/v1/run` envelope."""
    output = envelope.get("output") or {}
    calls = [
        {"name": c["name"], "arguments": c.get("arguments") or {}}
        for c in output.get("tool_calls") or []
    ]
    return str(output.get("text") or ""), calls


def report(
    engine: str,
    rows: Sequence[dict[str, Any]],
    scores: Mapping[str, int],
    status: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """`eval-report.v0`, cases sorted by id; `meta.judge` is the judge's score per case, and
    `meta.status` the status problem of each case whose run ended outside the allowed ones."""
    ordered = sorted(rows, key=lambda r: r["id"])
    passed = sum(1 for r in ordered if r["pass"])
    return {
        "schema": REPORT_SCHEMA_ID,
        "engine": engine,
        "tool": TOOL,
        "cases": ordered,
        "summary": {"total": len(ordered), "passed": passed, "failed": len(ordered) - passed},
        "meta": {"runner": RUNNER, "judge": dict(scores), "status": dict(status or {})},
    }
