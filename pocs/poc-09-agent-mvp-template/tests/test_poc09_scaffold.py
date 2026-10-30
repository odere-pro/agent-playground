"""PoC-9 exit criteria 1 and 4, scaffold half.

Criterion 1: "A scaffolded agent's `make test` passes offline on its first commit."
Criterion 4: "A scaffolded agent passes its own CI with no manual edits."

`agentctl new` scaffolds one agent per sidecar framework (ADR-006). Each must have a first commit
on `main`, a clean tree, every file the agent's `make test` and CI need, and a `uv.lock` pinned
to the platform's tested versions. Then the agent's own `make test` runs: its unit tests and the
lane contract over A2A, with the chassis in the `fake` profile as a separate process
(agent-testkit). Nothing reaches the network: `UV_OFFLINE=1` for every uv call.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SIDECAR_FRAMEWORKS = ("python", "pydanticai", "langgraph", "openai-agents")
REMOTE_FRAMEWORKS = ("smolagents", "claude-agent")
TREE = (
    ".copier-answers.yml",
    "uv.lock",
    "Dockerfile",
    "Makefile",
    "README.md",
    "CLAUDE.md",
    "pyproject.toml",
    "config/agent.yaml",
    "config/fake.yaml",
    "tests/conftest.py",
    "tests/test_contract.py",
    "tests/test_handle.py",
)


def _env() -> dict[str, str]:
    """No user git config (identity, signing, hooks), no key, nothing from this shell.

    `HOME` stays the user's: uv finds its cache and its Python there, and offline it has no other
    source. Git ignores it (`GIT_CONFIG_GLOBAL`), so no identity or hook leaks in.
    """
    return {
        "PATH": os.environ["PATH"],
        "HOME": os.environ["HOME"],
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "UV_OFFLINE": "1",
    }


def _scaffold(tmp_path: Path, framework: str, connector: str = "sidecar") -> Path:
    tmp_path.mkdir(exist_ok=True)
    name = f"poc09-{framework}"
    done = subprocess.run(
        [
            *(sys.executable, "-m", "agentctl", "new", name, "--kind", "transformer"),
            *("--framework", framework, "--connector", connector, "--dest", str(tmp_path)),
            *("--platform-path", str(ROOT)),
        ],
        env=_env(),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
    return tmp_path / name


def _git(agent: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(agent), *args],
        env=_env(),
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@pytest.mark.parametrize("framework", SIDECAR_FRAMEWORKS)
def test_c1_c4_scaffold_has_a_first_commit_and_a_complete_tree(
    tmp_path: Path, framework: str
) -> None:
    agent = _scaffold(tmp_path, framework)
    assert len(_git(agent, "log", "--format=%H").split()) == 1
    assert _git(agent, "status", "--porcelain") == ""
    committed = set(_git(agent, "ls-files").split())
    package = agent.name.replace("-", "_")
    for path in (*TREE, f"src/{package}/handle.py", f"src/{package}/__init__.py"):
        assert path in committed, path


@pytest.mark.slow
@pytest.mark.parametrize("framework", SIDECAR_FRAMEWORKS)
def test_c1_scaffolded_make_test_passes_offline(tmp_path: Path, framework: str) -> None:
    agent = _scaffold(tmp_path, framework)
    done = subprocess.run(
        ["make", "test"],
        cwd=agent,
        env=_env(),
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-2000:]


CI_STEPS = ("lint", "test", "load-smoke", "hostile", "redteam", "eval")
"""The template CI's steps that need no registry. `image` and `sign` need Docker and a push."""


@pytest.mark.slow
@pytest.mark.parametrize("framework", (*SIDECAR_FRAMEWORKS, *REMOTE_FRAMEWORKS))
def test_c4_ci_steps_pass_from_a_fresh_clone(tmp_path: Path, framework: str) -> None:
    """Criterion 4, what a runner does: clone the agent, the platform at another path, no edits."""
    connector = "remote" if framework in REMOTE_FRAMEWORKS else "sidecar"
    source = _scaffold(tmp_path / "made", framework, connector)
    runner = tmp_path / "runner"
    runner.mkdir()
    (runner / "platform").symlink_to(ROOT, target_is_directory=True)
    agent = runner / "agent"
    subprocess.run(["git", "clone", "-q", str(source), str(agent)], env=_env(), check=True)
    env = {**_env(), "PLATFORM_PATH": str(runner / "platform")}
    for step in CI_STEPS:
        done = subprocess.run(
            ["make", step], cwd=agent, env=env, capture_output=True, text=True, timeout=900
        )
        assert done.returncode == 0, (step, done.stdout[-3000:], done.stderr[-2000:])
    assert subprocess.run(["uv", "lock", "--check"], cwd=agent, env=env).returncode == 0
    assert _git(agent, "status", "--porcelain") == ""
