"""PoC-6b on kind: one fresh remote pod per run (core review R6-11).

Spec: "given a remote-claude-agent pod, when run 1's Bash writes `/tmp/marker` and starts
`sleep 600 &`, then run 2 sees no marker and no stray process."

Exit criteria 1 and 4. The runs go through the chassis `chassis-claude-agent-remote`, its remote
connector, and the front door `remote-run-dispatch`, which claims a pod from the warm pool
`remote-claude-agent` per run. The fake model drives the Claude engine's Bash tool
(deploy/kind/poc06/platform/fake-model-script.yaml, `isolation [plant]` and `isolation [probe]`).
The probe prints the pod's hostname (its name: the pool names each pod uniquely), whether
`/tmp/marker` exists, and how many `sleep 600` processes it sees in `/proc`.

The paired control: a second Bash step inside run 1 sees both the marker and the process, so a
"no" in run 2 is the fresh pod, not a probe that cannot see. Modelled on PoC-5's
`test_poc05_kind_code_runner.py::test_files_do_not_cross_calls`.

A kind test: marked `POC06_KIND` (`poc_harness.drills`), so also `network`, and skipped unless
`POC06_KIND=1`.
Run: `deploy/kind/poc06/run.sh test`.
"""

from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import pytest
from poc_harness.bakeoff_kind import REMOTES, KindEngine, run_stream
from poc_harness.drills import POC06_KIND
from poc_harness.kind import REMOTE_NS, get_json, kubectl

pytestmark = POC06_KIND

ENGINE: KindEngine = next(e for e in REMOTES if e.pod == "remote-claude-agent")
PLANT = "isolation [plant]"
PROBE = "isolation [probe]"
PROBE_LINE = re.compile(r"PROBE host=(?P<host>\S+) marker=(?P<marker>yes|no) sleepers=(?P<n>\d+)")
DOOR = "system:serviceaccount:poc05-platform:remote-run-dispatch"
GONE_WAIT_S = 90  # suggested: a deleted claim's pod is gone after its grace period (40 s)
POLL_S = 2


@dataclass(frozen=True)
class Probe:
    host: str
    marker: bool
    sleepers: int


def _probes(engine: KindEngine, text: str) -> list[Probe]:
    """Every probe line in the run's Bash results, in order. The run must end `ok`."""
    result = run_stream(engine, text)
    assert result.status == 200, result.head
    calls = [e for e in result.events if e.get("type") == "tool_call" and e.get("name") == "Bash"]
    found = [
        Probe(m["host"], m["marker"] == "yes", int(m["n"]))
        for e in calls
        for m in PROBE_LINE.finditer(json.dumps(e.get("result")))
    ]
    assert found, f"no probe output in the Bash results: {calls}"
    return found


def _claims() -> list[str]:
    items: list[dict[str, Any]] = get_json("sandboxclaims", "-n", REMOTE_NS)["items"]
    return [str(c["metadata"]["name"]) for c in items if c["metadata"]["name"].startswith("run-")]


def _pod_gone(name: str) -> bool:
    deadline = time.monotonic() + GONE_WAIT_S
    while time.monotonic() < deadline:
        got = kubectl("get", "pod", name, "-n", REMOTE_NS, "-o", "json")
        if got.returncode != 0 or "deletionTimestamp" in json.loads(got.stdout)["metadata"]:
            return True
        time.sleep(POLL_S)
    return False


def test_run_two_sees_no_marker_and_no_process_from_run_one() -> None:
    """The spec case. Run 1 plants a file and a background process, and its own second Bash step
    sees both (the control). Run 2 sees neither, in another pod; run 1's claim is gone and its
    pod is deleted."""
    first = _probes(ENGINE, PLANT)
    (inside,) = first
    assert inside.marker and inside.sleepers >= 1, inside

    second = _probes(ENGINE, PROBE)
    (after,) = second
    assert after.host != inside.host, (inside, after)
    assert not after.marker, after
    assert after.sleepers == 0, after

    assert _claims() == [], _claims()
    assert _pod_gone(inside.host), f"run 1's pod {inside.host} is still there"


def test_two_concurrent_runs_land_in_two_pods() -> None:
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: _probes(ENGINE, PROBE), range(2)))
    hosts = {r[0].host for r in results}
    assert len(hosts) == 2, results
    assert all(not r[0].marker for r in results), results


def _can_i(*args: str) -> str:
    return kubectl("auth", "can-i", *args, f"--as={DOOR}").stdout.strip()


@pytest.mark.parametrize("verb", ["create", "get", "delete"])
def test_the_front_door_may_manage_claims_in_poc05_remote(verb: str) -> None:
    assert _can_i(verb, "sandboxclaims.extensions.agents.x-k8s.io", "-n", REMOTE_NS) == "yes"


@pytest.mark.parametrize(
    "args",
    [
        ("get", "secrets", "-n", REMOTE_NS),
        ("create", "pods/exec", "-n", REMOTE_NS),
        ("create", "sandboxes.agents.x-k8s.io", "-n", REMOTE_NS),
        ("create", "sandboxclaims.extensions.agents.x-k8s.io", "-n", "poc05-tools"),
        ("list", "sandboxclaims.extensions.agents.x-k8s.io", "-n", REMOTE_NS),
        ("update", "sandboxclaims.extensions.agents.x-k8s.io", "-n", REMOTE_NS),
    ],
    ids=lambda a: " ".join(a),
)
def test_the_front_door_may_do_nothing_else(args: tuple[str, ...]) -> None:
    """The paired control is the test above: the same identity gets `yes` on its own verbs."""
    assert _can_i(*args) == "no"
