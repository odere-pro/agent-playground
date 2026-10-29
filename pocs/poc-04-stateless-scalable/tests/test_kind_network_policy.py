"""PoC-4 kind NetworkPolicies, read offline: default deny and the agent pod's egress allow list.

`deploy/kind/poc04/base/network-policy.yaml` (deploy/CLAUDE.md: default-deny egress per pod, only
what the chassis needs). Every pod in `poc04` is denied in and out; the agent pod (chassis plus
workload) may call the fake model server on 8081, Valkey on 6379, and kube-dns on 53; no rule
names an `ipBlock`, so the cloud metadata address and the Kubernetes API are not reachable. A
NetworkPolicy peer is an `ipBlock`, a pod selector, or a namespace selector; those two are not
pods this repo selects, so only an `ipBlock` or a peerless rule could allow them.

Checked on the output of `kubectl kustomize` for each variant (a local render: no cluster, no
socket), as `test_read_only_kind.py` does; skipped when `kubectl` is not on PATH. The packets are
the kind drill's job (`test_kind.py`, `POC04_KIND=1`).
"""

from __future__ import annotations

import shutil
import subprocess
from functools import cache
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
POC04 = ROOT / "deploy/kind/poc04"
OVERLAYS = ("native-sidecar", "prestop")
NAMESPACE = "poc04"
NAME = "app.kubernetes.io/name"
BOTH = ["Ingress", "Egress"]
DNS = (("kubernetes.io/metadata.name", "kube-system"), ("k8s-app", "kube-dns"))
AGENT_EGRESS = {
    ((), ((NAME, "fake-model-server"),), (("TCP", 8081),)),
    ((), ((NAME, "valkey"),), (("TCP", 6379),)),
    ((DNS[0],), (DNS[1],), (("TCP", 53), ("UDP", 53))),
}
"""(namespace labels, pod labels, sorted (protocol, port)) per egress peer of the agent pod."""

Doc = dict[str, Any]
Peer = tuple[tuple[tuple[str, str], ...], tuple[tuple[str, str], ...], tuple[tuple[str, int], ...]]


@cache
def _render(overlay: str) -> tuple[Doc, ...]:
    kubectl = shutil.which("kubectl")
    if kubectl is None:
        pytest.skip("kubectl is not on PATH: the rendered check needs `kubectl kustomize`")
    out = subprocess.run(
        [kubectl, "kustomize", str(POC04 / overlay)], check=True, capture_output=True, text=True
    ).stdout
    return tuple(d for d in yaml.safe_load_all(out) if d)


def _policies(overlay: str) -> list[Doc]:
    return [d for d in _render(overlay) if d["kind"] == "NetworkPolicy"]


def _labels(selector: Doc | None) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(((selector or {}).get("matchLabels") or {}).items()))


def _egress_peers(policy: Doc) -> set[Peer]:
    """Every egress rule as (namespace labels, pod labels, ports), one entry per peer."""
    peers: set[Peer] = set()
    for rule in policy["spec"].get("egress") or []:
        ports = tuple(sorted((p.get("protocol", "TCP"), p["port"]) for p in rule.get("ports", [])))
        for peer in rule.get("to") or [{}]:
            peers |= {
                (_labels(peer.get("namespaceSelector")), _labels(peer.get("podSelector")), ports)
            }
    return peers


def _walk_keys(node: Any) -> list[str]:
    if isinstance(node, dict):
        return [str(k) for k in node] + [k for v in node.values() for k in _walk_keys(v)]
    if isinstance(node, list):
        return [k for item in node for k in _walk_keys(item)]
    return []


@pytest.mark.parametrize("overlay", OVERLAYS)
def test_network_policy_denies_every_pod_both_ways_when_overlay_is_rendered(overlay: str) -> None:
    """One policy selects every pod in poc04, covers both directions, and allows nothing."""
    # Arrange
    policies = _policies(overlay)

    # Act
    deny_all = [
        p
        for p in policies
        if p["spec"].get("podSelector") == {}
        and sorted(p["spec"].get("policyTypes") or []) == sorted(BOTH)
        and not p["spec"].get("ingress")
        and not p["spec"].get("egress")
    ]

    # Assert
    assert len(deny_all) == 1, [p["metadata"]["name"] for p in policies]
    assert deny_all[0]["metadata"].get("namespace") == NAMESPACE


@pytest.mark.parametrize("overlay", OVERLAYS)
def test_agent_egress_allows_only_model_valkey_and_dns_when_overlay_is_rendered(
    overlay: str,
) -> None:
    """The agent pod's egress is exactly the fake model server, Valkey, and kube-dns."""
    # Arrange
    policies = _policies(overlay)

    # Act
    by_selected = {_labels(p["spec"].get("podSelector")): p for p in policies}
    agent = by_selected.get(((NAME, "agent"),))
    others_with_egress = sorted(
        p["metadata"]["name"] for p in policies if p is not agent and p["spec"].get("egress")
    )

    # Assert
    assert agent is not None, "no policy selects the agent pod"
    assert "Egress" in agent["spec"]["policyTypes"]
    assert _egress_peers(agent) == AGENT_EGRESS
    assert others_with_egress == []


@pytest.mark.parametrize("overlay", OVERLAYS)
def test_network_policy_names_no_ip_block_when_overlay_is_rendered(overlay: str) -> None:
    """No rule in any policy has an `ipBlock`, so no address outside the pods is allowed."""
    # Arrange
    policies = _policies(overlay)

    # Act
    with_ip_block = sorted(p["metadata"]["name"] for p in policies if "ipBlock" in _walk_keys(p))

    # Assert
    assert policies, f"{overlay}: no NetworkPolicy rendered"
    assert with_ip_block == []
