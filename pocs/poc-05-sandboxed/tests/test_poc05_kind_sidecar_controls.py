"""PoC-5 on kind, the sidecar lane: the pod-level controls of `agent-echo` (T21; plan sections
2.10, 2.11; threat model H01 to H04, H13, H25, H26).

Exit criteria 4 (the workload holds no credential and no service account token), 5 (the
workload reaches no internal service, metadata address, or Kubernetes API except through the
chassis), and 7's sidecar half (the trusted sidecar pod as admitted runs with these controls; the
admission rules themselves are in `test_poc05_kind_admission.py`). Each check runs in the
workload container with `kubectl exec` of the echo-python image's own Python, or reads the live
pod spec, and asserts its paired allowed control in the same test. Env and `/proc` checks return
names only, never a value.

H01 limit: nothing answers at 169.254.169.254 on kind, so a refused connection there does not by
itself prove the policy. The test proves it statically (no live NetworkPolicy in a PoC-5
namespace has an `ipBlock` or an empty peer that covers the address, so default deny holds) and
records the live refusal next to an allowed edge.

A kind test: marked `network`, skipped unless `POC05_KIND=1` (`poc05_conftest.py`). Run:
`POC05_KIND=1 deploy/kind/poc05/run.sh with-gateway uv run pytest -m network <this file>`.
"""

from __future__ import annotations

import ipaddress
import shutil
import subprocess
from typing import Any

import pytest
from poc05_kind import (
    AGENTS_NS,
    CHASSIS,
    CHASSIS_SECRET_ENV,
    METADATA_IP,
    PLATFORM_NS,
    SIDECAR_POD,
    WORKLOAD,
    get_json,
    node_ip,
    pod,
    probe,
    service_ip,
)

SA_DIR = "/var/run/secrets/kubernetes.io"
REFUSED_TCP = {"TimeoutError", "ConnectionRefusedError", "OSError"}
NODE_CONTAINER = "poc05-control-plane"
API_PORT = 6443


@pytest.fixture(scope="module")
def sidecar() -> dict[str, Any]:
    return pod(AGENTS_NS, SIDECAR_POD)


def container(spec: dict[str, Any], name: str) -> dict[str, Any]:
    every = spec["spec"].get("initContainers", []) + spec["spec"]["containers"]
    found: dict[str, Any] = next(c for c in every if c["name"] == name)
    return found


def secret_refs(c: dict[str, Any], volumes: dict[str, dict[str, Any]]) -> list[str]:
    """Where container `c` gets anything from a Secret: env, envFrom, or a mounted volume."""
    refs = [e["name"] for e in c.get("env", []) if "secretKeyRef" in e.get("valueFrom", {})]
    refs += ["envFrom" for e in c.get("envFrom", []) if "secretRef" in e]
    for m in c.get("volumeMounts", []):
        v = volumes.get(m["name"], {})
        projected = v.get("projected", {}).get("sources", [])
        if "secret" in v or any("secret" in s or "serviceAccountToken" in s for s in projected):
            refs.append(f"volume:{m['name']}")
    return refs


def allowed_edge() -> dict[str, Any]:
    """The workload's allowed control: LiteLLM on 4000 (the shared pod's egress)."""
    return {"kind": "tcp", "host": service_ip(PLATFORM_NS, "litellm"), "port": 4000}


def test_h02_no_service_account_token_in_the_workload(sidecar: dict[str, Any]) -> None:
    """H02, criterion 4: the control refuses the workload a service account token: the pod spec
    turns automount off and the token directory does not exist in the workload container. The
    control: the same check sees a file the kubelet does mount there (/etc/hosts)."""
    assert sidecar["spec"].get("automountServiceAccountToken") is False
    name = sidecar["metadata"]["name"]
    token_dir, kubelet_file = probe(
        AGENTS_NS,
        name,
        WORKLOAD,
        [{"kind": "path", "path": SA_DIR}, {"kind": "path", "path": "/etc/hosts"}],
    )
    assert token_dir == {"exists": False}
    assert kubelet_file == {"exists": True}


def test_h13_public_port_refused_on_loopback_served_on_the_pod_ip(sidecar: dict[str, Any]) -> None:
    """H13, kind half, criterion 5: the control refuses the workload's connection to
    127.0.0.1:8080 (the chassis binds its public port to the pod IP only); the control: the pod
    IP's /health answers 200, and the proxies on 127.0.0.1:8090 answer too."""
    pod_ip = sidecar["status"]["podIP"]
    loop, public, proxy = probe(
        AGENTS_NS,
        sidecar["metadata"]["name"],
        WORKLOAD,
        [
            {"kind": "tcp", "host": "127.0.0.1", "port": 8080},
            {"kind": "http", "url": f"http://{pod_ip}:8080/health"},
            {"kind": "tcp", "host": "127.0.0.1", "port": 8090},
        ],
    )
    assert loop == {"error": "ConnectionRefusedError"}, loop
    assert public["status"] == 200, public
    assert proxy.get("connected") is True, proxy


def test_h25_no_secret_value_reaches_the_workload(sidecar: dict[str, Any]) -> None:
    """H25, criterion 4: the control refuses the workload any Secret: its container spec has no
    `secretKeyRef`, no `envFrom` Secret, and no Secret or token volume, and its live env holds none
    of the chassis's Secret-sourced names. The control: the chassis container's spec references
    both Secrets and its live env has both names. Names only; no value is read or printed."""
    volumes = {v["name"]: v for v in sidecar["spec"].get("volumes", [])}
    assert secret_refs(container(sidecar, WORKLOAD), volumes) == []
    assert set(secret_refs(container(sidecar, CHASSIS), volumes)) == CHASSIS_SECRET_ENV

    name = sidecar["metadata"]["name"]
    (workload_env,) = probe(AGENTS_NS, name, WORKLOAD, [{"kind": "env_names"}])
    (chassis_env,) = probe(AGENTS_NS, name, CHASSIS, [{"kind": "env_names"}])
    assert CHASSIS_SECRET_ENV.isdisjoint(workload_env["names"])
    assert set(chassis_env["names"]) >= CHASSIS_SECRET_ENV


def test_h26_chassis_environ_not_readable_from_the_workload(sidecar: dict[str, Any]) -> None:
    """H26, criterion 4: the control refuses the workload the chassis's `/proc/<pid>/environ`:
    the pod does not share a PID namespace, so the workload's /proc lists only the workload's own
    processes (its pid 1 is `workload-a2a`), and none holds a chassis Secret name. The control:
    the same scan in the chassis container finds the chassis process and those names."""
    assert sidecar["spec"].get("shareProcessNamespace") is not True
    name = sidecar["metadata"]["name"]
    check = [{"kind": "procs", "names": sorted(CHASSIS_SECRET_ENV)}]
    (workload,) = probe(AGENTS_NS, name, WORKLOAD, check)
    (chassis,) = probe(AGENTS_NS, name, CHASSIS, check)

    seen = workload["procs"]
    assert seen, "the workload sees no process at all"
    assert seen[0]["pid"] == 1 and seen[0]["argv"][1].endswith("workload-a2a"), seen[0]
    assert not any(p["argv"][1:2] and p["argv"][1].endswith("/chassis") for p in seen), seen
    assert all(p["secret_names"] == [] for p in seen), seen

    own = chassis["procs"][0]
    assert own["argv"][1].endswith("/chassis"), own
    assert own["environ_readable"] is True
    assert set(own["secret_names"]) == CHASSIS_SECRET_ENV


def node_reaches_its_api(ip: str) -> str:
    """The node container's own curl to https://<node IP>:6443/livez (anonymous, allowed by the
    default `system:public-info-viewer` role)."""
    docker = shutil.which("docker")
    assert docker is not None, "docker is not on PATH"
    got = subprocess.run(
        [
            docker,
            "exec",
            NODE_CONTAINER,
            "curl",
            "-sk",
            "-m",
            "5",
            f"https://{ip}:{API_PORT}/livez",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return got.stdout.strip()


def test_h03_h04_kubernetes_api_unreachable_from_the_workload(sidecar: dict[str, Any]) -> None:
    """H03 and H04, criterion 5: the control refuses the workload's connection to the Kubernetes
    API at the node IP on 6443 (H04) and at the Service VIP on 443 (H03; with no token either,
    H02). The controls: the node itself reaches the same address (/livez is ok), and the workload's
    allowed edge to LiteLLM connects in the same call."""
    ip = node_ip()
    vip = service_ip("default", "kubernetes")
    node_api, svc_api, allowed = probe(
        AGENTS_NS,
        sidecar["metadata"]["name"],
        WORKLOAD,
        [
            {"kind": "tcp", "host": ip, "port": API_PORT},
            {"kind": "tcp", "host": vip, "port": 443},
            allowed_edge(),
        ],
    )
    assert node_api.get("error") in REFUSED_TCP, node_api
    assert svc_api.get("error") in REFUSED_TCP, svc_api
    assert allowed.get("connected") is True, allowed
    assert node_reaches_its_api(ip) == "ok"


def policy_peers_covering(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> list[str]:
    """Every egress rule in a live PoC-5 NetworkPolicy that could allow `address`: an `ipBlock`
    whose CIDR holds it (minus its `except`), or a rule with no `to` (any destination)."""
    found = []
    for policy in get_json("networkpolicy", "-A")["items"]:
        meta = policy["metadata"]
        if not meta["namespace"].startswith("poc05-"):
            continue
        where = f"{meta['namespace']}/{meta['name']}"
        for rule in policy["spec"].get("egress", []):
            if not rule.get("to"):
                found.append(f"{where}: egress rule with no `to`")
            for peer in rule.get("to", []):
                block = peer.get("ipBlock")
                if block is None:
                    continue
                excluded = any(address in ipaddress.ip_network(e) for e in block.get("except", []))
                if address in ipaddress.ip_network(block["cidr"]) and not excluded:
                    found.append(f"{where}: ipBlock {block['cidr']}")
    return found


def test_h01_metadata_address_denied(sidecar: dict[str, Any]) -> None:
    """H01, criterion 5: the control refuses egress to 169.254.169.254: no live NetworkPolicy in a
    PoC-5 namespace allows it (no `ipBlock` holds it, no rule lacks a `to`), so each namespace's
    default deny applies; the workload's connection does not open, while its allowed edge does.
    Stated limit: nothing listens at that address on kind, so the live refusal alone proves
    nothing; the static check is the evidence. The control for the static check: the live
    policies are there (every PoC-5 namespace has `default-deny`)."""
    namespaces = {
        p["metadata"]["namespace"]
        for p in get_json("networkpolicy", "-A")["items"]
        if p["metadata"]["name"] == "default-deny"
    }
    assert {"poc05-agents", "poc05-platform", "poc05-remote", "poc05-tools"} <= namespaces
    assert policy_peers_covering(METADATA_IP) == []

    meta, allowed = probe(
        AGENTS_NS,
        sidecar["metadata"]["name"],
        WORKLOAD,
        [{"kind": "tcp", "host": str(METADATA_IP), "port": 80}, allowed_edge()],
    )
    assert meta.get("error") in REFUSED_TCP, meta
    assert allowed.get("connected") is True, allowed
