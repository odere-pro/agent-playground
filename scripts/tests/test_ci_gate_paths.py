"""The CI gate leaves finished PoCs out, and no test folder drops out of both runs.

`make check` (the CI `check` job) runs `make test-gate`; a finished PoC's scenario tests run only
locally, in `make test-pocs-done`. `make gate-paths` prints both lists. These tests run in the
gate, so a PoC folder that falls out of both lists fails CI instead of losing coverage silently.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
from poc_harness.workflows import gate_paths

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"


def _recipe(target: str) -> list[str]:
    lines = MAKEFILE.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f"{target}:"))
    recipe: list[str] = []
    for line in lines[start + 1 :]:
        if not line.startswith("\t"):
            break
        recipe.append(line.strip())
    return recipe


def _poc_test_folders() -> set[str]:
    return {str(p.relative_to(ROOT)) for p in ROOT.glob("pocs/poc-*/tests") if p.is_dir()}


def _done_by_status() -> set[str]:
    done = set()
    for readme in ROOT.glob("pocs/poc-*/README.md"):
        if re.search(r"^Status: done", readme.read_text(), re.M):
            done.add(f"{readme.parent.relative_to(ROOT)}/tests")
    return done


@pytest.fixture(scope="module")
def paths() -> dict[str, list[str]]:
    return gate_paths(ROOT)


def test_gate_paths_put_every_poc_folder_in_exactly_one_list(paths: dict[str, list[str]]) -> None:
    """Every PoC test folder is in the gate or in the local list, never both, never neither."""
    gate = {p for p in paths["gate"] if p.startswith("pocs/")}
    local = set(paths["local"])

    both = gate & local
    neither = _poc_test_folders() - gate - local

    assert not both, both
    assert not neither, neither


def test_gate_paths_keep_packages_and_scripts_tests_in_the_gate(
    paths: dict[str, list[str]],
) -> None:
    """The gate always runs the package tests and the script tests."""
    gate = paths["gate"]

    assert {"packages", "scripts/tests"} <= set(gate)


def test_gate_paths_cover_every_pytest_testpath(paths: dict[str, list[str]]) -> None:
    """Every `testpaths` root is in the gate, or (for `pocs`) split over the two lists."""
    ini = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["pytest"]["ini_options"]
    listed = set(paths["gate"]) | set(paths["local"])

    roots = {root for root in ini["testpaths"] if root != "pocs"}

    assert roots <= listed
    assert _poc_test_folders() <= listed


def test_gate_paths_run_a_poc_with_status_done_only_locally(
    paths: dict[str, list[str]],
) -> None:
    """A PoC whose README says `Status: done` is in the local list, not in the gate."""
    local = set(paths["local"])

    missing = _done_by_status() - local

    assert not missing, missing


def test_make_check_runs_the_gate_and_make_test_runs_everything() -> None:
    """`make check` runs `test-gate`; `make test` keeps no path, so it runs every test."""
    check = _recipe("check")[0].split()

    test = _recipe("test")

    assert "test-gate" in check and "test" not in check
    assert test == ["$(PYTEST)"]
    assert _recipe("test-gate") == ["$(PYTEST) $(GATE_TESTS)"]
    assert _recipe("test-pocs-done") == ["$(PYTEST) $(DONE_POC_TESTS)"]


@pytest.mark.parametrize("lane", ["inprocess", "sidecar", "remote"])
def test_the_gate_binds_the_lane_contract_over_every_lane(lane: str) -> None:
    """ADR-001 hard requirement 2: a package test binds `LaneContract` over every lane."""
    bindings = sorted((ROOT / "packages/chassis/tests").glob("test_lane_contract*.py"))

    labels = re.findall(
        r"lane_labels: ClassVar\[tuple\[str, \.\.\.\]\] = \(([^)]*)\)",
        "\n".join(p.read_text() for p in bindings),
    )

    assert any(f'"{lane}"' in found for found in labels), (lane, labels)
