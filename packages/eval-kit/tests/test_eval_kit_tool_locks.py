"""R7-18: the eval tools install from hashed locks, and the evals jobs keep no token or PR cache.

Static: it reads the locks, the launcher's install argv, and the workflows. No install runs here.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from bakeoff.registry import ROOT
from eval_kit.evaltrial import (
    DEEPEVAL_LOCK,
    DEEPEVAL_VERSION,
    PROMPTFOO_DIR,
    PROMPTFOO_VERSION,
    tool_argv,
    tool_install_argv,
)

WORKFLOWS = ROOT / ".github/workflows"
EVAL_JOBS = [("poc07-evals.yml", "evals"), ("poc08-evals.yml", "evals")]
# Every job that installs tools on `pull_request`: no cache and no credential reach a PR run.
PR_JOBS = [
    *EVAL_JOBS,
    ("poc08-evals.yml", "regression"),
    ("poc08-evals.yml", "redteam"),
    ("poc06-load.yml", "poc06-load"),
]
_PIN = re.compile(r"^([A-Za-z0-9_.\-\[\],]+)==(\S+)")


def _requirements() -> dict[str, list[str]]:
    """Each pinned requirement of the DeepEval lock and its `--hash` values."""
    found: dict[str, list[str]] = {}
    name = ""
    for line in (ROOT / DEEPEVAL_LOCK).read_text(encoding="utf-8").splitlines():
        pin = _PIN.match(line)
        if pin:
            name = pin.group(1).lower()
            found[name] = []
        elif line.strip().startswith("--hash=sha256:"):
            found[name].append(line.strip())
    return found


def _lock_packages() -> dict[str, Any]:
    data = json.loads((ROOT / PROMPTFOO_DIR / "package-lock.json").read_text(encoding="utf-8"))
    packages: dict[str, Any] = data["packages"]
    return {path: meta for path, meta in packages.items() if path}


def _job(workflow: str, job: str) -> dict[str, Any]:
    data = yaml.safe_load((WORKFLOWS / workflow).read_text(encoding="utf-8"))
    found: dict[str, Any] = data["jobs"][job]
    return found


def _uses(job: dict[str, Any], action: str) -> dict[str, Any]:
    steps: list[dict[str, Any]] = [
        s for s in job["steps"] if str(s.get("uses", "")).startswith(action)
    ]
    assert len(steps) == 1, action
    return steps[0]


def test_eval_kit_deepeval_lock_pins_and_hashes_every_requirement() -> None:
    """Every requirement in the DeepEval lock is `==` pinned and carries a sha256."""
    reqs = _requirements()

    assert {"deepeval", "httpx", "pytest", "pyyaml"} <= reqs.keys()
    assert [name for name, hashes in reqs.items() if not hashes] == []


def test_eval_kit_deepeval_lock_holds_the_pinned_version() -> None:
    """The lock holds the DeepEval version the launcher names."""
    text = (ROOT / DEEPEVAL_LOCK).read_text(encoding="utf-8")

    assert f"\ndeepeval=={DEEPEVAL_VERSION} \\" in text


def test_eval_kit_deepeval_installs_with_require_hashes_and_no_deps(tmp_path: Path) -> None:
    """DeepEval installs from the lock only: `--require-hashes --no-deps -r <lock>`."""
    install = tool_install_argv("deepeval", tmp_path)[-1]

    assert install[:3] == ["uv", "pip", "install"]
    assert {"--require-hashes", "--no-deps"} <= set(install)
    assert install[install.index("-r") + 1] == str(ROOT / DEEPEVAL_LOCK)
    assert "--with" not in tool_argv("deepeval", tmp_path)


def test_eval_kit_promptfoo_lock_has_an_integrity_hash_for_every_package() -> None:
    """Every package in the promptfoo lock carries an `integrity` hash, and promptfoo is pinned."""
    packages = _lock_packages()
    manifest = json.loads((ROOT / PROMPTFOO_DIR / "package.json").read_text(encoding="utf-8"))

    assert manifest["dependencies"] == {"promptfoo": PROMPTFOO_VERSION}
    assert packages["node_modules/promptfoo"]["version"] == PROMPTFOO_VERSION
    assert [p for p, meta in packages.items() if "integrity" not in meta] == []


def test_eval_kit_promptfoo_installs_with_npm_ci_never_npx(tmp_path: Path) -> None:
    """promptfoo installs with `npm ci` from the committed lock and runs the installed binary."""
    install = tool_install_argv("promptfoo", tmp_path)
    run = tool_argv("promptfoo", tmp_path)

    assert [argv[:2] for argv in install] == [["npm", "ci"]]
    assert "npx" not in run and f"promptfoo@{PROMPTFOO_VERSION}" not in run
    assert run[0].endswith("node_modules/.bin/promptfoo")


@pytest.mark.parametrize(("workflow", "job"), PR_JOBS)
def test_eval_kit_evals_job_checkout_keeps_no_credentials(workflow: str, job: str) -> None:
    """The job's checkout sets `persist-credentials: false`."""
    checkout = _uses(_job(workflow, job), "actions/checkout@")

    assert checkout["with"]["persist-credentials"] is False


@pytest.mark.parametrize(("workflow", "job"), PR_JOBS)
def test_eval_kit_evals_job_has_no_uv_cache_on_pull_request(workflow: str, job: str) -> None:
    """The job's `setup-uv` turns its cache off on `pull_request`."""
    setup = _uses(_job(workflow, job), "astral-sh/setup-uv@")

    assert setup["with"]["enable-cache"] == "${{ github.event_name != 'pull_request' }}"


@pytest.mark.parametrize(("workflow", "job"), EVAL_JOBS)
def test_eval_kit_evals_job_installs_from_the_uv_lock_only(workflow: str, job: str) -> None:
    """The evals job installs Python packages with `uv sync --locked`: no `pip`, no `--with`."""
    runs = " ".join(str(s.get("run", "")) for s in _job(workflow, job)["steps"])

    assert "uv sync --all-packages --locked" in runs
    assert not re.search(r"\bpip install\b|--with\b|\bnpx\b", runs)
