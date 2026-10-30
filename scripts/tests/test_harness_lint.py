"""harness_lint.py: drift is a finding with exit 1, never a traceback."""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "harness_lint.py"


@pytest.fixture
def lint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[ModuleType]:
    """A fresh harness_lint module whose ROOT is a minimal, clean tree in tmp_path."""
    spec = importlib.util.spec_from_file_location("harness_lint_under_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _minimal_tree(tmp_path)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "CLAUDE", tmp_path / ".claude")
    yield module
    sys.modules.pop("harness_lint_under_test", None)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _minimal_tree(root: Path) -> None:
    _write(root / ".claude" / "settings.json", json.dumps({}))
    _write(
        root / ".claude" / "agents" / "dev.md",
        "---\nname: dev\ndescription: d\ntools: Read\nmodel: x\n---\n",
    )
    _write(
        root / ".claude" / "skills" / "flow" / "SKILL.md", "---\nname: flow\ndescription: d\n---\n"
    )
    _write(
        root / "CLAUDE.md",
        "# x\n\n## Delegation\n\n| Need | Agent |\n| a | `dev` |\n\nSkills: `flow`.\n",
    )
    poc = root / "pocs" / "poc-01-x"
    for name in ("tests", "demo", "notes"):
        (poc / name).mkdir(parents=True)
    _write(poc / "CLAUDE.md", "# poc\n")
    _write(poc / "README.md", "- [x] it works: `tests/test_a.py::test_it_works`\n")
    _write(poc / "tests" / "test_a.py", "def test_it_works():\n    pass\n")
    _write(root / "pocs" / "CURRENT", "poc-01-x\n")
    pkg = root / "packages" / "suite"
    for name in ("tests",):
        (pkg / name).mkdir(parents=True)
    _write(pkg / "CLAUDE.md", "# s\n")
    _write(pkg / "README.md", "# s\n")
    (root / "docs" / "plans").mkdir(parents=True)


def test_the_minimal_tree_is_clean(lint: ModuleType, capsys: pytest.CaptureFixture[str]) -> None:
    assert lint.main() == 0
    assert capsys.readouterr().out.strip() == "harness-lint: ok"


def test_a_missing_current_file_is_a_finding(
    lint: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    (lint.ROOT / "pocs" / "CURRENT").unlink()
    assert lint.main() == 1
    out = capsys.readouterr().out
    assert "harness-lint: pocs/CURRENT is missing" in out
    assert "1 finding(s)" in out


def test_a_claude_md_with_no_skills_line_is_a_finding(
    lint: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    claude_md = lint.ROOT / "CLAUDE.md"
    claude_md.write_text(claude_md.read_text().replace("Skills: `flow`.\n", ""))
    assert lint.main() == 1
    assert "CLAUDE.md: no `Skills:` line under ## Delegation" in capsys.readouterr().out


def test_a_stale_test_citation_fails_and_names_the_readme_line(
    lint: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    readme = lint.ROOT / "pocs" / "poc-01-x" / "README.md"
    readme.write_text(readme.read_text() + "- [x] old: `tests/test_a.py::test_old_name[*]`\n")
    assert lint.main() == 1
    out = capsys.readouterr().out
    assert (
        "pocs/poc-01-x/README.md:2: cites test 'test_old_name', which no test file defines" in out
    )


def test_a_class_citation_counts_methods_inherited_from_a_contract_suite(
    lint: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(
        lint.ROOT / "packages" / "suite" / "src" / "suite" / "contract.py",
        "class PortContract:\n    async def test_streams(self):\n        pass\n",
    )
    _write(
        lint.ROOT / "pocs" / "poc-01-x" / "tests" / "test_b.py",
        "from suite.contract import PortContract\n\nclass TestFake(PortContract):\n    pass\n",
    )
    readme = lint.ROOT / "pocs" / "poc-01-x" / "README.md"
    readme.write_text(readme.read_text() + "- [x] `tests/test_b.py::TestFake::test_streams`\n")
    assert lint.main() == 0, capsys.readouterr().out


def test_a_stale_class_citation_fails(lint: ModuleType, capsys: pytest.CaptureFixture[str]) -> None:
    readme = lint.ROOT / "pocs" / "poc-01-x" / "README.md"
    readme.write_text(readme.read_text() + "see `::TestGone`\n")
    assert lint.main() == 1
    assert "README.md:2: cites test 'TestGone'" in capsys.readouterr().out
