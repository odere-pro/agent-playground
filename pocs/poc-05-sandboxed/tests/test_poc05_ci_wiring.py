"""PoC-5 CI wiring, offline (plan `docs/plans/2026-10-02-poc-05-sandboxed.md`, section 7, T24).

Exit criterion 1: the hostile suites run in CI, and the no-cluster parts run offline on every
commit. The offline half: `ci.yml` runs `make check`, and `make check` collects the PoC-5
offline tests. The kind half: `remote-lane.yml` runs on push and pull request, now that the x86_64
gVisor sum, the kind remote test files, and the kind and kubectl sha256 sums are all pinned (the
trigger test requires every one). Whether gVisor starts on a GitHub runner is shown only by a run.

Exit criterion 2: a fake workload goes through the `remote` lane on every commit. Offline that is
the `LaneContract` file in `make check`. On kind, the `test-remote` verb selects the kind remote
test files by path and fails, naming them, if one is missing.

Also checked: the workflow pins every action by a full commit SHA, uses no secret, is never
triggered by `pull_request_target`, always deletes the cluster, and sends every pod log through
one redaction filter in `run.sh`; the gVisor installer it relies on pins a sum for both arches
and fails closed for any other.
"""

from __future__ import annotations

import re
import shutil
import subprocess
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
REMOTE_TESTS = (
    "pocs/poc-05-sandboxed/tests/test_poc05_kind_remote_lane.py",
    "pocs/poc-05-sandboxed/tests/test_poc05_kind_remote_controls.py",
    "pocs/poc-05-sandboxed/tests/test_poc05_kind_code_runner.py",
    "pocs/poc-05-sandboxed/tests/test_poc05_kind_remote_shm.py",
)
SHA256 = re.compile(r"^[0-9a-f]{64}$")


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


def _triggers(doc: dict[str, Any]) -> set[str]:
    triggers = doc["on"]
    return set(triggers) if isinstance(triggers, (dict, list)) else {triggers}


def _job_env(doc: dict[str, Any]) -> dict[str, Any]:
    env = next(iter(doc["jobs"].values())).get("env", {})
    assert isinstance(env, dict)
    return env


def test_poc05_remote_lane_runs_on_push_with_everything_pinned(
    remote_lane: dict[str, Any],
) -> None:
    """The job runs on push and pull request (and by hand), so all three must hold (security
    review item 3): the x86_64 gVisor sum is pinned, the kind remote test files exist, and
    `KIND_SHA256` and `KUBECTL_SHA256` are pinned in the job env."""
    assert _triggers(remote_lane) == {"push", "pull_request", "workflow_dispatch"}
    gvisor = INSTALL_GVISOR.read_text()
    assert re.search(r"^GVISOR_SHA512_X86_64=[0-9a-f]{128}$", gvisor, re.M), "x86_64 not pinned"
    missing = [f for f in REMOTE_TESTS if not (ROOT / f).is_file()]
    assert not missing, missing
    env = _job_env(remote_lane)
    for key in ("KIND_SHA256", "KUBECTL_SHA256"):
        assert SHA256.match(str(env.get(key) or "")), f"{key} not pinned"


def test_poc05_no_workflow_uses_pull_request_target() -> None:
    """Security review item 3: a fork's code never runs with this repo's token."""
    for path in sorted(WORKFLOWS.glob("*.y*ml")):
        assert "pull_request_target" not in _triggers(_workflow(path)), path.name
        code = [ln for ln in path.read_text().splitlines() if not ln.lstrip().startswith("#")]
        assert not any("pull_request_target" in ln for ln in code), path.name


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


def _remote_selection(text: str) -> list[str]:
    match = re.search(r"(?ms)^REMOTE_TESTS=\((.*?)\)$", text)
    assert match, "run.sh has no REMOTE_TESTS array"
    return [w for w in match.group(1).split() if not w.startswith("#")]


def _function(text: str, name: str) -> str:
    match = re.search(rf"(?ms)^{name}\(\) \{{\n(.*?)^\}}", text)
    assert match, f"run.sh has no {name}()"
    return match.group(1)


def test_poc05_run_sh_test_remote_selects_by_file_path() -> None:
    """Reviewer B1: `-k "remote or code_runner"` matched only admission fixture ids. The verb
    selects the kind remote-lane and code-runner files by path, and no admission file."""
    text = RUN_SH.read_text()
    assert re.search(r"^\s*test-remote\)", text, re.M), "run.sh has no test-remote verb"
    selection = _remote_selection(text)
    assert selection == list(REMOTE_TESTS)
    assert not any("admission" in f for f in selection)
    body = _function(text, "run_tests_remote")
    assert "POC05_KIND=1" in body and '"${REMOTE_TESTS[@]}"' in body
    assert " -k " not in body and "pocs/poc-05-sandboxed/tests " not in body


def test_poc05_run_sh_test_remote_fails_naming_missing_files() -> None:
    """The verb fails and names each missing file instead of running nothing. Runs only while a
    file is missing, so it never reaches pytest or kind."""
    missing = [f for f in REMOTE_TESTS if not (ROOT / f).is_file()]
    if not missing:
        pytest.skip("every kind remote test file exists")
    out = subprocess.run(
        ["bash", str(RUN_SH), "test-remote"], capture_output=True, text=True, timeout=30
    )
    assert out.returncode != 0
    for f in missing:
        assert f in out.stderr, out.stderr


def test_poc05_remote_lane_tools_checked_by_checksum(remote_lane: dict[str, Any]) -> None:
    """Security review item 3: kind and kubectl are checked against sums pinned in the job env,
    not a sum fetched from the same release. The shape check before any download stays, so a
    blanked sum fails closed."""
    runs = _runs(remote_lane)
    env = _job_env(remote_lane)
    assert env.get("KIND_VERSION") == "v0.33.0"
    assert env.get("KUBECTL_VERSION") == "v1.37.0"
    for key in ("KIND_SHA256", "KUBECTL_SHA256"):
        assert key in env, f"{key} missing from the job env"
        assert SHA256.match(str(env[key] or "")), f"{key} is not a pinned sha256"
        guard = runs.find(f"[[ ${{{key}}} =~ ^[0-9a-f]{{64}}$ ]] ||")
        assert guard >= 0, f"no fail-closed check on {key}"
        check = runs.find(f'"${{{key}}}  ', guard)
        assert check > guard and "sha256sum -c" in runs[check : check + 80], key
        assert guard < runs.find("curl", guard), f"{key} is checked after the download"
    assert ".sha256sum" not in runs and "kubectl.sha256" not in runs, "same-release sum fetched"
    head = REMOTE_LANE.read_text().split("\nname:", 1)[0]
    assert "KIND_SHA256" in head and "KUBECTL_SHA256" in head


def test_poc05_remote_lane_always_deletes(remote_lane: dict[str, Any]) -> None:
    deletes = [s for s in _steps(remote_lane) if "run.sh delete" in str(s.get("run", ""))]
    assert deletes, "no delete step"
    assert any(str(s.get("if", "")).strip() == "always()" for s in deletes)


def test_poc05_remote_lane_logs_on_failure(remote_lane: dict[str, Any]) -> None:
    """The failure step prints pod logs through `run.sh logs` (pinned to kind-poc05 by `kctl`)
    and the probe results through `run.sh redact`."""
    failing = [s for s in _steps(remote_lane) if str(s.get("if", "")).strip() == "failure()"]
    runs = "\n".join(str(s.get("run", "")) for s in failing)
    assert "deploy/kind/poc05/run.sh logs" in runs
    assert "deploy/kind/poc05/run.sh redact" in runs
    assert re.search(r"^pod_logs\(\) \{", RUN_SH.read_text(), re.M)
    assert re.search(r"^\s*logs\) pod_logs ;;", RUN_SH.read_text(), re.M)


def _logical_lines(text: str) -> list[str]:
    """Lines with backslash continuations joined; comments dropped."""
    joined = re.sub(r"\\\n\s*", " ", text)
    return [ln for ln in joined.splitlines() if not ln.lstrip().startswith("#")]


@pytest.mark.parametrize("path", [RUN_SH, REMOTE_LANE], ids=["run.sh", "remote-lane.yml"])
def test_poc05_every_kubectl_logs_goes_through_redact_logs(path: Path) -> None:
    """Security review item 2: LiteLLM's 401 log line carries the key suffix and hash. Every
    `kubectl logs` output in run.sh (`explain` included) and in the workflow is piped through the
    one filter, `redact_logs` in run.sh. The workflow has no `kubectl logs` of its own."""
    calls = [
        ln
        for ln in _logical_lines(path.read_text())
        if re.search(r"\b(kubectl|kctl)\b.*\slogs\s", ln)
    ]
    if path == REMOTE_LANE:
        assert calls == [], calls
        return
    assert len(calls) >= 2, "expected the logs calls in explain and pod_logs"
    for ln in calls:
        assert re.search(r"\|\s*redact_logs\b", ln), f"logs not redacted: {ln.strip()}"
    assert "redact_logs" in _function(RUN_SH.read_text(), "explain")


# Made-up values only; none is a real key, token, or hash.
_REDACT_CASES = [
    ("Authorization: Bearer madeuptoken123 sent", "madeuptoken123", "Authorization: Bearer "),
    (
        "Invalid proxy server token passed. Received API Key = sk-...q9z7, Key Hash (Token) = "
        "0f1e2d3c4b5a6978, done",
        "q9z7",
        "Invalid proxy server token passed. Received API Key = ",
    ),
    ("Key Hash (Token) = 0f1e2d3c4b5a6978 end", "0f1e2d3c4b5a6978", "Key Hash (Token) = "),
    ("using key=sk-madeupvalue_42 for route", "madeupvalue_42", "using key="),
]


@pytest.mark.skipif(shutil.which("bash") is None or shutil.which("sed") is None, reason="no bash")
@pytest.mark.parametrize(("line", "value", "kept"), _REDACT_CASES)
def test_poc05_redact_logs_strips_the_value_and_keeps_the_text(
    line: str, value: str, kept: str
) -> None:
    """Runs the real filter (`run.sh redact`, which is `redact_logs`) on a sample line, offline."""
    out = subprocess.run(
        ["bash", str(RUN_SH), "redact"],
        input=line + "\n",
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert out.returncode == 0, out.stderr
    assert value not in out.stdout
    assert kept in out.stdout and "[redacted]" in out.stdout


@pytest.mark.skipif(shutil.which("bash") is None or shutil.which("sed") is None, reason="no bash")
def test_poc05_redact_logs_leaves_a_clean_line_alone() -> None:
    """Control: a line with no credential shape passes unchanged (`task-runner` holds `sk-`)."""
    line = "task-runner ready on 0.0.0.0:8080; disk-pressure false; key hash pending\n"
    out = subprocess.run(
        ["bash", str(RUN_SH), "redact"], input=line, capture_output=True, text=True, timeout=30
    )
    assert out.returncode == 0 and out.stdout == line


def test_poc05_remote_lane_uses_no_secret() -> None:
    text = REMOTE_LANE.read_text()
    assert "secrets." not in text
    assert "GITHUB_TOKEN" not in text


def test_poc05_ci_runs_make_check() -> None:
    """The offline gate runs on every push on every branch and on every pull request, read-only
    (PoC-3 exit criterion 1 needs every commit on every branch)."""
    ci = _workflow(CI)
    triggers = ci["on"]
    assert isinstance(triggers, dict)
    assert set(triggers) == {"push", "pull_request"}
    assert not triggers["push"], "push must not be narrowed by a branch filter (PoC-3 exit 1)"
    assert ci.get("permissions") == {"contents": "read"}
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


def test_poc05_gvisor_both_arches_pinned_and_other_arch_fails_closed() -> None:
    text = INSTALL_GVISOR.read_text()
    # Both sums sit in the file for the one pinned release; neither comes from the environment.
    assert re.search(r"^GVISOR_RELEASE=\d{8}\.\d+$", text, re.M)
    arm = re.search(r"^GVISOR_SHA512_AARCH64=([0-9a-f]{128})$", text, re.M)
    x86 = re.search(r"^GVISOR_SHA512_X86_64=([0-9a-f]{128})$", text, re.M)
    assert arm and x86 and arm.group(1) != x86.group(1)
    assert "${GVISOR_SHA512_" not in text, "a sum may not be overridden from the environment"
    # The sum is checked for shape before any download; an empty one dies.
    guard = text.find("[[ $sum =~ ^[0-9a-f]{128}$ ]] ||")
    assert guard >= 0, "no fail-closed check on the pinned sum"
    assert "refusing to install" in text[guard : guard + 300]
    assert guard < text.find("curl -fsSLO")
    assert re.search(r"\*\) die .*unsupported arch", text)


def test_poc05_remote_lane_notes_gvisor_on_runner_is_open() -> None:
    """The header says gVisor on a GitHub runner is not yet shown, that the job fails rather
    than skips if it cannot start, and that `make check` stays the every-commit gate."""
    head = REMOTE_LANE.read_text().split("\nname:", 1)[0]
    assert "gVisor" in head and "ubuntu-latest" in head and "never skips" in head
    assert "x86_64" in head and "sha512" in head and "fails closed" in head
    assert "make check" in head
