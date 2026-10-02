"""PoC-2 records: the measurements note and contract v1.

Each test names the exit criterion in docs/planning/poc/002-PoC-2-two-engines-one-contract.md it
covers. Both read files only. The PoC-1 model is `test_contract_v0_is_written_down` in
pocs/poc-01-walking-skeleton/tests/test_a2a_inprocess.py.
"""

from __future__ import annotations

import re
from pathlib import Path

from poc02_harness import POC, ROOT

NOTES = POC / "notes"
DATED = re.compile(r"^\d{4}-\d{2}-\d{2}-[a-z0-9-]+\.md$")
CONTRACT_V1 = ROOT / "docs/contracts/contract-v1.md"

MEASUREMENTS = (
    "event mapping size",
    "token overhead",
    "latency",
    "per streamed delta",
    "time to first token",
)
"""One heading each, in one dated note."""


def _headings(text: str) -> list[str]:
    return [line.lstrip("#").strip().lower() for line in text.splitlines() if line.startswith("#")]


def _measurement_notes() -> list[Path]:
    return [
        p
        for p in sorted(NOTES.glob("*.md"))
        if DATED.match(p.name)
        and all(any(m in h for h in _headings(p.read_text())) for m in MEASUREMENTS)
    ]


def test_measurements_are_recorded() -> None:
    """Exit criterion: event mapping size (lines of code), token overhead against plain Python,
    the local hop's extra latency (p50 and p95), the overhead per streamed delta, and the time to
    first token are recorded. One dated note in `notes/` has a heading per measure, names both
    frameworks and plain Python, gives p50 and p95, and holds the command that produced it.
    """
    notes = _measurement_notes()
    assert notes, f"no dated note in {NOTES.relative_to(ROOT)} has the headings {MEASUREMENTS}"
    text = notes[-1].read_text()
    lower = text.lower()
    for word in ("p50", "p95", "pydanticai", "langgraph", "plain python"):
        assert word in lower, f"{notes[-1].name} does not mention {word!r}"
    assert "```bash" in text or "```sh" in text, "the command that produced it is missing"


def test_contract_v1_is_written_down() -> None:
    """Exit criterion: contract v1 is written down: the `handle` contract, the chassis event
    schema, and its A2A mapping, with any changes from v0 and why.
    """
    text = CONTRACT_V1.read_text()
    for section in (
        "## The `handle` contract",
        "## Events",
        "## Chassis events over A2A",
        "## Changes from v0",
    ):
        assert section in text, section
    for name in ("`start`", "`delta`", "`tool_call`", "`metrics`", "`end`", "`error`"):
        assert name in text, name
    changes = text.split("## Changes from v0", 1)[1].split("\n## ", 1)[0]
    assert changes.strip(), "the changes from v0 section is empty"
    assert "why" in changes.lower() or "because" in changes.lower(), "a change without a reason"
    assert "sidecar" in text and "inprocess" in text, "v1 names both lanes"
