"""Exit criterion 7: the red-team set has a pass rate recorded, and every miss has an owner.

The set is `chassis_contracts/redteam/cases.yaml` (four categories, at least three cases each).
The run goes through a real chassis app with each guardrail stack (`litellm`: LiteLLMGuardrails ->
the LiteLLM stub -> the guardrail server with the pattern stand-in; `keyword`: the port's fake).
This test runs it again, offline, and holds the result to the recorded file
`notes/2026-10-28-redteam-results.json`, so the note's numbers cannot drift from the code. It also
holds every missed case to an owner line in `notes/2026-10-28-redteam.md`.

Re-record: `uv run python -m chassis_contracts.redteam --write
pocs/poc-07-cross-cutting-decisions/notes/2026-10-28-redteam-results.json`.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import pytest
from chassis_contracts.redteam import CATEGORIES, CONFIGS, load_cases, results, run_all

NOTES = Path(__file__).resolve().parents[1] / "notes"
RESULTS = NOTES / "2026-10-28-redteam-results.json"
NOTE = NOTES / "2026-10-28-redteam.md"
OWNER = re.compile(r"\b(\d{3} [A-Z]{1,3}-\d+|PoC-\d+)\b")


def test_the_set_has_four_categories_of_at_least_three_cases() -> None:
    counts = Counter(case.category for case in load_cases())
    assert set(counts) == set(CATEGORIES)
    assert min(counts.values()) >= 3, counts
    long = [c for c in load_cases() if c.kind == "tool_output" and c.filler_words >= 600]
    assert long, "one tool output must be past 512 tokens"


@pytest.fixture(scope="module")
def recorded() -> dict[str, object]:
    data: dict[str, object] = json.loads(RESULTS.read_text())
    return data


async def test_the_run_matches_the_recorded_pass_rates(recorded: dict[str, object]) -> None:
    outcomes = []
    for config in CONFIGS:
        outcomes.extend(await run_all(config))
    assert results(outcomes) == recorded, "re-record the results file (module docstring)"


def test_every_miss_has_an_owner_and_a_reason_in_the_note(recorded: dict[str, object]) -> None:
    note = NOTE.read_text()
    summary = recorded["summary"]
    assert isinstance(summary, dict)
    missed = {case for row in summary.values() for case in row["missed"]}
    for case in sorted(missed):
        lines = [line for line in note.splitlines() if f"`{case}`" in line and "|" in line]
        assert lines, f"{case} is missed but not listed in {NOTE.name}"
        assert any(OWNER.search(line) for line in lines), f"{case}: no owner"


def test_the_note_says_prompt_guard_numbers_wait() -> None:
    note = NOTE.read_text()
    assert "Prompt Guard 2" in note
    assert "Hugging Face" in note
