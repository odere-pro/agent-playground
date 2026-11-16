"""025 H-10, the generated repo's CI: its tests and the lane contract gate the build, and the
build signs the workload image.

The workflow is rendered by agentctl and read as data. The Makefile targets the workflow calls run
for real, with `uv` and `cosign` swapped for stubs on PATH, so the exit codes are the ones CI sees.
Offline: no socket, no key, no registry. A hosted run in a generated repo is the only full proof
of the sign step; `pocs/poc-09-agent-mvp-template/notes/2026-10-30-open-items.md` records one.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from agentctl import rules
from agentctl.scaffold import Answers, render

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ".github/workflows/ci.yml"
PINNED_ACTION = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
DEFAULT_BRANCH = "github.ref == format('refs/heads/{0}', github.event.repository.default_branch)"
ALLOWED_SECRETS = {"GITHUB_TOKEN", "PLATFORM_TOKEN"}
SWALLOWS = ("|| true", "; true", "set +e")
"""Shell that turns a failed command green."""
DIGEST_REF = "ghcr.io/acme/my-agent@sha256:" + "a" * 64
UV_STUB = """\
import os, sys
args = sys.argv[1:]
if args[:2] == ["run", "pytest"]:
    os.execv(sys.executable, [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args[2:]])
sys.exit(0)
"""
"""`uv run pytest ...` runs pytest in this venv; every other `uv` call (the chassis env) passes."""
COSIGN_STUB = """\
import os, sys
with open(os.environ["COSIGN_CALLS"], "a") as calls:
    calls.write(" ".join(sys.argv[1:]) + "\\n")
"""


def _render(dest: Path, framework: str = "python") -> Path:
    answers = Answers(
        agent_name="my-agent",
        kind="transformer",
        framework=framework,
        connector=rules.default_connector(rules.resolve_trust(framework, None)),
        platform_path=ROOT,
    )
    return render(answers, dest)


@pytest.fixture(scope="module")
def agent(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _render(tmp_path_factory.mktemp("ci"))


@pytest.fixture(scope="module")
def workflow(agent: Path) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((agent / WORKFLOW).read_text())
    return data


@pytest.fixture(scope="module")
def gitlab(agent: Path) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((agent / ".gitlab-ci.yml").read_text())
    return data


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = job["steps"]
    return out


def _runs(job: dict[str, Any]) -> str:
    return "\n".join(step.get("run", "") for step in _steps(job))


def _stub(bin_dir: Path, name: str, body: str) -> None:
    bin_dir.mkdir(exist_ok=True)
    path = bin_dir / name
    path.write_text(f"#!{sys.executable}\n{body}")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _make(agent: Path, bin_dir: Path, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
    make = shutil.which("make")
    assert make is not None
    env = {k: v for k, v in os.environ.items() if k in ("HOME", "TMPDIR", "LANG")}
    env |= {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "PLATFORM_PATH": str(ROOT)}
    return subprocess.run(
        [make, "--no-print-directory", *args],
        cwd=agent,
        env=env | extra,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


# --- Box 1: CI runs the unit tests and the lane contract, and a failing test fails the build ----


def test_the_test_job_runs_make_test_and_gates_the_image(workflow: dict[str, Any]) -> None:
    jobs = workflow["jobs"]
    assert "make test" in _runs(jobs["test"])
    assert "test" in jobs["image"]["needs"], "a red test must stop the image"
    assert jobs["sign"]["needs"] == "image", "no signature without the image job"


def _after(jobs: dict[str, Any], first: str) -> set[str]:
    """The jobs that need `first`, directly or through another job."""
    found: set[str] = set()
    grew = True
    while grew:
        grew = False
        for name, job in jobs.items():
            needs = job.get("needs", [])
            needs = [needs] if isinstance(needs, str) else needs
            if name not in found and (first in needs or found & set(needs)):
                found.add(name)
                grew = True
    return found


def test_no_job_or_step_can_turn_a_failure_green(
    workflow: dict[str, Any], gitlab: dict[str, Any]
) -> None:
    """In both CI files: no failure is allowed or swallowed, and no job after `test` runs anyway
    when `test` is red."""
    jobs = workflow["jobs"]
    for name, job in jobs.items():
        assert "continue-on-error" not in job, name
        for step in _steps(job):
            assert "continue-on-error" not in step, (name, step)
            for swallow in SWALLOWS:
                assert swallow not in step.get("run", ""), (name, swallow, step)
    for name in _after(jobs, "test"):
        assert "always()" not in str(jobs[name].get("if", "")), name
    gitlab_jobs = {k: v for k, v in gitlab.items() if isinstance(v, dict) and "script" in v}
    assert {"test", "build-image", "sign"} <= set(gitlab_jobs)
    for name, job in gitlab_jobs.items():
        assert "allow_failure" not in job, name
        assert job.get("when") != "always", name
        for line in job.get("before_script", []) + job["script"]:
            for swallow in SWALLOWS:
                assert swallow not in line, (name, swallow, line)


@pytest.mark.parametrize("framework", rules.FRAMEWORKS)
def test_make_test_holds_the_unit_tests_and_the_lane_contract(
    tmp_path: Path, framework: str
) -> None:
    """`make test` runs `tests/` but the suites: `test_handle.py` calls `handle` in memory, and
    `test_contract.py` is the kit's `WorkloadContract` over A2A on a local Unix socket, whose lane
    cases also compare each A2A stream with the handle called in memory."""
    agent = _render(tmp_path, framework)
    assert (agent / "tests/test_handle.py").is_file()
    contract = (agent / "tests/test_contract.py").read_text()
    assert "(WorkloadContract):" in contract and "class Test" in contract
    assert 'pytest_plugins = ["agent_testkit.plugin"]' in (agent / "tests/conftest.py").read_text()
    plan = _make(agent, tmp_path / "bin", "-n", "test")
    assert plan.returncode == 0, plan.stderr
    pytest_line = next(line for line in plan.stdout.splitlines() if "pytest" in line)
    assert "--ignore=tests/suites" in pytest_line
    assert "--deselect" not in pytest_line and " -k " not in pytest_line


@pytest.mark.parametrize(("body", "code"), [("assert 1 + 1 == 3", 2), ("assert True", 0)])
def test_a_failing_test_fails_make_test(agent: Path, tmp_path: Path, body: str, code: int) -> None:
    """The real `make test` recipe, with `uv run pytest` running pytest here: pytest's exit code
    is make's. A non-zero make fails the `test` step, the job, and the run."""
    probe = agent / "tests" / f"test_probe_{code}.py"
    probe.write_text(f"def test_probe() -> None:\n    {body}\n")
    try:
        _stub(tmp_path / "bin", "uv", UV_STUB)
        done = _make(agent, tmp_path / "bin", "test", f"ARGS={probe.relative_to(agent)}")
    finally:
        probe.unlink()
    assert ("1 failed" if code else "1 passed") in done.stdout, done.stdout + done.stderr
    assert (done.returncode != 0) == bool(code), done.stdout + done.stderr


# --- Box 2: CI signs the workload image ---------------------------------------------------------


def test_the_sign_job_signs_the_pushed_digest_keyless(workflow: dict[str, Any]) -> None:
    image, sign = workflow["jobs"]["image"], workflow["jobs"]["sign"]
    assert {"lint", "test", "suites"} <= set(image["needs"]), "sign only after every suite"
    push = next(s for s in _steps(image) if s.get("id") == "push")
    assert 'echo "ref=${IMAGE%:*}@${digest}"' in push["run"], "the output is a digest, no tag"
    assert "sha256:*)" in push["run"], "no digest in the build metadata fails the push"
    assert image["outputs"]["ref"] == "${{ steps.push.outputs.ref }}"
    assert DEFAULT_BRANCH in sign["if"] and "github.event_name == 'push'" in sign["if"]
    by_name = {s.get("name", s.get("uses", "")): s for s in _steps(sign)}
    signing = by_name["Sign the digest (keyless)"]
    assert signing["env"]["IMAGE_REF"] == "${{ needs.image.outputs.ref }}"
    assert 'make sign IMAGE_REF="$IMAGE_REF"' in signing["run"]
    verify = by_name["Verify the signature against this workflow"]
    assert "cosign verify --certificate-identity" in verify["run"]
    assert "https://token.actions.githubusercontent.com" in verify["run"]
    assert verify["env"]["IDENTITY"] == "${{ github.server_url }}/${{ github.workflow_ref }}"
    installer = next(s for s in _steps(sign) if "cosign-installer@" in s.get("uses", ""))
    assert re.fullmatch(r"v\d+\.\d+\.\d+", installer["with"]["cosign-release"])


def test_permissions_are_least_privilege(workflow: dict[str, Any]) -> None:
    assert workflow["permissions"] == {"contents": "read"}
    jobs = workflow["jobs"]
    assert jobs["sign"]["permissions"] == {
        "contents": "read",
        "packages": "write",
        "id-token": "write",
    }
    assert jobs["image"]["permissions"] == {"contents": "read", "packages": "write"}
    for name in ("lint", "test", "suites"):
        assert "permissions" not in jobs[name], f"{name} widens the workflow's read-only token"


def test_no_key_or_secret_but_the_two_tokens(agent: Path) -> None:
    text = (agent / WORKFLOW).read_text()
    assert set(re.findall(r"secrets\.(\w+)", text)) <= ALLOWED_SECRETS
    assert "--key" not in text and "COSIGN_PRIVATE_KEY" not in text
    raw: dict[Any, Any] = yaml.safe_load(text)
    triggers = raw.get("on") or raw[True]  # PyYAML reads a bare `on` as True
    assert "pull_request_target" not in triggers


def test_every_action_is_pinned_by_full_sha(workflow: dict[str, Any]) -> None:
    uses = [s["uses"] for job in workflow["jobs"].values() for s in _steps(job) if "uses" in s]
    assert any(u.startswith("sigstore/cosign-installer@") for u in uses)
    assert [u for u in uses if not PINNED_ACTION.match(u)] == []
    for job in workflow["jobs"].values():
        for step in _steps(job):
            if step.get("uses", "").startswith("actions/checkout@"):
                assert step["with"]["persist-credentials"] is False


@pytest.mark.parametrize(
    ("ref", "signed"),
    [
        (DIGEST_REF, True),
        ("ghcr.io/acme/my-agent:latest", False),
        ("ghcr.io/acme/my-agent@sha256:", False),
        ("ghcr.io/acme/my-agent@sha256:" + "a" * 63, False),
        ("ghcr.io/acme/my-agent@sha256:" + "A" * 64, False),
        ("", False),
    ],
)
def test_make_sign_signs_a_digest_and_refuses_a_tag(
    agent: Path, tmp_path: Path, ref: str, signed: bool
) -> None:
    """The real `make sign` recipe, with `cosign` stubbed: a tag can move after signing, so only
    `<image>@sha256:<digest>` reaches cosign."""
    calls = tmp_path / "cosign-calls"
    calls.touch()
    _stub(tmp_path / "bin", "cosign", COSIGN_STUB)
    done = _make(agent, tmp_path / "bin", "sign", f"IMAGE_REF={ref}", COSIGN_CALLS=str(calls))
    assert (done.returncode == 0) == signed, done.stdout + done.stderr
    assert calls.read_text() == (f"sign --yes {ref}\n" if signed else "")


def test_actionlint_accepts_the_rendered_workflow(agent: Path) -> None:
    actionlint = shutil.which("actionlint") or str(Path(sys.executable).with_name("actionlint"))
    if not Path(actionlint).is_file():
        pytest.skip("actionlint is not installed (uv sync installs actionlint-py)")
    done = subprocess.run(
        [actionlint, "-no-color", WORKFLOW],
        cwd=agent,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr


@pytest.mark.parametrize("job", ["build-image", "sign"])
def test_gitlab_make_jobs_install_bash(gitlab: dict[str, Any], job: str) -> None:
    """The Makefile runs its recipes with `SHELL := /bin/bash`; the docker:cli image is Alpine,
    which has no bash, so a job that calls `make` there must install it."""
    before = gitlab[job].get("before_script") or gitlab[gitlab[job]["extends"]]["before_script"]
    installs = [line for line in before if line.startswith("apk add")]
    assert any(re.search(r"\bbash\b", line) for line in installs), installs
    assert any("make " in line for line in gitlab[job]["script"]), "the job calls make"
