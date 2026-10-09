"""PoC-6a, task T-SCORE: the offline scorecard note and the bakeoff run it rests on are on record.

It reads files only (no process, no socket). It checks that:

- `notes/2026-10-09-scorecard.md` has one section for each of the 12 bake-off criteria
  (`docs/planning/poc/000-plan.md#bake-off-criteria`) and one row for each of the 8 engines in
  each, with no empty cell. "not measured: <where>" counts as filled, a bare "not measured" or
  a bare dash does not;
- it cites the CI run for the image sizes and gives the size of every engine that has an image;
- it has a router-compatibility table per engine and the closing "Reading the scorecard";
- `notes/2026-10-09-bakeoff-offline/results.json` has every engine x lane x task, and every
  cell that can run here passed all its repetitions.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from bakeoff.registry import ENGINES, lanes_for

NOTES = Path(__file__).resolve().parents[1] / "notes"
SCORECARD = NOTES / "2026-10-09-scorecard.md"
RESULTS = NOTES / "2026-10-09-bakeoff-offline" / "results.json"

ENGINE_NAMES = [
    "echo-python",
    "echo-pydanticai",
    "echo-langgraph",
    "echo-openai-agents",
    "echo-typescript",
    "echo-smolagents",
    "echo-claude-agent",
    "kagent-adk",
]
CRITERIA = [
    "event mapping effort",
    "streaming fidelity",
    "tool support",
    "model agnostic",
    "token overhead",
    "latency",
    "footprint",
    "statelessness",
    "lane under the trust rule",
    "observability hooks",
    "durability",
    "license and maturity",
]
IMAGE_MB = {
    "echo-python": 181,
    "echo-pydanticai": 255,
    "echo-langgraph": 252,
    "echo-openai-agents": 209,
    "echo-typescript": 252,
    "echo-smolagents": 256,
    "echo-claude-agent": 434,
}
TASKS = ("smoke", "simplifier", "lookup")
NOT_MEASURED = re.compile(r"^not measured: \S")


def _sections(text: str) -> dict[str, str]:
    """Second-level sections by heading text."""
    parts = re.split(r"^## (.+)$", text, flags=re.MULTILINE)
    return {parts[i].strip(): parts[i + 1] for i in range(1, len(parts) - 1, 2)}


def _criterion_section(sections: dict[str, str], number: int, name: str) -> str:
    prefix = f"Criterion {number}: "
    matches = [body for head, body in sections.items() if head.lower() == (prefix + name).lower()]
    assert len(matches) == 1, f"no single section '## {prefix}{name}'"
    return matches[0]


def _data_rows(section: str) -> list[list[str]]:
    """Data rows of every table in `section`; the header and the separator are skipped."""
    rows: list[list[str]] = []
    block: list[str] = []
    for line in [*section.splitlines(), ""]:
        if line.startswith("|"):
            block.append(line)
            continue
        rows.extend(
            [cell.strip() for cell in raw.strip().strip("|").split("|")] for raw in block[2:]
        )
        block = []
    return rows


@pytest.fixture(scope="module")
def text() -> str:
    assert SCORECARD.is_file(), f"missing {SCORECARD}"
    return SCORECARD.read_text()


@pytest.fixture(scope="module")
def sections(text: str) -> dict[str, str]:
    return _sections(text)


@pytest.mark.parametrize(("number", "name"), list(enumerate(CRITERIA, start=1)))
def test_every_criterion_covers_every_engine_with_no_empty_cell(
    sections: dict[str, str], number: int, name: str
) -> None:
    """Scorecard: each of the 12 criteria has a table with all 8 engines and no empty cell."""
    body = _criterion_section(sections, number, name)
    rows = _data_rows(body)
    assert rows, f"criterion {number} has no table"
    for engine in ENGINE_NAMES:
        assert any(row[0].startswith(engine) or engine in row[0] for row in rows), (
            f"criterion {number} ({name}): no row for {engine}"
        )
    for row in rows:
        for cell in row:
            assert cell, f"criterion {number} ({name}): empty cell in {row[0]!r}"
            assert cell != "-", f"criterion {number} ({name}): bare dash in {row[0]!r}"
            if cell.lower().startswith("not measured"):
                assert NOT_MEASURED.match(cell), (
                    f"criterion {number} ({name}): 'not measured' needs a source: {cell[:60]!r}"
                )
            assert "TBD" not in cell and "TODO" not in cell


def test_image_sizes_cite_the_ci_run(sections: dict[str, str]) -> None:
    """Scorecard criterion 7: image sizes cite CI run 37997346774 on 772b9a6 and list each size."""
    body = _criterion_section(sections, 7, "footprint")
    assert "37997346774" in body
    assert "772b9a6" in body
    assert "271 MB" in body, "the chassis image size is missing"
    rows = {row[0]: row for row in _data_rows(body)}
    for engine, megabytes in IMAGE_MB.items():
        assert rows[engine][1] == str(megabytes), f"{engine}: image size is not {megabytes}"
    assert rows["kagent-adk"][1].startswith("not measured: kind")


def test_router_table_has_every_engine(sections: dict[str, str]) -> None:
    """Scorecard: a router-compatibility table per engine, with the keys dropped by the proxy."""
    body = sections["Router compatibility"]
    rows = _data_rows(body)
    assert [row[0] for row in rows] == ENGINE_NAMES
    assert all(len(row) == 5 and all(row) for row in rows)


def test_offline_is_stated_and_the_reading_closes_the_note(
    text: str, sections: dict[str, str]
) -> None:
    """Scorecard: every measurement table says it is offline; the note ends with the reading."""
    assert text.count("Table caption: offline, fake model, localhost") >= 7
    assert "make poc06-mac" in text
    headings = list(sections)
    reading = [h for h in headings if h.startswith("Reading the scorecard")]
    assert reading, "no 'Reading the scorecard' section"
    assert "ADR-006" in sections[reading[0]] or "ADR-006" in text


def test_results_json_has_every_engine_lane_and_task() -> None:
    """Bakeoff run: results.json has every engine x lane x task; runnable cells passed 10/10."""
    data = json.loads(RESULTS.read_text())
    assert data["meta"]["tasks"] == list(TASKS)
    assert data["meta"]["repeat"] == 10
    assert data["meta"]["target_mode"] is False
    cells = {(c["engine"], c["lane"]): c for c in data["cells"]}
    expected = {(engine.name, lane) for engine in ENGINES for lane in lanes_for(engine)}
    assert set(cells) == expected
    assert {name for name, _ in cells} == set(ENGINE_NAMES)
    for (name, lane), cell in cells.items():
        if name == "kagent-adk":
            assert cell["status"] == "skip", "kagent-adk runs only on kind"
            assert cell["reason"]
            continue
        assert cell["status"] == "ok", f"{name} {lane}: {cell['status']} {cell['reason']}"
        assert set(cell["tasks"]) == set(TASKS), f"{name} {lane}: tasks"
        for task in TASKS:
            result = cell["tasks"][task]
            assert result["runs"] == 10, f"{name} {lane} {task}: runs"
            assert result["passed"] == 10, f"{name} {lane} {task}: passed"
            assert result["latency_p50_ms"] is not None
        assert cell["tasks"]["lookup"]["tool_call_pass_rate"] == 1.0


def test_results_md_sits_next_to_results_json() -> None:
    """Bakeoff run: results.md and the extra measurements are committed with results.json."""
    folder = RESULTS.parent
    assert (folder / "results.md").is_file()
    extra = json.loads((folder / "extra.json").read_text())
    assert [row["engine"] for row in extra] == ENGINE_NAMES
