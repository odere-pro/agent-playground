"""`make lint-extra` wiring, offline: the Makefile target, the CI job, and the pinned tools.

The extra linters (shellcheck, actionlint, hadolint, codespell, yamllint, detect-secrets) come
from PyPI wheels in the root dev group, so `uv.lock` pins each binary by hash. They run in their
own CI job and stay out of `make quick` and `make check`, so the pre-commit hook stays fast.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
CI = ROOT / ".github/workflows/ci.yml"
MAKEFILE = ROOT / "Makefile"
PYPROJECT = ROOT / "pyproject.toml"

# Wheel name on PyPI -> the command the Makefile target runs.
TOOLS = {
    "shellcheck-py": "shellcheck",
    "actionlint-py": "actionlint",
    "hadolint-py": "hadolint",
    "codespell": "codespell",
    "yamllint": "yamllint",
    "detect-secrets": "detect-secrets-hook",
}
FULL_SHA = re.compile(r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")


def _jobs() -> dict[str, Any]:
    jobs: dict[str, Any] = yaml.safe_load(CI.read_text())["jobs"]
    return jobs


def _recipe(target: str) -> str:
    """The recipe lines of one Makefile target."""
    lines = MAKEFILE.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f"{target}:"))
    body = []
    for line in lines[start + 1 :]:
        if not line.startswith("\t"):
            break
        body.append(line)
    return "\n".join(body)


def test_ci_has_a_lint_extra_job_that_runs_the_make_target() -> None:
    job = _jobs()["lint-extra"]
    assert job["runs-on"] == "ubuntu-latest"
    runs = [step.get("run", "") for step in job["steps"]]
    assert "make lint-extra" in runs
    assert "uv sync --all-packages --locked" in runs


def test_lint_extra_job_pins_the_same_actions_as_the_check_job() -> None:
    jobs = _jobs()
    uses = [step["uses"] for step in jobs["lint-extra"]["steps"] if "uses" in step]
    assert uses, "the lint-extra job uses no action"
    for ref in uses:
        assert FULL_SHA.match(ref), f"{ref} is not pinned by a full commit SHA"
    check_uses = [step["uses"] for step in jobs["check"]["steps"] if "uses" in step]
    assert uses == check_uses


def test_makefile_target_runs_every_tool() -> None:
    recipe = _recipe("lint-extra")
    for command in TOOLS.values():
        assert re.search(rf"\b{re.escape(command)}\b", recipe), f"lint-extra does not run {command}"


def test_lint_extra_is_phony_and_in_help() -> None:
    text = MAKEFILE.read_text()
    phony = next(line for line in text.splitlines() if line.startswith(".PHONY:"))
    assert "lint-extra" in phony.split()
    assert re.search(r"^lint-extra:.*## \S", text, re.MULTILINE)


@pytest.mark.parametrize("gate", ["quick", "check"])
def test_fast_gates_do_not_run_lint_extra(gate: str) -> None:
    assert "lint-extra" not in _recipe(gate)


def test_tools_are_pinned_in_the_root_dev_group() -> None:
    dev = tomllib.loads(PYPROJECT.read_text())["dependency-groups"]["dev"]
    pins = {spec.split("==")[0]: spec for spec in dev}
    for wheel in TOOLS:
        assert wheel in pins, f"{wheel} is not in the root dev group"
        assert "==" in pins[wheel], f"{wheel} is not pinned exactly"


def test_tool_configs_exist() -> None:
    for name in (".hadolint.yaml", ".yamllint.yaml", ".secrets.baseline"):
        assert (ROOT / name).is_file(), f"{name} is missing"
    assert "codespell" in tomllib.loads(PYPROJECT.read_text())["tool"]
