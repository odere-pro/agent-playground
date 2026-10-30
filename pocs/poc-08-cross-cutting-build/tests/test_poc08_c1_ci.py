"""PoC-8 exit criterion 1: "All suites, including red-team and offline evals, run in CI, and the
`fake` profile still needs no network or keys." (docs/planning/poc/008-PoC-8-build-cross-cutting.md)

Static checks of the CI wiring, the way PoC-3's `test_exit_1_the_suites_run_offline_in_ci_on_every_
commit` checks it, extended to PoC-8:

- `ci.yml` runs `make check` on every push and pull request; `make check` runs `make test`, which
  is `scripts/check_offline.sh` with no path, so pytest's `testpaths` (`packages`, `pocs`) take in
  every PoC test folder, this one included. The script strips `*_API_KEY` and passes
  `--disable-socket --allow-unix-socket --record-mode=none`. Under those flags this folder is
  collected and no test in it is marked `network`.
- `poc08-evals.yml` runs the eval gate (`bakeoff.evalgate`) for every supported engine, the
  drop-facts regression (exit 1 required; criterion 5 checks the recipe), and the red-team set for
  every engine `evals/redteam/baseline.json` records.
- Both workflows: no `secrets.` reference, `contents: read` and nothing more, every action pinned
  by a full commit SHA, never `pull_request_target`. (`poc07-agent-scan.yml` holds the one secret
  in the repo, `SNYK_TOKEN`; it runs no suite of this criterion.)
- At run time, under `make test`: a TCP connect is refused and no `*_API_KEY` is in the
  environment, while every PoC-8 cell runs (the rest of this folder). Under a plain `pytest`
  without the gate's flags this one test is skipped: it checks the gate, not the code.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import socket
import subprocess
import sys
import tomllib
from typing import Any

import pytest
import yaml
from poc_harness.paths import ROOT
from pytest_socket import SocketBlockedError

WORKFLOWS = ROOT / ".github/workflows"
SUITE_WORKFLOWS = ("ci.yml", "poc08-evals.yml", "poc08-stack.yml")
HERE = "pocs/poc-08-cross-cutting-build/tests"
SUPPORTED = {
    "echo-python",
    "echo-pydanticai",
    "echo-langgraph",
    "echo-openai-agents",
    "echo-typescript",
    "echo-smolagents",
    "echo-claude-agent",
}
"""ADR-006's supported engines that run offline. `kagent-adk` runs only on kind (poc06-kind.yml);
both baselines record it as skipped."""
FULL_SHA = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")
GATED_DRILLS = {"test_poc08_stack_metrics.py", "test_poc08_stack_litellm.py"}
"""`network` drills against a running stack, collected only with their variable (conftest.py)."""


def _workflow(name: str) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((WORKFLOWS / name).read_text())
    return data


def _triggers(workflow: dict[Any, Any]) -> dict[str, Any]:
    triggers = workflow.get("on", workflow.get(True))  # YAML 1.1 reads a bare `on` as true
    assert isinstance(triggers, dict)
    return triggers


def _runs(job: dict[str, Any]) -> list[str]:
    return [str(step.get("run", "")) for step in job["steps"]]


def _recipe(makefile: str, target: str) -> list[str]:
    lines = makefile.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f"{target}:"))
    recipe: list[str] = []
    for line in lines[start + 1 :]:
        if not line.startswith("\t"):
            break
        recipe.append(line.strip())
    return recipe


def _gate_flags() -> list[str]:
    """The pytest flags `scripts/check_offline.sh` passes (its `exec uv run pytest` line)."""
    script = (ROOT / "scripts/check_offline.sh").read_text()
    [line] = [line for line in script.splitlines() if line.startswith("exec uv run pytest")]
    words = shlex.split(line)
    return words[words.index("pytest") + 1 : words.index("$@")]


def _matrix(job: dict[str, Any]) -> set[str]:
    return set(job["strategy"]["matrix"]["engine"])


def test_ci_runs_make_check_and_make_check_runs_every_poc_folder_offline() -> None:
    """Criterion 1: CI's `check` job runs `make check` on every commit; `make check` runs the
    offline gate over `packages` and `pocs`, with no filter that could drop a suite."""
    workflow = _workflow("ci.yml")
    triggers = _triggers(workflow)
    assert set(triggers) >= {"push", "pull_request"}
    assert not (triggers["push"] or {}).get("branches"), "every commit, on every branch"
    assert "make check" in _runs(workflow["jobs"]["check"])

    makefile = (ROOT / "Makefile").read_text()
    assert "PYTEST := scripts/check_offline.sh" in makefile
    assert "test" in _recipe(makefile, "check")[0].split()
    assert _recipe(makefile, "test") == ["$(PYTEST)"], "no path, no -m: testpaths decide"

    script = (ROOT / "scripts/check_offline.sh").read_text()
    assert "_API_KEY" in script and 'unset "$v"' in script
    flags = _gate_flags()
    assert {"--disable-socket", "--allow-unix-socket", "--record-mode=none"} <= set(flags)
    assert not any(flag.startswith(("-m", "-k", "--ignore", "--deselect")) for flag in flags)

    ini = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["pytest"]["ini_options"]
    assert {"packages", "pocs"} <= set(ini["testpaths"])
    assert "addopts" not in ini


_COLLECT = r"""
import json, sys, pytest
class Seen:
    items = {}
    def pytest_collection_finish(self, session):
        for item in session.items:
            self.items[item.nodeid] = sorted({m.name for m in item.iter_markers()})
seen = Seen()
code = pytest.main(sys.argv[1:], plugins=[seen])
print("@@COLLECTED@@" + json.dumps({"code": int(code), "items": seen.items}))
"""


@pytest.mark.slow
def test_the_gate_collects_this_folder_with_no_network_test() -> None:
    """Criterion 1: with the gate's own flags, every PoC-8 scenario file is collected, every
    cell is there, and none of it is marked `network` (so none is left out of `make test`).
    The stack drills in `GATED_DRILLS` are not scenario files: they need a running Compose
    stack and are collected only with their variable (conftest.py; test_poc08_litellm_static.py
    checks that gate)."""
    env = {k: v for k, v in os.environ.items() if not k.endswith(("_API_KEY", "_TOKEN"))}
    done = subprocess.run(
        [sys.executable, "-c", _COLLECT, *_gate_flags(), "--collect-only", "-q", HERE],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    [line] = [line for line in done.stdout.splitlines() if line.startswith("@@COLLECTED@@")]
    report = json.loads(line.removeprefix("@@COLLECTED@@"))
    assert report["code"] == 0, done.stdout[-3000:] + done.stderr[-3000:]
    items: dict[str, list[str]] = report["items"]
    files = {node.split("::")[0].rsplit("/", 1)[-1] for node in items}
    expected = {p.name for p in (ROOT / HERE).glob("test_poc08_*.py")} - GATED_DRILLS
    assert files == expected
    assert not [node for node, marks in items.items() if "network" in marks]
    assert not [node for node, marks in items.items() if "skip" in marks]
    assert any("echo-claude-agent-remote" in node for node in items)


def test_poc08_evals_runs_the_gate_and_the_redteam_set_on_every_supported_engine() -> None:
    """Criterion 1 (and 2): the eval gate runs on every supported engine; the red-team job covers
    every engine of `evals/redteam/baseline.json`; the drop-facts regression runs."""
    workflow = _workflow("poc08-evals.yml")
    assert set(_triggers(workflow)) >= {"push", "pull_request"}
    jobs = workflow["jobs"]

    assert _matrix(jobs["evals"]) == SUPPORTED
    evals_runs = " ".join(_runs(jobs["evals"]))
    assert "-m bakeoff.evalrun --engine ${{ matrix.engine }}" in evals_runs
    assert "-m bakeoff.evalgate --report" in evals_runs and "--engine ${{ matrix.engine }}" in (
        evals_runs
    )
    gate = json.loads((ROOT / "evals/gate/baseline.json").read_text())
    gated = {e for agent in gate["agents"].values() for e in agent["engines"]}
    assert gated == SUPPORTED, "every supported engine has a gate baseline"
    assert "kagent-adk" in gate["skipped"]

    assert any(run.startswith("make evals-regression") for run in _runs(jobs["regression"]))

    redteam = json.loads((ROOT / "evals/redteam/baseline.json").read_text())
    recorded = {target.split("/")[0] for cfg in redteam["configs"].values() for target in cfg}
    assert recorded <= _matrix(jobs["redteam"]), recorded - _matrix(jobs["redteam"])
    assert _matrix(jobs["redteam"]) == SUPPORTED
    assert set(redteam["configs"]) == {"litellm", "keyword"}
    assert "-m bakeoff.redteam --engine ${{ matrix.engine }}" in " ".join(_runs(jobs["redteam"]))


@pytest.mark.parametrize("name", SUITE_WORKFLOWS)
def test_the_suite_workflows_hold_no_secret_read_only_and_pinned(name: str) -> None:
    """Criterion 1 ("no keys"): no `secrets.` reference, `contents: read` only, no job widens it,
    every action pinned by a full commit SHA, and no `pull_request_target`."""
    text = (WORKFLOWS / name).read_text()
    assert "secrets." not in text
    workflow = yaml.safe_load(text)
    assert "pull_request_target" not in _triggers(workflow)
    assert workflow["permissions"] == {"contents": "read"}
    for job_name, job in workflow["jobs"].items():
        assert job.get("permissions", {"contents": "read"}) == {"contents": "read"}, job_name
        for step in job["steps"]:
            if "uses" in step:
                assert FULL_SHA.match(step["uses"]), f"{name}/{job_name}: {step['uses']}"


@pytest.mark.filterwarnings("ignore:A test tried to use socket.socket")
def test_under_make_test_no_socket_and_no_key(request: pytest.FixtureRequest) -> None:
    """Criterion 1 ("the `fake` profile needs no network or keys"), at run time: under the gate,
    a TCP connect is refused before it leaves, and no `*_API_KEY` is set. The scenario tests in
    this folder pass in that same session."""
    if not request.config.getoption("--disable-socket", default=False):
        pytest.skip("checks the offline gate: run through `make test` or `make test-poc POC=08`")
    keys = sorted(k for k in os.environ if k.endswith("_API_KEY"))
    assert keys == []
    with pytest.raises(SocketBlockedError):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).close()
