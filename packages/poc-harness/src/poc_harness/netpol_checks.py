"""The checks over `poc_harness.netpol_model`: each returns a list of problems, empty when the
policies hold. The tests also feed them mutated models, so each check is shown to bite.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from poc_harness.manifests import Doc, load_manifests
from poc_harness.netpol_model import (
    ANY_PORT,
    BOTH,
    EXPECT,
    Labels,
    Model,
    Pod,
    Policy,
    Rule,
    Sel,
    parse_rules,
    rule_ports,
    selector_of,
    yaml_files,
)


def _walk(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield str(key)
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def structure_problems(model: Model) -> list[str]:
    """No ipBlock but the fixture's, no other policy kind, no wide peer, no peerless egress."""
    problems = [
        f"{policy.label()}: kind {policy.doc['kind']} is not modeled"
        for policy in model.policies
        if policy.doc["kind"] != "NetworkPolicy"
    ]
    problems += ip_block_problems(model)
    parse_rules(model.policies, problems)
    return sorted(set(problems))


# ---- the one ipBlock: the dispatcher to the API server ----------------------------------------


def _ip_block_rules(policy: Policy) -> list[tuple[str, Doc]]:
    """(direction, rule) for every rule of `policy` that has an ipBlock peer."""
    return [
        (direction, raw)
        for direction, rule_key, peer_key in (
            ("Ingress", "ingress", "from"),
            ("Egress", "egress", "to"),
        )
        for raw in policy.spec.get(rule_key) or []
        if any("ipBlock" in peer for peer in raw.get(peer_key) or [])
    ]


def ip_block_problems(model: Model) -> list[str]:
    """Exactly the fixture's ipBlocks: each in its policy, on its one pod, `/32`, its ports only."""
    wanted = {(e["policy"]["ns"], e["policy"]["name"]): e for e in EXPECT["ip_blocks"]}
    problems: list[str] = []
    total = sum(1 for p in model.policies for key in _walk(p.doc) if key == "ipBlock")
    if total != len(wanted):
        problems.append(f"expected {len(wanted)} ipBlock across every PoC-5 policy, found {total}")
    for policy in model.policies:
        rules = _ip_block_rules(policy)
        entry = wanted.get((policy.ns, policy.name))
        if entry is None:
            if rules or "ipBlock" in set(_walk(policy.doc)):
                problems.append(f"{policy.label()}: uses an ipBlock")
            continue
        problems += _one_ip_block_problems(model, policy, entry, rules)
    return problems


def _one_ip_block_problems(
    model: Model, policy: Policy, entry: Doc, rules: list[tuple[str, Doc]]
) -> list[str]:
    where = policy.label()
    problems: list[str] = []
    pod = selector_of(entry["pod"])
    if policy.ns != pod.ns or _policy_labels(policy) != pod.labels:
        problems.append(f"{where}: podSelector must be exactly {pod}")
    selected = selected_pods(model, policy)
    if len(selected) != 1:
        problems.append(f"{where}: selects {[str(p) for p in selected]}, want one pod")
    if not entry["cidr"].endswith("/32"):
        problems.append(f"{where}: fixture cidr {entry['cidr']} is not a /32")
    if len(rules) != 1:
        problems.append(f"{where}: {len(rules)} rules hold an ipBlock, want one")
    for direction, raw in rules:
        if direction != "Egress":
            problems.append(f"{where}: an ipBlock in an {direction} rule")
        if raw.get("to") != [{"ipBlock": {"cidr": entry["cidr"]}}]:
            problems.append(
                f"{where}: `to` must hold only ipBlock {entry['cidr']} with no except, "
                f"got {raw.get('to')}"
            )
        ports = rule_ports(raw.get("ports"), where, problems)
        if ports != tuple(entry["ports"]):
            problems.append(f"{where}: ports {ports}, want exactly {entry['ports']}")
    return problems


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
            src = "any" if e["src"] == "any" else str(selector_of(e["src"]))
            out.update((src, str(selector_of(e["dst"])), port) for port in e["ports"])
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


def selected_pods(model: Model, policy: Policy) -> list[Pod]:
    sel = Sel(policy.ns, _policy_labels(policy))
    return [p for p in model.pods + model.externals if sel.matches(p)]


def _port_ok(rule: Rule, port: str) -> bool:
    return ANY_PORT in rule.ports or port in rule.ports


def _rules(model: Model) -> list[Rule]:
    return parse_rules(model.policies, [])


def egress_allowed(model: Model, rules: list[Rule], src: Pod, dst: Pod, port: str) -> bool:
    return any(
        r.direction == "Egress"
        and src in selected_pods(model, r.policy)
        and any(peer.matches(dst) for peer in r.peers or ())
        and _port_ok(r, port)
        for r in rules
    )


def ingress_allowed(model: Model, rules: list[Rule], src: Pod, dst: Pod, port: str) -> bool:
    return any(
        r.direction == "Ingress"
        and dst in selected_pods(model, r.policy)
        and (r.peers is None or any(peer.matches(src) for peer in r.peers))
        and _port_ok(r, port)
        for r in rules
    )


def concrete_egress(model: Model, src: Pod) -> set[tuple[Pod, str]]:
    out: set[tuple[Pod, str]] = set()
    for r in _rules(model):
        if r.direction == "Egress" and src in selected_pods(model, r.policy):
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
        if r.direction == "Ingress" and dst in selected_pods(model, r.policy):
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
        selected = [p for p in selected_pods(model, rule.policy) if p in model.pods]
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
        if _policy_labels(policy) and not selected_pods(model, policy):
            sel = Sel(policy.ns, _policy_labels(policy))
            problems.add(f"{policy.label()}: podSelector {sel} matches no pod template")
    return sorted(problems)


# ---- DNS --------------------------------------------------------------------------------------


NO_DNS = {"remote-echo", "code-runner", "code-runner-dispatch"}


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
        if pod.app in NO_DNS and pod.app in got:
            problems.append(f"{pod}: the remote sandbox and the code runner have no DNS")
    return problems


# ---- the remote sandbox and the code runner ---------------------------------------------------


def _chassis_ports(model: Model, chassis: Pod) -> set[int]:
    ports: set[int] = set()
    for path in yaml_files(("admission/",)):
        for doc in load_manifests(path):
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
    return problems + _code_runner_problems(model)


def _apps(model: Model, app: str) -> set[tuple[str, str]]:
    return {(str(p), "TCP/8000") for p in model.pods if p.app == app}


def _code_runner_problems(model: Model) -> list[str]:
    """The per-call sandboxes: no egress, only the dispatcher in. The dispatcher: only LiteLLM in,
    only the sandboxes out (pods; its API server ipBlock is `ip_block_problems`)."""
    problems: list[str] = []
    runners = [p for p in model.pods if p.ns == "poc05-tools"]
    if not runners:
        problems.append("no pod in poc05-tools")
    for runner in runners:
        if concrete_egress(model, runner):
            problems.append(f"{runner}: must have no egress, got {concrete_egress(model, runner)}")
        got_in = {(str(s), port) for s, port in concrete_ingress(model, runner)}
        if got_in != _apps(model, "code-runner-dispatch"):
            problems.append(f"{runner}: ingress must be only the dispatcher TCP/8000, got {got_in}")
    dispatchers = [p for p in model.pods if p.app == "code-runner-dispatch"]
    if [str(p) for p in dispatchers] != ["poc05-platform/code-runner-dispatch"]:
        problems.append(f"want one dispatcher pod in poc05-platform, got {dispatchers}")
    for dispatcher in dispatchers:
        got_out = {(str(d), port) for d, port in concrete_egress(model, dispatcher)}
        if got_out != _apps(model, "code-runner"):
            problems.append(f"{dispatcher}: pod egress must be only the sandboxes, got {got_out}")
        got_in = {(str(s), port) for s, port in concrete_ingress(model, dispatcher)}
        if got_in != _apps(model, "litellm"):
            problems.append(f"{dispatcher}: ingress must be only LiteLLM TCP/8000, got {got_in}")
    return problems
