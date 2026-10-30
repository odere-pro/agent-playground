"""A model of the PoC-5 NetworkPolicies as written: the policies, the pods, and the rules, parsed
from the files under `deploy/kind/poc05/`. `poc_harness.netpol_checks` reads it.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from poc_harness.manifests import load_manifests
from poc_harness.paths import KIND_POC05, ROOT

EDGES = ROOT / "pocs/poc-05-sandboxed/tests/fixtures/netpol_edges.yaml"
SENTINEL = "__API_SERVER_IP__/32"

NAME = "app.kubernetes.io/name"
NS_LABEL = "kubernetes.io/metadata.name"
POD_KINDS = {"Deployment", "StatefulSet", "DaemonSet", "Job", "Pod", "Sandbox", "SandboxTemplate"}
POD_TEMPLATE_KINDS = {"Sandbox", "SandboxTemplate"}  # the pod template is `spec.podTemplate`
BOTH = {"Ingress", "Egress"}
ANY_PORT = "ANY"

Doc = dict[str, Any]
Labels = tuple[tuple[str, str], ...]


def _fmt(labels: Labels) -> str:
    return ",".join(f"{k}={v}" for k, v in labels) or "*"


@dataclass(frozen=True)
class Sel:
    """Namespace plus the pod labels a selector requires; no labels means every pod."""

    ns: str
    labels: Labels

    def __str__(self) -> str:
        return f"{self.ns}/{_fmt(self.labels)}"

    def matches(self, pod: Pod) -> bool:
        return pod.ns == self.ns and set(self.labels) <= set(pod.labels)


@dataclass(frozen=True)
class Pod:
    ns: str
    name: str
    labels: Labels

    @property
    def app(self) -> str:
        return dict(self.labels).get(NAME, self.name)

    def __str__(self) -> str:
        return f"{self.ns}/{self.name}"


@dataclass(frozen=True)
class Policy:
    ns: str
    name: str
    doc: Doc
    source: str

    @property
    def spec(self) -> Doc:
        return dict(self.doc["spec"])

    def label(self) -> str:
        return f"{self.source}:{self.ns}/{self.name}"


@dataclass(frozen=True)
class Rule:
    policy: Policy
    direction: str
    peers: tuple[Sel, ...] | None  # None: the rule names no peer (any source or destination)
    ports: tuple[str, ...]  # PROTO/NUMBER, or ANY


@dataclass
class Model:
    policies: list[Policy]
    pods: list[Pod]
    externals: list[Pod]
    namespaces: dict[str, Doc]


# ---- loading ----------------------------------------------------------------------------------


def yaml_files(excluded: tuple[str, ...]) -> Iterator[Path]:
    for path in sorted(KIND_POC05.rglob("*.yaml")):
        rel = path.relative_to(KIND_POC05).as_posix()
        if rel.startswith("base/agent-sandbox/") or rel.startswith(excluded):
            continue
        yield path


def _dir_namespace(path: Path) -> str | None:
    kustomization = path.parent / "kustomization.yaml"
    if not kustomization.exists():
        return None
    return dict(yaml.safe_load(kustomization.read_text())).get("namespace")


def _doc_namespace(path: Path, doc: Doc) -> str:
    ns = doc.get("metadata", {}).get("namespace") or _dir_namespace(path)
    assert ns, (
        f"{path.relative_to(KIND_POC05)}: {doc['kind']}/{doc['metadata']['name']} has no namespace"
    )
    return str(ns)


def load_policies() -> list[Policy]:
    out: list[Policy] = []
    for path in yaml_files(()):
        out.extend(
            Policy(
                _doc_namespace(path, doc),
                str(doc["metadata"]["name"]),
                doc,
                path.relative_to(KIND_POC05).as_posix(),
            )
            for doc in load_manifests(path)
            if doc.get("kind", "").endswith("NetworkPolicy")
        )
    return out


def _template_labels(doc: Doc) -> dict[str, str]:
    kind = doc["kind"]
    if kind == "Pod":
        return dict(doc["metadata"].get("labels", {}))
    template = doc["spec"]["podTemplate"] if kind in POD_TEMPLATE_KINDS else doc["spec"]["template"]
    return dict(template.get("metadata", {}).get("labels", {}))


def load_pods() -> list[Pod]:
    out: list[Pod] = []
    for path in yaml_files(("admission/",)):
        for doc in load_manifests(path):
            if doc.get("kind") in POD_KINDS:
                labels = tuple(sorted(_template_labels(doc).items()))
                out.append(Pod(_doc_namespace(path, doc), str(doc["metadata"]["name"]), labels))
    return out


def load_namespaces() -> dict[str, Doc]:
    docs = load_manifests(KIND_POC05 / "base/namespaces.yaml")
    return {str(d["metadata"]["name"]): d for d in docs if d["kind"] == "Namespace"}


EXPECT: Doc = yaml.safe_load(EDGES.read_text())


def selector_of(raw: Doc) -> Sel:
    return Sel(raw["ns"], tuple(sorted(raw["labels"].items())))


def load_model() -> Model:
    externals = [
        Pod(
            e["ns"],
            "external-" + "-".join(e["labels"].values()),
            tuple(sorted(e["labels"].items())),
        )
        for e in EXPECT["external_pods"]
    ]
    return Model(load_policies(), load_pods(), externals, load_namespaces())


# ---- parsing ----------------------------------------------------------------------------------


def _match_labels(selector: Doc | None, where: str, problems: list[str]) -> Labels:
    selector = selector or {}
    extra = set(selector) - {"matchLabels"}
    if extra:
        problems.append(f"{where}: unsupported selector keys {sorted(extra)}")
    return tuple(sorted((selector.get("matchLabels") or {}).items()))


def _peer(policy: Policy, raw: Doc, where: str, problems: list[str]) -> Sel | None:
    """One peer as a Sel. Records a problem and returns None for a peer that allows too much."""
    if set(raw) == {"ipBlock"}:  # checked by `ip_block_problems`, against the fixture
        return None
    extra = set(raw) - {"podSelector", "namespaceSelector"}
    if extra:  # anything else is not modeled
        problems.append(f"{where}: peer keys {sorted(extra)} are not allowed")
        return None
    ns = policy.ns
    if "namespaceSelector" in raw:
        ns_labels = dict(_match_labels(raw["namespaceSelector"], where, problems))
        if set(ns_labels) != {NS_LABEL}:
            problems.append(
                f"{where}: namespaceSelector must be exactly {NS_LABEL}=<name> "
                f"(an empty or wider one allows every namespace), got {ns_labels}"
            )
            return None
        ns = ns_labels[NS_LABEL]
    labels = _match_labels(raw.get("podSelector"), where, problems)
    if not labels:
        problems.append(f"{where}: peer selects every pod in {ns} (empty or missing podSelector)")
        return None
    return Sel(ns, labels)


def rule_ports(raw_ports: list[Doc] | None, where: str, problems: list[str]) -> tuple[str, ...]:
    if not raw_ports:
        return (ANY_PORT,)
    out = []
    for p in raw_ports:
        if "endPort" in p or not isinstance(p.get("port"), int):
            problems.append(f"{where}: port {p} must be one numbered port")
            continue
        out.append(f"{p.get('protocol', 'TCP')}/{p['port']}")
    return tuple(out)


def parse_rules(policies: list[Policy], problems: list[str]) -> list[Rule]:
    rules: list[Rule] = []
    for policy in policies:
        if policy.doc["kind"] != "NetworkPolicy":
            continue
        types = set(policy.spec.get("policyTypes") or [])
        for direction, rule_key, peer_key in (
            ("Ingress", "ingress", "from"),
            ("Egress", "egress", "to"),
        ):
            raw_rules = policy.spec.get(rule_key) or []
            if raw_rules and direction not in types:
                problems.append(f"{policy.label()}: {rule_key} rules but policyTypes lacks it")
            for i, raw in enumerate(raw_rules):
                where = f"{policy.label()} {rule_key}[{i}]"
                raw_peers = raw.get(peer_key) or []
                ports = rule_ports(raw.get("ports"), where, problems)
                if not raw_peers:
                    if direction == "Egress":
                        problems.append(
                            f"{where}: no `to` peer allows every destination (metadata, API)"
                        )
                    elif ports == (ANY_PORT,):
                        problems.append(f"{where}: no `from` and no ports allows everything")
                    rules.append(Rule(policy, direction, None, ports))
                    continue
                peers = [_peer(policy, p, where, problems) for p in raw_peers]
                rules.append(Rule(policy, direction, tuple(p for p in peers if p), ports))
    return rules
