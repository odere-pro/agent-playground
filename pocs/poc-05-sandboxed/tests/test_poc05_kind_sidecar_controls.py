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

A kind test: marked `network`, skipped unless `POC05_KIND=1` (`poc_harness.drills`). Run:
`POC05_KIND=1 deploy/kind/poc05/run.sh with-gateway uv run pytest -m network <this file>`.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from poc_harness.drills import POC05_KIND
from poc_harness.kind import (
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
    pod,
    probe,
    service_ip,
    tcp_check,
    unpoliced_caller,
)
from poc_harness.kind_controls import (
    API_PORT,
    SA_DIR,
    allowed_edge,
    container,
    metadata_stand_in,
    node_reaches_its_api,
    policy_peers_covering,
    secret_refs,
)

pytestmark = POC05_KIND

DISPATCH = "code-runner-dispatch"
# What the probe's `http` check returns when the connect fails: urllib raises URLError for a
# timeout and a refusal alike (AbstractHTTPHandler.do_open wraps every OSError).
HTTP_CONNECT_FAILED = "URLError"


@pytest.fixture(scope="module")
def sidecar() -> dict[str, Any]:
    return pod(AGENTS_NS, SIDECAR_POD)


def test_h02_no_service_account_token_in_the_workload(sidecar: dict[str, Any]) -> None:
    """Criterion 4: "No provider key or internal credential is visible in any workload container or
    remote pod, and neither holds a service account token."

    H02, criterion 4: the control refuses the workload a service account token: the pod spec
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
    """Criterion 5: "From the workload container, the cloud metadata service, the Kubernetes API,
    and the chassis's public port on localhost are unreachable."

    H13, kind half, criterion 5: the control refuses the workload's connection to
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
    """Criterion 4: "No provider key or internal credential is visible in any workload container or
    remote pod, and neither holds a service account token."

    H25, criterion 4: the control refuses the workload any Secret: its container spec has no
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
    """Criterion 4: "No provider key or internal credential is visible in any workload container or
    remote pod, and neither holds a service account token."

    H26, criterion 4: the control refuses the workload the chassis's `/proc/<pid>/environ`:
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


def test_h03_h04_kubernetes_api_unreachable_from_the_workload(sidecar: dict[str, Any]) -> None:
    """Criterion 5: "From the workload container, the cloud metadata service, the Kubernetes API,
    and the chassis's public port on localhost are unreachable."

    H03 and H04, criterion 5: the control refuses the workload's connection to the Kubernetes
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
    up = probe(PLATFORM_NS, dispatch, DISPATCH, [tcp_check(ip, API_PORT), tcp_check(vip, 443)])
    assert all(r.get("connected") is True for r in up), up


def test_h01_metadata_address_denied(sidecar: dict[str, Any], metadata_listener: str) -> None:
    """Criterion 5: "From the workload container, the cloud metadata service, the Kubernetes API,
    and the chassis's public port on localhost are unreachable."

    H01, criterion 5: the control refuses egress to 169.254.169.254: no live NetworkPolicy in a
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
        [tcp_check(str(METADATA_IP), 80), {"kind": "http", "url": url}, allowed_edge()],
    )
    assert meta == {"error": POLICY_DROPPED}, meta
    # urllib wraps a connect timeout in URLError, so the probe's HTTP check cannot name the
    # timeout; the TCP check above, to the same address and port, shows the drop is one.
    assert fetched == {"error": HTTP_CONNECT_FAILED}, fetched
    assert allowed.get("connected") is True, allowed

    with unpoliced_caller() as caller:
        reached, answered = in_caller(
            caller, [tcp_check(str(METADATA_IP), 80), {"kind": "http", "url": url}]
        )
    assert reached.get("connected") is True, reached
    assert answered.get("status") == 200 and metadata_listener in answered["head"], answered


@pytest.fixture
def metadata_listener() -> Iterator[str]:
    with metadata_stand_in() as mark:
        yield mark
