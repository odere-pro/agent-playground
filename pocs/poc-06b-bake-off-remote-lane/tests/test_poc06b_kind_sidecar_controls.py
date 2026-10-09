"""PoC-6b on kind, the sidecar lane: the PoC-5 sidecar controls
(`test_poc05_kind_sidecar_controls.py`, plan sections 2.10, 2.11; threat model H01 to H04, H13,
H25) for each new sidecar pod: `agent-openai-agents` and `agent-typescript`.

Exit criteria 1 and 4: the workload holds no service account token and no Secret, the metadata
address is denied, the Kubernetes API is unreachable from it, and the chassis's public port is
refused on loopback and served on the pod IP. Every refusal asserts its paired allowed control in
the same test, and a policy refusal expects a timeout (`POLICY_DROPPED`), never a refused
connection.

Reuse: the metadata stand-in listener, the policy scan, the node's API check, and the container and
Secret readers are PoC-5's, imported unchanged. The probe runs with Python or Node by the image
(`poc06b_kind.probe_in`); `agent-typescript`'s workload is Node, so its checks use `node_probe.js`.
The H26 `/proc` scan (the workload cannot read the chassis's environ) is not repeated: it reads the
process table with Python, and the pod-level fact it rests on, no shared PID namespace, is checked
on the spec.

A kind test: marked `kind` (and `network`), skipped unless `POC06_KIND=1` (`poc06b_conftest.py`).
Run: `deploy/kind/poc06/run.sh test`.
"""

from __future__ import annotations

from typing import Any

import pytest
from poc05_kind import (
    AGENTS_NS,
    CHASSIS,
    CHASSIS_SECRET_ENV,
    METADATA_IP,
    POLICY_DROPPED,
    WORKLOAD,
    get_json,
    in_caller,
    node_ip,
    pod,
    probe,
    service_ip,
    tcp,
    unpoliced_caller,
)
from poc06b_kind import SIDECARS, KindEngine, probe_in
from test_poc05_kind_sidecar_controls import (
    API_PORT,
    SA_DIR,
    allowed_edge,
    container,
    metadata_listener,  # noqa: F401  (a fixture, used by name below)
    node_reaches_its_api,
    policy_peers_covering,
    secret_refs,
)

pytestmark = pytest.mark.kind

ENGINE_IDS = [e.id for e in SIDECARS]


def sidecar_pod(engine: KindEngine) -> dict[str, Any]:
    return pod(AGENTS_NS, engine.pod)


def in_workload(
    engine: KindEngine, sidecar: dict[str, Any], checks: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    return probe_in(engine, sidecar["metadata"]["name"], checks)


@pytest.mark.parametrize("engine", SIDECARS, ids=ENGINE_IDS)
def test_no_service_account_token_in_the_workload(engine: KindEngine) -> None:
    """H02: the control refuses the workload a service account token: the pod spec turns automount
    off and the token directory does not exist in the workload container. The control: the same
    check sees a file the kubelet does mount there (/etc/hosts)."""
    sidecar = sidecar_pod(engine)
    assert sidecar["spec"].get("automountServiceAccountToken") is False
    token_dir, kubelet_file = in_workload(
        engine,
        sidecar,
        [{"kind": "path", "path": SA_DIR}, {"kind": "path", "path": "/etc/hosts"}],
    )
    assert token_dir == {"exists": False}
    assert kubelet_file == {"exists": True}


@pytest.mark.parametrize("engine", SIDECARS, ids=ENGINE_IDS)
def test_public_port_refused_on_loopback_served_on_the_pod_ip(engine: KindEngine) -> None:
    """H13, criterion 5: the control refuses the workload's connection to 127.0.0.1:8080 (the
    chassis binds its public port to the pod IP only); the control: the pod IP's /health answers
    200, and the proxies on 127.0.0.1:8090 answer too."""
    sidecar = sidecar_pod(engine)
    pod_ip = sidecar["status"]["podIP"]
    loop, public, proxy = in_workload(
        engine,
        sidecar,
        [
            {"kind": "tcp", "host": "127.0.0.1", "port": 8080},
            {"kind": "http", "url": f"http://{pod_ip}:8080/health"},
            {"kind": "tcp", "host": "127.0.0.1", "port": 8090},
        ],
    )
    assert loop == {"error": "ConnectionRefusedError"}, loop
    assert public["status"] == 200, public
    assert proxy.get("connected") is True, proxy


@pytest.mark.parametrize("engine", SIDECARS, ids=ENGINE_IDS)
def test_no_secret_value_reaches_the_workload(engine: KindEngine) -> None:
    """H25: the control refuses the workload any Secret: its container spec has no `secretKeyRef`,
    no `envFrom` Secret, and no Secret or token volume, and its live env holds none of the
    chassis's Secret-sourced names. The control: the chassis container's spec references both
    Secrets and its live env has both names. Names only; no value is read or printed. The pod does
    not share a PID namespace, so the workload cannot read the chassis's environ."""
    sidecar = sidecar_pod(engine)
    volumes = {v["name"]: v for v in sidecar["spec"].get("volumes", [])}
    assert secret_refs(container(sidecar, WORKLOAD), volumes) == []
    assert set(secret_refs(container(sidecar, CHASSIS), volumes)) == CHASSIS_SECRET_ENV
    assert sidecar["spec"].get("shareProcessNamespace") is not True

    (workload_env,) = in_workload(engine, sidecar, [{"kind": "env_names"}])
    (chassis_env,) = probe(AGENTS_NS, sidecar["metadata"]["name"], CHASSIS, [{"kind": "env_names"}])
    assert CHASSIS_SECRET_ENV.isdisjoint(workload_env["names"])
    assert set(chassis_env["names"]) >= CHASSIS_SECRET_ENV


@pytest.mark.parametrize("engine", SIDECARS, ids=ENGINE_IDS)
def test_kubernetes_api_unreachable_from_the_workload(engine: KindEngine) -> None:
    """H03 and H04: the control refuses the workload's connection to the Kubernetes API at the node
    IP on 6443 (H04) and at the Service VIP on 443 (H03). Both drops are timeouts. The controls:
    the node itself reaches the same address (/livez is ok), and the workload's allowed edge to
    LiteLLM connects in the same call."""
    sidecar = sidecar_pod(engine)
    ip = node_ip()
    vip = service_ip("default", "kubernetes")
    node_api, svc_api, allowed = in_workload(
        engine,
        sidecar,
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


@pytest.mark.parametrize("engine", SIDECARS, ids=ENGINE_IDS)
def test_metadata_address_denied(engine: KindEngine, metadata_listener: str) -> None:  # noqa: F811
    """H01: the control refuses egress to 169.254.169.254: no live NetworkPolicy in a PoC-5
    namespace (PoC-6 pods are in two of them) allows it, so each namespace's default deny
    applies, and the workload's connection to a live listener there is dropped (a timeout), while
    its allowed edge connects. The allowed controls: a pod with no egress policy (`default`) gets
    the listener's reply from the same address in the same test, and every PoC-5 namespace has
    its `default-deny`."""
    sidecar = sidecar_pod(engine)
    namespaces = {
        p["metadata"]["namespace"]
        for p in get_json("networkpolicy", "-A")["items"]
        if p["metadata"]["name"] == "default-deny"
    }
    assert {"poc05-agents", "poc05-platform", "poc05-remote", "poc05-tools"} <= namespaces
    assert policy_peers_covering(METADATA_IP) == []

    url = f"http://{METADATA_IP}/"
    meta, fetched, allowed = in_workload(
        engine,
        sidecar,
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
