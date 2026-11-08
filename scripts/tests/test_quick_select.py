"""scripts/quick_select.py: which tests `make quick`, and so the pre-commit hook, runs.

Every test builds its own small tree under tmp_path, never the real repository.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "quick_select.py"


@pytest.fixture
def quick() -> Iterator[ModuleType]:
    """The quick_select module, imported without running it."""
    spec = importlib.util.spec_from_file_location("quick_select_under_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop("quick_select_under_test", None)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A tree with two packages that have tests, one that has none, scripts/tests, and a PoC."""
    for d in (
        "packages/chassis/tests",
        "packages/agentctl/tests",
        "scripts/tests",
        "pocs/poc-01-x/tests",
    ):
        (tmp_path / d).mkdir(parents=True)
        (tmp_path / d / "test_a.py").write_text("def test_a():\n    pass\n")
    (tmp_path / "packages/fake-langfuse/src").mkdir(parents=True)
    return tmp_path


def test_a_docs_only_change_runs_no_test(quick: ModuleType, root: Path) -> None:
    changed = ["docs/plans/p.md", "HANDOVER.md", "docs/planning/issues/001-x.md"]

    assert quick.select(changed, root) == []


def test_a_poc_change_runs_no_poc_test(quick: ModuleType, root: Path) -> None:
    changed = ["pocs/poc-01-x/tests/test_a.py", "pocs/poc-01-x/README.md"]

    assert quick.select(changed, root) == []


def test_a_package_change_runs_that_package(quick: ModuleType, root: Path) -> None:
    changed = ["packages/chassis/src/chassis/core/trace.py", "packages/chassis/README.md"]

    assert quick.select(changed, root) == ["packages/chassis"]


def test_a_package_with_no_tests_runs_nothing(quick: ModuleType, root: Path) -> None:
    assert quick.select(["packages/fake-langfuse/src/x.py"], root) == []


def test_a_scripts_change_runs_the_scripts_tests(quick: ModuleType, root: Path) -> None:
    assert quick.select(["scripts/check_offline.sh"], root) == ["scripts/tests"]


def test_a_planning_tools_change_runs_the_scripts_tests(quick: ModuleType, root: Path) -> None:
    assert quick.select(["docs/planning/tools/progress.py"], root) == ["scripts/tests"]


@pytest.mark.parametrize("shared", ["Makefile", "pyproject.toml", "uv.lock", "conftest.py"])
def test_a_shared_file_runs_every_non_poc_test(quick: ModuleType, root: Path, shared: str) -> None:
    assert quick.select([shared, "pocs/poc-01-x/README.md"], root) == ["packages", "scripts/tests"]


def test_each_folder_is_listed_once_in_a_stable_order(quick: ModuleType, root: Path) -> None:
    changed = [
        "scripts/lint_staged.sh",
        "packages/chassis/a.py",
        "packages/agentctl/b.py",
        "packages/chassis/c.py",
    ]

    assert quick.select(changed, root) == ["packages/agentctl", "packages/chassis", "scripts/tests"]


def test_a_deleted_package_runs_nothing(quick: ModuleType, root: Path) -> None:
    assert quick.select(["packages/gone/src/x.py"], root) == []
