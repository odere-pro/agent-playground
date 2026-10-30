"""PoC-5 NetworkPolicy static test, offline (plan `docs/plans/2026-10-02-poc-05-sandboxed.md`,
section 2.10; task T20).

Exit criterion 5 (offline part): "an agent pod has no route to the internal services except
through the chassis". Exit criterion 6 (offline part): "the remote pod holds no way out but its
chassis, and the code runner holds none at all". Exit criterion 8 (offline part): the per-call
code-runner sandboxes take only the dispatcher and send nothing
(`docs/plans/2026-10-09-poc-05-per-call-sandbox.md`, section "NetworkPolicy"). The packets are
tested on kind (T19, T21); this file is the only offline control behind the claims that the cloud
metadata address (169.254.169.254) and the Kubernetes API (the Service IP and the node on 6443)
are not reachable, except the one recorded exception below.

Why those claims follow from these checks. A NetworkPolicy peer is an `ipBlock`, a pod selector, or
a namespace selector. The metadata service and the API server are not pods in a PoC-5 namespace.
So only an `ipBlock`, or a rule that names no peer (egress with no `to`, which allows every
destination), or a peer selecting everything in every namespace, could allow them. The tests
refuse all three, refuse any other policy kind (a CNI-specific policy could carry a CIDR), and
require the namespace default deny. What is left is the edges in `fixtures/netpol_edges.yaml`,
each between pods this repo defines (plus kube-dns).

The one exception is the `ipBlock` in policy `code-runner-dispatch` (poc05-platform): the
dispatcher's egress to the API server endpoint, `/32`, TCP 6443 only. The file in git holds the
sentinel `__API_SERVER_IP__/32`; `run.sh` fills in the `kubernetes` EndpointSlice address and
refuses the sentinel, a link-local address, and the pod and service ranges. Both halves are
checked here, the fill step by running it.

`agent-sandbox-system` (the vendored controller, `base/agent-sandbox/`) is excluded: it needs the
API server and has no default deny, as `base/default-deny.yaml` says. The probe pods the plan
table lists (`agent-probe-sidecar`, `chassis-probe-remote`, `remote-probe`) are not built, so the
edges file covers what exists.
"""

from __future__ import annotations

import copy
import re
import subprocess
from typing import Any

import pytest
import yaml
from poc_harness.manifests import Doc, load_manifests
from poc_harness.netpol_checks import (
    NO_DNS,
    computed_edges,
    dead_selector_problems,
    default_deny_problems,
    dns_problems,
    edge_diff,
    ip_block_problems,
    isolation_problems,
    one_sided_problems,
    selected_pods,
    structure_problems,
)
from poc_harness.netpol_model import EXPECT, NAME, NS_LABEL, SENTINEL, Model, Policy, load_model
from poc_harness.paths import KIND_POC05

RUN_SH = KIND_POC05 / "run.sh"
CLUSTER = KIND_POC05 / "cluster.yaml"


# ---- the tests --------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def model() -> Model:
    return load_model()


def test_poc05_netpol_default_deny_in_every_namespace(model: Model) -> None:
    """Criterion 5: "From the workload container, the cloud metadata service, the Kubernetes API,
    and the chassis's public port on localhost are unreachable."

    Exit criterion 5 (offline part): every PoC-5 namespace default-denies ingress and egress.

    Anything the later policies do not allow by label is refused, including the metadata address
    and the Kubernetes API.
    """
    assert len(model.namespaces) >= 6, "base/namespaces.yaml lost namespaces"
    assert default_deny_problems(model) == []


def test_poc05_netpol_no_rule_can_reach_metadata_or_api(model: Model) -> None:
    """Criterion 5: "From the workload container, the cloud metadata service, the Kubernetes API,
    and the chassis's public port on localhost are unreachable."

    Exit criterion 5 (offline part): no policy has an ipBlock but the dispatcher's, and no
    any-destination rule.

    The metadata service (169.254.169.254) and the Kubernetes API (Service IP, node on 6443) are
    not pods in a PoC-5 namespace. Only an `ipBlock`, a peerless egress rule, or a peer selecting
    every namespace could allow them. All three fail here, except the one ipBlock the next test
    pins down, so no other rule can allow them.
    """
    assert any(p.ns == "poc05-agents" for p in model.policies)
    assert structure_problems(model) == []


def test_poc05_netpol_one_ip_block_only_for_the_dispatcher(model: Model) -> None:
    """Criterion 8: "The hostile suites pass in both lanes."

    Exit criterion 8 (offline part): the one ipBlock in PoC-5 is the dispatcher's API egress.

    Exactly one ipBlock across every PoC-5 policy. It is in policy `code-runner-dispatch` in
    poc05-platform, whose podSelector selects only the dispatcher; its egress rule holds only that
    block, as a `/32`, with no `except`, on exactly TCP 6443. The file in git holds the sentinel.
    """
    (entry,) = EXPECT["ip_blocks"]
    assert entry["policy"] == {"ns": "poc05-platform", "name": "code-runner-dispatch"}
    assert entry["cidr"] == SENTINEL and entry["ports"] == ["TCP/6443"]
    assert ip_block_problems(model) == []
    (policy,) = [p for p in model.policies if p.name == "code-runner-dispatch"]
    assert policy.source == "platform/network-policy.yaml"
    assert SENTINEL in (KIND_POC05 / policy.source).read_text()
    (pod,) = selected_pods(model, policy)
    assert str(pod) == "poc05-platform/code-runner-dispatch"


def test_poc05_netpol_edges_equal_the_fixture(model: Model) -> None:
    """Criterion 5: "From the workload container, the cloud metadata service, the Kubernetes API,
    and the chassis's public port on localhost are unreachable."

    Exit criterion 5 (offline part): the allowed edges equal `fixtures/netpol_edges.yaml`.

    An extra edge fails and a missing edge fails, in both directions of every rule.
    """
    egress, ingress = computed_edges(model)
    assert len(egress) >= 15 and len(ingress) >= 10, "the policies were not read"
    assert edge_diff(model) == []


def test_poc05_netpol_dns_only_for_the_listed_pods(model: Model) -> None:
    """Criterion 6: "The remote pod reaches only the chassis's model and tool proxies, and only with
    its own credential."

    Exit criterion 6 (offline part): kube-dns on 53 only for the fixture's pods.

    The remote sandbox, the code runner, and its dispatcher have no DNS: a name lookup gets no
    answer. The dispatcher uses `KUBERNETES_SERVICE_HOST` and pod IPs.
    """
    assert dns_problems(model) == []
    assert NO_DNS.isdisjoint(EXPECT["dns_allowed"])


def test_poc05_netpol_every_edge_has_both_sides(model: Model) -> None:
    """Criterion 5: "From the workload container, the cloud metadata service, the Kubernetes API,
    and the chassis's public port on localhost are unreachable."

    Exit criterion 5 (offline part): each egress edge has its ingress allowance, and back.

    A one-sided rule would pass here and fail at bring-up (default deny drops the packet).
    """
    assert one_sided_problems(model) == []


def test_poc05_netpol_no_selector_is_dead(model: Model) -> None:
    """Criterion 5: "From the workload container, the cloud metadata service, the Kubernetes API,
    and the chassis's public port on localhost are unreachable."

    Exit criterion 5 (offline part): every selector matches a pod template under poc05.

    A typo in a label allows nothing, silently, and the edge looks present in review.
    """
    assert dead_selector_problems(model) == []


def test_poc05_netpol_remote_and_code_runner_are_boxed_in(model: Model) -> None:
    """Criterion 6: "The remote pod reaches only the chassis's model and tool proxies, and only with
    its own credential."

    Exit criterion 6 (offline part): the remote has one way out and the runner has none.

    The remote sandbox's only egress is its chassis's remote listener port (8091); its only
    ingress is its chassis on 9000; the chassis takes 8091 only from that remote. Each per-call
    code-runner sandbox has no egress and takes ingress only from the dispatcher; the dispatcher
    takes only LiteLLM and reaches only the sandboxes (and the API server, by its one ipBlock).
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
    """Criterion 5: "From the workload container, the cloud metadata service, the Kubernetes API,
    and the chassis's public port on localhost are unreachable."

    Exit criterion 5 (offline part): control passes, an added ipBlock fails the check."""
    assert structure_problems(model) == []
    bad = _mutated(
        model,
        "remote-echo",
        lambda s: s["egress"].append({"to": [{"ipBlock": {"cidr": "169.254.169.254/32"}}]}),
    )
    assert any("ipBlock" in p for p in structure_problems(bad))


def test_poc05_netpol_checks_bite_empty_peer(model: Model) -> None:
    """Criterion 5: "From the workload container, the cloud metadata service, the Kubernetes API,
    and the chassis's public port on localhost are unreachable."

    Exit criterion 5 (offline part): empty peers and peerless egress fail the checks."""
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
    """Criterion 5: "From the workload container, the cloud metadata service, the Kubernetes API,
    and the chassis's public port on localhost are unreachable."

    Exit criterion 5 (offline part): a changed port is both an extra and a missing edge."""
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
    """Criterion 5: "From the workload container, the cloud metadata service, the Kubernetes API,
    and the chassis's public port on localhost are unreachable."

    Exit criterion 5 (offline part): a removed ingress side and a typo selector both fail."""
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
    """Criterion 6: "The remote pod reaches only the chassis's model and tool proxies, and only with
    its own credential."

    Exit criterion 6 (offline part): DNS on the remote and any runner egress fail."""
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


def _api_rule(spec: Doc) -> Doc:
    """The dispatcher's API rule itself (not a copy), for the mutations to change."""
    rules: list[Doc] = [r for r in spec["egress"] if any("ipBlock" in p for p in r["to"])]
    (rule,) = rules
    return rule


def test_poc05_netpol_checks_bite_the_dispatcher_ip_block(model: Model) -> None:
    """Criterion 8: "The hostile suites pass in both lanes."

    Exit criterion 8 (offline part): control passes; each widening of the one ipBlock fails."""
    assert ip_block_problems(model) == []

    def bad(change: Any) -> list[str]:
        return ip_block_problems(_mutated(model, "code-runner-dispatch", change))

    assert bad(lambda s: _api_rule(s)["to"][0]["ipBlock"].update({"except": ["10.0.0.1/32"]}))
    assert bad(lambda s: _api_rule(s)["ports"].append({"port": 10250, "protocol": "TCP"}))
    assert bad(lambda s: _api_rule(s).pop("ports"))
    assert bad(lambda s: _api_rule(s)["to"][0]["ipBlock"].update(cidr="172.18.0.0/16"))
    assert bad(lambda s: _api_rule(s)["to"][0]["ipBlock"].update(cidr="172.18.0.2/32"))
    assert bad(lambda s: _api_rule(s)["to"].append({"ipBlock": {"cidr": SENTINEL}}))
    assert bad(
        lambda s: s["podSelector"].update(matchLabels={"app.kubernetes.io/part-of": "poc05"})
    )
    assert bad(lambda s: s.update(ingress=[{"from": [{"ipBlock": {"cidr": SENTINEL}}]}]))
    # An ipBlock in any other policy fails too, even with the dispatcher's rule intact.
    other = _mutated(
        model, "litellm", lambda s: s["egress"].append({"to": [{"ipBlock": {"cidr": SENTINEL}}]})
    )
    assert any("litellm: uses an ipBlock" in p for p in ip_block_problems(other))


# ---- run.sh fills the sentinel and refuses a bad address ---------------------------------------


def _fill(ip: str, text: str | None = None) -> subprocess.CompletedProcess[str]:
    """`run.sh api-ip-fill IP`: the fill step as a filter, stdin to stdout, no cluster call."""
    source = (KIND_POC05 / "platform/network-policy.yaml").read_text() if text is None else text
    return subprocess.run(
        ["bash", str(RUN_SH), "api-ip-fill", ip],
        input=source,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _cluster_subnets() -> dict[str, str]:
    networking = dict(yaml.safe_load(CLUSTER.read_text())["networking"])
    return {k: str(networking[k]) for k in ("podSubnet", "serviceSubnet")}


def test_poc05_run_sh_fills_the_api_server_ip() -> None:
    """Criterion 8: "The hostile suites pass in both lanes."

    Exit criterion 8 (offline part): the control. A node address replaces the sentinel, once."""
    out = _fill("172.18.0.2")
    assert out.returncode == 0, out.stderr
    assert SENTINEL not in out.stdout
    assert out.stdout.count("cidr: 172.18.0.2/32") == 1
    policies = [d for d in yaml.safe_load_all(out.stdout) if d]
    assert len(policies) == len(load_manifests(KIND_POC05 / "platform/network-policy.yaml"))


@pytest.mark.parametrize(
    ("ip", "why"),
    [
        ("", "empty"),
        ("__API_SERVER_IP__", "sentinel"),
        ("169.254.169.254", "link-local"),
        ("169.254.0.1", "link-local"),
        ("10.244.0.7", "pod range"),
        ("10.244.255.255", "pod range"),
        ("10.96.0.1", "service range"),
        ("10.96.200.3", "service range"),
        ("172.18.0.2/32", "not an address"),
        ("256.1.1.1", "not an address"),
        ("172.18.0", "not an address"),
        ("fd00::1", "not an address"),
        ("172.18.0.2 ", "not an address"),
    ],
)
def test_poc05_run_sh_refuses_a_bad_api_server_ip(ip: str, why: str) -> None:
    """Criterion 8: "The hostile suites pass in both lanes."

    Exit criterion 8 (offline part): `run.sh` never writes a sentinel, link-local, pod-range,
    or service-range block. Nothing reaches stdout, so nothing is applied."""
    out = _fill(ip)
    assert out.returncode != 0, f"{why}: {ip!r} was accepted"
    assert out.stdout == "", why
    assert "api-ip-fill" in out.stderr, out.stderr


def test_poc05_run_sh_refuses_input_without_exactly_one_sentinel() -> None:
    """A policy file whose sentinel was replaced by a literal address in git is refused."""
    filled = (
        (KIND_POC05 / "platform/network-policy.yaml").read_text().replace(SENTINEL, "1.2.3.4/32")
    )
    assert _fill("172.18.0.2", filled).returncode != 0
    twice = (KIND_POC05 / "platform/network-policy.yaml").read_text() + f"\n# {SENTINEL}\n"
    assert _fill("172.18.0.2", twice).returncode != 0


def test_poc05_run_sh_reads_the_ranges_from_cluster_yaml() -> None:
    """The pod and service ranges the fill refuses are the ones the cluster is created with."""
    assert _cluster_subnets() == {"podSubnet": "10.244.0.0/16", "serviceSubnet": "10.96.0.0/16"}
    text = RUN_SH.read_text()
    assert re.search(r"podSubnet", text) and re.search(r"serviceSubnet", text)
    assert "169.254.0.0/16" in text


def test_poc05_run_sh_applies_platform_only_through_the_fill() -> None:
    """`apply -k platform` would send the sentinel; every platform apply renders, then fills."""
    text = RUN_SH.read_text()
    assert not re.search(r"apply\s+-k\s+\"?\$HERE/platform", text), "platform applied unfilled"
    assert re.search(r"(?m)^render_platform\(\) \{", text)
    assert "fill_api_server_ip" in text
    assert not re.search(r"apply_folder\s+platform\b", text), "platform applied unfilled"
    assert "if [[ $1 == api-ip-fill ]]; then" in text, "run.sh has no api-ip-fill verb"
