"""`bakeoff smoke` on real localhost processes. Needs sockets, so it is marked `network`."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from bakeoff.envs import build_env
from bakeoff.registry import ROOT

pytestmark = pytest.mark.network


def test_smoke_passes_for_echo_python_inprocess_and_sidecar(tmp_path: Path) -> None:
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "bakeoff",
            "smoke",
            "--engine",
            "echo-python",
            "--lane",
            "inprocess,sidecar",
        ],
        env=build_env(tmp_path),
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    lines = [line.split() for line in done.stdout.splitlines()]
    assert lines == [["PASS", "echo-python", "inprocess"], ["PASS", "echo-python", "sidecar"]]
