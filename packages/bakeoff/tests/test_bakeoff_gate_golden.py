"""The PoC-8 gate golden set (50 cases) and its business-logic variants. Offline: no process."""

from __future__ import annotations

import re

import pytest
import yaml
from bakeoff.golden import GATE_SIZE, gate_data, load_cases, load_gate_cases
from bakeoff.golden.generate import (
    GATE_DROP_FACTS_SCRIPT,
    GATE_JUDGE_SCRIPT,
    GATE_MODEL_SCRIPT,
    JUDGE_DEFAULT,
    judge_script,
    main,
    model_script,
    rendered_gate,
    simulate,
)
from bakeoff.golden.logic import LOGICS, apply_logic, drop_first_fact
from bakeoff.registry import ROOT
from fake_model_server import Script


def test_bakeoff_gate_golden_has_50_labelled_cases_holding_the_poc07_set() -> None:
    cases = load_gate_cases()
    assert len(cases) == GATE_SIZE == 50
    assert [c.id for c in cases] == [f"g{n:02d}" for n in range(1, 51)]
    assert cases[:20] == load_cases(), "the PoC-7 20 stay as they are"
    labels = [c.label for c in cases]
    assert labels.count("pass") == 40 and labels.count("fail") == 10
    assert {c.task for c in cases} == {"simplifier", "lookup"}


def test_bakeoff_gate_golden_data_is_one_file_the_eval_tool_reads() -> None:
    data = gate_data()
    assert data["version"] == 1
    assert [row["id"] for row in data["cases"]] == [c.id for c in load_gate_cases()]


def test_bakeoff_gate_golden_generated_files_are_in_sync() -> None:
    files = rendered_gate(load_gate_cases())
    assert set(files) == {GATE_MODEL_SCRIPT, GATE_DROP_FACTS_SCRIPT, GATE_JUDGE_SCRIPT}
    for rel, text in files.items():
        assert (ROOT / rel).read_text(encoding="utf-8") == text, f"{rel}: run --write"
    assert main(["--check"]) == 0


@pytest.mark.parametrize("compact", [False, True])
@pytest.mark.parametrize("logic", LOGICS)
def test_bakeoff_gate_golden_script_answers_each_case_per_logic(compact: bool, logic: str) -> None:
    cases = apply_logic(load_gate_cases(), logic)
    script = Script.model_validate(model_script(cases))
    for case in cases:
        calls, reply = simulate(case, script, compact=compact)
        assert calls == [c.as_dict() for c in case.model_calls]
        assert reply == case.reference


def test_bakeoff_gate_golden_drop_facts_removes_the_first_fact_of_every_answer_holding_it() -> None:
    normal = load_gate_cases()
    dropped = apply_logic(normal, "drop-facts")
    changed = 0
    for before, after in zip(normal, dropped, strict=True):
        fact = before.contains[0]
        if fact.casefold() in before.reference.casefold():
            assert fact.casefold() not in after.reference.casefold(), before.id
            assert after.reference.strip() and "  " not in after.reference
            changed += 1
        else:
            assert after.reference == before.reference, before.id
    assert changed >= 45, "the variant must hit nearly every case"
    assert drop_first_fact("The library opens at 9 am.", "9 am") == "The library opens at."


def test_bakeoff_gate_golden_normal_logic_is_the_identity() -> None:
    assert apply_logic(load_gate_cases(), "normal") == load_gate_cases()
    with pytest.raises(ValueError, match="unknown logic"):
        apply_logic(load_gate_cases(), "nope")


def test_bakeoff_gate_golden_judge_agrees_with_labels_but_g08_and_fails_dropped_answers() -> None:
    cases = load_gate_cases()
    script = Script.model_validate(judge_script(cases))

    def verdict(answer: str, rubric: str) -> dict[str, object]:
        prompt = f"<Output>\n{answer}\n</Output>\n<Rubric>{rubric}</Rubric>"
        data: dict[str, object] = yaml.safe_load(
            script.pick([{"role": "user", "content": prompt}]).reply
        )
        return data

    disagree = [
        c.id for c in cases if verdict(c.reference, c.rubric)["pass"] != (c.label == "pass")
    ]
    assert disagree == ["g08"]
    for before, after in zip(cases, apply_logic(cases, "drop-facts"), strict=True):
        if after.reference != before.reference:
            assert verdict(after.reference, after.rubric) == JUDGE_DEFAULT, before.id


def test_bakeoff_gate_golden_references_hold_no_quote_or_backslash() -> None:
    for case in apply_logic(load_gate_cases(), "drop-facts"):
        assert not re.search(r'["\\\n]', case.reference), case.id
