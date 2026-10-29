"""`python -m bakeoff.redteam` on real localhost processes. Needs sockets, so it is marked
`network`; it runs the CLI as a child process, the same way as the smoke test."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from bakeoff.envs import build_env
from bakeoff.registry import ROOT

pytestmark = pytest.mark.network


def test_redteam_on_echo_python_inprocess_meets_the_baseline(tmp_path: Path) -> None:
    out = tmp_path / "redteam.json"
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "bakeoff.redteam",
            "--engine",
            "echo-python",
            "--lane",
            "inprocess",
            "--config",
            "keyword",
            "--out",
            str(out),
        ],
        env=build_env(tmp_path),
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "PASS keyword echo-python/inprocess" in done.stdout
    summary = json.loads(out.read_text())["summary"]["keyword"]["echo-python/inprocess"]
    baseline = json.loads((ROOT / "evals/redteam/baseline.json").read_text())
    recorded = baseline["configs"]["keyword"]["echo-python/inprocess"]
    assert summary["stopped"] == recorded["stopped"] and summary["cases"] == recorded["cases"]
