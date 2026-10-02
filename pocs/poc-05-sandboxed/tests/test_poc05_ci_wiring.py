"""PoC-5 CI wiring, offline (plan `docs/plans/2026-10-02-poc-05-sandboxed.md`, section 7, T24).

Exit criterion 1: the hostile suites run in CI, and the no-cluster parts run offline on every
commit. Here: `.github/workflows/remote-lane.yml` runs by hand until the x86_64 gVisor sum is
pinned, then on push and pull request (the trigger test enforces both states); it brings the kind
cluster up, and runs the remote suite; `ci.yml` runs `make check`, and `make check` collects the
PoC-5 offline tests.

Exit criterion 2: a fake workload goes through the `remote` lane on every commit. Offline that is
the `LaneContract` file in `make check`; on kind it is the `test-remote` verb in the workflow.

Also checked: the workflow pins every action by a full commit SHA, uses no secret, always deletes
the cluster, and the gVisor installer it relies on fails closed for an arch with no pinned sum.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = ROOT / ".github/workflows"
REMOTE_LANE = WORKFLOWS / "remote-lane.yml"
CI = WORKFLOWS / "ci.yml"
MAKEFILE = ROOT / "Makefile"
PYPROJECT = ROOT / "pyproject.toml"
RUN_SH = ROOT / "deploy/kind/poc05/run.sh"
INSTALL_GVISOR = ROOT / "deploy/kind/poc05/install-gvisor.sh"
TESTS = Path(__file__).resolve().parent
PINNED = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")


def _workflow(path: Path) -> dict[str, Any]:
    doc = yaml.safe_load(path.read_text())
    assert isinstance(doc, dict), path
    # YAML 1.1 reads the bare key `on` as the boolean True.
    if True in doc:
        doc["on"] = doc.pop(True)
    return doc


def _steps(doc: dict[str, Any]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for job in doc["jobs"].values():
        steps.extend(job.get("steps", []))
    return steps


def _runs(doc: dict[str, Any]) -> str:
    return "\n".join(str(s.get("run", "")) for s in _steps(doc))


@pytest.fixture(scope="module")
def remote_lane() -> dict[str, Any]:
    assert REMOTE_LANE.is_file(), "missing .github/workflows/remote-lane.yml"
    return _workflow(REMOTE_LANE)


def test_poc05_remote_lane_runs_on_push_once_gvisor_is_pinned(remote_lane: dict[str, Any]) -> None:
    """Manual only while the x86_64 sum is unpinned (a known-red job on every push helps nobody);
    on push and pull request once it is pinned."""
    triggers = remote_lane["on"]
    names = set(triggers) if isinstance(triggers, (dict, list)) else {triggers}
    gvisor = INSTALL_GVISOR.read_text()
    x86_pinned = re.search(r'^GVISOR_SHA512_X86_64="[0-9a-f]{128}"$', gvisor, re.M) is not None
    if x86_pinned:
        assert {"push", "pull_request"} <= names
    else:
        assert names == {"workflow_dispatch"}


def test_poc05_remote_lane_job_shape(remote_lane: dict[str, Any]) -> None:
    jobs = remote_lane["jobs"]
    assert len(jobs) == 1
    job = next(iter(jobs.values()))
    assert job["runs-on"] == "ubuntu-latest"
    assert isinstance(job.get("timeout-minutes"), int) and 0 < job["timeout-minutes"] <= 30
    perms = remote_lane.get("permissions", job.get("permissions"))
    assert perms == {"contents": "read"}


def test_poc05_every_action_pinned_by_sha(remote_lane: dict[str, Any]) -> None:
    uses = [s["uses"] for s in _steps(remote_lane) if "uses" in s]
    assert uses, "expected checkout and setup-uv"
    for ref in uses:
        assert PINNED.match(ref), f"not pinned by a 40-hex SHA: {ref}"
    # Every pin also comes with a version comment on its line.
    for line in REMOTE_LANE.read_text().splitlines():
        if "uses:" in line and not line.lstrip().startswith("#"):
            assert "# v" in line, f"no version comment: {line.strip()}"


def test_poc05_remote_lane_reuses_ci_pins(remote_lane: dict[str, Any]) -> None:
    ci_pins = {s["uses"].split("@")[0]: s["uses"] for s in _steps(_workflow(CI)) if "uses" in s}
    for ref in (s["uses"] for s in _steps(remote_lane) if "uses" in s):
        action = ref.split("@")[0]
        assert action in ci_pins, f"{action} has no SHA in ci.yml; use a run step instead"
        assert ref == ci_pins[action], f"{action} pinned differently from ci.yml"


def test_poc05_remote_lane_brings_up_and_runs_remote_suite(remote_lane: dict[str, Any]) -> None:
    runs = _runs(remote_lane)
    up = runs.find("deploy/kind/poc05/run.sh up")
    test = runs.find("deploy/kind/poc05/run.sh test-remote")
    assert up >= 0 and test > up, "the job must run run.sh up, then run.sh test-remote"


def test_poc05_run_sh_has_test_remote_verb() -> None:
    text = RUN_SH.read_text()
    assert re.search(r"^\s*test-remote\)", text, re.M), "run.sh has no test-remote verb"
    assert "POC05_KIND=1" in text and '-k "remote or code_runner"' in text


def test_poc05_remote_lane_tools_checked_by_checksum(remote_lane: dict[str, Any]) -> None:
    runs = _runs(remote_lane)
    env = next(iter(remote_lane["jobs"].values())).get("env", {})
    assert env.get("KIND_VERSION") == "v0.33.0"
    assert env.get("KUBECTL_VERSION") == "v1.37.0"
    assert "kind-linux-amd64.sha256sum" in runs and "kubectl.sha256" in runs
    assert runs.count("sha256sum -c") >= 2, "kind and kubectl must each be checked against sha256"


def test_poc05_remote_lane_always_deletes(remote_lane: dict[str, Any]) -> None:
    deletes = [s for s in _steps(remote_lane) if "run.sh delete" in str(s.get("run", ""))]
    assert deletes, "no delete step"
    assert any(str(s.get("if", "")).strip() == "always()" for s in deletes)


def test_poc05_remote_lane_logs_on_failure(remote_lane: dict[str, Any]) -> None:
    failing = [s for s in _steps(remote_lane) if str(s.get("if", "")).strip() == "failure()"]
    assert any("--context kind-poc05" in str(s.get("run", "")) for s in failing)


def test_poc05_remote_lane_uses_no_secret() -> None:
    text = REMOTE_LANE.read_text()
    assert "secrets." not in text
    assert "GITHUB_TOKEN" not in text


def test_poc05_ci_runs_make_check() -> None:
    ci = _workflow(CI)
    assert {"push", "pull_request"} <= set(ci["on"])
    assert re.search(r"^\s*make check\s*$", _runs(ci), re.M)


def test_poc05_make_check_collects_poc05_offline_tests() -> None:
    make = MAKEFILE.read_text()
    check = re.search(r"^check:.*\n\t(.+)$", make, re.M)
    assert check and re.search(r"\btest\b", check.group(1)), "make check no longer runs test"
    assert re.search(r'^testpaths = \[.*"pocs".*\]', PYPROJECT.read_text(), re.M)
    offline = {
        "test_poc05_ci_wiring.py",
        "test_poc05_remote_lane_contract.py",
        "test_poc05_hostile_offline.py",
        "test_poc05_netpol_static.py",
        "test_poc05_hardening_static.py",
        "test_poc05_admission_static.py",
        "test_poc05_seed_static.py",
    }
    present = {p.name for p in TESTS.glob("test_poc05_*.py")}
    assert offline <= present, f"missing: {sorted(offline - present)}"


def test_poc05_gvisor_arm64_pinned_and_unpinned_arch_fails_closed() -> None:
    text = INSTALL_GVISOR.read_text()
    assert re.search(r"^GVISOR_SHA512_AARCH64=[0-9a-f]{128}$", text, re.M)
    # x86_64 has no sum in the file; it comes from the environment or stays empty.
    assert re.search(r'^GVISOR_SHA512_X86_64="\$\{GVISOR_SHA512_X86_64:-\}"$', text, re.M)
    # The sum is checked for shape before any download; an empty one dies.
    guard = text.find("[[ $sum =~ ^[0-9a-f]{128}$ ]] ||")
    assert guard >= 0, "no fail-closed check on the pinned sum"
    assert "refusing to install" in text[guard : guard + 300]
    assert guard < text.find("curl -fsSLO")
    assert re.search(r"\*\) die .*unsupported arch", text)


def test_poc05_remote_lane_notes_gvisor_unpinned() -> None:
    head = REMOTE_LANE.read_text().split("\nname:", 1)[0]
    assert "x86_64" in head and "sha512" in head and "fails closed" in head
    assert "make check" in head
