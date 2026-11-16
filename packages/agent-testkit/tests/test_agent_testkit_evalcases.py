"""The kit's judged eval (`agent_testkit.evalcases`) still equals `eval_kit` (monorepo only).

The kit cannot import `eval_kit` (it pulls in `bakeoff` and `chassis`), so the case schema, the
case reader, the scripted judge, and `score_case` are copies. Here, where both are installed, each
copy must give what its original gives on the platform's 50 gate cases.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest
from agent_testkit import evalcases
from fake_model_server import Script

golden = pytest.importorskip("eval_kit.golden")
generate = pytest.importorskip("eval_kit.golden.generate")
evalcheck = pytest.importorskip("eval_kit.evalcheck")
evalreport = pytest.importorskip("eval_kit.evalreport")

GATE = golden.gate_data()


def _plain(case: Any) -> dict[str, Any]:
    return dataclasses.asdict(case)


def test_the_schema_is_eval_kits() -> None:
    assert evalcases.CASES_SCHEMA == golden.CASES_SCHEMA


def test_the_reader_gives_eval_kits_cases() -> None:
    mine = evalcases.parse_cases(GATE)
    theirs = golden.parse_cases(GATE, size=None)
    assert [_plain(c) for c in mine] == [_plain(c) for c in theirs]


@pytest.mark.parametrize(
    "data",
    [
        {"cases": []},
        {"version": 1, "cases": [{"id": "greeting", "input": {"text": "hi"}, "expect": {}}]},
    ],
)
def test_a_file_outside_the_schema_is_refused_by_both(data: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="golden set"):
        evalcases.parse_cases(data)
    with pytest.raises(ValueError, match="golden set"):
        golden.parse_cases(data, size=None)


def test_the_judge_script_is_eval_kits() -> None:
    cases = evalcases.parse_cases(GATE)
    assert evalcases.judge_script(cases) == generate.judge_script(
        golden.parse_cases(GATE, size=None)
    )
    assert evalcases.JUDGE_DEFAULT == generate.JUDGE_DEFAULT


@pytest.mark.parametrize("answer", ["reference", "dropped"])
def test_score_case_gives_what_eval_kit_gives(answer: str) -> None:
    """The reference answer and an answer that drops every fact, with the expected tool calls."""
    mine = evalcases.parse_cases(GATE)
    theirs = golden.parse_cases(GATE, size=None)
    my_judge = Script.model_validate(evalcases.judge_script(mine))
    their_judge = Script.model_validate(generate.judge_script(theirs))
    for case, original in zip(mine, theirs, strict=True):
        text = case.reference if answer == "reference" else "No facts here."
        calls = [c.as_dict() for c in case.tool_calls]
        got = evalcases.score_case(case, text, calls, my_judge)
        assert got == evalcheck.score_case(original, text, calls, their_judge), case.id


def test_the_report_is_an_eval_report_v0() -> None:
    cases = evalcases.parse_cases(GATE)[:3]
    judge = Script.model_validate(evalcases.judge_script(cases))
    rows = [evalcases.score_case(c, c.reference, [], judge) for c in cases]
    scores = {c.id: evalcases.ask_judge(c, c.reference, judge)["score"] for c in cases}
    report = evalcases.report("my-agent", rows, scores)
    evalreport.validate_report(report)
    assert report["meta"]["judge"] == scores
    assert report["summary"]["total"] == 3


def test_the_model_script_answers_each_case_with_its_reference() -> None:
    cases = [c for c in evalcases.parse_cases(GATE) if not c.model_calls][:2]
    script = evalcases.model_script(cases)
    assert script == [{"match": c.input, "reply": c.reference} for c in cases]
    framed = evalcases.model_script(cases, reply=lambda c: f"<code>{c.reference}</code>")
    assert framed[0]["reply"] == f"<code>{cases[0].reference}</code>"


def test_a_case_that_scripts_tool_calls_is_refused() -> None:
    lookup = next(c for c in evalcases.parse_cases(GATE) if c.model_calls)
    with pytest.raises(ValueError, match=f"{lookup.id}: .*tool calls"):
        evalcases.model_script([lookup])


def test_the_answer_and_calls_come_from_the_envelope() -> None:
    envelope = {
        "status": "ok",
        "output": {
            "text": "Plain words.",
            "tool_calls": [
                {"call_id": "c1", "name": "glossary_lookup", "arguments": {"term": "x"}}
            ],
        },
    }
    assert evalcases.answer_of(envelope) == (
        "Plain words.",
        [{"name": "glossary_lookup", "arguments": {"term": "x"}}],
    )
    assert evalcases.answer_of({"status": "error", "output": None}) == ("", [])


STATUSES = ("ok", "fallback")


class _CountingJudge:
    """The scripted judge, counting how often it is asked."""

    def __init__(self, script: Script) -> None:
        self.script = script
        self.calls = 0

    def pick(self, messages: list[dict[str, Any]]) -> Any:
        self.calls += 1
        return self.script.pick(messages)


def _answered(status: str) -> tuple[Any, dict[str, Any]]:
    """The first gate case, and a run that ends in `status` with the case's reference text."""
    case = evalcases.parse_cases(GATE)[0]
    calls = [c.as_dict() for c in case.tool_calls]
    return case, {"status": status, "output": {"text": case.reference, "tool_calls": calls}}


def test_judge_run_asks_the_judge_once_and_gives_its_score() -> None:
    case, envelope = _answered("ok")
    judge = _CountingJudge(Script.model_validate(evalcases.judge_script([case])))
    row, score = evalcases.judge_run(case, envelope, judge, STATUSES)  # type: ignore[arg-type]
    assert judge.calls == 1
    assert (row["pass"], score) == (True, 1)


def test_a_run_outside_the_allowed_statuses_fails_even_with_the_right_text() -> None:
    """A refused or blocked run that still carries the reference text must not pass."""
    case, envelope = _answered("error")
    judge = Script.model_validate(evalcases.judge_script([case]))
    row, score = evalcases.judge_run(case, envelope, judge, STATUSES)
    assert row["pass"] is False
    assert score == 1, "the judge still scores the text; the status fails the case"
    assert evalcases.status_problem(envelope, STATUSES) == (
        "run ended 'error', expected one of ('ok', 'fallback')"
    )
    assert evalcases.status_problem(_answered("fallback")[1], STATUSES) == ""
