"""The rendered agent's own gates, run in the monorepo venv (it has every framework): its unit
tests pass offline and its code is ruff clean under its own config. The kit-backed contract test
needs `agent-testkit`; the PoC-9 scenario test runs the agent's `make test` once it is merged.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from agentctl import rules
from agentctl.scaffold import Answers, render

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.slow


def _agent(tmp_path: Path, framework: str) -> Path:
    connector = "remote" if framework in rules.UNTRUSTED else "sidecar"
    answers = Answers(
        agent_name="my-agent",
        kind="transformer",
        framework=framework,
        connector=connector,
        platform_path=ROOT,
    )
    return render(answers, tmp_path)


def _env(agent: Path) -> dict[str, str]:
    keep = ("PATH", "HOME", "TMPDIR", "LANG")
    env = {k: v for k, v in os.environ.items() if k in keep}
    return env | {"PYTHONPATH": str(agent / "src")}


@pytest.mark.parametrize("framework", rules.FRAMEWORKS)
def test_rendered_unit_tests_pass_offline(tmp_path: Path, framework: str) -> None:
    agent = _agent(tmp_path, framework)
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "--noconftest",
            "--disable-socket",
            "--allow-unix-socket",
            "-c",
            str(agent / "pyproject.toml"),
            "--rootdir",
            str(agent),
            str(agent / "tests/test_handle.py"),
        ],
        cwd=agent,
        env=_env(agent),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-2000:]


@pytest.mark.parametrize("framework", rules.FRAMEWORKS)
def test_rendered_code_is_ruff_clean(tmp_path: Path, framework: str) -> None:
    agent = _agent(tmp_path, framework)
    for args in (["check", "--no-cache", "."], ["format", "--check", "--no-cache", "."]):
        done = subprocess.run(
            [sys.executable, "-m", "ruff", *args],
            cwd=agent,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert done.returncode == 0, done.stdout + done.stderr
