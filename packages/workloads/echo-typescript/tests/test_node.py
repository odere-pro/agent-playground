"""Runs the TypeScript workload's own tests (`npm test`) from the Python gate.

The Node tests open no socket: `fetch` is stubbed and the A2A mapping runs on an in-memory event
bus. The subprocess is not under pytest's socket guard, which is why this wrapper is `slow`. It
skips when `node_modules` is absent: CI does not run `npm ci` for this package yet.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1]


@pytest.mark.slow
def test_npm_test_passes() -> None:
    if not (PACKAGE / "node_modules").is_dir():
        pytest.skip("node_modules is absent: run `npm ci` in packages/workloads/echo-typescript")
    npm = shutil.which("npm")
    if npm is None:
        pytest.skip("npm is not on the PATH")
    result = subprocess.run(
        [npm, "test"], cwd=PACKAGE, capture_output=True, text=True, timeout=120, check=False
    )
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
