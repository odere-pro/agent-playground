"""PoC-4 container-role drills on the kind cluster `poc04` (plan, section 8b).

Exit criteria covered (docs/planning/poc/004-PoC-4-stateless-scalable.md):

- 9. "A hung workload restarts the pod, and the chosen container roles fail no request under a
  rolling restart." Both drills, for both variants (`native-sidecar`, `prestop`) and both engines
  (`echo-python`, `echo-typescript`), through `deploy/kind/run.sh`.

Every test here is a `kind` drill (poc04_conftest.py marks the whole file `kind` and `network`):
skipped unless `POC04_KIND=1`, and never part of the offline gate. They need the cluster up with
the images loaded first: `deploy/kind/run.sh up native-sidecar` (create, build, load, apply).

Result on 2026-10-01 (notes/2026-10-01-container-roles.md), after the chassis drain fix
(`Connection: close` while draining) and `--drain-delay-s 10` in the native-sidecar variant: the
native sidecar failed no call in 5 runs (3 echo-python, 2 echo-typescript); the preStop variant
failed 0 in 3 echo-python runs but 1 to 3 calls in 3 of 4 echo-typescript runs. Each preStop
failure came on a reused keep-alive connection 23 to 44 ms after that pod's chassis got SIGTERM:
the preStop hook only sleeps, so the chassis is not draining during it and never sends
`Connection: close`; with `--drain-delay-s 0` it then closes idle connections at once. The
native sidecar is the chosen container role (criterion 9), so its rolling test is the gate for
the criterion. The preStop rolling test is `xfail`, not strict: it loses calls only sometimes, so
a strict xfail would turn a lucky clean run into a failure.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
RUN = ROOT / "deploy/kind/run.sh"
VARIANTS = ("native-sidecar", "prestop")
ENGINES = ("python", "typescript")
# suggested: 90 s of load, the rollout starts 10 s in.
ROLLING_S = 90
SUMMARY = re.compile(r"sent (\d+), ok (\d+), failed (\d+)")

pytestmark = pytest.mark.kind


def run(*args: str, timeout_s: float) -> subprocess.CompletedProcess[str]:
    """`run.sh <args>`, output captured; run.sh pins `--context kind-poc04` itself."""
    return subprocess.run(
        [str(RUN), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )


def apply(variant: str, engine: str) -> None:
    done = run("apply", variant, engine, timeout_s=600)
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-2000:]


PRESTOP_LOSES_CALLS = pytest.mark.xfail(
    reason="preStop only sleeps: the chassis is not draining then and sends no Connection: "
    "close, so keep-alive calls race the close at SIGTERM (notes/2026-10-01-container-roles.md)",
    strict=False,
)


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize(
    "variant", [VARIANTS[0], pytest.param(VARIANTS[1], marks=PRESTOP_LOSES_CALLS)]
)
def test_rolling_restart_fails_no_request(variant: str, engine: str) -> None:
    """Criterion 9: a rolling restart under 20 clients with no retry fails no request."""
    apply(variant, engine)
    done = run("drill-rolling", str(ROLLING_S), timeout_s=ROLLING_S + 400)
    output = done.stdout + done.stderr
    match = SUMMARY.search(output)
    assert match is not None, output[-2000:]
    sent, ok, failed = (int(group) for group in match.groups())
    assert sent > 0
    assert failed == 0 and ok == sent, output[-3000:]
    assert done.returncode == 0, output[-2000:]


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("variant", VARIANTS)
def test_hung_workload_restarts_and_pod_is_ready_again(variant: str, engine: str) -> None:
    """Criterion 9: a SIGSTOPped workload makes `/ready` false, restarts, and the pod is Ready.

    run.sh fails the drill when the pod is still Ready after 10 s, the workload has not restarted
    after 60 s, or the pod is not Ready again after 120 s.
    """
    apply(variant, engine)
    done = run("drill-hung", timeout_s=300)
    output = done.stdout + done.stderr
    assert done.returncode == 0, output[-2000:]
    assert "not Ready after" in output
    assert "workload restarted after" in output
    assert "PASS: Ready again after" in output
