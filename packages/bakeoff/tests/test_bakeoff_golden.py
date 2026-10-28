"""The golden set: the loader's validation, and the generated scripts and promptfoo tests."""

from __future__ import annotations

import copy
from typing import Any

import pytest
import yaml
from bakeoff.golden import CASES_FILE, GOLDEN_SIZE, load_cases, parse_cases
from bakeoff.golden.generate import (
    JUDGE_DEFAULT,
    judge_script,
    main,
    model_script,
    rendered,
    simulate,
)
from bakeoff.registry import ROOT
from fake_model_server import Script


def _raw() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(CASES_FILE.read_text(encoding="utf-8"))
    return data


def test_bakeoff_golden_has_20_labelled_cases_of_both_tasks() -> None:
    cases = load_cases()
    assert len(cases) == GOLDEN_SIZE
    labels = [c.label for c in cases]
    assert labels.count("pass") == 14 and labels.count("fail") == 6
    assert {c.task for c in cases} == {"simplifier", "lookup"}


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d["cases"][0].pop("label"), "'label' is a required property"),
        (lambda d: d["cases"][0].update(label="maybe"), "'maybe' is not one of"),
        (lambda d: d["cases"][1].update(id="g01"), "duplicate id"),
        (lambda d: d["cases"].pop(), "19 cases, expected 20"),
        (lambda d: d["cases"][0].update(reference='say "hi"'), "does not match"),
        (lambda d: d["cases"][12]["expect"].update(tool_calls=[]), "expects a tool call"),
    ],
)
def test_bakeoff_golden_rejects_a_bad_file(mutate: Any, message: str) -> None:
    data = copy.deepcopy(_raw())
    mutate(data)
    with pytest.raises(ValueError, match=message):
        parse_cases(data)


def test_bakeoff_golden_generated_files_are_in_sync() -> None:
    for rel, text in rendered(load_cases()).items():
        assert (ROOT / rel).read_text(encoding="utf-8") == text, f"{rel}: run --write"
    assert main(["--check"]) == 0


@pytest.mark.parametrize("compact", [False, True])
def test_bakeoff_golden_script_answers_each_case_with_its_plan(compact: bool) -> None:
    script = Script.model_validate(model_script(load_cases()))
    for case in load_cases():
        calls, reply = simulate(case, script, compact=compact)
        assert calls == [c.as_dict() for c in case.model_calls]
        assert reply == case.reference


def test_bakeoff_golden_judge_agrees_with_the_labels_but_g08() -> None:
    script = Script.model_validate(judge_script(load_cases()))
    disagree = []
    for case in load_cases():
        prompt = f"<Output>\n{case.reference}\n</Output>\n<Rubric>{case.rubric}</Rubric>"
        verdict = yaml.safe_load(script.pick([{"role": "user", "content": prompt}]).reply)
        assert verdict["score"] == (1 if verdict["pass"] else 0)
        if verdict["pass"] != (case.label == "pass"):
            disagree.append(case.id)
    assert disagree == ["g08"]
    unknown = script.pick([{"role": "user", "content": "something else"}]).reply
    assert yaml.safe_load(unknown) == JUDGE_DEFAULT
