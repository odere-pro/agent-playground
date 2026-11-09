"""`make quick` fails when a selected test fails.

Since PR 113 the `quick` recipe selects tests from the changed files and runs them on one shell
line that ends in an `echo`. A shell line's status is its last command's, so without an explicit
exit the `echo` hid a failing pytest run and the pre-commit hook let the commit through. These
tests take the recipe as `make -n` expands it, with pytest replaced by `false` or `true` and the
selector replaced by `echo` so one test path is always selected, and run that line in `sh`.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _test_line(pytest_command: str) -> str:
    """The expanded shell line of `quick` that runs the selected tests."""
    env = {**os.environ, "MAKEFLAGS": ""}
    out = subprocess.run(
        ["make", "-n", "quick", f"PYTEST={pytest_command}", "PY=echo"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    # `make -n` prints the recipe's continuation lines one per line, each ending in a backslash.
    joined: list[str] = []
    current = ""
    for line in out.splitlines():
        if line.endswith("\\"):
            current += line[:-1]
            continue
        joined.append(current + line)
        current = ""
    lines = [line for line in joined if "quick_select.py" in line]
    assert len(lines) == 1, out
    return lines[0]


@pytest.mark.parametrize(
    ("pytest_command", "expected"), [("false", 1), ("true", 0)], ids=["failing", "passing"]
)
def test_quick_exits_nonzero_when_the_selected_tests_fail(
    pytest_command: str, expected: int
) -> None:
    line = _test_line(pytest_command)
    assert "echo scripts/quick_select.py" in line, "the selector stand-in picks one path"
    result = subprocess.run(
        ["sh", "-c", line], cwd=ROOT, capture_output=True, text=True, check=False
    )
    assert result.returncode == expected, (result.returncode, result.stdout, result.stderr)
    if expected == 0:
        assert "PoC tests are not in quick" in result.stdout, "the closing hint prints on a pass"
