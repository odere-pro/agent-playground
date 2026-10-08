"""PoC-5 on kind, the remote lane: the controls around the `remote-echo` Sandbox and its chassis's
remote listener (T22; plan sections 2.2, 2.3, 2.10, 2.11; threat model H17 to H21, H23, H28, H30).

Exit criteria 4 (the remote workload holds no credential but its own token), 6 (the remote
listener answers only the remote's own token inside a run; the remote pod reaches nothing but
that listener), and 8, partly shown (the sandbox: gVisor, read-only root, pids cap, no egress; T10,
the in-pod probe workload, is dropped, so the controls are checked from outside: `kubectl exec`
of echo-python's own Python, the live pod spec, the node's cgroup files). Every refusal asserts its
paired allowed control in the same test.

The remote's token never leaves its pod: a check names its env variable (`auth_env`), the probe
reads it inside the container, and nothing returns it. H18 needs a caller that no egress policy
holds back: a short-lived pod in `default` (no NetworkPolicy there) with the echo-python image
already loaded on the node, and the label `app.kubernetes.io/name: remote-echo` in the wrong
namespace, so the test also shows that the label alone is not the key. It is deleted after.

H23 never exhausts the cap (under gVisor that would end the sandbox): it reads the pod's pids
cgroup on the node and compares it with the kubelet's configured `podPidsLimit`. The per-call
process limit inside the code runner is in `test_poc05_kind_code_runner.py`.

A kind test: marked `network`, skipped unless `POC05_KIND=1` (`poc05_conftest.py`). Run:
`POC05_KIND=1 uv run pytest -m network <this file>`.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from poc05_kind import (
    AGENTS_NS,
    CHASSIS,
    CONTEXT,
    NOT_A_KEY,
    PLATFORM_NS,
    REMOTE_NS,
    SIDECAR_POD,
    TIMEOUT_S,
    WORKLOAD,
    chat,
    get_json,
    kubectl,
    node_sh,
    pod,
    probe,
    service_ip,
)

REPO = Path(__file__).resolve().parents[3]
CLUSTER_YAML = REPO / "deploy" / "kind" / "poc05" / "cluster.yaml"
REMOTE = "remote-echo"
REMOTE_CHASSIS = "chassis-echo-remote"
PROXY_IP = "10.96.85.91"  # the fixed ClusterIP of chassis-echo-remote-proxy
TOKEN_ENV = "CHASSIS_API_TOKEN"
TOKEN_SECRET = "remote-echo-token"  # pragma: allowlist secret (a Secret name)
OUTSIDE_IP = "1.1.1.1"  # a literal public address: no DNS involved
REFUSED_TCP = {"TimeoutError", "ConnectionRefusedError", "OSError"}
CALLER = "poc05-t22-caller"
CALLER_NS = "default"
CALLER_IMAGE = "kind.local/agent-platform/echo-python:poc05"


def traceparent(trace_id: str) -> dict[str, str]:
    return {"traceparent": f"00-{trace_id}-00f067aa0ba902b7-01"}


def tcp(host: str, port: int) -> dict[str, Any]:
    return {"kind": "tcp", "host": host, "port": port}


@pytest.fixture(scope="module")
def remote() -> dict[str, Any]:
    return pod(REMOTE_NS, REMOTE)


@pytest.fixture(scope="module")
def remote_chassis() -> dict[str, Any]:
    return pod(AGENTS_NS, REMOTE_CHASSIS)


def in_remote(remote: dict[str, Any], checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return probe(REMOTE_NS, remote["metadata"]["name"], WORKLOAD, checks)


def in_chassis(chassis: dict[str, Any], checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return probe(AGENTS_NS, chassis["metadata"]["name"], CHASSIS, checks)


CALLER_POD = {
    "apiVersion": "v1",
    "kind": "Pod",
    "metadata": {
        "name": CALLER,
        "namespace": CALLER_NS,
        "labels": {"app.kubernetes.io/name": REMOTE, "app.kubernetes.io/part-of": "poc05-t22"},
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


@pytest.fixture(scope="module")
def caller() -> Iterator[str]:
    """A pod outside every PoC-5 namespace, with no egress policy, labeled like the remote."""
    exe = shutil.which("kubectl")
    assert exe is not None
    made = subprocess.run(
        [exe, "--context", CONTEXT, "apply", "-f", "-"],
        input=json.dumps(CALLER_POD),
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
        check=False,
    )
    assert made.returncode == 0, made.stderr
    try:
        ready = kubectl(
            "wait", "-n", CALLER_NS, "--for=condition=Ready", f"pod/{CALLER}", "--timeout=90s"
        )
        assert ready.returncode == 0, ready.stderr
        yield CALLER
    finally:
        kubectl("delete", "pod", CALLER, "-n", CALLER_NS, "--wait=false", "--grace-period=0")


def in_caller(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return probe(CALLER_NS, CALLER, "caller", checks)


# --- H17: the remote listener's bearer check and run_required ----------------------------------

RUN_PY = r"""
import json, os, sys, urllib.request
body = json.dumps({"input": {"text": "hello"}}).encode()
req = urllib.request.Request("http://%s:8080/v1/run" % os.environ["POD_IP"], body,
                             {"Content-Type": "application/json", "traceparent": sys.argv[1]})
with urllib.request.urlopen(req, timeout=60) as r:
    out = json.loads(r.read())
    print(json.dumps({"status": r.status, "run": out["status"], "output": out["output"]}))
"""


def remote_calls_served(chassis: dict[str, Any], remote_ip: str) -> dict[str, int]:
    """How many calls from the remote's IP the listener answered 200, per path, from the chassis's
    access log lines (method, path, status; no header is logged). Only counts leave here."""
    got = kubectl("logs", "-n", AGENTS_NS, chassis["metadata"]["name"], "-c", CHASSIS)
    assert got.returncode == 0, got.stderr[-300:]
    line = re.compile(
        rf'^INFO:\s+{re.escape(remote_ip)}:\d+ - "POST (/v1/chat/completions|/mcp) HTTP/1.1" 200 '
    )
    counts = {"/v1/chat/completions": 0, "/mcp": 0}
    for match in filter(None, map(line.match, got.stdout.splitlines())):
        counts[match.group(1)] += 1
    return counts


def test_h17_listener_answers_only_the_remote_token_inside_a_run(
    remote: dict[str, Any], remote_chassis: dict[str, Any]
) -> None:
    """Criterion 6, H17: the control refuses a model call to the remote listener (8091, from the
    remote pod) with no token (401) and with a token it did not issue (401), and refuses the
    remote's own token outside a run (403 `run_required`). The allowed control: inside a run with
    that same traceparent, the remote's calls with its own token are answered 200 (the run ends
    `ok` with the fake model's reply, and the listener's 200 count for the remote's IP grows);
    after the run the same call is 403 again."""
    url = f"http://{PROXY_IP}:8091/v1"
    trace = traceparent(uuid.uuid4().hex)
    bare, stranger, outside = in_remote(
        remote,
        [
            chat(url, headers=trace),
            chat(url, headers={**trace, "Authorization": f"Bearer {NOT_A_KEY}"}),
            chat(url, headers=trace, auth_env=TOKEN_ENV),
        ],
    )
    assert bare["status"] == 401 and "remote_unauthenticated" in bare["head"], bare
    assert stranger["status"] == 401 and "remote_unauthenticated" in stranger["head"], stranger
    assert NOT_A_KEY not in stranger["head"]
    assert outside["status"] == 403 and "run_required" in outside["head"], outside

    remote_ip = remote["status"]["podIP"]
    before = remote_calls_served(remote_chassis, remote_ip)
    got = kubectl(
        "exec", "-n", AGENTS_NS, remote_chassis["metadata"]["name"], "-c", CHASSIS, "--",
        "python", "-c", RUN_PY, trace["traceparent"],
    )  # fmt: skip
    assert got.returncode == 0, got.stderr[-300:]
    run = json.loads(got.stdout.strip().splitlines()[-1])
    assert run == {"status": 200, "run": "ok", "output": {"text": "ok"}}, run
    after = remote_calls_served(remote_chassis, remote_ip)
    assert after["/v1/chat/completions"] > before["/v1/chat/completions"], (before, after)
    assert after["/mcp"] > before["/mcp"], (before, after)

    (ended,) = in_remote(remote, [chat(url, headers=trace, auth_env=TOKEN_ENV)])
    assert ended["status"] == 403 and "run_required" in ended["head"], ended


# --- H18, H30: who may reach whom --------------------------------------------------------------


def test_h18_listener_refuses_every_caller_but_the_remote_pod(
    caller: str, remote: dict[str, Any], remote_chassis: dict[str, Any]
) -> None:
    """Criterion 6, H18/H30: the control refuses a TCP connection to the remote listener (8091,
    by its ClusterIP and by the chassis pod IP) from a pod outside poc05-remote that carries the
    remote's label (`default`, no egress policy) and from the sidecar workload in `agent-echo`.
    The allowed controls: the same caller reaches the chassis's public port 8080 on the same pod
    IP, so its own egress works; the remote pod connects to 8091 by both addresses."""
    chassis_ip = remote_chassis["status"]["podIP"]
    to_proxy, to_pod, public = in_caller(
        [tcp(PROXY_IP, 8091), tcp(chassis_ip, 8091), tcp(chassis_ip, 8080)]
    )
    assert to_proxy.get("error") in REFUSED_TCP, to_proxy
    assert to_pod.get("error") in REFUSED_TCP, to_pod
    assert public.get("connected") is True, public

    sidecar = pod(AGENTS_NS, SIDECAR_POD)["metadata"]["name"]
    (lateral,) = probe(AGENTS_NS, sidecar, WORKLOAD, [tcp(PROXY_IP, 8091)])
    assert lateral.get("error") in REFUSED_TCP, lateral

    by_proxy, by_pod = in_remote(remote, [tcp(PROXY_IP, 8091), tcp(chassis_ip, 8091)])
    assert by_proxy.get("connected") is True, by_proxy
    assert by_pod.get("connected") is True, by_pod


def test_h18_remote_port_refuses_every_caller_but_its_chassis(
    caller: str, remote: dict[str, Any], remote_chassis: dict[str, Any]
) -> None:
    """Criterion 6, H18: the control refuses a TCP connection to the remote's A2A port (9000, by
    the pod IP and the Service) from the labeled caller in `default`. The allowed control: the
    remote's own chassis connects to the same port by both addresses, and the caller reaches
    another pod's open port (the chassis's 8080)."""
    remote_ip, svc = remote["status"]["podIP"], service_ip(REMOTE_NS, REMOTE)
    by_pod, by_svc, public = in_caller(
        [tcp(remote_ip, 9000), tcp(svc, 9000), tcp(remote_chassis["status"]["podIP"], 8080)]
    )
    assert by_pod.get("error") in REFUSED_TCP, by_pod
    assert by_svc.get("error") in REFUSED_TCP, by_svc
    assert public.get("connected") is True, public

    own_pod, own_svc = in_chassis(remote_chassis, [tcp(remote_ip, 9000), tcp(svc, 9000)])
    assert own_pod.get("connected") is True, own_pod
    assert own_svc.get("connected") is True, own_svc


def test_h30_remote_reaches_no_other_pod(
    remote: dict[str, Any], remote_chassis: dict[str, Any]
) -> None:
    """Criterion 6, H30: the control refuses the remote pod's TCP connection to another agent
    (`agent-echo`'s public port), to its own chassis's public port, to LiteLLM, to Valkey, and to
    the code runner, by literal address. The allowed control in the same call: 8091 connects."""
    sidecar_ip = pod(AGENTS_NS, SIDECAR_POD)["status"]["podIP"]
    checks = [
        tcp(sidecar_ip, 8080),
        tcp(remote_chassis["status"]["podIP"], 8080),
        tcp(service_ip(PLATFORM_NS, "litellm"), 4000),
        tcp(service_ip(PLATFORM_NS, "valkey"), 6379),
        tcp(service_ip("poc05-tools", "code-runner"), 8000),
        tcp(PROXY_IP, 8091),
    ]
    *refused, allowed = in_remote(remote, checks)
    for check, result in zip(checks, refused, strict=False):
        assert result.get("error") in REFUSED_TCP, (check, result)
    assert allowed.get("connected") is True, allowed


# --- H19, H20: no way out ----------------------------------------------------------------------


def test_h19_remote_reaches_no_outside_address(caller: str, remote: dict[str, Any]) -> None:
    """Criterion 8 (partly shown), H19: the control refuses the remote pod's TCP connection to a
    literal outside address (1.1.1.1:443 and :80; no DNS involved). The allowed controls: in the
    same call the remote reaches 8091, and the caller pod, which no egress policy holds, reaches
    the same outside address, so the address answers from this cluster."""
    https, http, allowed = in_remote(
        remote, [tcp(OUTSIDE_IP, 443), tcp(OUTSIDE_IP, 80), tcp(PROXY_IP, 8091)]
    )
    assert https.get("error") in REFUSED_TCP, https
    assert http.get("error") in REFUSED_TCP, http
    assert allowed.get("connected") is True, allowed

    (outside,) = in_caller([tcp(OUTSIDE_IP, 443)])
    assert outside.get("connected") is True, outside


def test_h20_remote_has_no_dns(remote: dict[str, Any], remote_chassis: dict[str, Any]) -> None:
    """Criterion 8 (partly shown), H20: the remote lane has no DNS at all (its policy has no port
    53 rule), so DNS cannot carry data out. The control refuses the remote's A query to kube-dns
    by its ClusterIP and by a CoreDNS pod IP, and to an outside resolver (1.1.1.1), and its own
    resolver finds no name. The allowed control: the same A query from its chassis (which may use
    kube-dns) is answered. Limit: the sidecar lane keeps kube-dns, so the tunneling caveat in the
    threat model still holds there."""
    policy = get_json("networkpolicy", REMOTE, "-n", REMOTE_NS)["spec"]
    ports = [p.get("port") for rule in policy.get("egress", []) for p in rule.get("ports", [])]
    assert ports == [8091], ports

    kube_dns = service_ip("kube-system", "kube-dns")
    coredns = get_json("pods", "-n", "kube-system", "-l", "k8s-app=kube-dns")["items"][0]
    name = "litellm.poc05-platform.svc.cluster.local"
    query = {"kind": "dns", "name": name}
    by_svc, by_pod, outside, own = in_remote(
        remote,
        [
            {**query, "server": kube_dns},
            {**query, "server": coredns["status"]["podIP"]},
            {**query, "server": OUTSIDE_IP},
            {"kind": "resolve", "name": name},
        ],
    )
    for result in (by_svc, by_pod, outside):
        assert result.get("error") == "TimeoutError", result
    assert "addresses" not in own, own

    (allowed,) = in_chassis(remote_chassis, [{**query, "server": kube_dns}])
    assert allowed.get("rcode") == 0 and allowed.get("answers", 0) >= 1, allowed


# --- H21, H23, H28: the sandbox ----------------------------------------------------------------


def test_h21_root_is_read_only_and_tmp_is_writable(remote: dict[str, Any]) -> None:
    """Criterion 8 (partly shown), H21: `/proc/mounts` shows `/` mounted `ro`, and the control
    refuses a write into `/var/tmp` (mode 1777 on the root file system, so permissions would allow
    it) with EROFS. The allowed control: the same write to `/tmp` works."""
    spec = remote["spec"]["containers"][0]["securityContext"]
    assert spec["readOnlyRootFilesystem"] is True, spec
    mounts, root_write, tmp_write = in_remote(
        remote,
        [
            {"kind": "read", "path": "/proc/mounts"},
            {"kind": "write", "path": "/var/tmp/poc05-h21"},
            {"kind": "write", "path": "/tmp/poc05-h21"},
        ],
    )
    table = {f[1]: f[3].split(",") for f in (line.split() for line in mounts["text"].splitlines())}
    assert "ro" in table["/"], table["/"]
    assert "rw" in table["/tmp"], table["/tmp"]
    assert root_write == {"error": "EROFS"}, root_write
    assert tmp_write == {"written": True}, tmp_write


def test_h23_pids_cap_is_the_configured_limit(remote: dict[str, Any]) -> None:
    """Criterion 8 (partly shown), H23: the remote pod's pids cgroup on the node has `pids.max`
    equal to the kubelet's `podPidsLimit`, which is the value `cluster.yaml` configures (256).
    The cap is never exhausted here. The allowed control: `pids.current` is above zero and under
    the cap, so the sandbox's processes run inside it."""
    cluster = yaml.safe_load(CLUSTER_YAML.read_text())
    patches = "\n".join(n.get("kubeadmConfigPatches", [""])[0] for n in cluster["nodes"])
    configured = int(re.search(r"podPidsLimit:\s*(\d+)", patches).group(1))  # type: ignore[union-attr]
    kubelet = node_sh("grep -E '^podPidsLimit:' /var/lib/kubelet/config.yaml")
    assert int(kubelet.split()[1]) == configured

    uid = remote["metadata"]["uid"].replace("-", "_")
    out = node_sh(
        "d=$(find /sys/fs/cgroup/kubelet.slice/kubelet-kubepods.slice -maxdepth 2 -type d "
        f'-name \'*-pod{uid}.slice\'); cat "$d/pids.max" "$d/pids.current"'
    )
    pids_max, pids_current = out.split()
    assert int(pids_max) == configured == 256, out
    assert 0 < int(pids_current) < int(pids_max), out


def test_h28_remote_runs_on_gvisor_next_to_a_runc_pod(remote: dict[str, Any]) -> None:
    """Criterion 8 (partly shown), H28: the remote pod has `runtimeClassName: gvisor`, the
    RuntimeClass's handler is `runsc`, the node's containerd ran its sandbox with `runsc`, and the
    kernel it sees is gVisor's (`/proc/version`). The control: `agent-echo`'s workload, on the
    default runtime, sees the node's real kernel and its sandbox handler is not `runsc`."""
    assert remote["spec"]["runtimeClassName"] == "gvisor"
    assert get_json("runtimeclass", "gvisor")["handler"] == "runsc"
    sidecar = pod(AGENTS_NS, SIDECAR_POD)
    assert sidecar["spec"].get("runtimeClassName") is None

    def handler(name: str) -> str:
        pods = json.loads(node_sh(f"crictl pods --name '^{name}' --state ready -o json"))["items"]
        assert len(pods) == 1, [p["metadata"]["name"] for p in pods]
        found: str = pods[0].get("runtimeHandler", "")
        return found

    assert handler(remote["metadata"]["name"]) == "runsc"
    assert handler(sidecar["metadata"]["name"]) != "runsc"

    (gvisor,) = in_remote(remote, [{"kind": "read", "path": "/proc/version"}])
    (runc,) = probe(
        AGENTS_NS,
        sidecar["metadata"]["name"],
        WORKLOAD,
        [{"kind": "read", "path": "/proc/version"}],
    )
    assert "gvisor" in gvisor["text"], gvisor
    assert "gvisor" not in runc["text"] and runc["text"].startswith("Linux version"), runc


# --- One secret: the remote's own token, nothing else ------------------------------------------


def secret_sources(spec: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Every place a pod spec takes a Secret: (container, env name or kind, Secret name)."""
    found = []
    containers = [
        *spec.get("initContainers", []),
        *spec.get("containers", []),
        *spec.get("ephemeralContainers", []),
    ]
    for c in containers:
        for e in c.get("env", []):
            ref = (e.get("valueFrom") or {}).get("secretKeyRef")
            if ref:
                found.append((c["name"], e["name"], ref["name"]))
        found.extend(
            (c["name"], "envFrom", e["secretRef"]["name"])
            for e in c.get("envFrom", [])
            if "secretRef" in e
        )
    for v in spec.get("volumes", []):
        if "secret" in v:
            found.append(("volume", v["name"], v["secret"]["secretName"]))
        for source in (v.get("projected") or {}).get("sources", []):
            if "secret" in source:
                found.append(("volume", v["name"], source["secret"]["name"]))
            if "serviceAccountToken" in source:
                found.append(("volume", v["name"], "serviceAccountToken"))
    return found


def test_remote_holds_one_secret_its_own_token(remote: dict[str, Any]) -> None:
    """Criterion 4 (decision 2026-10-08: the remote's own token is allowed, nothing else), H25 for
    the remote lane: the control refuses every other Secret. The pod spec takes exactly one value
    from a Secret, `CHASSIS_API_TOKEN` from `remote-echo-token` in the workload container; no
    `envFrom` Secret, no Secret or projected volume, no service account token
    (`automountServiceAccountToken: false`, `/var/run/secrets/kubernetes.io` absent). Its live
    env holds no chassis credential name. The allowed control: the live env does hold
    `CHASSIS_API_TOKEN` (names only), and the path check sees `/etc/hosts`."""
    spec = remote["spec"]
    assert secret_sources(spec) == [(WORKLOAD, TOKEN_ENV, TOKEN_SECRET)], secret_sources(spec)
    assert spec["automountServiceAccountToken"] is False

    names, sa_dir, hosts = in_remote(
        remote,
        [
            {"kind": "env_names"},
            {"kind": "path", "path": "/var/run/secrets/kubernetes.io"},
            {"kind": "path", "path": "/etc/hosts"},
        ],
    )
    assert TOKEN_ENV in names["names"], names
    chassis_credentials = {"LITELLM_API_KEY", "VALKEY_PASSWORD", "REMOTE_TOKEN"}
    assert not chassis_credentials & set(names["names"]), names
    assert not [n for n in names["names"] if n.endswith(("_API_KEY", "_PASSWORD", "_SECRET"))]
    assert sa_dir == {"exists": False}, sa_dir
    assert hosts == {"exists": True}, hosts
