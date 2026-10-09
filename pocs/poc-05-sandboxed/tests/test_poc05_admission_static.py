"""PoC-5 admission, offline: the trust rule's ValidatingAdmissionPolicy, its binding, its params,
the submitter's and deployer's RBAC, and the paired fixtures under `deploy/kind/poc05/admission/`
(plan `docs/plans/2026-10-02-poc-05-sandboxed.md`, section 2.5; ADR-005 decisions 4 and 5; security
review `notes/2026-10-02-review-security.md` F1 to F3: rules 6, 7, and 8). The per-call sandbox
(plan `docs/plans/2026-10-09-poc-05-per-call-sandbox.md`, sections "RBAC" and "Admission", task 4)
adds the template rules T1 to T3, the claim rules C1 to C5, the dispatcher's claim role, the tools
quota, and drops the submitter from `poc05-tools`.

Exit criterion 7 (offline part): "Admission rejects `untrusted` and a third-party image in
`sidecar`." A policy that fails to compile enforces nothing (spike question 5), so this file
checks as much as it can without a cluster:

- the policies, bindings, params, and RBAC parse and carry the fields the design names;
- a model of the RBAC answers the design's `kubectl auth can-i` lists as the kind test expects;
- every CEL expression uses only the optional lookup form `[?'key']` (the spike found that
  `.?'key'` does not compile), has balanced delimiters, and names only variables defined above it;
- each rule (1 to 5, 6a to 6c, 7a to 7d, 8, T1 to T3, C1 to C5) has a rejected fixture and an
  admitted twin that differ only in the fields the rule reads, and each header names the rule's
  message;
- a Python model of the rules agrees with every fixture's header: a rejected fixture breaks
  exactly its rule, an admitted twin breaks none. The model mirrors the CEL; it does not run it.
  `test_poc05_kind_admission.py` runs the real policy on the `kind-poc05` cluster.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
ADMISSION = ROOT / "deploy/kind/poc05/admission"
FIXTURES = ADMISSION / "fixtures"
NAMESPACES = ROOT / "deploy/kind/poc05/base/namespaces.yaml"

POLICY_NAME = "agent-trust-rule"
PARAMS_NAME = "agent-trust-params"
PARAMS_NAMESPACE = "agent-platform-system"
ENFORCE_LABEL = ("agents.platform/admission", "enforce")
ENFORCED_NAMESPACES = {"poc05-agents", "poc05-remote", "poc05-tools"}
SUBMITTER = ("poc05-tenant", "submitter")
DEPLOYER = ("agent-platform-system", "deployer")
DISPATCHER = ("poc05-platform", "code-runner-dispatch")
# Per-call sandbox plan, "RBAC": the submitter loses poc05-tools (security review, required 4).
SUBMITTER_NAMESPACES = {"poc05-remote"}
SHAPE_LABEL = "agents.platform/pod-shape"
NAMESPACE_SHAPES = {"poc05-agents": "chassis", "poc05-remote": "remote", "poc05-tools": "tool"}
TRUST = "agents.platform/trust"
LANE = "agents.platform/lane"
ROLE = "agents.platform/role"
NAME = "app.kubernetes.io/name"
PREFIX = "kind.local/agent-platform/"
RULES = ("1", "2", "3", "4", "5", "6a", "6b", "6c", "7a", "7b", "7c", "7d", "8")
EXT_GROUP = "extensions.agents.x-k8s.io"
TEMPLATE_RULES = ("T1", "T2", "T3", "T4", "T5")
CLAIM_RULES = ("C1", "C2", "C3", "C4", "C5")
# Extension policy name to the one resource it matches and its rules. Two policies, not one: a
# policy matching both kinds would read `object.spec.env` on a SandboxTemplate, which has no env.
EXTENSION_POLICIES = {
    "sandbox-template-rule": ("sandboxtemplates", TEMPLATE_RULES),
    "sandbox-claim-rule": ("sandboxclaims", CLAIM_RULES),
}
# The fields of the v1.0.5 extension CRDs the rules read (design section "agent-sandbox v1.0.5").
EXTENSION_FIELDS = {
    "sandbox-template-rule": {
        "networkPolicyManagement",
        "envVarsInjectionPolicy",
        "volumeClaimTemplatesPolicy",
        "volumeClaimTemplates",
        "networkPolicy",
    },
    "sandbox-claim-rule": {
        "env",
        "additionalPodMetadata",
        "warmPoolRef",
        "warmPoolRef.name",
        "lifecycle",
        "lifecycle.shutdownPolicy",
        "lifecycle.shutdownTime",
        "volumeClaimTemplates",
    },
}
POOL = "code-runner"
ADMISSION_OBJECTS = {
    "params.yaml",
    "rbac.yaml",
    "policy.yaml",
    "binding.yaml",
    "extension-policy.yaml",
    "extension-binding.yaml",
}

Doc = dict[str, Any]


def _load_all(path: Path) -> list[Doc]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def _one(path: Path, kind: str) -> Doc:
    docs = [d for d in _load_all(path) if d.get("kind") == kind]
    assert len(docs) == 1, f"{path.name}: expected one {kind}, found {len(docs)}"
    return docs[0]


@pytest.fixture(scope="module")
def policy() -> Doc:
    return _one(ADMISSION / "policy.yaml", "ValidatingAdmissionPolicy")


@pytest.fixture(scope="module")
def binding() -> Doc:
    return _one(ADMISSION / "binding.yaml", "ValidatingAdmissionPolicyBinding")


@pytest.fixture(scope="module")
def extension_policies() -> dict[str, Doc]:
    docs = _load_all(ADMISSION / "extension-policy.yaml")
    assert all(d["kind"] == "ValidatingAdmissionPolicy" for d in docs)
    return {d["metadata"]["name"]: d for d in docs}


@pytest.fixture(scope="module")
def extension_bindings() -> dict[str, Doc]:
    docs = _load_all(ADMISSION / "extension-binding.yaml")
    assert all(d["kind"] == "ValidatingAdmissionPolicyBinding" for d in docs)
    return {d["metadata"]["name"]: d for d in docs}


@pytest.fixture(scope="module")
def all_policies(policy: Doc, extension_policies: dict[str, Doc]) -> list[Doc]:
    return [policy, *extension_policies.values()]


@pytest.fixture(scope="module")
def params() -> Doc:
    return _one(ADMISSION / "params.yaml", "ConfigMap")


@pytest.fixture(scope="module")
def rbac() -> list[Doc]:
    return _load_all(ADMISSION / "rbac.yaml")


def _messages(policy: Doc) -> dict[str, str]:
    """Rule id to message. Every message starts with `trust rule <id>:`, the id `N` or `Na`."""
    found: dict[str, str] = {}
    for v in policy["spec"]["validations"]:
        m = re.match(r"trust rule (\d[a-z]?): ", v["message"])
        assert m, f"message does not start with 'trust rule N: ': {v['message']!r}"
        assert m.group(1) not in found, f"two rules {m.group(1)}"
        found[m.group(1)] = v["message"]
    return found


EXT_MESSAGE = re.compile(r"(template|claim) rule ([TC]\d): ")


def _extension_messages(extension_policies: dict[str, Doc]) -> dict[str, str]:
    """Rule id to message. Every message starts with `template rule Tn:` or `claim rule Cn:`."""
    found: dict[str, str] = {}
    for policy in extension_policies.values():
        for v in policy["spec"]["validations"]:
            m = EXT_MESSAGE.match(v["message"])
            assert m, f"message does not start with 'template|claim rule Xn: ': {v['message']!r}"
            assert m.group(2) not in found, f"two rules {m.group(2)}"
            found[m.group(2)] = v["message"]
    return found


# --- policy -----------------------------------------------------------------------------------


def test_policy_is_named_fails_closed_and_takes_configmap_params(policy: Doc) -> None:
    assert policy["apiVersion"] == "admissionregistration.k8s.io/v1"
    assert policy["metadata"]["name"] == POLICY_NAME
    spec = policy["spec"]
    assert spec["failurePolicy"] == "Fail"
    assert spec["paramKind"] == {"apiVersion": "v1", "kind": "ConfigMap"}


def test_policy_matches_pods_and_every_kind_that_makes_them(policy: Doc) -> None:
    """The spike's policy matched Deployments only; bare Pods and Sandboxes got past it."""
    wanted = {
        ("", "pods"),
        ("", "pods/ephemeralcontainers"),
        ("", "replicationcontrollers"),
        ("apps", "deployments"),
        ("apps", "replicasets"),
        ("apps", "statefulsets"),
        ("apps", "daemonsets"),
        ("batch", "jobs"),
        ("batch", "cronjobs"),
        ("agents.x-k8s.io", "sandboxes"),
        # Per-call sandbox plan, "Admission": rules 0 to 8 apply to the template's pod at apply
        # time, not only when the pool makes its first Sandbox.
        (EXT_GROUP, "sandboxtemplates"),
    }
    matched: set[tuple[str, str]] = set()
    for rule in policy["spec"]["matchConstraints"]["resourceRules"]:
        assert set(rule["operations"]) == {"CREATE", "UPDATE"}
        matched |= {(g, r) for g in rule["apiGroups"] for r in rule["resources"]}
    assert wanted <= matched, f"not matched: {sorted(wanted - matched)}"
    assert (EXT_GROUP, "sandboxclaims") not in matched, "a claim has no pod; the claim rule owns it"


def test_the_template_variable_reads_a_sandbox_template_pod(policy: Doc) -> None:
    (template,) = [v for v in policy["spec"]["variables"] if v["name"] == "template"]
    expr = " ".join(template["expression"].split())
    assert "request.kind.kind in ['Sandbox', 'SandboxTemplate'] ? object.spec.podTemplate" in expr


def test_policy_has_every_rule_and_the_params_check(policy: Doc) -> None:
    validations = policy["spec"]["validations"]
    assert sorted(_messages(policy)) == sorted(["0", *RULES])
    for v in validations:
        assert v["reason"] == "Forbidden"
        if "messageExpression" in v:
            # The dynamic message starts with the static one, so the kind test's match holds.
            assert v["messageExpression"].strip().startswith("'" + v["message"]), v["message"]


def _expressions(policy: Doc) -> Iterator[tuple[str, str]]:
    spec = policy["spec"]
    for var in spec.get("variables", []):
        yield f"variables.{var['name']}", var["expression"]
    for i, v in enumerate(spec["validations"]):
        yield f"validations[{i}]", v["expression"]
        if "messageExpression" in v:
            yield f"validations[{i}].messageExpression", v["messageExpression"]
    for c in spec.get("matchConditions", []):
        yield f"matchConditions.{c['name']}", c["expression"]


def _strip_strings(expr: str) -> str:
    """The expression with every single-quoted literal emptied, so delimiters inside are ignored."""
    out: list[str] = []
    in_str = False
    i = 0
    while i < len(expr):
        ch = expr[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == "'":
                in_str = False
                out.append("'")
        else:
            if ch == '"':
                raise AssertionError("use single quotes in CEL here, so YAML stays plain")
            if ch == "'":
                in_str = True
            out.append(ch)
        i += 1
    assert not in_str, "unterminated string literal"
    return "".join(out)


def test_cel_uses_only_the_optional_index_form_and_is_balanced(all_policies: list[Doc]) -> None:
    """`[?'key/with-slash']` compiles; `.?'key'` does not, and `x['key']` errors when the key is
    missing, which with `failurePolicy: Fail` rejects for the wrong reason."""
    for where, expr in (w for p in all_policies for w in _expressions(p)):
        assert ".?" not in expr, f"{where}: `.?` optional select; write [?'key']"
        assert not re.search(r"[\w)\]]\['", expr), f"{where}: plain index; write [?'key']"
        assert "labels." not in expr, f"{where}: dotted label access; write labels[?'key']"
        bare = _strip_strings(expr)
        stack: list[str] = []
        pairs = {")": "(", "]": "[", "}": "{"}
        for ch in bare:
            if ch in "([{":
                stack.append(ch)
            elif ch in pairs:
                assert stack and stack.pop() == pairs[ch], f"{where}: unbalanced {ch!r}"
        assert not stack, f"{where}: unclosed {stack}"


def test_cel_variables_are_defined_before_use(all_policies: list[Doc]) -> None:
    for policy in all_policies:
        defined: set[str] = set()
        for where, expr in _expressions(policy):
            used = set(re.findall(r"variables\.(\w+)", _strip_strings(expr)))
            assert used <= defined, f"{where}: uses undefined {sorted(used - defined)}"
            if where.startswith("variables."):
                defined.add(where.removeprefix("variables."))


def test_label_lookups_name_the_labels_the_rules_read(policy: Doc) -> None:
    text = " ".join(e for _, e in _expressions(policy))
    for label in (TRUST, LANE, ROLE, NAME, SHAPE_LABEL):
        assert f"[?'{label}']" in text, label
    assert "namespaceObject" in text


def test_cel_strings_hold_no_apostrophe(all_policies: list[Doc]) -> None:
    """A `'` inside prose would end a CEL string literal early (e.g. "pod's")."""
    for where, expr in (w for p in all_policies for w in _expressions(p)):
        assert not re.search(r"[A-Za-z]'[A-Za-z]", expr), f"{where}: apostrophe inside a string"


# --- the extension policies -----------------------------------------------------------------


def test_extension_policies_fail_closed_and_match_one_kind_each(
    extension_policies: dict[str, Doc],
) -> None:
    """Security review of the per-call design, required 1: the policy must see templates and
    claims. A template without `networkPolicyManagement` gets `Managed`, and the controller then
    writes a NetworkPolicy that allows internet egress (B2)."""
    assert set(extension_policies) == set(EXTENSION_POLICIES)
    for name, (resource, _) in EXTENSION_POLICIES.items():
        doc = extension_policies[name]
        assert doc["apiVersion"] == "admissionregistration.k8s.io/v1"
        spec = doc["spec"]
        assert spec["failurePolicy"] == "Fail"
        assert "paramKind" not in spec, "the rules are literals; no params to go missing"
        (rule,) = spec["matchConstraints"]["resourceRules"]
        assert rule["apiGroups"] == [EXT_GROUP]
        assert rule["resources"] == [resource]
        assert set(rule["operations"]) == {"CREATE", "UPDATE"}


def test_extension_policies_have_every_rule(extension_policies: dict[str, Doc]) -> None:
    messages = _extension_messages(extension_policies)
    assert sorted(messages) == sorted([*TEMPLATE_RULES, *CLAIM_RULES])
    for name, (_, rules) in EXTENSION_POLICIES.items():
        validations = extension_policies[name]["spec"]["validations"]
        assert [EXT_MESSAGE.match(v["message"]).group(2) for v in validations] == list(rules)  # type: ignore[union-attr]
        assert all(v["reason"] == "Forbidden" for v in validations)


def _spec_paths(expr: str) -> set[str]:
    """Every `object.spec.a.b` the expression reads, as `a.b`."""
    return set(re.findall(r"object\.spec\.([\w.]*\w)", _strip_strings(expr)))


def test_extension_rules_read_only_the_named_crd_fields(extension_policies: dict[str, Doc]) -> None:
    """The field names come from the design, not from a CRD in this repo: a typo would read a
    field that is never set and admit everything. Pin them, so a change is a reviewed change."""
    for name, fields in EXTENSION_FIELDS.items():
        read = {p for _, e in _expressions(extension_policies[name]) for p in _spec_paths(e)}
        assert read == fields, f"{name}: reads {sorted(read)}"


def test_extension_rules_guard_every_field_with_has(extension_policies: dict[str, Doc]) -> None:
    """With `failurePolicy: Fail` an unguarded missing field rejects for the wrong reason."""
    for policy in extension_policies.values():
        for where, expr in _expressions(policy):
            bare = " ".join(_strip_strings(expr).split())
            for path in _spec_paths(expr):
                parts = path.split(".")
                for i in range(1, len(parts) + 1):
                    guard = "has(object.spec." + ".".join(parts[:i]) + ")"
                    assert guard in bare, f"{where}: {guard} missing"


def test_the_claim_rule_names_the_one_pool(extension_policies: dict[str, Doc]) -> None:
    text = " ".join(e for _, e in _expressions(extension_policies["sandbox-claim-rule"]))
    assert f"== '{POOL}'" in text
    assert "== 'Delete'" in text


def test_the_template_rule_refuses_an_absent_network_policy_management(
    extension_policies: dict[str, Doc],
) -> None:
    """Absent means `Managed` (the CRD default), so T1 needs the field set, not merely not
    `Managed`."""
    (t1,) = [
        v
        for v in extension_policies["sandbox-template-rule"]["spec"]["validations"]
        if v["message"].startswith("template rule T1:")
    ]
    expr = " ".join(t1["expression"].split())
    assert expr.startswith("has(object.spec.networkPolicyManagement) &&"), expr
    assert "== 'Unmanaged'" in expr


def test_extension_bindings_deny_in_the_enforced_namespaces(
    binding: Doc, extension_bindings: dict[str, Doc]
) -> None:
    assert set(extension_bindings) == set(EXTENSION_POLICIES)
    for name, doc in extension_bindings.items():
        spec = doc["spec"]
        assert spec["policyName"] == name
        assert spec["validationActions"] == binding["spec"]["validationActions"]
        assert spec["matchResources"] == binding["spec"]["matchResources"]
        assert "paramRef" not in spec


def test_the_kustomization_lists_the_cluster_objects_and_no_fixture() -> None:
    """`kubectl kustomize deploy/kind/poc05/admission` renders the admission objects; fixtures
    are sent by the kind test with --dry-run=server only."""
    doc = yaml.safe_load((ADMISSION / "kustomization.yaml").read_text())
    assert doc["kind"] == "Kustomization"
    assert set(doc["resources"]) == ADMISSION_OBJECTS
    assert "namespace" not in doc, "the objects name their own namespaces"


# --- binding, params, namespaces --------------------------------------------------------------


def test_binding_denies_in_the_enforced_namespaces_with_platform_params(binding: Doc) -> None:
    spec = binding["spec"]
    assert binding["metadata"]["name"] == POLICY_NAME
    assert spec["policyName"] == POLICY_NAME
    assert "Deny" in spec["validationActions"]
    assert set(spec["validationActions"]) <= {"Deny", "Audit"}
    assert spec["matchResources"]["namespaceSelector"] == {
        "matchLabels": {ENFORCE_LABEL[0]: ENFORCE_LABEL[1]}
    }
    assert spec["paramRef"] == {
        "name": PARAMS_NAME,
        "namespace": PARAMS_NAMESPACE,
        "parameterNotFoundAction": "Deny",
    }


def _namespace_labels() -> dict[str, dict[str, str]]:
    return {
        d["metadata"]["name"]: dict(d["metadata"].get("labels", {}))
        for d in _load_all(NAMESPACES)
        if d.get("kind") == "Namespace"
    }


def test_the_selector_picks_exactly_the_workload_namespaces() -> None:
    labels = _namespace_labels()
    labelled = {n for n, lab in labels.items() if lab.get(ENFORCE_LABEL[0]) == ENFORCE_LABEL[1]}
    assert labelled == ENFORCED_NAMESPACES
    assert {PARAMS_NAMESPACE, SUBMITTER[0], "poc05-smoke"} <= set(labels)


def test_each_enforced_namespace_names_one_pod_shape() -> None:
    """Rule 7a reads it through `namespaceObject` (review F3)."""
    labels = _namespace_labels()
    assert {n: labels[n].get(SHAPE_LABEL) for n in ENFORCED_NAMESPACES} == NAMESPACE_SHAPES
    assert all(SHAPE_LABEL not in lab for n, lab in labels.items() if n not in NAMESPACE_SHAPES)


def test_params_live_in_the_platform_namespace_and_list_our_repositories(params: Doc) -> None:
    assert params["metadata"]["name"] == PARAMS_NAME
    assert params["metadata"]["namespace"] == PARAMS_NAMESPACE
    p = _Params.of(params)
    # A registry host no public registry serves (review F1): never a bare Docker Hub namespace.
    assert p.prefix == PREFIX
    assert p.prefix.split("/")[0].count(".") >= 1, "the prefix must start with a registry host"
    assert p.image_source == "pull-never"
    assert {PREFIX + "echo-python", PREFIX + "echo-typescript"} <= set(p.trusted)
    assert PREFIX + "hostile" not in p.trusted
    assert all(r.startswith(p.prefix) for r in p.trusted)
    assert all(":" not in r and "@" not in r for r in p.trusted), "repositories, not tags"


# --- RBAC -------------------------------------------------------------------------------------


def _bindings_to(rbac: list[Doc], account: tuple[str, str]) -> list[Doc]:
    ns, name = account
    return [
        d
        for d in rbac
        if d["kind"] in {"RoleBinding", "ClusterRoleBinding"}
        and any(
            s.get("kind") == "ServiceAccount" and s.get("name") == name and s.get("namespace") == ns
            for s in d.get("subjects", [])
        )
    ]


def _roles(rbac: list[Doc]) -> dict[tuple[str, str], Doc]:
    return {
        (d["kind"], d["metadata"]["name"]): d for d in rbac if d["kind"] in {"Role", "ClusterRole"}
    }


def _role_of(rbac: list[Doc], binding: Doc) -> Doc:
    return _roles(rbac)[(binding["roleRef"]["kind"], binding["roleRef"]["name"])]


def can_i(
    rbac: list[Doc], account: tuple[str, str], verb: str, resource: str, group: str, ns: str
) -> bool:
    """`kubectl auth can-i` for the RoleBindings in rbac.yaml (no ClusterRoleBinding is used)."""
    return any(
        group in rule["apiGroups"] and resource in rule["resources"] and verb in rule["verbs"]
        for b in _bindings_to(rbac, account)
        if b["metadata"].get("namespace") == ns
        for rule in _role_of(rbac, b)["rules"]
    )


def _resources(rbac: list[Doc], account: tuple[str, str]) -> set[str]:
    roles = _roles(rbac)
    return {
        r
        for b in _bindings_to(rbac, account)
        for rule in roles[(b["roleRef"]["kind"], b["roleRef"]["name"])]["rules"]
        for r in rule["resources"]
    }


def test_accounts_carry_no_token(rbac: list[Doc]) -> None:
    accounts = {
        (a["metadata"]["namespace"], a["metadata"]["name"]): a
        for a in rbac
        if a["kind"] == "ServiceAccount"
    }
    assert set(accounts) == {SUBMITTER, DEPLOYER}
    assert all(a["automountServiceAccountToken"] is False for a in accounts.values())


def test_submitter_cannot_create_pods_where_the_chassis_secrets_live(rbac: list[Doc]) -> None:
    """Review F2: a pod creator reads every Secret in its namespace through the pod."""
    found = _bindings_to(rbac, SUBMITTER)
    assert all(b["kind"] == "RoleBinding" for b in found), "no cluster-wide grant"
    assert {b["metadata"]["namespace"] for b in found} == SUBMITTER_NAMESPACES
    assert "poc05-agents" not in SUBMITTER_NAMESPACES


def test_deployer_deploys_every_workload_namespace(rbac: list[Doc]) -> None:
    found = _bindings_to(rbac, DEPLOYER)
    assert all(b["kind"] == "RoleBinding" for b in found), "no cluster-wide grant"
    assert {b["metadata"]["namespace"] for b in found} == ENFORCED_NAMESPACES
    needed = {"pods", "configmaps", "services", "serviceaccounts", "deployments", "sandboxes"}
    assert needed | {"networkpolicies"} <= _resources(rbac, DEPLOYER)


@pytest.mark.parametrize(
    "account", [SUBMITTER, DEPLOYER, DISPATCHER], ids=["submitter", "deployer", "dispatcher"]
)
def test_tenant_and_deployer_cannot_touch_params_policy_rbac_or_secrets(
    rbac: list[Doc], account: tuple[str, str]
) -> None:
    roles = _roles(rbac)
    forbidden = {
        "secrets",
        "namespaces",
        "validatingadmissionpolicies",
        "validatingadmissionpolicybindings",
        "roles",
        "rolebindings",
        "clusterroles",
        "clusterrolebindings",
        "resourcequotas",
    }
    for b in _bindings_to(rbac, account):
        assert b["metadata"]["namespace"] != PARAMS_NAMESPACE
        role = roles[(b["roleRef"]["kind"], b["roleRef"]["name"])]
        for rule in role["rules"]:
            assert "*" not in rule["verbs"] and "*" not in rule["resources"]
            assert "*" not in rule["apiGroups"]
            assert not set(rule["resources"]) & forbidden, rule
            for verb in ("escalate", "bind", "impersonate"):
                assert verb not in rule["verbs"]


def test_submitter_cannot_open_network_edges_or_services(rbac: list[Doc]) -> None:
    """Review F3: Services and NetworkPolicies select by labels; only the platform writes them."""
    assert not {"networkpolicies", "services"} & _resources(rbac, SUBMITTER)


def test_deployer_applies_templates_and_pools_but_never_claims(rbac: list[Doc]) -> None:
    """`run.sh workloads` applies tools/ as the deployer; claims are the dispatcher's alone."""
    for ns in ENFORCED_NAMESPACES:
        for resource in ("sandboxtemplates", "sandboxwarmpools"):
            assert can_i(rbac, DEPLOYER, "create", resource, EXT_GROUP, ns), (resource, ns)
        assert not can_i(rbac, DEPLOYER, "create", "sandboxclaims", EXT_GROUP, ns), ns


def test_the_dispatcher_role_is_create_get_delete_on_claims_in_tools(rbac: list[Doc]) -> None:
    (b,) = _bindings_to(rbac, DISPATCHER)
    assert b["kind"] == "RoleBinding"
    assert b["metadata"]["namespace"] == "poc05-tools"
    role = _role_of(rbac, b)
    assert role["kind"] == "Role" and role["metadata"]["namespace"] == "poc05-tools"
    (rule,) = role["rules"]
    assert rule["apiGroups"] == [EXT_GROUP]
    assert rule["resources"] == ["sandboxclaims"]
    assert sorted(rule["verbs"]) == ["create", "delete", "get"]
    assert "resourceNames" not in rule, "claims have generated names"


def test_the_dispatcher_account_is_not_made_here(rbac: list[Doc]) -> None:
    """platform/code-runner-dispatch.yaml owns the ServiceAccount; this file only binds it."""
    assert not [
        d for d in rbac if d["kind"] == "ServiceAccount" and d["metadata"]["name"] == DISPATCHER[1]
    ]


TOOLS = "poc05-tools"
# Per-call sandbox plan, "RBAC": the `kubectl auth can-i` lists the kind test checks. Each refusal
# sits next to a control that the same identity may do.
CAN_I: list[tuple[tuple[str, str], str, str, str, str, bool]] = [
    *[
        (DISPATCHER, v, "sandboxclaims", EXT_GROUP, TOOLS, True)
        for v in ("create", "get", "delete")
    ],
    *[
        (DISPATCHER, v, "sandboxclaims", EXT_GROUP, TOOLS, False)
        for v in ("list", "watch", "update", "patch")
    ],
    (DISPATCHER, "create", "sandboxclaims", EXT_GROUP, "poc05-remote", False),
    (DISPATCHER, "create", "sandboxtemplates", EXT_GROUP, TOOLS, False),
    (DISPATCHER, "create", "sandboxwarmpools", EXT_GROUP, TOOLS, False),
    (DISPATCHER, "create", "pods", "", TOOLS, False),
    (DISPATCHER, "get", "secrets", "", TOOLS, False),
    (DISPATCHER, "get", "secrets", "", "poc05-platform", False),
    (SUBMITTER, "create", "pods", "", "poc05-remote", True),
    (SUBMITTER, "create", "pods", "", TOOLS, False),
    *[
        (SUBMITTER, "create", r, EXT_GROUP, ns, False)
        for r in ("sandboxclaims", "sandboxtemplates", "sandboxwarmpools")
        for ns in sorted(ENFORCED_NAMESPACES)
    ],
]


@pytest.mark.parametrize("case", CAN_I, ids=[f"{a[1]}-{v}-{r}-{ns}" for a, v, r, _, ns, _ in CAN_I])
def test_can_i_matches_the_design(
    rbac: list[Doc], case: tuple[tuple[str, str], str, str, str, str, bool]
) -> None:
    account, verb, resource, group, ns, allowed = case
    assert can_i(rbac, account, verb, resource, group, ns) is allowed


def test_the_tools_quota_caps_claims_pods_and_memory(rbac: list[Doc]) -> None:
    """A stolen dispatcher token starts at most a handful of sandboxes (suggested: values)."""
    (quota,) = [d for d in rbac if d["kind"] == "ResourceQuota"]
    assert quota["metadata"]["namespace"] == TOOLS
    assert quota["spec"]["hard"] == {
        f"count/sandboxclaims.{EXT_GROUP}": "4",
        "pods": "6",
        "limits.memory": "1536Mi",
    }


def test_only_the_platform_role_may_edit_the_params(rbac: list[Doc]) -> None:
    in_platform = [
        d for d in rbac if d["metadata"].get("namespace") == PARAMS_NAMESPACE and "rules" in d
    ]
    assert len(in_platform) == 1
    (rule,) = in_platform[0]["rules"]
    assert rule["resources"] == ["configmaps"]
    assert rule["resourceNames"] == [PARAMS_NAME]
    platform_bindings = [
        d
        for d in rbac
        if d["kind"] == "RoleBinding" and d["metadata"].get("namespace") == PARAMS_NAMESPACE
    ]
    assert len(platform_bindings) == 1
    assert all(s["kind"] == "Group" for s in platform_bindings[0]["subjects"])


# --- fixtures ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Fixture:
    path: Path
    expect: str
    rule: str
    message: str | None
    differs: tuple[str, ...]
    doc: Doc

    @property
    def twin(self) -> Path:
        return self.path.parent / "admitted.yaml"

    @property
    def id(self) -> str:
        return f"{self.path.parent.name}/{self.path.stem}"


HEADER = re.compile(r"^#\s*(expect|rule|message|differs):\s*(.*?)\s*$")


def _fixture(path: Path) -> Fixture:
    fields: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if not line.startswith("#"):
            break
        m = HEADER.match(line)
        if m:
            fields[m.group(1)] = m.group(2)
    docs = _load_all(path)
    assert len(docs) == 1, f"{path}: one object per fixture"
    differs = tuple(p.strip() for p in fields.get("differs", "").split(",") if p.strip())
    return Fixture(
        path=path,
        expect=fields.get("expect", ""),
        rule=fields.get("rule", ""),
        message=fields.get("message"),
        differs=differs,
        doc=docs[0],
    )


def _all_fixtures() -> list[Fixture]:
    return [_fixture(p) for p in sorted(FIXTURES.glob("*/*.yaml"))]


ALL = _all_fixtures() if FIXTURES.is_dir() else []
REJECTED = [f for f in ALL if f.expect == "rejected"]
# A SandboxClaim describes no pod; the trust rule does not match it.
PODS = [f for f in ALL if f.doc["kind"] != "SandboxClaim"]
ALL_RULES = (*RULES, *TEMPLATE_RULES, *CLAIM_RULES)


def test_every_rule_has_a_rejected_fixture_and_an_admitted_twin() -> None:
    assert ALL, f"no fixtures under {FIXTURES}"
    for rule in ALL_RULES:
        rejected = [f for f in REJECTED if f.rule == rule]
        assert rejected, f"rule {rule}: no rejected fixture"
        for f in rejected:
            assert f.twin.is_file(), f"{f.id}: no admitted twin"
            assert _fixture(f.twin).expect == "admitted"


def test_workload_kinds_are_covered_too() -> None:
    kinds = {f.doc["kind"] for f in REJECTED}
    assert {"Pod", "Deployment", "CronJob", "Sandbox", "SandboxTemplate", "SandboxClaim"} <= kinds
    template_trust = [f for f in REJECTED if f.doc["kind"] == "SandboxTemplate" and f.rule in RULES]
    assert template_trust, "no SandboxTemplate fixture for a trust rule"


def test_the_review_cases_have_fixtures() -> None:
    """Security review F1 to F3: each named case is a rejected fixture."""
    images = {
        c.get("image")
        for f in REJECTED
        if f.rule == "3"
        for c in _containers(_template(f.doc)["spec"])
    }
    assert "docker.io/agent-platform/echo-python:poc05" in images
    assert "agent-platform/echo-python:poc05" in images
    pulls = {
        c.get("imagePullPolicy")
        for f in REJECTED
        if f.rule == "8"
        for c in _template(f.doc)["spec"]["containers"]
    }
    assert {"Always", "IfNotPresent"} <= pulls


def test_c4_refuses_a_claim_without_lifecycle_or_shutdown_policy() -> None:
    """lifecycle is optional and shutdownPolicy defaults to Retain: both gaps need a fixture."""
    differs = {f.differs for f in REJECTED if f.rule == "C4"}
    assert {
        (".spec.lifecycle",),
        (".spec.lifecycle.shutdownPolicy",),
        (".spec.lifecycle.shutdownTime",),
    } <= differs


def test_fixture_headers_are_complete(policy: Doc, extension_policies: dict[str, Doc]) -> None:
    messages = _messages(policy) | _extension_messages(extension_policies)
    for f in ALL:
        assert f.expect in {"rejected", "admitted"}, f.id
        assert f.rule in ALL_RULES, f.id
        if f.expect == "rejected":
            assert f.message == messages[f.rule], f"{f.id}: header message is not the policy's"
            assert f.differs, f"{f.id}: no `differs:` line"
        else:
            assert f.path.name == "admitted.yaml"
            assert f.message is None and not f.differs


def test_fixtures_sit_in_enforced_namespaces() -> None:
    for f in ALL:
        assert f.doc["metadata"]["namespace"] in ENFORCED_NAMESPACES, f.id


def test_fixture_images_use_the_params_prefix_or_are_meant_not_to() -> None:
    """Admitted twins outside the remote lane use the new prefix: a stale name would pass offline
    and fail on kind with ErrImageNeverPull, or be admitted for the wrong reason."""
    for f in ALL:
        if f.expect != "admitted" or f.doc["kind"] == "SandboxClaim":
            continue
        if _template(f.doc)["metadata"]["labels"].get(LANE) == "remote":
            continue
        for c in _containers(_template(f.doc)["spec"]):
            assert c["image"].startswith(PREFIX), (f.id, c["image"])


def _key(k: str) -> str:
    return f"[{k}]" if "/" in k or "." in k else f".{k}"


def _diff(a: Any, b: Any, path: str = "") -> list[str]:
    if isinstance(a, dict) and isinstance(b, dict):
        out: list[str] = []
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append(path + _key(k))
            else:
                out += _diff(a[k], b[k], path + _key(k))
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [
            d
            for i, (x, y) in enumerate(zip(a, b, strict=True))
            for d in _diff(x, y, f"{path}[{i}]")
        ]
    return [] if a == b else [path]


TEMPLATE_PREFIXES = (".spec.template", ".spec.podTemplate", ".spec.jobTemplate.spec.template")
CONTAINER = r"\.spec\.(containers|initContainers|ephemeralContainers)\[\d+\]"
IMAGE = rf"{CONTAINER}\.image"
SECRET_REF = (
    rf"{CONTAINER}\.(env\[\d+\]\.valueFrom\.secretKeyRef\.name|envFrom\[\d+\]\.secretRef\.name)"
    r"|\.spec\.volumes\[\d+\]\.(secret\.secretName|projected\.sources\[\d+\]\.secret\.name)"
)
GVISOR = r"\.spec\.(runtimeClassName|automountServiceAccountToken)"
READS: dict[str, str] = {
    "1": rf"\.metadata\.labels\[{re.escape(TRUST)}\]",
    "2": rf"\.metadata\.labels\[{re.escape(TRUST)}\]",
    "3": IMAGE,
    "4": IMAGE,
    "5": rf"{GVISOR}|{SECRET_REF}",
    "6a": IMAGE,
    "6b": rf"{CONTAINER}\.command",
    "6c": rf"{CONTAINER}\.(env\[\d+\]\.(value|valueFrom)|envFrom|volumeMounts)(\..*|\[.*)?",
    "7a": r"\.metadata\.namespace",
    "7b": rf"\.metadata\.labels\[{re.escape(ROLE)}\]",
    "7c": SECRET_REF,
    "7d": GVISOR,
    "8": rf"{CONTAINER}\.imagePullPolicy",
    "T1": r"\.spec\.networkPolicyManagement",
    "T2": r"\.spec\.envVarsInjectionPolicy",
    "T3": r"\.spec\.volumeClaimTemplatesPolicy",
    "T4": r"\.spec\.volumeClaimTemplates",
    "T5": r"\.spec\.networkPolicy",
    "C1": r"\.spec\.env",
    "C2": r"\.spec\.additionalPodMetadata",
    "C3": r"\.spec\.warmPoolRef\.name",
    "C4": r"\.spec\.lifecycle(\.(shutdownPolicy|shutdownTime))?",
    "C5": r"\.spec\.volumeClaimTemplates",
}


def _pod_path(path: str) -> str:
    for prefix in TEMPLATE_PREFIXES:
        if path.startswith(prefix + "."):
            return path.removeprefix(prefix)
    return path


@pytest.mark.parametrize("fixture", REJECTED, ids=[f.id for f in REJECTED])
def test_twins_differ_only_in_the_field_the_rule_reads(fixture: Fixture) -> None:
    """The pitfall rule: if the twins differed in more, the admitted one could pass for another
    reason and prove nothing about this rule."""
    twin = _fixture(fixture.twin)
    diff = _diff(fixture.doc, twin.doc)
    assert diff, f"{fixture.id}: identical to its twin"
    assert sorted(diff) == sorted(fixture.differs), f"{fixture.id}: differs in {diff}"
    for path in diff:
        assert re.fullmatch(READS[fixture.rule], _pod_path(path)), (
            f"{fixture.id}: {path} is not a field rule {fixture.rule} reads"
        )


def _containers(spec: Doc) -> list[Doc]:
    return [
        *spec.get("initContainers", []),
        *spec["containers"],
        *spec.get("ephemeralContainers", []),
    ]


def _template(doc: Doc) -> Doc:
    kind = doc["kind"]
    if kind == "Pod":
        return doc
    if kind == "CronJob":
        return dict(doc["spec"]["jobTemplate"]["spec"]["template"])
    if kind in {"Sandbox", "SandboxTemplate"}:
        return dict(doc["spec"]["podTemplate"])
    return dict(doc["spec"]["template"])


@pytest.mark.parametrize("fixture", PODS, ids=[f.id for f in PODS])
def test_fixtures_meet_the_restricted_pod_security_standard(fixture: Fixture) -> None:
    """The enforced namespaces run PSA `restricted`: a fixture that broke it would be rejected by
    PSA on kind, and an admitted twin would then fail for the wrong reason."""
    spec = _template(fixture.doc)["spec"]
    pod_sc = spec.get("securityContext", {})
    for c in _containers(spec):
        sc = c.get("securityContext", {})
        assert sc.get("allowPrivilegeEscalation") is False, (fixture.id, c["name"])
        assert sc.get("capabilities", {}).get("drop") == ["ALL"], (fixture.id, c["name"])
        assert sc.get("runAsNonRoot", pod_sc.get("runAsNonRoot")) is True, (fixture.id, c["name"])
        seccomp = sc.get("seccompProfile", pod_sc.get("seccompProfile", {}))
        assert seccomp.get("type") == "RuntimeDefault", (fixture.id, c["name"])


# --- the rule model ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Params:
    prefix: str
    trusted: tuple[str, ...]
    image_source: str = "pull-never"

    @classmethod
    def of(cls, doc: Doc) -> _Params:
        data = doc.get("data", {})
        trusted = tuple(
            r.strip() for r in data.get("trustedRepositories", "").split(",") if r.strip()
        )
        return cls(
            prefix=data.get("registryPrefix", ""),
            trusted=trusted,
            image_source=data.get("imageSource", ""),
        )


def _is_repo(image: str, repo: str) -> bool:
    return image == repo or image.startswith((repo + ":", repo + "@"))


TOKEN_SECRET = re.compile(r"remote-[a-z0-9]([-a-z0-9]*[a-z0-9])?-token")


def _secret_names(spec: Doc) -> Iterator[str]:
    for c in _containers(spec):
        for e in c.get("env", []):
            ref = e.get("valueFrom", {}).get("secretKeyRef")
            if ref is not None:
                yield str(ref.get("name", ""))
        for f in c.get("envFrom", []):
            if "secretRef" in f:
                yield str(f["secretRef"].get("name", ""))
    for v in spec.get("volumes", []):
        if "secret" in v:
            yield str(v["secret"].get("secretName", ""))
        for s in v.get("projected", {}).get("sources", []):
            if "secret" in s:
                yield str(s["secret"].get("name", ""))


def _projected_token(spec: Doc) -> bool:
    return any(
        "serviceAccountToken" in s
        for v in spec.get("volumes", [])
        for s in v.get("projected", {}).get("sources", [])
    )


def _uses_secret(c: Doc, secret_volumes: set[str]) -> bool:
    return (
        any("secretKeyRef" in e.get("valueFrom", {}) for e in c.get("env", []))
        or any("secretRef" in f for f in c.get("envFrom", []))
        or any(m["name"] in secret_volumes for m in c.get("volumeMounts", []))
    )


def _secret_volumes(spec: Doc) -> set[str]:
    return {
        v["name"]
        for v in spec.get("volumes", [])
        if "secret" in v or any("secret" in s for s in v.get("projected", {}).get("sources", []))
    }


def _gvisor_no_token(spec: Doc) -> bool:
    return (
        spec.get("runtimeClassName") == "gvisor"
        and spec.get("automountServiceAccountToken") is False
    )


def broken_rules(doc: Doc, p: _Params, namespace: str | None = None) -> set[str]:
    """The rules `doc` breaks, by the same logic as the CEL in `policy.yaml`. `namespace` defaults
    to the object's own; its shape label comes from `base/namespaces.yaml`."""
    ns = namespace or doc.get("metadata", {}).get("namespace", "")
    ns_shape = _namespace_labels().get(ns, {}).get(SHAPE_LABEL, "")
    template = _template(doc)
    labels = template.get("metadata", {}).get("labels", {})
    spec = template["spec"]
    containers = _containers(spec)
    images = [c.get("image", "") for c in containers]
    chassis_repo = p.prefix + "chassis"
    chassis = [i for i in images if _is_repo(i, chassis_repo)]
    workload = [i for i in images if i not in chassis]
    trust = labels.get(TRUST, "")
    sidecar = bool(chassis) and bool(workload)
    remote = not chassis and (labels.get(LANE, "") == "remote" or trust == "untrusted")
    shape = "chassis" if chassis else ("remote" if remote else "tool")
    names = list(_secret_names(spec))
    own_token = labels.get(NAME, "") + "-token"
    secret_users = [
        c.get("image", "") for c in containers if _uses_secret(c, _secret_volumes(spec))
    ]
    if p.image_source == "pull-never":
        own_store = all(c.get("imagePullPolicy") == "Never" for c in containers)
    else:
        own_store = all("@sha256:" in i for i in images)

    checks: dict[str, Callable[[], bool]] = {
        "0": lambda: (
            p.prefix != "" and bool(p.trusted) and p.image_source in {"pull-never", "digest"}
        ),
        "1": lambda: trust in {"trusted", "untrusted"},
        "2": lambda: not (sidecar and trust == "untrusted"),
        "3": lambda: remote or all(i.startswith(p.prefix) for i in images),
        "4": lambda: (
            not (sidecar and trust == "trusted")
            or all(
                any(_is_repo(i, r) for r in p.trusted) for i in workload if i.startswith(p.prefix)
            )
        ),
        "5": lambda: (
            not remote
            or (
                _gvisor_no_token(spec)
                and not _projected_token(spec)
                and all(TOKEN_SECRET.fullmatch(n) for n in names)
            )
        ),
        "6a": lambda: len(chassis) <= 1,
        "6b": lambda: all(
            not c.get("command") for c in containers if c.get("image", "") in chassis
        ),
        "6c": lambda: remote or all(i in chassis for i in secret_users),
        "7a": lambda: shape == ns_shape,
        "7b": lambda: labels.get(ROLE, "") != "chassis" or bool(chassis),
        "7c": lambda: (
            not remote or all(not TOKEN_SECRET.fullmatch(n) or n == own_token for n in names)
        ),
        "7d": lambda: shape != "tool" or _gvisor_no_token(spec),
        "8": lambda: remote or own_store,
    }
    return {n for n, ok in checks.items() if not ok()}


def extension_broken_rules(doc: Doc) -> set[str]:
    """The template and claim rules `doc` breaks, by the same logic as `extension-policy.yaml`."""
    spec = doc.get("spec", {})
    if doc["kind"] == "SandboxTemplate":
        checks = {
            "T1": spec.get("networkPolicyManagement") == "Unmanaged",
            "T2": spec.get("envVarsInjectionPolicy", "Disallowed") == "Disallowed",
            "T3": spec.get("volumeClaimTemplatesPolicy", "Disallowed") == "Disallowed",
            "T4": not spec.get("volumeClaimTemplates"),
            "T5": "networkPolicy" not in spec,
        }
    elif doc["kind"] == "SandboxClaim":
        lifecycle = spec.get("lifecycle", {})
        checks = {
            "C1": not spec.get("env"),
            "C2": "additionalPodMetadata" not in spec,
            "C3": spec.get("warmPoolRef", {}).get("name") == POOL,
            "C4": lifecycle.get("shutdownPolicy") == "Delete" and "shutdownTime" in lifecycle,
            "C5": not spec.get("volumeClaimTemplates"),
        }
    else:
        checks = {}
    return {n for n, ok in checks.items() if not ok}


def all_broken_rules(doc: Doc, p: _Params) -> set[str]:
    trust = set() if doc["kind"] == "SandboxClaim" else broken_rules(doc, p)
    return trust | extension_broken_rules(doc)


@pytest.mark.parametrize("fixture", ALL, ids=[f.id for f in ALL])
def test_the_rule_model_agrees_with_each_fixture(fixture: Fixture, params: Doc) -> None:
    """A rejected fixture breaks exactly its own rule, so its message is the one the kind test
    sees; an admitted twin breaks none."""
    broken = all_broken_rules(fixture.doc, _Params.of(params))
    expected = {fixture.rule} if fixture.expect == "rejected" else set()
    assert broken == expected, f"{fixture.id}: breaks {sorted(broken)}"


def test_the_rule_model_fails_closed_on_empty_params() -> None:
    pod = {
        "kind": "Pod",
        "metadata": {"namespace": "poc05-tools", "labels": {TRUST: "trusted"}},
        "spec": {"containers": [{"name": "a", "image": PREFIX + "echo-python:1"}]},
    }
    assert "0" in broken_rules(pod, _Params(prefix="", trusted=()))
    assert "0" in broken_rules(pod, _Params(prefix=PREFIX, trusted=("x",), image_source=""))


def test_the_digest_mode_needs_a_digest_on_every_image() -> None:
    """The cloud setting of rule 8 (`imageSource: digest`), in the model only."""
    p = _Params(prefix=PREFIX, trusted=(PREFIX + "echo-python",), image_source="digest")
    pod: Doc = {
        "kind": "Pod",
        "metadata": {
            "namespace": "poc05-tools",
            "labels": {TRUST: "trusted", NAME: "t"},
        },
        "spec": {
            "runtimeClassName": "gvisor",
            "automountServiceAccountToken": False,
            "containers": [{"name": "a", "image": PREFIX + "code-runner:poc05"}],
        },
    }
    assert broken_rules(pod, p) == {"8"}
    pod["spec"]["containers"][0]["image"] = PREFIX + "code-runner@sha256:" + "0" * 64
    assert broken_rules(pod, p) == set()
