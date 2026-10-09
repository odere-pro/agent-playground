"""PoC-5 on kind, the sidecar lane: the pod-level controls of `agent-echo` (T21; plan sections
2.10, 2.11; threat model H01 to H04, H13, H25, H26).

Exit criteria 4 (the workload holds no credential and no service account token), 5 (the
workload reaches no internal service, metadata address, or Kubernetes API except through the
chassis), and 7's sidecar half (the trusted sidecar pod as admitted runs with these controls; the
admission rules themselves are in `test_poc05_kind_admission.py`). Each check runs in the
workload container with `kubectl exec` of the echo-python image's own Python, or reads the live
pod spec, and asserts its paired allowed control in the same test. Env and `/proc` checks return
names only, never a value.

H01 on kind: nothing answers at 169.254.169.254 on its own, so the test starts a stand-in for
the length of the test (user decision, 2026-10-09): the address on the kind node's loopback and a
tiny perl listener on port 80, through `docker exec` into the node, both removed after. It
returns one fixed line and serves nothing else. A pod with no egress policy reaches it (the
control); the workload's connection is dropped. The static half stays: no live NetworkPolicy in a
PoC-5 namespace has an `ipBlock` or an empty peer that covers the address.

A policy refusal expects a timeout (`POLICY_DROPPED`), never a refused connection, and each
target is shown up from an allowed peer in the same test. H13's loopback refusal is the one place
a refused connection is the claim.

A kind test: marked `network`, skipped unless `POC05_KIND=1` (`poc05_conftest.py`). Run:
`POC05_KIND=1 deploy/kind/poc05/run.sh with-gateway uv run pytest -m network <this file>`.
"""

from __future__ import annotations

import ipaddress
import shlex
import shutil
import subprocess
from collections.abc import Iterator
from typing import Any

import pytest
from poc05_kind import (
    AGENTS_NS,
    CHASSIS,
    CHASSIS_SECRET_ENV,
    METADATA_IP,
    PLATFORM_NS,
    POLICY_DROPPED,
    SIDECAR_POD,
    WORKLOAD,
    get_json,
    in_caller,
    node_ip,
    node_run,
    pod,
    probe,
    service_ip,
    tcp,
    unpoliced_caller,
)

SA_DIR = "/var/run/secrets/kubernetes.io"
DISPATCH = "code-runner-dispatch"
METADATA_MARK = "poc05-metadata-stub"
METADATA_PID = "/tmp/poc05-metadata.pid"
# A one-line HTTP answer per connection; nothing else. Bound to the metadata address only.
METADATA_LISTENER = (
    "use IO::Socket::INET; "
    f'my $s = IO::Socket::INET->new(LocalAddr => "{METADATA_IP}", LocalPort => 80, '
    "Listen => 5, ReuseAddr => 1) or die; "
    "while (my $c = $s->accept) { my $b; $c->recv($b, 1024); "
    f'print $c "HTTP/1.0 200 OK\r\nContent-Type: text/plain\r\n\r\n{METADATA_MARK}\n"; '
    "close $c }"
)
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
    H02). Both drops are timeouts. The controls: the node itself reaches the same address (/livez
    is ok); the code-runner dispatcher, the one pod with an API edge, connects to both addresses;
    and the workload's allowed edge to LiteLLM connects in the same call."""
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
    assert node_api.get("error") == POLICY_DROPPED, node_api
    assert svc_api.get("error") == POLICY_DROPPED, svc_api
    assert allowed.get("connected") is True, allowed
    assert node_reaches_its_api(ip) == "ok"

    dispatch = pod(PLATFORM_NS, DISPATCH)["metadata"]["name"]
    up = probe(PLATFORM_NS, dispatch, DISPATCH, [tcp(ip, API_PORT), tcp(vip, 443)])
    assert all(r.get("connected") is True for r in up), up


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


@pytest.fixture
def metadata_listener() -> Iterator[str]:
    """A stand-in at 169.254.169.254:80 on the kind node for the length of one test: the address
    on the node's loopback and a perl listener. Both are removed after, also on failure."""
    stop = (
        f"[ -f {METADATA_PID} ] && kill $(cat {METADATA_PID}) 2>/dev/null; rm -f {METADATA_PID}; "
        f"ip addr del {METADATA_IP}/32 dev lo 2>/dev/null; true"
    )
    node_run(stop)
    node_run(
        f"ip addr replace {METADATA_IP}/32 dev lo && "
        f"(nohup perl -e {shlex.quote(METADATA_LISTENER)} >/dev/null 2>&1 & "
        f"echo $! > {METADATA_PID})"
    )
    try:
        reply = node_run(f"sleep 1; curl -s -m 3 http://{METADATA_IP}/")
        assert reply.strip() == METADATA_MARK, reply
        yield METADATA_MARK
    finally:
        node_run(stop)
        left = node_run(f"ip -4 addr show dev lo | grep -c {METADATA_IP} || true")
        assert left.strip() == "0", f"{METADATA_IP} left on the node's loopback"


def test_h01_metadata_address_denied(sidecar: dict[str, Any], metadata_listener: str) -> None:
    """H01, criterion 5: the control refuses egress to 169.254.169.254: no live NetworkPolicy in a
    PoC-5 namespace allows it (no `ipBlock` holds it, no rule lacks a `to`), so each namespace's
    default deny applies, and the workload's connection to a live listener there is dropped (a
    timeout), while its allowed edge connects. The allowed controls: a pod with no egress policy
    (`default`) gets the listener's reply from the same address in the same test, so something
    answers there; and the live policies are there (every PoC-5 namespace has `default-deny`)."""
    namespaces = {
        p["metadata"]["namespace"]
        for p in get_json("networkpolicy", "-A")["items"]
        if p["metadata"]["name"] == "default-deny"
    }
    assert {"poc05-agents", "poc05-platform", "poc05-remote", "poc05-tools"} <= namespaces
    assert policy_peers_covering(METADATA_IP) == []

    url = f"http://{METADATA_IP}/"
    meta, fetched, allowed = probe(
        AGENTS_NS,
        sidecar["metadata"]["name"],
        WORKLOAD,
        [tcp(str(METADATA_IP), 80), {"kind": "http", "url": url}, allowed_edge()],
    )
    assert meta.get("error") == POLICY_DROPPED, meta
    assert "status" not in fetched, fetched
    assert allowed.get("connected") is True, allowed

    with unpoliced_caller() as caller:
        reached, answered = in_caller(
            caller, [tcp(str(METADATA_IP), 80), {"kind": "http", "url": url}]
        )
    assert reached.get("connected") is True, reached
    assert answered.get("status") == 200 and metadata_listener in answered["head"], answered
