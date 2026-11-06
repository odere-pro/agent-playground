"""Spec (review finding R4-02): given CI and PoC-6 run the load matrix and the steady client, when
they start, then they import a package and name no `pocs/poc-04-*` path."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
# Built from two parts, so this file does not match its own search.
OLD_LOAD_DIR = "poc-04-stateless-scalable" + "/load"
CALLERS = (".github", "scripts", "deploy", "packages", "Makefile")
SKIP_PARTS = frozenset({".venv", "node_modules", "__pycache__", ".git", ".mypy_cache"})


def caller_files() -> list[Path]:
    """Every file under the CI, script, deploy, and package roots, and the Makefile."""
    roots = [ROOT / name for name in CALLERS]
    files = [path for path in roots if path.is_file()]
    files += [
        path
        for root in roots
        if root.is_dir()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not SKIP_PARTS.intersection(path.relative_to(ROOT).parts)
    ]
    return files


def lines_naming(needle: str, files: list[Path]) -> list[str]:
    """Each `path:line` in `files` whose text has `needle`. Binary files are skipped."""
    hits: list[str] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        hits += [
            f"{path.relative_to(ROOT)}:{number}"
            for number, line in enumerate(text.splitlines(), 1)
            if needle in line
        ]
    return hits


def test_load_tools_callers_name_no_poc_04_load_path() -> None:
    """CI, the scripts, the Makefile, deploy, and packages reach the load tools as a package."""
    files = caller_files()

    hits = lines_naming(OLD_LOAD_DIR, files)

    assert hits == []
