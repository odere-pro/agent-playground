"""S-3 (028): `facts_kept` and banned words, offline, with fixed fixtures and the offline judge."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from chassis.adapters.evaluators import RUBRIC_VERSION, JudgeEvaluator
from eval_kit.golden import Case, parse_cases
from eval_kit.simplifier_metrics import (
    OFFLINE_ROUTE,
    SCHEMA_ID,
    FactsModel,
    banned_hits,
    extract_facts,
    main,
    score_cases,
)

INPUT = "simplify: The firm intends to recruit 25 engineers in Lisbon prior to December."

FIXTURE: dict[str, Any] = {
    "version": 1,
    "cases": [
        {
            "id": "g91",
            "task": "simplifier",
            "input": INPUT,
            "expect": {
                "contains": ["25 engineers", "Lisbon"],
                "banned": ["intends", "prior to"],
                "tool_calls": [],
            },
            "rubric": "Keeps 25 engineers, Lisbon, December.",
            "reference": "The firm plans to hire 25 engineers in Lisbon before December.",
            "label": "pass",
        },
        {
            "id": "g92",
            "task": "simplifier",
            "input": "simplify: Payment of 300 dollars is due prior to 4 May.",
            "expect": {"contains": ["300 dollars"], "banned": ["prior to"], "tool_calls": []},
            "rubric": "Keeps 300 dollars and 4 May.",
            "reference": "Pay 300 dollars by 4 May.",
            "label": "pass",
        },
    ],
}


def _cases() -> list[Case]:
    return parse_cases(FIXTURE, size=None)


def _judge() -> JudgeEvaluator:
    return JudgeEvaluator(FactsModel(), route=OFFLINE_ROUTE)


def _score(answers: dict[str, str] | None = None) -> dict[str, Any]:
    return asyncio.run(score_cases(_cases(), _judge(), answers))


def _row(report: dict[str, Any], case_id: str) -> dict[str, Any]:
    row: dict[str, Any] = next(r for r in report["cases"] if r["id"] == case_id)
    return row


def test_extract_facts_takes_numbers_and_names_not_sentence_starts() -> None:
    assert extract_facts(INPUT) == ["25", "Lisbon", "December"]


def test_facts_kept_is_one_for_an_identical_pair() -> None:
    report = _score(
        {
            "g91": INPUT.removeprefix("simplify: "),
            "g92": "Payment of 300 dollars is due prior to 4 May.",
        }
    )
    assert _row(report, "g91")["facts_kept"] == 1.0
    assert _row(report, "g92")["facts_kept"] == 1.0


def test_facts_kept_drops_when_a_number_is_removed() -> None:
    report = _score({"g91": "The firm plans to hire engineers in Lisbon before December."})
    row = _row(report, "g91")
    assert row["facts_kept"] == pytest.approx(2 / 3)
    assert "25" in row["facts_reason"]


def test_facts_kept_drops_when_a_name_is_removed() -> None:
    report = _score({"g91": "The firm plans to hire 25 engineers before December."})
    row = _row(report, "g91")
    assert row["facts_kept"] == pytest.approx(2 / 3)
    assert "Lisbon" in row["facts_reason"]


def test_a_changed_number_is_not_kept() -> None:
    report = _score({"g91": "The firm plans to hire 250 engineers in Lisbon before December."})
    assert _row(report, "g91")["facts_kept"] < 1.0


def test_banned_words_use_the_case_banned_list() -> None:
    answer = "The firm Intends to hire 25 engineers in Lisbon prior to December."
    report = _score({"g91": answer, "g92": "The firm intends to pay 300 dollars by 4 May."})
    assert _row(report, "g91")["banned_hits"] == ["intends", "prior to"]
    assert _row(report, "g91")["banned"] == 2
    # "intends" is banned in g91 only; g92's list is ["prior to"].
    assert _row(report, "g92")["banned"] == 0
    assert report["aggregate"]["banned_total"] == 2
    assert report["aggregate"]["cases_with_banned"] == 1


def test_banned_hits_is_case_insensitive() -> None:
    assert banned_hits("PRIOR TO noon", ["prior to", "furnish"]) == ["prior to"]


def test_scoring_the_same_input_twice_gives_the_same_scores() -> None:
    answers = {"g91": "The firm plans to hire engineers in Lisbon prior to December."}
    assert _score(answers) == _score(answers)


def test_report_names_the_judge_route_and_the_rubric_version() -> None:
    report = _score()
    assert report["schema"] == SCHEMA_ID
    assert report["judge"]["route"] == OFFLINE_ROUTE
    assert report["judge"]["rubric_version"] == RUBRIC_VERSION == "judge-facts-v1"
    assert report["judge"]["temperature"] == 0.0


def test_aggregate_over_the_references() -> None:
    agg = _score()["aggregate"]
    assert agg["cases"] == 2
    assert agg["facts_kept_mean"] == 1.0
    assert agg["facts_kept_at_target"] == 2
    assert agg["label_agreement"] == 1.0


def test_command_scores_a_case_file_and_writes_per_case_and_aggregate(tmp_path: Path) -> None:
    cases = tmp_path / "cases.yaml"
    cases.write_text(yaml.safe_dump(FIXTURE), encoding="utf-8")
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({"g91": "Hire 25 engineers before December."}), encoding="utf-8")
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    argv = ["--cases", str(cases), "--answers", str(answers), "--out"]

    assert main([*argv, str(first)]) == 0
    assert main([*argv, str(second)]) == 0

    report = json.loads(first.read_text(encoding="utf-8"))
    assert first.read_bytes() == second.read_bytes()
    assert [r["id"] for r in report["cases"]] == ["g91", "g92"]
    assert _row(report, "g91")["facts_kept"] == pytest.approx(2 / 3)
    assert _row(report, "g92")["facts_kept"] == 1.0
    assert report["aggregate"]["facts_kept_mean"] == pytest.approx(5 / 6, abs=1e-6)


def test_command_defaults_to_the_50_gate_cases(tmp_path: Path) -> None:
    out = tmp_path / "gate.json"
    assert main(["--out", str(out)]) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["aggregate"]["cases"] == 50
    assert report["judge"]["rubric_version"] == RUBRIC_VERSION


def test_command_rejects_answers_for_unknown_cases(tmp_path: Path) -> None:
    cases = tmp_path / "cases.yaml"
    cases.write_text(yaml.safe_dump(FIXTURE), encoding="utf-8")
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({"nope": "x"}), encoding="utf-8")
    argv = ["--cases", str(cases), "--answers", str(answers), "--out", str(tmp_path / "o.json")]
    assert main(argv) == 2
