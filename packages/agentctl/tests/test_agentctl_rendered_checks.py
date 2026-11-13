"""The rendered agent's own gates, run in the monorepo venv (it has every framework): its unit
tests and its kit-backed contract test pass offline, and its code is ruff clean under its own
config. The PoC-9 scenario test runs the agent's `make test` once it is merged.
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


LANES: list[tuple[str, str | None]] = [
    *((framework, None) for framework in rules.FRAMEWORKS),
    ("langgraph", "untrusted"),
]
"""(framework, --trust). None leaves trust to the framework; `langgraph` with `--trust untrusted`
is a trusted engine in the remote lane (P-15)."""


def _agent(tmp_path: Path, framework: str, trust: str | None = None) -> Path:
    resolved = rules.resolve_trust(framework, trust)
    answers = Answers(
        agent_name="my-agent",
        kind="transformer",
        framework=framework,
        connector=rules.default_connector(resolved),
        platform_path=ROOT,
        trust=trust,
    )
    return render(answers, tmp_path)


def _env(agent: Path) -> dict[str, str]:
    # UV_CACHE_DIR: the chassis starts with `uv --offline`, and CI's cache is not under HOME.
    keep = ("PATH", "HOME", "TMPDIR", "LANG", "UV_CACHE_DIR")
    env = {k: v for k, v in os.environ.items() if k in keep}
    return env | {"PYTHONPATH": str(agent / "src")}


def _pytest(
    agent: Path, test_file: str, plugins: tuple[str, ...] = ()
) -> subprocess.CompletedProcess[str]:
    loaded = [arg for plugin in plugins for arg in ("-p", plugin)]
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            *loaded,
            "--noconftest",
            "--disable-socket",
            "--allow-unix-socket",
            "-c",
            str(agent / "pyproject.toml"),
            "--rootdir",
            str(agent),
            str(agent / "tests" / test_file),
        ],
        cwd=agent,
        env=_env(agent),
        capture_output=True,
        text=True,
        timeout=300,
    )


@pytest.mark.parametrize(("framework", "trust"), LANES)
def test_rendered_unit_tests_pass_offline(
    tmp_path: Path, framework: str, trust: str | None
) -> None:
    done = _pytest(_agent(tmp_path, framework, trust), "test_handle.py")
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-2000:]


@pytest.mark.parametrize(("framework", "trust"), LANES)
def test_rendered_contract_test_passes_offline(
    tmp_path: Path, framework: str, trust: str | None
) -> None:
    """059 H-15 box 4: each framework's event mapping passes the lane contract over A2A, with the
    chassis in the `fake` profile as its own process (agent-testkit), on Unix sockets only."""
    agent = _agent(tmp_path, framework, trust)
    done = _pytest(agent, "test_contract.py", plugins=("agent_testkit.plugin",))
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-2000:]
    assert " passed" in done.stdout
    expected_skips = " 1 skipped" if framework in NO_INPUT_AS_DATA else " skipped"
    assert (expected_skips in done.stdout) == (framework in NO_INPUT_AS_DATA), done.stdout[-3000:]


NO_INPUT_AS_DATA = {"smolagents"}
"""The template turns off the kit's input-as-data case (025 H-10) only for these; it is their one
allowed skip."""


IN_PROCESS_STATELESS = tuple(f for f in rules.FRAMEWORKS if f != "claude-agent")
"""The claude-agent example runs its own CLI with a fresh HOME on disk per run (an emptyDir in
the pod), so the in-process stateless check (057 H-19) is not rendered for it."""


@pytest.mark.parametrize("framework", IN_PROCESS_STATELESS)
def test_rendered_stateless_check_passes_offline(tmp_path: Path, framework: str) -> None:
    """057 H-19: the template's example workload keeps nothing on disk or between calls."""
    done = _pytest(_agent(tmp_path, framework), "test_stateless.py")
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-2000:]
    assert " passed" in done.stdout


def test_the_stateless_check_is_not_rendered_for_claude_agent(tmp_path: Path) -> None:
    assert not (_agent(tmp_path, "claude-agent") / "tests/test_stateless.py").exists()


@pytest.mark.parametrize(("framework", "trust"), LANES)
def test_rendered_code_is_ruff_clean(tmp_path: Path, framework: str, trust: str | None) -> None:
    agent = _agent(tmp_path, framework, trust)
    for args in (["check", "--no-cache", "."], ["format", "--check", "--no-cache", "."]):
        done = subprocess.run(
            [sys.executable, "-m", "ruff", *args],
            cwd=agent,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert done.returncode == 0, done.stdout + done.stderr


def test_rendered_remote_lane_suite_passes_offline_for_trust_untrusted(tmp_path: Path) -> None:
    """`--trust untrusted` on langgraph: the generated remote-lane suite runs and every run must
    answer `ok` or `fallback`, as for any trusted engine (no `error` allowance)."""
    agent = _agent(tmp_path, "langgraph", "untrusted")
    done = _pytest(agent, "test_remote_lane.py", plugins=("agent_testkit.plugin",))
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-2000:]
    assert " passed" in done.stdout and " skipped" not in done.stdout, done.stdout[-3000:]
