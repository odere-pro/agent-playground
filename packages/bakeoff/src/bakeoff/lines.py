"""Code-line counts for an engine's mapping files, counted like PoC-2's `measure.py`.

Code lines are total lines minus blank lines minus comment lines. For Python, comment lines are
`#` lines and docstring lines. For TypeScript, they are `//` and `/* */` lines.
"""

from __future__ import annotations

import ast
import io
import tokenize
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

__all__ = ["LineCount", "count_file", "count_files", "count_source"]


@dataclass(frozen=True)
class LineCount:
    path: str
    total: int
    blank: int
    comment: int

    @property
    def code(self) -> int:
        return self.total - self.blank - self.comment


def _python_comment_lines(source: str) -> set[int]:
    rows = source.splitlines()
    lines: set[int] = set()
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT and not rows[tok.start[0] - 1][: tok.start[1]].strip():
            lines.add(tok.start[0])
    for node in ast.walk(ast.parse(source)):
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            continue
        for stmt in body:  # a bare string expression is a docstring
            if (
                isinstance(stmt, ast.Expr)
                and isinstance(stmt.value, ast.Constant)
                and isinstance(stmt.value.value, str)
            ):
                lines.update(range(stmt.lineno, (stmt.end_lineno or stmt.lineno) + 1))
    return lines


def _typescript_comment_lines(source: str) -> set[int]:
    lines: set[int] = set()
    in_block = False
    for number, line in enumerate(source.splitlines(), start=1):
        stripped = line.strip()
        if in_block:
            lines.add(number)
            in_block = "*/" not in stripped
        elif stripped.startswith("//"):
            lines.add(number)
        elif stripped.startswith("/*"):
            lines.add(number)
            in_block = "*/" not in stripped
    return lines


def count_source(path: str, source: str) -> LineCount:
    rows = source.splitlines()
    blank = {i for i, line in enumerate(rows, start=1) if not line.strip()}
    comments = (
        _python_comment_lines(source) if path.endswith(".py") else _typescript_comment_lines(source)
    )
    return LineCount(path, len(rows), len(blank), len(comments - blank))


def count_file(root: Path, relative: str) -> LineCount:
    return count_source(relative, (root / relative).read_text())


def count_files(root: Path, relatives: Sequence[str]) -> list[LineCount]:
    """The counts of the files that exist; a missing file is left out."""
    return [count_file(root, rel) for rel in relatives if (root / rel).is_file()]
