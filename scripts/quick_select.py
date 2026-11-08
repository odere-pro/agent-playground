"""Pick the tests `make quick` runs, and so the pre-commit hook, from the changed files.

Usage: git diff --name-only HEAD | python3 scripts/quick_select.py
Prints one test folder per line, or nothing when no test covers the change.

PoC scenario tests never run here. Run them on demand: `make test-poc POC=NN` locally, or the
PoC's CI workflow by dispatch. The CI `check` job runs every non-PoC test on each PR.

Rules for each changed file:
- A shared file (`Makefile`, `pyproject.toml`, `uv.lock`, a root `conftest.py`) runs every
  non-PoC test: `packages` and `scripts/tests`.
- `packages/<name>/...` runs `packages/<name>`, when that folder has tests.
- `scripts/...` or `docs/planning/tools/...` runs `scripts/tests`.
- Anything else runs no test: `pocs/`, docs, Markdown, `.github/`.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHARED = frozenset({"Makefile", "pyproject.toml", "uv.lock", "conftest.py"})
EVERYTHING = ["packages", "scripts/tests"]
SCRIPT_TESTS = "scripts/tests"


def has_tests(folder: Path) -> bool:
    return folder.is_dir() and any("node_modules" not in p.parts for p in folder.rglob("test_*.py"))


def folder_for(path: str, root: Path) -> str | None:
    """The test folder one changed file needs, or None."""
    parts = Path(path).parts
    if parts[:1] == ("packages",) and len(parts) > 1:
        package = f"packages/{parts[1]}"
        return package if has_tests(root / package) else None
    if parts[:1] == ("scripts",) or parts[:3] == ("docs", "planning", "tools"):
        return SCRIPT_TESTS
    return None


def select(changed: Iterable[str], root: Path = ROOT) -> list[str]:
    """The test folders for the changed files: sorted, each once."""
    files = [c.strip() for c in changed if c.strip()]
    if any(f in SHARED for f in files):
        return list(EVERYTHING)
    return sorted({f for c in files if (f := folder_for(c, root)) is not None})


def main() -> int:
    print("\n".join(select(sys.stdin.read().splitlines())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
