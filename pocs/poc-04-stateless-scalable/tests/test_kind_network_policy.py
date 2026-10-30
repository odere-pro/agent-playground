"""PoC-4 kind NetworkPolicies, read offline: default deny and the agent pod's egress allow list.

`deploy/kind/poc04/base/network-policy.yaml` (deploy/CLAUDE.md: default-deny egress per pod, only
what the chassis needs). Every pod in `poc04` is denied in and out; the agent pod (chassis plus
workload) may call the fake model server on 8081, Valkey on 6379, and kube-dns on 53; no rule
names an `ipBlock`, so the cloud metadata address and the Kubernetes API are not reachable. A
NetworkPolicy peer is an `ipBlock`, a pod selector, or a namespace selector; those two are not
pods this repo selects, so only an `ipBlock` or a peerless rule could allow them.

The egress checker refuses anything wider than one labeled pod on one numbered port: an empty
selector (`{}` selects everything, unlike a missing `namespaceSelector`), any `matchExpressions`,
a peer key other than `podSelector` or `namespaceSelector`, a rule with no peer or no port, an
`endPort`, and a named port. The parsing mirrors PoC-5's `_peer` and `_ports`
(`pocs/poc-05-sandboxed/tests/test_poc05_netpol_static.py`), copied, not imported.

Open, not fixed here: DNS egress to kube-dns lets CoreDNS forward any query out of the cluster, so
a pod can tunnel data out in DNS names. PoC-5's threat model records it as H20
(`pocs/poc-05-sandboxed/notes/2026-10-02-threat-model.md`, the H20 row and its notes).

Checked on the raw `base/network-policy.yaml` always, and on the output of `kubectl kustomize` for
each variant and engine (a local render: no cluster, no socket) when `kubectl` is on PATH; without
it an overlay falls back to the raw file, as `test_read_only_kind.py` keeps a raw layer. The
packets are the kind drill's job (`test_kind.py`, `POC04_KIND=1`).
"""

from __future__ import annotations

import copy
import shutil
import subprocess
from collections.abc import Callable
from functools import cache
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
POC04 = ROOT / "deploy/kind/poc04"
BASE = POC04 / "base"
RAW = "base (raw)"
"""The source name for the unrendered `base/network-policy.yaml`."""
OVERLAYS = ("native-sidecar", "prestop", "typescript/native-sidecar", "typescript/prestop")
SOURCES = (RAW, *OVERLAYS)
NAMESPACE = "poc04"
NAME = "app.kubernetes.io/name"
BOTH = ["Ingress", "Egress"]
PEER_KEYS = {"podSelector", "namespaceSelector"}
SELECTOR_KEYS = {"matchLabels"}
DNS = (("kubernetes.io/metadata.name", "kube-system"), ("k8s-app", "kube-dns"))
AGENT_EGRESS = {
    ((), ((NAME, "fake-model-server"),), (("TCP", 8081),)),
    ((), ((NAME, "valkey"),), (("TCP", 6379),)),
    ((DNS[0],), (DNS[1],), (("TCP", 53), ("UDP", 53))),
}
"""(namespace labels, pod labels, sorted (protocol, port)) per egress peer of the agent pod."""

Doc = dict[str, Any]
Labels = tuple[tuple[str, str], ...]
Ports = tuple[tuple[str, int], ...]
Peer = tuple[Labels, Labels, Ports]


def _raw_base() -> tuple[Doc, ...]:
    """`base/network-policy.yaml` as kustomize would place it: in the base's namespace."""
    namespace = yaml.safe_load((BASE / "kustomization.yaml").read_text())["namespace"]
    docs = [d for d in yaml.safe_load_all((BASE / "network-policy.yaml").read_text()) if d]
    return tuple(
        {**d, "metadata": {**d["metadata"], "namespace": d["metadata"].get("namespace", namespace)}}
        for d in docs
    )


@cache
def _render(source: str) -> tuple[Doc, ...]:
    kubectl = shutil.which("kubectl")
    if source == RAW or kubectl is None:
        return _raw_base()
    out = subprocess.run(
        [kubectl, "kustomize", str(POC04 / source)], check=True, capture_output=True, text=True
    ).stdout
    return tuple(d for d in yaml.safe_load_all(out) if d)


def _policies(source: str) -> list[Doc]:
    return [d for d in _render(source) if d["kind"] == "NetworkPolicy"]


def _labels(selector: Doc | None) -> Labels:
    """The `matchLabels` of a policy's own `podSelector`, sorted; used to find a policy."""
    return tuple(sorted(((selector or {}).get("matchLabels") or {}).items()))


def _selector(selector: Any, where: str, problems: list[str]) -> Labels:
    """A peer selector's labels. `{}` selects everything and any `matchExpressions` widens it."""
    if not isinstance(selector, dict):
        problems.append(f"{where}: selector {selector!r} is not a mapping")
        return ()
    extra = sorted(set(selector) - SELECTOR_KEYS)
    if extra:
        problems.append(f"{where}: selector keys {extra} are not allowed")
    labels = tuple(sorted((selector.get("matchLabels") or {}).items()))
    if not labels:
        problems.append(f"{where}: an empty selector selects everything")
    return labels


def _peer(raw: Doc, where: str, problems: list[str]) -> tuple[Labels, Labels] | None:
    """(namespace labels, pod labels) of one peer; a missing `namespaceSelector` is `()`."""
    extra = sorted(set(raw) - PEER_KEYS)
    if extra:
        problems.append(f"{where}: peer keys {extra} are not allowed")
        return None
    if "podSelector" not in raw:
        problems.append(f"{where}: a peer with no podSelector selects every pod")
        return None
    ns = _selector(raw["namespaceSelector"], where, problems) if "namespaceSelector" in raw else ()
    return ns, _selector(raw["podSelector"], where, problems)


def _ports(raw_ports: list[Doc] | None, where: str, problems: list[str]) -> Ports:
    """Sorted (protocol, port); no port list, an `endPort`, or a named port is refused."""
    if not raw_ports:
        problems.append(f"{where}: a rule with no ports allows every port")
        return ()
    bad = [p for p in raw_ports if "endPort" in p or type(p.get("port")) is not int]
    problems.extend(f"{where}: port {p} must be one numbered port" for p in bad)
    return tuple(sorted((p.get("protocol", "TCP"), p["port"]) for p in raw_ports if p not in bad))


def egress_check(policy: Doc) -> tuple[set[Peer], list[str]]:
    """Every egress peer as (namespace labels, pod labels, ports), and every problem found."""
    peers: set[Peer] = set()
    problems: list[str] = []
    name = policy["metadata"]["name"]
    for index, rule in enumerate(policy["spec"].get("egress") or []):
        where = f"{name} egress[{index}]"
        ports = _ports(rule.get("ports"), where, problems)
        if not rule.get("to"):
            problems.append(f"{where}: a rule with no peer allows every address")
        for raw in rule.get("to") or []:
            peer = _peer(raw, where, problems)
            peers |= set() if peer is None else {(*peer, ports)}
    return peers, problems


def _walk_keys(node: Any) -> list[str]:
    if isinstance(node, dict):
        return [str(k) for k in node] + [k for v in node.values() for k in _walk_keys(v)]
    if isinstance(node, list):
        return [k for item in node for k in _walk_keys(item)]
    return []


def _agent(source: str) -> Doc | None:
    return {_labels(p["spec"].get("podSelector")): p for p in _policies(source)}.get(
        ((NAME, "agent"),)
    )


@pytest.mark.parametrize("source", SOURCES)
def test_network_policy_denies_every_pod_both_ways_when_overlay_is_rendered(source: str) -> None:
    """One policy selects every pod in poc04, covers both directions, and allows nothing."""
    # Arrange
    policies = _policies(source)

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


@pytest.mark.parametrize("source", SOURCES)
def test_agent_egress_allows_only_model_valkey_and_dns_when_overlay_is_rendered(
    source: str,
) -> None:
    """The agent pod's egress is exactly the fake model server, Valkey, and kube-dns."""
    # Arrange
    policies = _policies(source)
    agent = _agent(source)

    # Act
    peers, problems = egress_check(agent) if agent is not None else (set(), ["no agent policy"])
    others_with_egress = sorted(
        p["metadata"]["name"] for p in policies if p is not agent and p["spec"].get("egress")
    )

    # Assert
    assert agent is not None, "no policy selects the agent pod"
    assert "Egress" in agent["spec"]["policyTypes"]
    assert problems == []
    assert peers == AGENT_EGRESS
    assert others_with_egress == []


@pytest.mark.parametrize("source", SOURCES)
def test_network_policy_names_no_ip_block_when_overlay_is_rendered(source: str) -> None:
    """No rule in any policy has an `ipBlock`, so no address outside the pods is allowed."""
    # Arrange
    policies = _policies(source)

    # Act
    with_ip_block = sorted(p["metadata"]["name"] for p in policies if "ipBlock" in _walk_keys(p))

    # Assert
    assert policies, f"{source}: no NetworkPolicy rendered"
    assert with_ip_block == []


Widen = Callable[[list[Doc]], None]
"""Edits a deep copy of the agent policy's egress rules in place: 0 model, 1 Valkey, 2 DNS."""

WIDENED: dict[str, Widen] = {
    "empty-namespace-selector": lambda e: e[0]["to"][0].update(namespaceSelector={}),
    "empty-pod-selector": lambda e: e[1]["to"][0].update(podSelector={}),
    "match-expressions": lambda e: e[0]["to"][0]["podSelector"].update(
        matchExpressions=[{"key": NAME, "operator": "Exists"}]
    ),
    "namespace-match-expressions": lambda e: e[2]["to"][0]["namespaceSelector"].update(
        matchExpressions=[{"key": "kubernetes.io/metadata.name", "operator": "Exists"}]
    ),
    "end-port": lambda e: e[0]["ports"][0].update(endPort=65535),
    "named-port": lambda e: e[2]["ports"][0].update(port="dns"),
    "string-port": lambda e: e[1]["ports"][0].update(port="6379"),
    "ip-block-peer": lambda e: e[0]["to"].append({"ipBlock": {"cidr": "0.0.0.0/0"}}),
    "ip-block-beside-pod": lambda e: e[0]["to"][0].update(ipBlock={"cidr": "0.0.0.0/0"}),
    "no-pod-selector": lambda e: e[2]["to"][0].pop("podSelector"),
    "no-peer": lambda e: e[1].pop("to"),
    "no-ports": lambda e: e[1].pop("ports"),
}


@pytest.mark.parametrize("widen", WIDENED.values(), ids=WIDENED.keys())
def test_egress_check_refuses_policy_when_a_rule_is_widened(widen: Widen) -> None:
    """Each widening of the agent's egress is reported as a problem, not passed as equal."""
    # Arrange
    agent = _agent(RAW)
    assert agent is not None
    widened = copy.deepcopy(agent)
    widen(widened["spec"]["egress"])

    # Act
    _, problems = egress_check(widened)

    # Assert
    assert problems != []
