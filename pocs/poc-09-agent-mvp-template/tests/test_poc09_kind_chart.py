"""Backlog 024 CH-3 on kind: the library chart's four live boxes, on the PoC-5 cluster `poc05`.

Two releases of `deploy/helm/example-agent` (`chart-a`, `chart-b`: two services that include the
library chart) run in `poc05-chart`, one replica each, with the kind values and a stand-in
credential on the chassis (`kind_chart.drill_values`). `poc05` runs kindnet, which enforces
NetworkPolicy (`run.sh smoke`, "netpol denied"). One test per box:

- The workload port: only the chassis port is in the Service and its endpoints. From a pod with
  no egress policy (`unpoliced_caller`), the chassis port answers (the control) and the workload
  port is dropped by the policy (a timeout, not a refusal). Inside the pod the workload answers
  on loopback, so the port is live.
- Secrets: the stored pod spec gives the Secret to the chassis container only. Inside the
  workload container there is no such env name, no mount, and no other process to read; the
  chassis container holds both (the paired control). Names and paths only, never a value.
- The liveness restart: the workload process is stopped (SIGSTOP from the node, the only place a
  container's init process takes it). Its exec probe times out, the kubelet kills it with the
  probe's own grace and starts it again, and the pod is Ready again. The chassis keeps running.
- The tag rollout: the library's chassis tag is bumped in a copy of the charts (`poc09` to
  `poc10`, as `test_poc09_chart_tag.py` renders it) and both releases are upgraded with their
  values unchanged. Each gets a new pod whose chassis runs the new image id; its workload runs
  the same image id as before, so no service image was built. The releases go back after.

A kind test: marked `network`, skipped unless `KIND_DRILLS=1` (`poc_harness.drills`). It needs
`deploy/kind/poc05/run.sh up` (images, the namespace, the Secret). Run:
`deploy/kind/poc05/run.sh test-chart`. Every kubectl and helm call pins `--context kind-poc05`.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from chart_render import Charts, bump_library, containers, make_charts, secret_names, values_file
from kind_chart import (
    CHASSIS_PORT,
    CHASSIS_REPOSITORY,
    CONTEXT,
    CURRENT_TAG,
    DRILL_IMAGES,
    NAMESPACE,
    NEXT_TAG,
    RELEASES,
    SECRET,
    SECRET_ENV,
    SECRET_MOUNT,
    WORKLOAD_IMAGE,
    WORKLOAD_PORT,
    drill_values,
)
from poc_harness.drills import KIND_SANDBOX
from poc_harness.kind import (
    POLICY_DROPPED,
    get_json,
    in_caller,
    kubectl,
    node_sh,
    probe,
    tcp_check,
    unpoliced_caller,
)
from poc_harness.kind_controls import SA_DIR

pytestmark = KIND_SANDBOX

CHASSIS = "chassis"
WORKLOAD = "workload"
HELM_WAIT_S = 300
# The helm process gets a minute more than its own wait, so helm reports a slow rollout itself.
HELM_PROCESS_S = HELM_WAIT_S + 60
# suggested: how long a pod may take to be Ready again after a restart or a rollout, on a Mac
# whose host is busy with other work.
READY_WITHIN_S = 240.0
POLL_S = 2.0
# The workload's liveness probe: every 10 s, three failures, then the probe's own grace of 10 s
# (agent-lib.workloadContainer). The pod's grace is 50 s.
PROBE_GRACE_S = 10
POD_GRACE_S = 50
# suggested: slack on the kill window for event and status timestamps (second resolution) and a
# busy kubelet; still well under the pod's grace.
KILL_SLACK_S = 10
CARD = "/.well-known/agent-card.json"


def _helm(charts: Charts, *args: str) -> None:
    charts.helm("--kube-context", CONTEXT, "-n", NAMESPACE, *args, timeout=HELM_PROCESS_S)


def _install(charts: Charts, values: Path) -> None:
    """Both releases from `charts`, with the same values; `upgrade --install` waits for Ready."""
    for release in RELEASES:
        _helm(
            charts, "upgrade", "--install", release, str(charts.example),
            "-f", str(charts.example / "values-kind.yaml"), "-f", str(values),
            "--wait", "--timeout", f"{HELM_WAIT_S}s",
        )  # fmt: skip


@pytest.fixture(scope="module")
def drill(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[Charts, Path]]:
    """The checkout's charts installed as both releases; uninstalled after."""
    held = node_sh("crictl images -o json")
    tags = {t for image in json.loads(held)["images"] for t in image.get("repoTags") or []}
    missing = [i for i in DRILL_IMAGES if i not in tags]
    assert not missing, f"not on the node: {missing}; run deploy/kind/poc05/run.sh build load"
    found = kubectl("get", "secret", SECRET, "-n", NAMESPACE, "-o", "name")
    assert found.returncode == 0, f"no {NAMESPACE}/{SECRET}; run deploy/kind/poc05/run.sh seed"
    charts = make_charts(tmp_path_factory.mktemp("chart-before"))
    values = values_file(tmp_path_factory.mktemp("chart-values"), drill_values())
    _install(charts, values)
    try:
        yield charts, values
    finally:
        for release in RELEASES:
            charts.helm(
                "--kube-context", CONTEXT, "-n", NAMESPACE, "uninstall", release, "--wait",
                check=False, timeout=HELM_PROCESS_S,
            )  # fmt: skip


def _pod(release: str) -> dict[str, Any]:
    """The release's one live pod: Running and not being deleted."""
    items = get_json("pods", "-n", NAMESPACE, "-l", f"app.kubernetes.io/instance={release}")
    live = [
        p
        for p in items["items"]
        if p["status"].get("phase") == "Running" and "deletionTimestamp" not in p["metadata"]
    ]
    assert len(live) == 1, f"{release}: want one live pod, got {len(live)}"
    found: dict[str, Any] = live[0]
    return found


def _ready(pod: dict[str, Any]) -> bool:
    conditions = pod["status"].get("conditions", [])
    return any(c["type"] == "Ready" and c["status"] == "True" for c in conditions)


def _status(pod: dict[str, Any], name: str) -> dict[str, Any]:
    every = [*pod["status"].get("initContainerStatuses", []), *pod["status"]["containerStatuses"]]
    (found,) = [s for s in every if s["name"] == name]
    status: dict[str, Any] = found
    return status


def _image_ids(pod: dict[str, Any]) -> dict[str, tuple[str, str]]:
    """Per container: the image the spec names and the image id the node runs."""
    return {
        name: (container["image"], _status(pod, name)["imageID"])
        for name, container in containers(pod["spec"]).items()
    }


def _when(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


@pytest.mark.usefixtures("drill")
def test_the_workload_port_cannot_be_reached_from_another_pod() -> None:
    """024 CH-3: "The workload port cannot be reached from another pod, on a CNI that enforces
    NetworkPolicy. Only the chassis port is in the Service."
    """
    release = RELEASES[0]
    service = get_json("service", release, "-n", NAMESPACE)
    assert [(p["port"], p["targetPort"]) for p in service["spec"]["ports"]] == [
        (CHASSIS_PORT, CHASSIS_PORT)
    ]
    slices = get_json(
        "endpointslices", "-n", NAMESPACE, "-l", f"kubernetes.io/service-name={release}"
    )["items"]
    assert {p["port"] for s in slices for p in s.get("ports") or []} == {CHASSIS_PORT}

    pod = _pod(release)
    ip = pod["status"]["podIP"]
    name = pod["metadata"]["name"]
    # Inside the pod the workload answers on loopback: the port is live.
    (inside,) = probe(
        NAMESPACE,
        name,
        WORKLOAD,
        [{"kind": "http", "url": f"http://127.0.0.1:{WORKLOAD_PORT}{CARD}"}],
    )
    assert inside.get("status") == 200, inside
    with unpoliced_caller() as caller:
        chassis, workload = in_caller(
            caller, [tcp_check(ip, CHASSIS_PORT), tcp_check(ip, WORKLOAD_PORT)]
        )
    assert chassis.get("connected") is True, chassis  # the control: the pod answers this caller
    assert workload == {"error": POLICY_DROPPED}, workload


@pytest.mark.usefixtures("drill")
def test_secrets_are_mounted_only_into_the_chassis_checked_from_inside_the_workload() -> None:
    """024 CH-3: "Secrets are mounted only into the chassis container, checked from inside the
    workload container."
    """
    pod = _pod(RELEASES[0])
    named = containers(pod["spec"])
    assert secret_names(named[CHASSIS], pod["spec"]) == {SECRET}
    assert secret_names(named[WORKLOAD], pod["spec"]) == set()
    assert not pod["spec"].get("shareProcessNamespace"), "the workload could see the chassis"

    checks: list[dict[str, Any]] = [
        {"kind": "env_names"},
        {"kind": "path", "path": SECRET_MOUNT},
        {"kind": "read", "path": "/proc/self/mountinfo", "size": 65536},
        {"kind": "procs", "names": [SECRET_ENV]},
        {"kind": "path", "path": SA_DIR},
    ]
    name = pod["metadata"]["name"]
    env, mount, mountinfo, procs, token = probe(NAMESPACE, name, WORKLOAD, checks)
    assert SECRET_ENV not in env["names"]
    assert mount == {"exists": False}
    assert SECRET_MOUNT not in mountinfo["text"] and "secret-drill" not in mountinfo["text"]
    assert not [p for p in procs["procs"] if p["secret_names"]], procs
    assert not [p for p in procs["procs"] if CHASSIS in " ".join(p["argv"])], procs
    assert token == {"exists": False}
    # The paired control: the same checks find the Secret in the chassis container.
    env, mount, mountinfo, procs, _ = probe(NAMESPACE, name, CHASSIS, checks)
    assert SECRET_ENV in env["names"]
    assert mount == {"exists": True}
    assert SECRET_MOUNT in mountinfo["text"]
    assert [p for p in procs["procs"] if p["secret_names"] == [SECRET_ENV]], procs


def _stop_workload(pod: dict[str, Any]) -> None:
    """SIGSTOP to the workload's process, from the node: a container's init process ignores a
    stop signal sent from inside its own PID namespace."""
    name = pod["metadata"]["name"]
    node_sh(
        f"sandbox=$(crictl pods --namespace {NAMESPACE} --name '^{name}$' -q) && "
        f"[ $(printf '%s\\n' \"$sandbox\" | grep -c .) = 1 ] && "
        f"cid=$(crictl ps --pod \"$sandbox\" --name '^{WORKLOAD}$' -q) && "
        "[ $(printf '%s\\n' \"$cid\" | grep -c .) = 1 ] && "
        "pid=$(crictl inspect --output go-template --template '{{.info.pid}}' \"$cid\") && "
        'kill -STOP "$pid"'
    )


def _restarted(release: str, uid: str, before: int) -> dict[str, Any]:
    """The release's pod once its workload has restarted, the kubelet reports how the stopped
    process ended (`lastState.terminated`), and the pod is Ready again."""
    deadline = time.monotonic() + READY_WITHIN_S
    status: dict[str, Any] = {}
    while time.monotonic() < deadline:
        pod = _pod(release)
        assert pod["metadata"]["uid"] == uid, "the pod was replaced, not restarted in place"
        status = _status(pod, WORKLOAD)
        restarted = status["restartCount"] > before and "terminated" in status.get("lastState", {})
        if restarted and _ready(pod):
            return pod
        time.sleep(POLL_S)
    pytest.fail(f"{release}: no restart and Ready within {READY_WITHIN_S} s; workload: {status}")


@pytest.mark.usefixtures("drill")
def test_a_hung_workload_is_restarted_by_its_liveness_probe() -> None:
    """024 CH-3: "A workload that stops answering its liveness probe gets the pod restarted, on
    this chart on kind."
    """
    release = RELEASES[1]
    pod = _pod(release)
    assert _ready(pod)
    uid, name = pod["metadata"]["uid"], pod["metadata"]["name"]
    workload_before = _status(pod, WORKLOAD)["restartCount"]
    chassis_before = _status(pod, CHASSIS)["restartCount"]

    start = time.monotonic()
    _stop_workload(pod)
    after = _restarted(release, uid, workload_before)
    took = time.monotonic() - start
    print(f"\n{release}: stopped to restarted and Ready in {took:.0f} s")

    workload = _status(after, WORKLOAD)
    terminated = workload["lastState"]["terminated"]
    # Stopped, it cannot act on SIGTERM: the kubelet's SIGKILL after the grace ends it.
    assert terminated["exitCode"] == 137, terminated
    events = get_json("events", "-n", NAMESPACE, "--field-selector", f"involvedObject.uid={uid}")[
        "items"
    ]
    kills = [
        e
        for e in events
        if e["reason"] == "Killing" and "failed liveness probe" in e.get("message", "")
    ]
    assert kills, [e.get("message") for e in events]
    killed_at = _when(kills[-1].get("lastTimestamp") or kills[-1]["eventTime"])
    window = (_when(terminated["finishedAt"]) - killed_at).total_seconds()
    print(f"{release}: kill to exit {window:.0f} s (probe grace {PROBE_GRACE_S} s)")
    # The probe's own grace, not the pod's: a hung workload is gone in about 10 s, not 50.
    assert PROBE_GRACE_S - KILL_SLACK_S <= window <= PROBE_GRACE_S + KILL_SLACK_S < POD_GRACE_S
    # The workload restarted in place; the chassis kept running.
    assert _status(after, CHASSIS)["restartCount"] == chassis_before
    assert after["metadata"]["name"] == name


Pods = tuple[dict[str, Any], dict[str, Any]]


@pytest.fixture(scope="module")
def rollout(
    drill: tuple[Charts, Path], tmp_path_factory: pytest.TempPathFactory
) -> dict[str, Pods]:
    """Per release, its pod before and after a chassis release in the library chart alone: the
    tag bumped in a copy of the charts, both releases upgraded with their values unchanged. Last
    in the file: it changes the running pods, and the drill fixture removes the releases after."""
    _, values = drill
    before = {release: _pod(release) for release in RELEASES}
    after_charts = make_charts(tmp_path_factory.mktemp("chart-after"))
    bump_library(after_charts, NEXT_TAG)
    _install(after_charts, values)
    return {release: (old, _pod(release)) for release, old in before.items()}


@pytest.mark.parametrize("release", RELEASES)
def test_a_library_tag_bump_rolls_the_new_chassis_out_to_every_service(
    rollout: dict[str, Pods], release: str
) -> None:
    """024 CH-3: "Changing the chassis tag in the library chart rolls the new chassis out to every
    service that includes it, with no service image rebuilt."
    """
    old, new = rollout[release]
    old_ids, new_ids = _image_ids(old), _image_ids(new)
    env = {e["name"]: e.get("value") for e in containers(new["spec"])[CHASSIS]["env"]}
    print(f"\n{release}: chassis {old_ids[CHASSIS][1][-8:]} -> {new_ids[CHASSIS][1][-8:]}")

    assert old_ids[CHASSIS][0] == f"{CHASSIS_REPOSITORY}:{CURRENT_TAG}"
    assert new_ids[CHASSIS][0] == f"{CHASSIS_REPOSITORY}:{NEXT_TAG}"
    assert new_ids[CHASSIS][1] != old_ids[CHASSIS][1], "the node runs the same chassis image"
    assert env["CHASSIS_IMAGE_TAG"] == NEXT_TAG
    assert new["metadata"]["uid"] != old["metadata"]["uid"], "the pod did not roll"
    assert _ready(new)
    # The service's own image: the same name and the same image id, so nothing was built.
    assert old_ids[WORKLOAD][0] == WORKLOAD_IMAGE
    assert new_ids[WORKLOAD] == old_ids[WORKLOAD]
