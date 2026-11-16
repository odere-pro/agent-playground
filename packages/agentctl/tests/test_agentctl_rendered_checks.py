"""The rendered agent's own gates, run in the monorepo venv (it has every framework): its unit
tests and its kit-backed contract test pass offline, and its code is ruff clean under its own
config. The PoC-9 scenario test runs the agent's `make test` once it is merged.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from agent_testkit.readonly_root import MARKER
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
    agent: Path, test_file: str, plugins: tuple[str, ...] = (), extra: tuple[str, ...] = ()
) -> subprocess.CompletedProcess[str]:
    loaded = [arg for plugin in plugins for arg in ("-p", plugin)] + list(extra)
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


@pytest.mark.parametrize("framework", rules.FRAMEWORKS)
def test_rendered_stateless_lane_check_passes_offline(tmp_path: Path, framework: str) -> None:
    """057 H-19: each framework's example passes the `sidecar` lane check: the workload and the
    chassis as processes, each with a read-only root, fresh pods against one reused pod.
    claude-agent runs its CLI against the fake model, with its HOME in a scratch volume that must
    be empty after each call; it is a known gap (`KNOWN_STATEFUL`), so the check runs as `xfail`."""
    agent = _agent(tmp_path, framework)
    done = _pytest(
        agent, "test_stateless_lane.py", plugins=("agent_testkit.plugin",), extra=("-rx",)
    )
    assert done.returncode == 0, _findings(done) + done.stdout[-3000:] + done.stderr[-2000:]
    if framework not in KNOWN_STATEFUL:
        assert "1 passed" in done.stdout, _findings(done) + done.stdout[-3000:]
        return
    assert "1 xfailed" in done.stdout and "XFAIL" in done.stdout, done.stdout[-3000:]
    # `-rx` names the reason only; with `--runxfail` the same run fails and shows the finding.
    shown = _pytest(
        agent, "test_stateless_lane.py", plugins=("agent_testkit.plugin",), extra=("--runxfail",)
    )
    assert shown.returncode == 1, shown.stdout[-3000:] + shown.stderr[-2000:]
    assert "ScratchLeftover" in shown.stdout, shown.stdout[-3000:]
    assert " left " in shown.stdout and "scratch volume" in shown.stdout, shown.stdout[-3000:]
    assert "claude-run-" in shown.stdout, shown.stdout[-3000:]  # the run's HOME


def _findings(done: subprocess.CompletedProcess[str]) -> str:
    """Every stateless finding in the whole output (the write marker, `wrote`, `left`), so a
    failure names the container and the path even when the tail cut them off."""
    lines = (done.stdout + done.stderr).splitlines()
    keys = (MARKER, "stateless check", " wrote ", " left ")
    hits = dict.fromkeys(line.strip() for line in lines if any(key in line for key in keys))
    return "stateless findings:\n" + "\n".join(hits) + "\n--- output tail ---\n" if hits else ""


KNOWN_STATEFUL = {"claude-agent"}
"""On `end` the A2A server closes `handle`, which stops the CLI mid-write, so the run's HOME can
outlive the call (057 H-19). The template marks its lane check `xfail` until `handle` is fixed."""


STATEFUL = """

_seen: list[str] = []
_plain = handle


async def handle(input, ctx):  # noqa: F811  (the red case: a handle that keeps data)
    _seen.append(str(input.get("text")))
    async for out in _plain(input, ctx):
        yield out
        if out["type"] == "start":
            yield {"schema_version": "0", "type": "delta", "text": f"[{len(_seen)}] "}
"""
WRITER = """

_plain = handle


async def handle(input, ctx):  # noqa: F811  (the red case: a handle that writes a file)
    try:
        open("{path}", "w").close()
    except OSError:
        pass
    async for out in _plain(input, ctx):
        yield out
"""


def _with(tmp_path: Path, extra: str) -> Path:
    agent = _agent(tmp_path, "python")
    handle = agent / "src/my_agent/handle.py"
    handle.write_text(handle.read_text() + extra)
    return agent


def test_the_stateless_lane_check_catches_a_stateful_handle(tmp_path: Path) -> None:
    """The red case on a rendered agent: a handle that keeps a count of past requests in a module
    list fails the rendered check, and the failure names the call that differed."""
    agent = _with(tmp_path, STATEFUL)
    done = _pytest(agent, "test_stateless_lane.py", plugins=("agent_testkit.plugin",))
    assert done.returncode == 1, done.stdout[-3000:] + done.stderr[-2000:]
    assert "differed on the reused pod" in done.stdout, done.stdout[-3000:]


def test_the_stateless_lane_check_catches_a_handle_that_writes_a_file(tmp_path: Path) -> None:
    """The red case for the disk: a handle that writes a file and hides the error fails, and the
    failure names the workload container and the path."""
    target = tmp_path / "kept.txt"
    agent = _with(tmp_path, WRITER.format(path=target))
    done = _pytest(agent, "test_stateless_lane.py", plugins=("agent_testkit.plugin",))
    assert done.returncode == 1, done.stdout[-3000:] + done.stderr[-2000:]
    assert f"container 'workload' wrote {target}" in done.stdout, done.stdout[-3000:]
    assert not target.exists()


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


@pytest.mark.parametrize(("framework", "trust"), LANES)
def test_rendered_judged_eval_passes_offline(
    tmp_path: Path, framework: str, trust: str | None
) -> None:
    """059 H-15: the template's judged case passes each framework's `make eval` suite, with the
    fake model scripted per case and the scripted judge (`agent_testkit.evalcases`)."""
    agent = _agent(tmp_path, framework, trust)
    done = _pytest(agent, "suites/test_eval.py", plugins=("agent_testkit.plugin",))
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-2000:]
    report = json.loads((agent / "reports/eval.json").read_text())
    assert report["summary"] == {"total": 1, "passed": 1, "failed": 0}, report
    assert report["meta"]["judge"] == {"g01": 1}
