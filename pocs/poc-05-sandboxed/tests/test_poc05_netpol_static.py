"""PoC-5 NetworkPolicy static test, offline (plan `docs/plans/2026-10-02-poc-05-sandboxed.md`,
section 2.10; task T20).

Exit criterion 5 (offline part): "an agent pod has no route to the internal services except
through the chassis". Exit criterion 6 (offline part): "the remote pod holds no way out but its
chassis, and the code runner holds none at all". The packets are tested on kind (T19, T21); this
file is the only offline control behind the claims that the cloud metadata address
(169.254.169.254) and the Kubernetes API (the Service IP and the node on 6443) are not reachable.

Why those claims follow from these checks. A NetworkPolicy peer is an `ipBlock`, a pod selector, or
a namespace selector. The metadata service and the API server are not pods in a PoC-5 namespace.
So only an `ipBlock`, or a rule that names no peer (egress with no `to`, which allows every
destination), or a peer selecting everything in every namespace, could allow them. The tests
refuse all three, refuse any other policy kind (a CNI-specific policy could carry a CIDR), and
require the namespace default deny. What is left is the edges in `fixtures/netpol_edges.yaml`,
each between pods this repo defines (plus kube-dns).

`agent-sandbox-system` (the vendored controller, `base/agent-sandbox/`) is excluded: it needs the
API server and has no default deny, as `base/default-deny.yaml` says. The probe pods the plan
table lists (`agent-probe-sidecar`, `chassis-probe-remote`, `remote-probe`) are not built, so the
edges file covers what exists.
"""

from __future__ import annotations

import copy
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
POC05 = ROOT / "deploy/kind/poc05"
EDGES = Path(__file__).resolve().parent / "fixtures/netpol_edges.yaml"

NAME = "app.kubernetes.io/name"
NS_LABEL = "kubernetes.io/metadata.name"
POD_KINDS = {"Deployment", "StatefulSet", "DaemonSet", "Job", "Pod", "Sandbox"}
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


def _yaml_files(excluded: tuple[str, ...]) -> Iterator[Path]:
    for path in sorted(POC05.rglob("*.yaml")):
        rel = path.relative_to(POC05).as_posix()
        if rel.startswith("base/agent-sandbox/") or rel.startswith(excluded):
            continue
        yield path


def _docs(path: Path) -> list[Doc]:
    return [d for d in yaml.safe_load_all(path.read_text()) if isinstance(d, dict)]


def _dir_namespace(path: Path) -> str | None:
    kustomization = path.parent / "kustomization.yaml"
    if not kustomization.exists():
        return None
    return dict(yaml.safe_load(kustomization.read_text())).get("namespace")


def _doc_namespace(path: Path, doc: Doc) -> str:
    ns = doc.get("metadata", {}).get("namespace") or _dir_namespace(path)
    assert ns, (
        f"{path.relative_to(POC05)}: {doc['kind']}/{doc['metadata']['name']} has no namespace"
    )
    return str(ns)


def load_policies() -> list[Policy]:
    out: list[Policy] = []
    for path in _yaml_files(()):
        for doc in _docs(path):
            if doc.get("kind", "").endswith("NetworkPolicy"):
                out.append(
                    Policy(
                        _doc_namespace(path, doc),
                        str(doc["metadata"]["name"]),
                        doc,
                        path.relative_to(POC05).as_posix(),
                    )
                )
    return out


def _template_labels(doc: Doc) -> dict[str, str]:
    kind = doc["kind"]
    if kind == "Pod":
        return dict(doc["metadata"].get("labels", {}))
    template = doc["spec"]["podTemplate"] if kind == "Sandbox" else doc["spec"]["template"]
    return dict(template.get("metadata", {}).get("labels", {}))


def load_pods() -> list[Pod]:
    out: list[Pod] = []
    for path in _yaml_files(("admission/",)):
        for doc in _docs(path):
            if doc.get("kind") in POD_KINDS:
                labels = tuple(sorted(_template_labels(doc).items()))
                out.append(Pod(_doc_namespace(path, doc), str(doc["metadata"]["name"]), labels))
    return out


def load_namespaces() -> dict[str, Doc]:
    docs = _docs(POC05 / "base/namespaces.yaml")
    return {str(d["metadata"]["name"]): d for d in docs if d["kind"] == "Namespace"}


EXPECT: Doc = yaml.safe_load(EDGES.read_text())


def _sel(raw: Doc) -> Sel:
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
    extra = set(raw) - {"podSelector", "namespaceSelector"}
    if extra:  # ipBlock is reported by `structure_problems`; anything else is not modeled
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


def _ports(raw_ports: list[Doc] | None, where: str, problems: list[str]) -> tuple[str, ...]:
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
                ports = _ports(raw.get("ports"), where, problems)
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


def _walk(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield str(key)
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def structure_problems(model: Model) -> list[str]:
    """No ipBlock, no other policy kind, no wide peer, no egress without a peer."""
    problems: list[str] = []
    for policy in model.policies:
        if policy.doc["kind"] != "NetworkPolicy":
            problems.append(f"{policy.label()}: kind {policy.doc['kind']} is not modeled")
        if "ipBlock" in set(_walk(policy.doc)):
            problems.append(f"{policy.label()}: uses an ipBlock")
    parse_rules(model.policies, problems)
    return sorted(set(problems))


# ---- edges ------------------------------------------------------------------------------------

Edge = tuple[str, str, str]  # source, destination, port


def computed_edges(model: Model) -> tuple[set[Edge], set[Edge]]:
    """(egress edges, ingress edges) as the policies state them, by selector."""
    egress: set[Edge] = set()
    ingress: set[Edge] = set()
    for rule in parse_rules(model.policies, []):
        own = Sel(rule.policy.ns, _policy_labels(rule.policy))
        for peer in rule.peers or ():
            for port in rule.ports:
                if rule.direction == "Egress":
                    egress.add((str(own), str(peer), port))
                else:
                    ingress.add((str(peer), str(own), port))
        if rule.peers is None and rule.direction == "Ingress":
            for port in rule.ports:
                ingress.add(("any", str(own), port))
    return egress, ingress


def _policy_labels(policy: Policy) -> Labels:
    return tuple(sorted(((policy.spec.get("podSelector") or {}).get("matchLabels") or {}).items()))


def fixture_edges() -> tuple[set[Edge], set[Edge]]:
    def expand(entries: list[Doc]) -> set[Edge]:
        out: set[Edge] = set()
        for e in entries:
            src = "any" if e["src"] == "any" else str(_sel(e["src"]))
            out.update((src, str(_sel(e["dst"])), port) for port in e["ports"])
        return out

    return expand(EXPECT["egress"]), expand(EXPECT["ingress"])


def edge_diff(model: Model) -> list[str]:
    got_e, got_i = computed_edges(model)
    want_e, want_i = fixture_edges()
    out = []
    for name, got, want in (("egress", got_e, want_e), ("ingress", got_i, want_i)):
        out += [f"extra {name} (in manifests, not in fixture): {e}" for e in sorted(got - want)]
        out += [f"missing {name} (in fixture, not in manifests): {e}" for e in sorted(want - got)]
    return out


# ---- default deny -----------------------------------------------------------------------------


def default_deny_problems(model: Model) -> list[str]:
    workload_ns = {p.ns for p in model.pods}
    problems = [
        f"namespace {ns} holds pods but is not in base/namespaces.yaml"
        for ns in sorted(workload_ns - set(model.namespaces))
    ]
    for ns in sorted(model.namespaces):
        denies = [p for p in model.policies if p.ns == ns and p.name == "default-deny"]
        if len(denies) != 1:
            problems.append(f"{ns}: expected one default-deny policy, found {len(denies)}")
            continue
        spec = denies[0].spec
        if spec.get("podSelector") != {}:
            problems.append(f"{ns}: default-deny must select all pods (podSelector: {{}})")
        if set(spec.get("policyTypes") or []) != BOTH:
            problems.append(f"{ns}: default-deny must name both Ingress and Egress")
        if spec.get("ingress") or spec.get("egress"):
            problems.append(f"{ns}: default-deny must have no allow rules")
    return problems


# ---- concrete resolution (which pods a selector reaches) --------------------------------------


def _selected(model: Model, policy: Policy) -> list[Pod]:
    sel = Sel(policy.ns, _policy_labels(policy))
    return [p for p in model.pods + model.externals if sel.matches(p)]


def _port_ok(rule: Rule, port: str) -> bool:
    return ANY_PORT in rule.ports or port in rule.ports


def _rules(model: Model) -> list[Rule]:
    return parse_rules(model.policies, [])


def egress_allowed(model: Model, rules: list[Rule], src: Pod, dst: Pod, port: str) -> bool:
    return any(
        r.direction == "Egress"
        and src in _selected(model, r.policy)
        and any(peer.matches(dst) for peer in r.peers or ())
        and _port_ok(r, port)
        for r in rules
    )


def ingress_allowed(model: Model, rules: list[Rule], src: Pod, dst: Pod, port: str) -> bool:
    return any(
        r.direction == "Ingress"
        and dst in _selected(model, r.policy)
        and (r.peers is None or any(peer.matches(src) for peer in r.peers))
        and _port_ok(r, port)
        for r in rules
    )


def concrete_egress(model: Model, src: Pod) -> set[tuple[Pod, str]]:
    out: set[tuple[Pod, str]] = set()
    for r in _rules(model):
        if r.direction == "Egress" and src in _selected(model, r.policy):
            for peer in r.peers or ():
                out.update(
                    (d, port)
                    for d in model.pods + model.externals
                    if peer.matches(d)
                    for port in r.ports
                )
    return out


def concrete_ingress(model: Model, dst: Pod) -> set[tuple[Pod | None, str]]:
    """Who may send to `dst`: (source pod, or None for any source, port)."""
    out: set[tuple[Pod | None, str]] = set()
    for r in _rules(model):
        if r.direction == "Ingress" and dst in _selected(model, r.policy):
            if r.peers is None:
                out.update((None, port) for port in r.ports)
            for peer in r.peers or ():
                out.update((s, port) for s in model.pods if peer.matches(s) for port in r.ports)
    return out


# ---- matching sides and dead selectors --------------------------------------------------------


def one_sided_problems(model: Model) -> list[str]:
    rules = _rules(model)
    problems: set[str] = set()
    for rule in rules:
        selected = [p for p in _selected(model, rule.policy) if p in model.pods]
        for peer in rule.peers or ():
            others = [p for p in model.pods if peer.matches(p)]
            for port in rule.ports:
                for mine in selected:
                    for other in others:
                        if rule.direction == "Egress":
                            if not ingress_allowed(model, rules, mine, other, port):
                                problems.add(f"egress {mine} -> {other} {port}: no ingress side")
                        elif not egress_allowed(model, rules, other, mine, port):
                            problems.add(f"ingress {other} -> {mine} {port}: no egress side")
    return sorted(problems)


def dead_selector_problems(model: Model) -> list[str]:
    """A selector that no pod template carries allows nothing, silently (a typo)."""
    problems: set[str] = set()
    for rule in _rules(model):
        for peer in rule.peers or ():
            if not any(peer.matches(p) for p in model.pods + model.externals):
                problems.add(f"{rule.policy.label()}: peer {peer} matches no pod template")
    for policy in model.policies:
        if _policy_labels(policy) and not _selected(model, policy):
            sel = Sel(policy.ns, _policy_labels(policy))
            problems.add(f"{policy.label()}: podSelector {sel} matches no pod template")
    return sorted(problems)


# ---- DNS --------------------------------------------------------------------------------------


def dns_problems(model: Model) -> list[str]:
    allowed = set(EXPECT["dns_allowed"])
    dns_pods = {p for p in model.externals if dict(p.labels).get("k8s-app") == "kube-dns"}
    got: set[str] = set()
    problems = []
    for pod in model.pods:
        for dst, port in concrete_egress(model, pod):
            number = port.split("/")[-1]
            if dst in dns_pods:
                if number == "53":
                    got.add(pod.app)
                else:
                    problems.append(f"{pod}: reaches kube-dns on {port}, not 53")
            elif number == "53" or number == ANY_PORT:
                problems.append(f"{pod}: port {port} to {dst}, DNS only goes to kube-dns")
    problems += [f"{a}: has DNS but the fixture does not list it" for a in sorted(got - allowed)]
    problems += [f"{a}: fixture lists DNS but no policy allows it" for a in sorted(allowed - got)]
    for pod in model.pods:
        if pod.app in {"remote-echo", "code-runner"} and pod.app in got:
            problems.append(f"{pod}: the remote sandbox and the code runner have no DNS")
    return problems


# ---- the remote sandbox and the code runner ---------------------------------------------------


def _chassis_ports(model: Model, chassis: Pod) -> set[int]:
    ports: set[int] = set()
    for path in _yaml_files(("admission/",)):
        for doc in _docs(path):
            if doc.get("kind") == "Deployment" and doc["metadata"]["name"] == chassis.name:
                for c in doc["spec"]["template"]["spec"]["containers"]:
                    ports.update(int(p["containerPort"]) for p in c.get("ports", []))
    return ports


def isolation_problems(model: Model) -> list[str]:
    problems: list[str] = []
    by_name = {p.name: p for p in model.pods}
    remotes = [p for p in model.pods if p.ns == "poc05-remote"]
    if not remotes:
        problems.append("no pod in poc05-remote")
    for remote in remotes:
        chassis = by_name.get(f"chassis-{remote.name.removeprefix('remote-')}-remote")
        if chassis is None:
            problems.append(f"{remote}: no chassis pod chassis-<name>-remote")
            continue
        listener = {p for p in _chassis_ports(model, chassis) if p == 8091}
        got_out = {(str(d), port) for d, port in concrete_egress(model, remote)}
        if got_out != {(str(chassis), "TCP/8091")}:
            problems.append(f"{remote}: egress must be only {chassis} TCP/8091, got {got_out}")
        if not listener:
            problems.append(f"{chassis}: does not declare the remote listener port 8091")
        got_in = {(str(s), port) for s, port in concrete_ingress(model, remote)}
        if got_in != {(str(chassis), "TCP/9000")}:
            problems.append(f"{remote}: ingress must be only {chassis} TCP/9000, got {got_in}")
        on_8091 = {(str(s), p) for s, p in concrete_ingress(model, chassis) if p.endswith("/8091")}
        if on_8091 != {(str(remote), "TCP/8091")}:
            problems.append(f"{chassis}: 8091 must take only {remote}, got {on_8091}")
    runners = [p for p in model.pods if p.ns == "poc05-tools"]
    if not runners:
        problems.append("no pod in poc05-tools")
    for runner in runners:
        if concrete_egress(model, runner):
            problems.append(f"{runner}: must have no egress, got {concrete_egress(model, runner)}")
        got_in = {(str(s), port) for s, port in concrete_ingress(model, runner)}
        want = {(str(p), "TCP/8000") for p in model.pods if p.app == "litellm"}
        if got_in != want:
            problems.append(f"{runner}: ingress must be only LiteLLM TCP/8000, got {got_in}")
    return problems


# ---- the tests --------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def model() -> Model:
    return load_model()


def test_poc05_netpol_default_deny_in_every_namespace(model: Model) -> None:
    """Exit criterion 5 (offline part): every PoC-5 namespace default-denies ingress and egress.

    Anything the later policies do not allow by label is refused, including the metadata address
    and the Kubernetes API.
    """
    assert len(model.namespaces) >= 6, "base/namespaces.yaml lost namespaces"
    assert default_deny_problems(model) == []


def test_poc05_netpol_no_rule_can_reach_metadata_or_api(model: Model) -> None:
    """Exit criterion 5 (offline part): no policy has an ipBlock or an any-destination rule.

    The metadata service (169.254.169.254) and the Kubernetes API (Service IP, node on 6443) are
    not pods in a PoC-5 namespace. Only an `ipBlock`, a peerless egress rule, or a peer selecting
    every namespace could allow them. All three fail here, so no rule can allow them.
    """
    assert any(p.ns == "poc05-agents" for p in model.policies)
    assert structure_problems(model) == []


def test_poc05_netpol_edges_equal_the_fixture(model: Model) -> None:
    """Exit criterion 5 (offline part): the allowed edges equal `fixtures/netpol_edges.yaml`.

    An extra edge fails and a missing edge fails, in both directions of every rule.
    """
    egress, ingress = computed_edges(model)
    assert len(egress) >= 15 and len(ingress) >= 10, "the policies were not read"
    assert edge_diff(model) == []


def test_poc05_netpol_dns_only_for_the_listed_pods(model: Model) -> None:
    """Exit criterion 6 (offline part): kube-dns on 53 only for the fixture's pods.

    The remote sandbox and the code runner have no DNS: a name lookup gets no answer.
    """
    assert dns_problems(model) == []
    assert {"remote-echo", "code-runner"}.isdisjoint(EXPECT["dns_allowed"])


def test_poc05_netpol_every_edge_has_both_sides(model: Model) -> None:
    """Exit criterion 5 (offline part): each egress edge has its ingress allowance, and back.

    A one-sided rule would pass here and fail at bring-up (default deny drops the packet).
    """
    assert one_sided_problems(model) == []


def test_poc05_netpol_no_selector_is_dead(model: Model) -> None:
    """Exit criterion 5 (offline part): every selector matches a pod template under poc05.

    A typo in a label allows nothing, silently, and the edge looks present in review.
    """
    assert dead_selector_problems(model) == []


def test_poc05_netpol_remote_and_code_runner_are_boxed_in(model: Model) -> None:
    """Exit criterion 6 (offline part): the remote has one way out and the runner has none.

    The remote sandbox's only egress is its chassis's remote listener port (8091); its only
    ingress is its chassis on 9000; the chassis takes 8091 only from that remote. The code runner
    has no egress and takes ingress only from LiteLLM.
    """
    assert isolation_problems(model) == []


# ---- the checks bite: each mutation of the loaded policies must fail its check ----------------


def _mutated(model: Model, policy_name: str, change: Any) -> Model:
    policies = []
    for p in model.policies:
        doc = copy.deepcopy(p.doc)
        if p.name == policy_name:
            change(doc["spec"])
        policies.append(Policy(p.ns, p.name, doc, p.source))
    return Model(policies, model.pods, model.externals, model.namespaces)


def test_poc05_netpol_checks_bite_ipblock(model: Model) -> None:
    """Exit criterion 5 (offline part): control passes, an added ipBlock fails the check."""
    assert structure_problems(model) == []
    bad = _mutated(
        model,
        "remote-echo",
        lambda s: s["egress"].append({"to": [{"ipBlock": {"cidr": "169.254.169.254/32"}}]}),
    )
    assert any("ipBlock" in p for p in structure_problems(bad))


def test_poc05_netpol_checks_bite_empty_peer(model: Model) -> None:
    """Exit criterion 5 (offline part): empty peers and peerless egress fail the checks."""
    assert structure_problems(model) == []
    wide = _mutated(
        model,
        "remote-echo",
        lambda s: s["egress"][0]["to"][0].update(podSelector={}),
    )
    assert any("every pod" in p for p in structure_problems(wide))
    open_egress = _mutated(
        model, "remote-echo", lambda s: s["egress"].append({"ports": [{"port": 80}]})
    )
    assert any("allows every destination" in p for p in structure_problems(open_egress))
    empty_to = _mutated(model, "remote-echo", lambda s: s["egress"].append({"to": [{}]}))
    assert any("peer keys" in p or "every pod" in p for p in structure_problems(empty_to))
    any_ns = _mutated(
        model,
        "remote-echo",
        lambda s: s["egress"][0]["to"][0].update(namespaceSelector={}),
    )
    assert any("namespaceSelector" in p for p in structure_problems(any_ns))


def test_poc05_netpol_checks_bite_extra_and_missing_edge(model: Model) -> None:
    """Exit criterion 5 (offline part): a changed port is both an extra and a missing edge."""
    assert edge_diff(model) == []
    bad = _mutated(
        model,
        "postgres",
        lambda s: s["ingress"][0]["ports"].append({"port": 22, "protocol": "TCP"}),
    )
    assert any(d.startswith("extra ingress") for d in edge_diff(bad))
    gone = _mutated(model, "postgres", lambda s: s["ingress"].clear())
    assert any(d.startswith("missing ingress") for d in edge_diff(gone))


def test_poc05_netpol_checks_bite_one_sided_and_dead(model: Model) -> None:
    """Exit criterion 5 (offline part): a removed ingress side and a typo selector both fail."""
    assert one_sided_problems(model) == [] and dead_selector_problems(model) == []
    one_sided = _mutated(model, "postgres", lambda s: s["ingress"].clear())
    assert any(
        "no ingress side" in p or "no egress side" in p for p in one_sided_problems(one_sided)
    )

    def typo(spec: Doc) -> None:
        spec["ingress"][0]["from"][0]["podSelector"]["matchLabels"] = {NAME: "litelm"}

    dead = _mutated(model, "postgres", typo)
    assert any("matches no pod template" in p for p in dead_selector_problems(dead))


def test_poc05_netpol_checks_bite_remote_dns_and_runner_egress(model: Model) -> None:
    """Exit criterion 6 (offline part): DNS on the remote and any runner egress fail."""
    assert dns_problems(model) == [] and isolation_problems(model) == []
    dns = {
        "to": [
            {
                "namespaceSelector": {"matchLabels": {NS_LABEL: "kube-system"}},
                "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
            }
        ],
        "ports": [{"port": 53, "protocol": "UDP"}],
    }
    remote_dns = _mutated(model, "remote-echo", lambda s: s["egress"].append(dns))
    assert any("remote-echo" in p for p in dns_problems(remote_dns))
    assert isolation_problems(remote_dns)
    runner = _mutated(model, "code-runner", lambda s: s.update(egress=[copy.deepcopy(dns)]))
    assert isolation_problems(runner)
    other = _mutated(
        model,
        "code-runner",
        lambda s: s["ingress"][0]["from"][0]["podSelector"]["matchLabels"].update(
            {NAME: "postgres"}
        ),
    )
    assert isolation_problems(other)
