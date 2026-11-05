"""PoC-6b, one test per exit criterion in the README (numbered 1 to 8 in order).

Each docstring starts with the README criterion, word for word, and
`test_the_criteria_tests_quote_every_readme_criterion` pins that. A test here is a cheap check
that the offline evidence the README cites is there: the bound suite class, the note, the pinning
test file. It never reruns the heavy suites; those run in their own files in the same gate. The
kind half of a criterion stays with the kind tests (`POC06_KIND`, `poc06-kind.yml`). An open
criterion is a skip that names what it waits on.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
POC = Path(__file__).resolve().parents[1]
TESTS = POC / "tests"
README = POC / "README.md"
SCORECARD = ROOT / "pocs/poc-06a-bake-off-sidecar-lane/notes/2026-10-09-scorecard.md"
LANES_NOTE = POC / "notes/2026-10-09-lanes-b-kind.md"
LOAD_NOTE = POC / "notes/2026-10-10-remote-load-on-kind.md"
CONTRACT_V5 = ROOT / "docs/contracts/contract-v5.md"
FREEZE_TESTS = ROOT / "packages/chassis/tests/test_contract_freeze.py"
SUITE = "EngineConnectorContract"
KIND_SUITES = (
    "test_poc06b_kind_remote_controls.py",
    "test_poc06b_kind_sidecar_controls.py",
    "test_poc06b_kind_engines.py",
    "test_poc06b_kind_load.py",
)
"""The kind tests criterion 4 cites."""
CRITERION_COUNT = 8
_CRITERION = re.compile(r"^- \[[ x]\] (\d+)\. (.+?)(?: · |$)")
_TEST_NAME = re.compile(r"^test_criterion_(\d+)_")


def readme_criteria() -> dict[int, str]:
    """The README's exit criteria: number -> sentence, the text before the first ` · `."""
    section = README.read_text(encoding="utf-8").partition("## Exit criteria")[2]
    section = section.partition("\n## ")[0]
    found = (_CRITERION.match(line) for line in section.splitlines())
    return {int(m.group(1)): m.group(2) for m in found if m}


def criterion_tests() -> dict[int, Callable[..., object]]:
    """This module's `test_criterion_N_*` functions, by N."""
    found = ((_TEST_NAME.match(name), fn) for name, fn in globals().items() if callable(fn))
    return {int(m.group(1)): fn for m, fn in found if m}


def binds_suite(path: Path, cls: str, suite: str = SUITE) -> bool:
    """True when `path` imports `suite` from `chassis_contracts` and `cls` subclasses it."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = any(
        isinstance(n, ast.ImportFrom)
        and (n.module or "").startswith("chassis_contracts")
        and any(a.name == suite for a in n.names)
        for n in ast.walk(tree)
    )
    subclassed = any(
        isinstance(n, ast.ClassDef)
        and n.name == cls
        and any(isinstance(b, ast.Name) and b.id == suite for b in n.bases)
        for n in ast.walk(tree)
    )
    return imported and subclassed


def is_kind_drill(path: Path) -> bool:
    """True when `path` imports `POC06_KIND` from `poc_harness.drills` and marks tests with it."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = any(
        isinstance(n, ast.ImportFrom)
        and n.module == "poc_harness.drills"
        and any(a.name == "POC06_KIND" for a in n.names)
        for n in ast.walk(tree)
    )
    uses = sum(isinstance(n, ast.Name) and n.id == "POC06_KIND" for n in ast.walk(tree))
    return imported and uses >= 1


def _flat(text: str | None) -> str:
    return " ".join((text or "").split())


def test_criterion_1_the_remote_engines_bind_the_engine_suite_over_a2a() -> None:
    """Every new Python workload passes the `EnginePort` contract suite over A2A on localhost and
    in memory, and runs offline against the fake model server.

    Here: `TestRemoteEngineOverA2A` binds the suite in the `remote` lane; it runs in this gate.
    The in-memory half is 6a's `TestEngineInMemory`, on purpose (the README says why)."""
    path = TESTS / "test_poc06b_remote_engine_contract.py"

    assert binds_suite(path, "TestRemoteEngineOverA2A")


def test_criterion_2_the_plain_a2a_remote_binds_the_engine_suite() -> None:
    """The non-Python agent and the remote solution pass the same contract suite, with no change
    to the chassis core.

    Here: `TestPlainA2ARemoteLane` binds the same suite over a plain-A2A stub. The real
    `kagent-adk` is the kind half, `test_poc06b_kind_engines.py` (`POC06_KIND`)."""
    path = TESTS / "test_poc06b_plain_a2a_contract.py"

    assert binds_suite(path, "TestPlainA2ARemoteLane")


def test_criterion_3_the_scorecard_marks_the_unfilled_kagent_cells() -> None:
    """Every shortlisted engine is scored on every criterion, with numbers where the criterion is
    measurable.

    Here: the scorecard exists and marks the `kagent-adk` cells "not measured: kind, from CI",
    not blank. 6a's `test_poc06a_scorecard_records.py` checks the numbers."""
    text = SCORECARD.read_text(encoding="utf-8")

    assert "kagent-adk" in text and "not measured: kind, from CI" in text


def test_criterion_4_the_kind_suites_are_kind_drills() -> None:
    """Every supported engine passes the contract, load, and hostile suites, in its lane.

    Here, the offline half: each kind test the criterion cites is gated on `POC06_KIND`, so it
    cannot pass offline having checked nothing. `test_poc06b_kind_static.py` runs in this gate."""
    not_drills = [name for name in KIND_SUITES if not is_kind_drill(TESTS / name)]

    assert not_drills == []


def test_criterion_5_token_overhead_on_a_hosted_model() -> None:
    """The token overhead against plain Python is known per engine, on a hosted big model."""
    pytest.skip(
        "waits on the Mac run (`make poc06-mac`) for hosted-model numbers; the offline half of "
        "the hosted run is test_poc06b_hosted_static.py"
    )


def test_criterion_6_the_uncontrolled_list_is_written_for_the_remote_solution() -> None:
    """What the chassis cannot control is listed for the remote solution.

    Here: contract v5 B.8 and the lanes-b-kind note each have the list's heading."""
    headings = (
        "### B.8 What the chassis cannot see or control" in CONTRACT_V5.read_text(),
        "## What the chassis cannot see or control for kagent-adk" in LANES_NOTE.read_text(),
    )

    assert headings == (True, True)


def test_criterion_7_the_adr_names_default_supported_and_rejected_engines() -> None:
    """An ADR names the default engine, the supported engines with their lane, and the rejected
    engines with reasons."""
    pytest.skip(
        "ADR-006 (docs/planning/adr/006-agent-engines-default-supported-lanes.md) is Accepted on "
        "2026-10-30, but the README box stays open until the owner ticks it"
    )


def test_criterion_8_the_freeze_is_pinned_by_its_own_test_file() -> None:
    """The `handle` contract and the chassis event schema are frozen as v1, or the changes they
    needed are listed.

    Here: `test_contract_freeze.py` exists and pins the schema files byte for byte; it runs in
    `make test`, not in this PoC's gate."""
    names = {
        n.name
        for n in ast.walk(ast.parse(FREEZE_TESTS.read_text(encoding="utf-8")))
        if isinstance(n, ast.FunctionDef)
    }

    assert "test_frozen_schema_file_is_byte_identical" in names


def test_the_criteria_tests_quote_every_readme_criterion() -> None:
    """Given the README's eight criteria, each `test_criterion_N_*` docstring starts with N."""
    criteria = readme_criteria()

    quoted = {
        n: _flat(fn.__doc__).startswith(criteria.get(n, "\0"))
        for n, fn in criterion_tests().items()
    }

    assert (len(criteria), quoted) == (CRITERION_COUNT, dict.fromkeys(criteria, True))
