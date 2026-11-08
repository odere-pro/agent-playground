"""The library chart on kind: the bind order holds by refusal (ADR-012 items 4 and 7; 024 CH-3;
contract v4 "Bind order", H14).

With the chassis as the main container and the workload as the native sidecar, the workload
starts first. So H14 no longer holds by start order. It holds by refusal: a workload that takes a
chassis port before the chassis starts makes the chassis exit, and a pod without a running chassis
is never Ready, so the Service sends it nothing. The chassis's startupProbe checks a marker file
the chassis writes only after it has bound, in a volume the workload does not mount, so a
squatter that answers HTTP on the chassis port cannot pass it.

Three releases of `deploy/helm/example-agent`, one replica each, in namespace `h14`. All run the
H14 test workload (`deploy/kind/chart/h14/`): echo-python plus a module that binds one port at
import, before the agent card answers. They differ only in the address held:

- `h14-taken` holds `127.0.0.1:8090`, the proxy port. The pod is never Ready, and the chassis
  logs the taken address.
- `h14-public` holds `0.0.0.0:8080`, the public port, and answers every GET with 200. The pod is
  never Ready and never in the Service's endpoints, although the squatter answers on the pod IP
  (shown in the same test, from inside the pod).
- `h14-free` holds `127.0.0.1:8095`, a free loopback port: the paired control. The pod goes Ready.

Item 7 rides on the control: the API server keeps the workload's probe-level
`terminationGracePeriodSeconds: 10` on a restartable init container (read back from the stored
pod spec, not from the render).

A kind test: marked `network`, skipped unless `KIND_DRILLS=1` (`poc_harness.plugin`). It needs
the `agent-chart` cluster with the images loaded: `deploy/kind/chart/run.sh up`, then
`KIND_DRILLS=1 uv run pytest -m network <this file>`. The chart is this checkout's
`deploy/helm`; `H14_CHART_DIR` points at another `deploy/helm` (a worktree) instead. Every
kubectl and helm call pins `--context kind-agent-chart`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.kind(context="kind-agent-chart", hint="deploy/kind/chart/run.sh up")

ROOT = Path(__file__).resolve().parents[3]
HELM_DIR = Path(os.environ.get("H14_CHART_DIR") or ROOT / "deploy" / "helm")
CONTEXT = "kind-agent-chart"
NS = "h14"
H14_IMAGE = "kind.local/agent-platform/h14-squat:poc09"
PROXY_PORT = 8090
PUBLIC_PORT = 8080
# suggested: a loopback port nothing in the pod binds (the chassis takes 8090; the workload 9000).
FREE_PORT = 8095
READY_WITHIN_S = 180.0
# suggested: a failing pod is watched this long after its chassis first fails; long enough for
# the kubelet to restart the chassis at least once (CrashLoopBackOff starts at 10 s).
NOT_READY_FOR_S = 60.0
PROBE_GRACE_S = 10


@dataclass(frozen=True)
class Squat:
    """One release: the address its workload holds, and whether it answers HTTP there."""

    release: str
    host: str
    port: int
    answer: bool = False


TAKEN = Squat("h14-taken", "127.0.0.1", PROXY_PORT)
PUBLIC = Squat("h14-public", "0.0.0.0", PUBLIC_PORT, answer=True)
FREE = Squat("h14-free", "127.0.0.1", FREE_PORT)
SQUATS = (TAKEN, PUBLIC, FREE)
TAKEN_LOG = f"could not bind 127.0.0.1:{PROXY_PORT}"
PUBLIC_LOG = "the public listener failed to start"
CMD_TIMEOUT_S = 120


def _run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    done = subprocess.run(list(args), capture_output=True, text=True, timeout=CMD_TIMEOUT_S)
    if check and done.returncode != 0:
        pytest.fail(
            f"{' '.join(args[:4])}: exit {done.returncode}: {done.stderr.strip()}", pytrace=False
        )
    return done


def _kubectl(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return _run("kubectl", "--context", CONTEXT, "-n", NS, *args, check=check)


def _helm(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return _run("helm", "--kube-context", CONTEXT, "-n", NS, *args, check=check)


def _values(squat: Squat) -> dict[str, Any]:
    """The kind values plus the H14 workload. The handle is in the image's entrypoint (the chart
    refuses `--handle` in workload.args); the address held is the only difference."""
    env = [
        {"name": "H14_BIND_HOST", "value": squat.host},
        {"name": "H14_BIND_PORT", "value": str(squat.port)},
        {"name": "H14_ANSWER", "value": "1" if squat.answer else "0"},
    ]
    return {
        "replicas": 1,
        "hpa": {"enabled": False},
        "workload": {"image": H14_IMAGE, "pullPolicy": "Never", "env": env},
    }


@pytest.fixture(scope="module")
def releases(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    if shutil.which("helm") is None:
        pytest.fail("KIND_DRILLS=1 but helm is not on PATH", pytrace=False)
    work = tmp_path_factory.mktemp("h14-helm")
    for chart in ("agent-lib", "example-agent"):
        shutil.copytree(HELM_DIR / chart, work / chart, ignore=shutil.ignore_patterns("charts"))
    chart_dir = work / "example-agent"
    _run("helm", "dependency", "update", "--skip-refresh", str(chart_dir))
    # A previous run deletes the namespace without waiting; a create into it would be refused.
    _run("kubectl", "--context", CONTEXT, "wait", "--for=delete", f"namespace/{NS}", check=False)
    _run("kubectl", "--context", CONTEXT, "create", "namespace", NS, check=False)
    for squat in SQUATS:
        extra = work / f"{squat.release}.yaml"
        extra.write_text(yaml.safe_dump(_values(squat)))
        _helm("uninstall", squat.release, "--wait", check=False)
        _helm(
            "install",
            squat.release,
            str(chart_dir),
            "-f",
            str(chart_dir / "values-kind.yaml"),
            "-f",
            str(extra),
        )
    yield
    for squat in SQUATS:
        _helm("uninstall", squat.release, "--wait", check=False)
    _run("kubectl", "--context", CONTEXT, "delete", "namespace", NS, "--wait=false", check=False)


def _pod(release: str) -> dict[str, Any]:
    found = _kubectl("get", "pod", "-l", f"app.kubernetes.io/instance={release}", "-o", "json")
    listed: list[dict[str, Any]] = json.loads(found.stdout)["items"]
    items = [p for p in listed if not p["metadata"].get("deletionTimestamp")]
    assert len(items) == 1, f"{release}: expected one pod, got {len(items)}"
    return items[0]


def _ready(pod: dict[str, Any]) -> bool:
    conditions = pod.get("status", {}).get("conditions", [])
    return any(c["type"] == "Ready" and c["status"] == "True" for c in conditions)


def _status(pod: dict[str, Any], name: str) -> dict[str, Any]:
    statuses = [
        *pod.get("status", {}).get("initContainerStatuses", []),
        *pod.get("status", {}).get("containerStatuses", []),
    ]
    return next((s for s in statuses if s["name"] == name), {})


def _wait_ready(release: str) -> dict[str, Any]:
    deadline = time.monotonic() + READY_WITHIN_S
    while time.monotonic() < deadline:
        pod = _pod(release)
        if _ready(pod):
            return pod
        time.sleep(2)
    pytest.fail(f"{release}: not Ready within {READY_WITHIN_S:.0f} s", pytrace=False)


def _chassis_failed(pod: dict[str, Any]) -> bool:
    chassis = _status(pod, "chassis")
    exited = chassis.get("lastState", {}).get("terminated") or chassis.get("state", {}).get(
        "terminated"
    )
    return bool(exited) and exited.get("exitCode", 0) != 0


def _chassis_logs(pod_name: str) -> str:
    current = _kubectl("logs", pod_name, "-c", "chassis", check=False).stdout
    previous = _kubectl("logs", pod_name, "-c", "chassis", "--previous", check=False).stdout
    return current + previous


def _ready_endpoints(release: str) -> list[str]:
    """The ready addresses in the release Service's EndpointSlices."""
    found = _kubectl(
        "get", "endpointslices", "-l", f"kubernetes.io/service-name={release}", "-o", "json"
    )
    slices: list[dict[str, Any]] = json.loads(found.stdout)["items"]
    return [
        address
        for piece in slices
        for endpoint in piece.get("endpoints") or []
        if endpoint.get("conditions", {}).get("ready")
        for address in endpoint.get("addresses", [])
    ]


def _fails_closed(release: str) -> dict[str, Any]:
    """Wait for the chassis to exit non-zero, then watch: never Ready, never a ready endpoint, and
    the chassis restarted. Returns the last pod read."""
    _wait_ready(FREE.release)  # the cluster runs this chart; a failure below is the port
    deadline = time.monotonic() + READY_WITHIN_S
    pod = _pod(release)
    while not _chassis_failed(pod):
        assert not _ready(pod), f"{release}: Ready before the chassis failed"
        assert time.monotonic() < deadline, f"{release}: the chassis never failed"
        time.sleep(2)
        pod = _pod(release)
    watch_until = time.monotonic() + NOT_READY_FOR_S
    while time.monotonic() < watch_until:
        pod = _pod(release)
        assert not _ready(pod), f"{release}: went Ready with a chassis port taken"
        assert _ready_endpoints(release) == [], f"{release}: a ready endpoint in its Service"
        time.sleep(2)
    assert _status(pod, "chassis").get("restartCount", 0) >= 1
    assert _status(pod, "workload").get("ready") is True  # the squatter itself was up
    return pod


@pytest.mark.usefixtures("releases")
def test_h14_free_loopback_port_goes_ready() -> None:
    """The control: the same workload on a free loopback port. The chassis starts after the
    workload, binds its ports, and the pod goes Ready, with its address in the Service."""
    pod = _wait_ready(FREE.release)
    assert _status(pod, "chassis").get("restartCount", 0) == 0
    assert _status(pod, "workload").get("ready") is True
    assert _ready_endpoints(FREE.release) == [pod["status"]["podIP"]]


@pytest.mark.usefixtures("releases")
def test_h14_taken_proxy_port_fails_the_pod_closed() -> None:
    """A workload that holds 127.0.0.1:8090 before the chassis starts: the chassis exits non-zero
    and names the taken address, and the pod is never Ready while it is watched."""
    pod = _fails_closed(TAKEN.release)
    assert TAKEN_LOG in _chassis_logs(pod["metadata"]["name"])


@pytest.mark.usefixtures("releases")
def test_h14_taken_public_port_that_answers_200_fails_the_pod_closed() -> None:
    """A workload that holds 0.0.0.0:8080 and answers every GET with 200: the chassis cannot bind
    its public port and exits, and the pod is never Ready, although an HTTP probe on the pod IP
    would have passed (the control, read from inside the pod in the same test)."""
    pod = _fails_closed(PUBLIC.release)
    url = f"http://{pod['status']['podIP']}:{PUBLIC_PORT}/ready"
    check = f"import urllib.request; print(urllib.request.urlopen({url!r}, timeout=3).status)"
    answered = _kubectl(
        "exec", pod["metadata"]["name"], "-c", "workload", "--", "python", "-c", check
    )
    assert answered.stdout.strip() == "200"
    logs = _chassis_logs(pod["metadata"]["name"])
    assert f"{PUBLIC_PORT}): [errno 98] address already in use" in logs
    assert PUBLIC_LOG in logs


@pytest.mark.usefixtures("releases")
def test_api_server_keeps_probe_grace_on_the_restartable_init_container() -> None:
    """ADR-012 item 7: the stored pod spec has the workload as a restartable init container with
    its liveness probe's own `terminationGracePeriodSeconds: 10`."""
    pod = _wait_ready(FREE.release)
    workload = [c for c in pod["spec"].get("initContainers", []) if c["name"] == "workload"]
    assert len(workload) == 1, "the workload is not an init container in the stored spec"
    assert workload[0].get("restartPolicy") == "Always"
    assert workload[0]["livenessProbe"].get("terminationGracePeriodSeconds") == PROBE_GRACE_S
    assert [c["name"] for c in pod["spec"]["containers"]] == ["chassis"]
