"""The boundary rules the library chart carries, offline (the product plan
`docs/plans/2026-11-06-agent-container-product.md`, phase A; ADR-001 hard requirement 1 and item
9; ADR-005 decisions 4 and 5; backlog 026 CH-4 and 056 CH-7).

PoC-5 exit criterion 7: "The admission check rejects an `untrusted` workload, and a third-party
image, in the `sidecar` lane." The admission rules are rendered by `agent-lib.admission` through
the platform's chart `deploy/helm/agent-admission`: the trust-rule policy, its binding to every
namespace labeled `agents.platform/admission: enforce`, and its params. The policy is PoC-5's,
held equal to `deploy/kind/poc05/admission/policy.yaml` here, so the kind admission suite in CI
(`test_poc05_kind_admission.py`, every rule's fixture) proves the CEL the chart ships. A pod is
checked against the rules offline with the Python model of them (`poc_harness.admission_model`),
the one the PoC-5 static test runs. No value switches the rules off: a service chart has no
admission key, and the platform chart refuses one. The minimum chassis version (rule 9) comes
from the library chart's own tag list, so a service cannot widen it.

PoC-5 exit criterion 5: "From the workload container, the cloud metadata service, the Kubernetes
API, and the chassis's public port on localhost are unreachable." Default-deny egress is a chart
rule (026 CH-4): every pod the chart renders is selected by a NetworkPolicy with both policy
types, every egress rule names a peer by namespace and pod labels and a port (never an
`ipBlock`, never an empty selector), and each namespace the chart renders pods into gets a
default deny of its own. The chassis pod's egress stays the named-peer allow-list; in the
sidecar lane the workload shares the pod's network namespace, so that list is its egress too; a
remote workload pod reaches only its chassis's remote proxy (`test_poc09_chart.py`).

Skipped, with the reason, when `helm` is not on PATH; `.github/workflows/poc09-helm.yml`
installs a pinned helm and runs this file.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
from chart_render import (
    HELM_DIR,
    NAMESPACE,
    NEEDS_HELM,
    PLATFORM_NAMESPACE,
    RELEASE,
    REMOTE_NAMESPACE,
    REMOTE_VALUES,
    Charts,
    Doc,
    containers,
    one,
    values_file,
    walk,
)
from poc_harness.admission_manifests import RULES, TRUST
from poc_harness.admission_model import AdmissionParams, broken_rules
from poc_harness.manifests import load_manifests
from poc_harness.paths import KIND_POC05

pytestmark = NEEDS_HELM

POC05_POLICY = KIND_POC05 / "admission/policy.yaml"
POLICY = "agent-trust-rule"
PARAMS = "agent-trust-params"
ENFORCE = {"agents.platform/admission": "enforce"}
# The platform labels each namespace with the shape of the pods it holds (rule 7a).
SHAPES = {NAMESPACE: "chassis", REMOTE_NAMESPACE: "remote"}
WORKLOAD = f"{RELEASE}-workload"
# The sidecar lane as the kind drill runs it (pull-never, admission rule 8), and the remote lane
# with its images from the cluster store too.
KIND_VALUES = HELM_DIR / "example-agent/values-kind.yaml"
REMOTE_KIND_VALUES: dict[str, Any] = {
    "chassis": {
        "image": {"pullPolicy": "Never"},
        "config": {"spec": {"trust": "untrusted", "engine": {"connector": "remote"}}},
    },
    "workload": {"image": "kind.local/agent-platform/echo-python:poc09", "pullPolicy": "Never"},
    "remote": {"namespace": REMOTE_NAMESPACE, "proxyClusterIP": "10.96.85.92"},
}
PEER = {
    "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "platform"}},
    "podSelector": {"matchLabels": {"app.kubernetes.io/name": "litellm"}},
}
PORT = [{"port": 4000, "protocol": "TCP"}]


def _params(chart: Charts) -> AdmissionParams:
    return AdmissionParams.of(one(chart.template_admission(), "ConfigMap", PARAMS))


def _selects(policy: Doc, labels: dict[str, str]) -> bool:
    wanted = policy["spec"]["podSelector"].get("matchLabels", {})
    return all(labels.get(k) == v for k, v in wanted.items())


# --- admission: the rules the chart ships ----------------------------------------------------


def test_helm_lint_passes_on_the_admission_chart(chart: Charts) -> None:
    lint = chart.helm("lint", str(chart.admission))

    assert "0 chart(s) failed" in lint.stdout + lint.stderr


def test_the_admission_chart_renders_the_poc05_trust_rule_unchanged(chart: Charts) -> None:
    """Criterion 7: "The admission check rejects an `untrusted` workload, and a third-party image,
    in the `sidecar` lane."

    The policy the chart renders is the one PoC-5 proves on kind, rule for rule, variable for
    variable, so the two cannot drift and the kind evidence carries over."""
    (expected,) = [
        d for d in load_manifests(POC05_POLICY) if d["kind"] == "ValidatingAdmissionPolicy"
    ]

    policy = one(chart.template_admission(), "ValidatingAdmissionPolicy", POLICY)

    assert policy["apiVersion"] == expected["apiVersion"]
    assert policy["spec"] == expected["spec"]
    assert policy["spec"]["failurePolicy"] == "Fail"
    rules = [v["message"].split(":", 1)[0] for v in policy["spec"]["validations"]]
    assert rules == [f"trust rule {r}" for r in ("0", *RULES)]


def test_the_binding_denies_in_every_enforced_namespace_with_platform_params(
    chart: Charts,
) -> None:
    """Criterion 7: "The admission check rejects an `untrusted` workload, and a third-party image,
    in the `sidecar` lane."

    Deny in every namespace the platform labels; the params are the platform release's own
    ConfigMap, and a missing one denies everything (ADR-005 decision 5)."""
    docs = chart.template_admission()

    binding = one(docs, "ValidatingAdmissionPolicyBinding", POLICY)
    params = one(docs, "ConfigMap", PARAMS)

    spec = binding["spec"]
    assert spec["policyName"] == POLICY
    assert spec["validationActions"] == ["Deny", "Audit"]
    assert spec["matchResources"] == {"namespaceSelector": {"matchLabels": ENFORCE}}
    assert spec["paramRef"] == {
        "name": PARAMS,
        "namespace": PLATFORM_NAMESPACE,
        "parameterNotFoundAction": "Deny",
    }
    assert params["metadata"]["namespace"] == PLATFORM_NAMESPACE
    assert {d["kind"] for d in docs} == {
        "ValidatingAdmissionPolicy",
        "ValidatingAdmissionPolicyBinding",
        "ConfigMap",
    }


def test_the_params_list_the_trusted_repositories_and_the_library_chassis_tags(
    chart: Charts,
) -> None:
    """ADR-001 item 9: the chassis tag is set once, in the library chart. The params carry its
    supported tags (the current and the previous), so the minimum version a pod must run is the
    library's, and the tag the chart renders today is the first of them."""
    params = _params(chart)
    pod = one(chart.template(), "Deployment", RELEASE)["spec"]["template"]["spec"]

    tag = containers(pod)["chassis"]["image"].rsplit(":", 1)[1]

    assert params.prefix == "kind.local/agent-platform/"
    assert params.image_source == "pull-never"
    assert "kind.local/agent-platform/echo-python" in params.trusted
    assert params.chassis_tags[0] == tag
    assert len(params.chassis_tags) == 2, "the current tag and the previous one a service may pin"


@pytest.mark.parametrize(
    ("lane", "name"),
    [("sidecar", RELEASE), ("remote", RELEASE), ("remote", WORKLOAD)],
    ids=["sidecar chassis pod", "remote chassis pod", "remote workload pod"],
)
def test_every_pod_the_chart_renders_passes_the_chart_admission_rules(
    chart: Charts, tmp_path: Path, lane: str, name: str
) -> None:
    """Criterion 7: "The admission check rejects an `untrusted` workload, and a third-party image,
    in the `sidecar` lane."

    The paired control: the chart's own pods, in a namespace labeled with their shape, break no
    rule of the chart's own policy (the model of every rule, 0 to 9)."""
    values = KIND_VALUES if lane == "sidecar" else values_file(tmp_path, REMOTE_KIND_VALUES)
    deployment = one(chart.template(values), "Deployment", name)

    assert broken_rules(deployment, _params(chart), shapes=SHAPES) == set()


def test_an_untrusted_sidecar_pod_is_refused_at_render_and_by_rule_2(
    chart: Charts, tmp_path: Path
) -> None:
    """Criterion 7: "The admission check rejects an `untrusted` workload, and a third-party image,
    in the `sidecar` lane."

    Checked twice (ADR-005 decision 4): the render fails, and a sidecar pod that reached the API
    server with the untrusted label breaks exactly rule 2 of the chart's policy."""
    values = values_file(tmp_path, {"chassis": {"config": {"spec": {"trust": "untrusted"}}}})
    deployment = copy.deepcopy(one(chart.template(KIND_VALUES), "Deployment", RELEASE))
    deployment["spec"]["template"]["metadata"]["labels"][TRUST] = "untrusted"

    refused = chart.render(chart.example, RELEASE, NAMESPACE, values, check=False)

    assert refused.returncode != 0, refused.stdout
    assert "agent-lib:" in refused.stderr and "untrusted" in refused.stderr, refused.stderr
    assert broken_rules(deployment, _params(chart), shapes=SHAPES) == {"2"}


def test_a_chassis_below_the_minimum_version_is_refused_at_render_and_by_rule_9(
    chart: Charts, tmp_path: Path
) -> None:
    """ADR-001 item 9: a pod below the minimum chassis version is refused. The render refuses a
    tag outside the library's list, and a pod that reached the API server with it breaks exactly
    rule 9. Its twin on the current tag passes (the test above)."""
    values = values_file(tmp_path, {"chassis": {"image": {"tag": "poc07"}}})
    deployment = copy.deepcopy(one(chart.template(KIND_VALUES), "Deployment", RELEASE))
    chassis = deployment["spec"]["template"]["spec"]["initContainers"][0]
    chassis["image"] = chassis["image"].rsplit(":", 1)[0] + ":poc07"

    refused = chart.render(chart.example, RELEASE, NAMESPACE, values, check=False)

    assert refused.returncode != 0, refused.stdout
    assert "agent-lib:" in refused.stderr and "poc07" in refused.stderr, refused.stderr
    assert broken_rules(deployment, _params(chart), shapes=SHAPES) == {"9"}


# (chart, values, a word the agent-lib: message must carry)
REFUSED: dict[str, tuple[str, dict[str, Any], str]] = {
    "a service chart with an admission key": (
        "example",
        {"admission": {"enabled": False}},
        "admission",
    ),
    "the platform chart switching the rules off": (
        "admission",
        {"admission": {"enabled": False}},
        "admission.enabled",
    ),
    "a service widening the supported chassis tags": (
        "example",
        {"chassis": {"image": {"tag": "poc07", "supportedTags": ["poc07"]}}},
        "supportedTags",
    ),
    "a chassis pinned by digest alone": (
        "example",
        {"chassis": {"image": {"tag": "", "digest": "sha256:" + "0" * 64}}},
        "tag",
    ),
    "params with no trusted repository": (
        "admission",
        {"admission": {"params": {"trustedRepositories": []}}},
        "trustedRepositories",
    ),
    "params with a bare Docker Hub prefix": (
        "admission",
        {"admission": {"params": {"registryPrefix": "agent-platform/"}}},
        "registryPrefix",
    ),
    "params with a tag in a trusted repository": (
        "admission",
        {"admission": {"params": {"trustedRepositories": ["kind.local/agent-platform/echo:v1"]}}},
        "trustedRepositories",
    ),
    "params with an unknown image source": (
        "admission",
        {"admission": {"params": {"imageSource": "pull-always"}}},
        "imageSource",
    ),
    "egress peer with an empty namespace selector": (
        "example",
        {
            "networkPolicy": {
                "chassisEgress": [
                    {"to": [{**PEER, "namespaceSelector": {}}], "ports": PORT},
                ]
            }
        },
        "namespaceSelector",
    ),
    "egress peer without a pod selector": (
        "example",
        {
            "networkPolicy": {
                "chassisEgress": [
                    {"to": [{"namespaceSelector": PEER["namespaceSelector"]}], "ports": PORT},
                ]
            }
        },
        "podSelector",
    ),
    "egress rule without ports": (
        "example",
        {"networkPolicy": {"chassisEgress": [{"to": [PEER]}]}},
        "ports",
    ),
    "ingress peer with an empty pod selector": (
        "example",
        {
            "networkPolicy": {
                "ingressFrom": [
                    {
                        "namespaceSelector": {
                            "matchLabels": {"kubernetes.io/metadata.name": "callers"}
                        },
                        "podSelector": {},
                    }
                ]
            }
        },
        "podSelector",
    ),
}


@pytest.mark.parametrize("case", sorted(REFUSED))
def test_the_render_refuses_values_that_weaken_the_boundary(
    chart: Charts, tmp_path: Path, case: str
) -> None:
    """No value switches a rule off: the render fails with the chart's own `agent-lib:` message."""
    which, values, needle = REFUSED[case]
    path = values_file(tmp_path, values)
    target = chart.example if which == "example" else chart.admission
    release, namespace = (RELEASE, NAMESPACE) if which == "example" else ("x", PLATFORM_NAMESPACE)

    proc = chart.render(target, release, namespace, path, check=False)

    assert proc.returncode != 0, f"{case}: rendered, want a refusal:\n{proc.stdout}"
    assert "agent-lib:" in proc.stderr and needle in proc.stderr, proc.stderr


# --- egress: default deny is a chart rule ----------------------------------------------------


@pytest.mark.parametrize(
    ("lane", "name", "namespace"),
    [
        ("sidecar", RELEASE, NAMESPACE),
        ("remote", RELEASE, NAMESPACE),
        ("remote", WORKLOAD, REMOTE_NAMESPACE),
    ],
    ids=["sidecar chassis pod", "remote chassis pod", "remote workload pod"],
)
def test_every_pod_the_chart_renders_is_default_deny_both_ways(
    chart: Charts, tmp_path: Path, lane: str, name: str, namespace: str
) -> None:
    """Criterion 5: "From the workload container, the cloud metadata service, the Kubernetes API,
    and the chassis's public port on localhost are unreachable."

    026 CH-4: the pod's own policy names both directions, so anything it does not allow is
    refused; every egress rule names a peer by namespace and pod labels and a port; no `ipBlock`.
    Neither the metadata service nor the API server is a pod, so no rule can reach them."""
    docs = chart.template(None if lane == "sidecar" else values_file(tmp_path, REMOTE_VALUES))
    labels = one(docs, "Deployment", name)["spec"]["template"]["metadata"]["labels"]

    selecting = [
        p
        for p in docs
        if p["kind"] == "NetworkPolicy"
        and p["metadata"]["namespace"] == namespace
        and _selects(p, labels)
    ]
    own = one(docs, "NetworkPolicy", name)
    rules = own["spec"]["egress"]
    peers = [peer for rule in rules for peer in rule["to"]]

    assert sorted(p["metadata"]["name"] for p in selecting) == sorted(
        [name, f"{name}-default-deny"]
    )
    assert own["metadata"]["namespace"] == namespace
    assert own["spec"]["policyTypes"] == ["Ingress", "Egress"]
    assert rules and all(rule.get("to") and rule.get("ports") for rule in rules), rules
    assert all(
        peer["namespaceSelector"]["matchLabels"] and peer["podSelector"]["matchLabels"]
        for peer in peers
    ), peers
    assert not [k for k, _ in walk(own) if k == "ipBlock"]


@pytest.mark.parametrize(
    ("lane", "name", "namespace"),
    [
        ("sidecar", RELEASE, NAMESPACE),
        ("remote", RELEASE, NAMESPACE),
        ("remote", WORKLOAD, REMOTE_NAMESPACE),
    ],
    ids=["sidecar namespace", "remote lane, chassis namespace", "remote lane, workload namespace"],
)
def test_each_namespace_the_chart_uses_gets_a_default_deny(
    chart: Charts, tmp_path: Path, lane: str, name: str, namespace: str
) -> None:
    """Criterion 5: "From the workload container, the cloud metadata service, the Kubernetes API,
    and the chassis's public port on localhost are unreachable."

    The namespace's default deny is the chart's, not a cluster folder's (PoC-5 kept it in
    `deploy/kind/poc05/base/`): a pod in the namespace with no policy of its own has no egress."""
    docs = chart.template(None if lane == "sidecar" else values_file(tmp_path, REMOTE_VALUES))

    deny = one(docs, "NetworkPolicy", f"{name}-default-deny")
    namespaces = {d["metadata"]["namespace"] for d in docs if d["kind"] == "NetworkPolicy"}

    assert deny["metadata"]["namespace"] == namespace
    assert deny["spec"] == {"podSelector": {}, "policyTypes": ["Ingress", "Egress"]}
    assert namespaces == ({NAMESPACE} if lane == "sidecar" else {NAMESPACE, REMOTE_NAMESPACE})


def test_the_chassis_egress_is_the_named_allow_list_and_nothing_else(chart: Charts) -> None:
    """026 CH-4: DNS plus the peers the values name, each by namespace and pod label and a port.
    In the sidecar lane the workload shares the pod's network namespace, so this list is its
    egress too, and the services' own auth is the control (hard requirement 1)."""
    policy = one(chart.template(), "NetworkPolicy", RELEASE)

    edges = sorted(
        (
            peer["namespaceSelector"]["matchLabels"]["kubernetes.io/metadata.name"],
            next(iter(peer["podSelector"]["matchLabels"].values())),
            port["port"],
            port["protocol"],
        )
        for rule in policy["spec"]["egress"]
        for peer in rule["to"]
        for port in rule["ports"]
    )

    assert edges == [
        ("kube-system", "kube-dns", 53, "TCP"),
        ("kube-system", "kube-dns", 53, "UDP"),
        ("platform", "litellm", 4000, "TCP"),
    ]
