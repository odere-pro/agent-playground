"""Helpers for the kind tests that check controls from inside a pod.

Every check runs with `kubectl exec` of the image's own interpreter (T10 is dropped: no probe
workload, no attack tool). `exec_probe` runs a check list with any interpreter; `probe` runs
`poc_harness.pod_probe.PROBE` with the image's Python. PoC-6b's `probe_node` and `probe_python`
in `poc06b_kind.py` copy this exec, parse, and assert; they move onto `exec_probe` in the next
step of the harness move.

A policy drop on kindnet is a timeout; a refused connection means nothing listens. So a check
that the network policy refuses an edge expects `POLICY_DROPPED` only, and proves the target is up
from an allowed peer in the same test (code review and security review, 2026-10-09).

`unpoliced_caller` starts a short-lived pod in `default` (no NetworkPolicy there): the control
that an address answers from this cluster when no egress policy holds the caller back.

Every kubectl call pins `--context kind-poc05`.
"""

from __future__ import annotations

import ipaddress
import json
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from poc_harness.pod_probe import PROBE

CONTEXT = "kind-poc05"
AGENTS_NS = "poc05-agents"
PLATFORM_NS = "poc05-platform"
REMOTE_NS = "poc05-remote"
TOOLS_NS = "poc05-tools"
NODE_CONTAINER = "poc05-control-plane"
SIDECAR_POD = "agent-echo"
WORKLOAD = "workload"
CHASSIS = "chassis"
PROXY = "http://127.0.0.1:8090"
TIMEOUT_S = 90
# The chassis's Secret-sourced variables in agent-echo.yaml (names only).
CHASSIS_SECRET_ENV = frozenset({"LITELLM_API_KEY", "VALKEY_PASSWORD"})
NOT_A_KEY = "sk-poc05-not-a-real-key-0000"
METADATA_IP = ipaddress.ip_address("169.254.169.254")
# kindnet drops a policy-refused packet: the connect times out. ConnectionRefusedError means no
# listener, so it never counts as a policy refusal.
POLICY_DROPPED = "TimeoutError"
CALLER = "poc05-t22-caller"  # a prefix: each caller pod gets its own name
CALLER_PART_OF = "poc05-t22"
CALLER_NS = "default"
CALLER_IMAGE = "kind.local/agent-platform/echo-python:poc05"


def kubectl(*args: str, timeout: int = TIMEOUT_S) -> subprocess.CompletedProcess[str]:
    exe = shutil.which("kubectl")
    assert exe is not None, "kubectl is not on PATH"
    return subprocess.run(
        [exe, "--context", CONTEXT, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def get_json(*args: str) -> Any:
    got = kubectl("get", *args, "-o", "json")
    assert got.returncode == 0, got.stderr
    return json.loads(got.stdout)


def pod(namespace: str, name: str) -> dict[str, Any]:
    """The one Running pod of the app `name` (label `app.kubernetes.io/name`)."""
    items = get_json("pods", "-n", namespace, "-l", f"app.kubernetes.io/name={name}")["items"]
    running = [p for p in items if p["status"].get("phase") == "Running"]
    assert len(running) == 1, f"{namespace}/{name}: want one Running pod, got {len(running)}"
    found: dict[str, Any] = running[0]
    return found


def pods(namespace: str, name: str) -> list[dict[str, Any]]:
    """Every Running pod of the app `name` (label `app.kubernetes.io/name`), maybe none."""
    items = get_json("pods", "-n", namespace, "-l", f"app.kubernetes.io/name={name}")["items"]
    return [p for p in items if p["status"].get("phase") == "Running"]


def warm_sandboxes() -> list[dict[str, Any]]:
    """The code-runner pool's unclaimed pods: Running, Ready, not being deleted, and no claim's
    label. A test that needs a sandbox nobody else uses picks one of these."""
    return [
        p
        for p in pods(TOOLS_NS, "code-runner")
        if "deletionTimestamp" not in p["metadata"]
        and "agents.x-k8s.io/claim-uid" not in p["metadata"].get("labels", {})
        and any(
            c["type"] == "Ready" and c["status"] == "True"
            for c in p["status"].get("conditions", [])
        )
    ]


def service_ip(namespace: str, name: str) -> str:
    ip: str = get_json("service", name, "-n", namespace)["spec"]["clusterIP"]
    return ip


def node_ip() -> str:
    node = get_json("nodes")["items"][0]
    ip: str = next(a["address"] for a in node["status"]["addresses"] if a["type"] == "InternalIP")
    return ip


def exec_probe(
    namespace: str, pod_name: str, container: str, argv: list[str], checks: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Run `argv` in `container` with `checks` as its last argument. The last stdout line is the
    JSON list of results: one result per check, in order.
    """
    got = kubectl(
        "exec", "-n", namespace, pod_name, "-c", container, "--", *argv, json.dumps(checks)
    )
    assert got.returncode == 0, f"exec in {pod_name}/{container} failed: {got.stderr[-400:]}"
    results: list[dict[str, Any]] = json.loads(got.stdout.strip().splitlines()[-1])
    assert len(results) == len(checks)
    return results


def probe(
    namespace: str, pod_name: str, container: str, checks: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Run `checks` in `container` with its own Python; one result per check, in order."""
    return exec_probe(namespace, pod_name, container, ["python", "-c", PROBE], checks)


def node_sh(script: str) -> str:
    """Run `script` as root with the kind node's own shell (`docker exec`); its stdout. A test that
    changes the node undoes the change itself.
    """
    docker = shutil.which("docker")
    assert docker is not None, "docker is not on PATH"
    got = subprocess.run(
        [docker, "exec", NODE_CONTAINER, "sh", "-c", script],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
        check=False,
    )
    assert got.returncode == 0, f"node exec failed: {got.stderr[-300:]}"
    return got.stdout


CALLER_POD: dict[str, Any] = {
    "apiVersion": "v1",
    "kind": "Pod",
    "metadata": {
        "name": CALLER,  # replaced per use
        "namespace": CALLER_NS,
        "labels": {
            "app.kubernetes.io/name": "remote-echo",
            "app.kubernetes.io/part-of": "poc05-t22",
        },
    },
    "spec": {
        "automountServiceAccountToken": False,
        "enableServiceLinks": False,
        "restartPolicy": "Never",
        "terminationGracePeriodSeconds": 0,
        "activeDeadlineSeconds": 600,
        "securityContext": {
            "runAsNonRoot": True,
            "runAsUser": 10002,
            "seccompProfile": {"type": "RuntimeDefault"},
        },
        "containers": [
            {
                "name": "caller",
                "image": CALLER_IMAGE,
                "imagePullPolicy": "Never",
                "command": ["python", "-c", "import time; time.sleep(600)"],
                "securityContext": {
                    "allowPrivilegeEscalation": False,
                    "readOnlyRootFilesystem": True,
                    "capabilities": {"drop": ["ALL"]},
                },
            }
        ],
    },
}


@contextmanager
def unpoliced_caller() -> Iterator[str]:
    """A pod in `default`, outside every PoC-5 namespace and every egress policy, labeled like the
    remote (`app.kubernetes.io/name: remote-echo`); its name. Each use gets its own name, so two
    test sessions on one cluster never delete each other's caller (full tier, 2026-10-09: a fixed
    name let one session's setup kill the pod another session was using). Caller pods left by an
    interrupted run that are no longer Running are deleted first. Deleted after."""
    exe = shutil.which("kubectl")
    assert exe is not None, "kubectl is not on PATH"
    gone = kubectl(
        "delete", "pod", "-n", CALLER_NS, "-l", f"app.kubernetes.io/part-of={CALLER_PART_OF}",
        "--field-selector=status.phase!=Running", "--ignore-not-found", "--wait=true",
    )  # fmt: skip
    assert gone.returncode == 0, gone.stderr
    name = f"{CALLER}-{uuid.uuid4().hex[:6]}"
    manifest = {**CALLER_POD, "metadata": {**CALLER_POD["metadata"], "name": name}}
    made = subprocess.run(
        [exe, "--context", CONTEXT, "create", "-f", "-"],
        input=json.dumps(manifest),
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
        check=False,
    )
    assert made.returncode == 0, made.stderr
    try:
        ready = kubectl(
            "wait", "-n", CALLER_NS, "--for=condition=Ready", f"pod/{name}", "--timeout=90s"
        )
        assert ready.returncode == 0, ready.stderr
        yield name
    finally:
        kubectl("delete", "pod", name, "-n", CALLER_NS, "--wait=false", "--grace-period=0")


def in_caller(caller: str, checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Run `checks` in the caller pod `caller` (the name `unpoliced_caller` gave)."""
    return probe(CALLER_NS, caller, "caller", checks)


def tcp_check(host: str, port: int) -> dict[str, Any]:
    """A probe check that connects to `host:port`. It opens no connection itself."""
    return {"kind": "tcp", "host": host, "port": port}


def chat(url: str, **extra: Any) -> dict[str, Any]:
    """One non-streamed chat call to the fake model's route, as a probe check."""
    body = {"model": "fake-chat", "messages": [{"role": "user", "content": "hello"}]}
    return {"kind": "http", "url": f"{url}/chat/completions", "body": body, **extra}
