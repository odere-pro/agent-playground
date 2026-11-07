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
"no" in run 2 is the fresh pod, not a probe that cannot see. Pods are compared by `metadata.uid`,
read as the cluster admin while each run is open (`PodUids`), not only by hostname: a name could
in principle come back, a UID cannot.

The concurrent case (session P-A): while run B holds `sleep 601`, run A's Bash scans every
`/proc/<pid>/cmdline` and `/proc/<pid>/environ` it can read and finds no process of run B and at
most one distinct `x-chassis-run-secret` value (its own, or none). It prints counts only, never a
value. Under gVisor, /proc lists only the processes of the pod's own sandbox, so this is the
expected shape; on runc the same scan would list only the pod's own PID namespace too. Run B's
second step runs the same scan in its own pod as the control: it sees its own `sleep 601`.
Modelled on PoC-5's `test_poc05_kind_code_runner.py::test_files_do_not_cross_calls`.

A kind test: marked `POC06_KIND` (`poc_harness.drills`), so also `network`, and skipped unless
`POC06_KIND=1`.
Run: `deploy/kind/poc06/run.sh test`.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
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
HOLD = "isolation [hold]"
SCAN = "isolation [scan]"
SCAN_LINE = re.compile(r"SCAN host=(?P<host>\S+) holders=(?P<holders>\d+) secrets=(?P<secrets>\d+)")
HOLD_OPEN_S = 4  # suggested: run B's claim exists and its `sleep 601` has started
UID_POLL_S = 1
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


@dataclass(frozen=True)
class Scan:
    host: str
    holders: int
    secrets: int


def _scans(engine: KindEngine, text: str) -> list[Scan]:
    """Every scan line in the run's Bash results, in order. The run must end `ok`."""
    result = run_stream(engine, text)
    assert result.status == 200, result.head
    calls = [e for e in result.events if e.get("type") == "tool_call" and e.get("name") == "Bash"]
    found = [
        Scan(m["host"], int(m["holders"]), int(m["secrets"]))
        for e in calls
        for m in SCAN_LINE.finditer(json.dumps(e.get("result")))
    ]
    assert found, f"no scan output in the Bash results: {calls}"
    return found


class PodUids:
    """The pool's pods, name to `metadata.uid`, read as the cluster admin every second while it
    runs. A claimed pod is in the pool's label set from before its claim until it is gone, so a
    run's pod is seen while the run is open."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.uids: dict[str, str] = {}
        self._stop = threading.Event()

    def poll(self) -> None:
        items = get_json("pods", "-n", REMOTE_NS, "-l", f"app.kubernetes.io/name={self.label}")
        for p in items["items"]:
            self.uids.setdefault(str(p["metadata"]["name"]), str(p["metadata"]["uid"]))

    def run(self) -> None:
        while not self._stop.is_set():
            self.poll()
            self._stop.wait(UID_POLL_S)

    def uid(self, host: str) -> str:
        assert host in self.uids, f"pod {host} was never seen in {sorted(self.uids)}"
        return self.uids[host]


@contextmanager
def pod_uids(label: str) -> Iterator[PodUids]:
    seen = PodUids(label)
    seen.poll()
    thread = threading.Thread(target=seen.run, daemon=True)
    thread.start()
    try:
        yield seen
    finally:
        seen._stop.set()
        thread.join(timeout=10)


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
    with pod_uids(ENGINE.pod) as seen:
        first = _probes(ENGINE, PLANT)
        (inside,) = first
        assert inside.marker and inside.sleepers >= 1, inside

        second = _probes(ENGINE, PROBE)
        (after,) = second
    assert after.host != inside.host, (inside, after)
    assert seen.uid(after.host) != seen.uid(inside.host), (inside, after)
    assert not after.marker, after
    assert after.sleepers == 0, after

    assert _claims() == [], _claims()
    assert _pod_gone(inside.host), f"run 1's pod {inside.host} is still there"


def test_two_concurrent_runs_land_in_two_pods() -> None:
    with pod_uids(ENGINE.pod) as seen, ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: _probes(ENGINE, PROBE), range(2)))
    hosts = {r[0].host for r in results}
    assert len(hosts) == 2, results
    assert len({seen.uid(h) for h in hosts}) == 2, results
    assert all(not r[0].marker for r in results), results


def test_a_run_sees_no_process_and_no_run_secret_of_a_concurrent_run() -> None:
    """Session P-A: the per-run secret reaches the Claude CLI through its env. Run B holds
    `sleep 601` and stays open; run A, meanwhile, scans /proc and finds no `sleep 601` and at most
    one distinct run secret (its own). The control: run B's own scan, in its pod, sees its
    `sleep 601`, so A's zero is not a scan that cannot see."""
    with pod_uids(ENGINE.pod) as seen, ThreadPoolExecutor(max_workers=1) as pool:
        held = pool.submit(_scans, ENGINE, HOLD)
        deadline = time.monotonic() + GONE_WAIT_S
        while not _claims() and time.monotonic() < deadline:
            time.sleep(POLL_S)
        assert _claims(), "run B made no claim"
        time.sleep(HOLD_OPEN_S)
        (a_scan,) = _scans(ENGINE, SCAN)
        assert not held.done(), "run B ended before run A scanned; raise its hold"
        (b_scan,) = held.result()
    assert b_scan.holders >= 1, b_scan
    # The positive control for the secret scan: run B finds its own `x-chassis-run-secret`
    # (contract v8), so a zero in run A is not a grep that never matches or an environ that
    # cannot be read.
    assert b_scan.secrets == 1, b_scan
    assert a_scan.host != b_scan.host, (a_scan, b_scan)
    assert seen.uid(a_scan.host) != seen.uid(b_scan.host), (a_scan, b_scan)
    assert a_scan.holders == 0, a_scan
    assert a_scan.secrets <= 1, a_scan


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
