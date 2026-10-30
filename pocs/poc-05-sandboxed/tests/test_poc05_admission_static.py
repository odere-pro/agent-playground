"""PoC-5 admission, offline: the trust rule's ValidatingAdmissionPolicy, its binding, its params,
and the paired fixtures under `deploy/kind/poc05/admission/`
(plan `docs/plans/2026-10-02-poc-05-sandboxed.md`, section 2.5; ADR-005 decisions 4 and 5; security
review `notes/2026-10-02-review-security.md` F1 to F3: rules 6, 7, and 8). The per-call sandbox
(plan `docs/plans/2026-10-09-poc-05-per-call-sandbox.md`, sections "RBAC" and "Admission", task 4)
adds the template rules T1 to T3, the claim rules C1 to C5, the dispatcher's claim role, the tools
quota, and drops the submitter from `poc05-tools`.

Exit criterion 7 (offline part): "Admission rejects `untrusted` and a third-party image in
`sidecar`." A policy that fails to compile enforces nothing (spike question 5), so this file
checks as much as it can without a cluster:

- the policies, bindings, and params parse and carry the fields the design names;
- `test_poc05_admission_rbac_static.py` checks the RBAC;
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

import hashlib
import re
from collections.abc import Iterator
from typing import Any

import pytest
import yaml
from poc_harness.admission_manifests import (
    ADMISSION,
    ADMISSION_OBJECTS,
    CLAIM_RULES,
    ENFORCE_LABEL,
    ENFORCED_NAMESPACES,
    EXT_GROUP,
    EXTENSION_FIELDS,
    EXTENSION_POLICIES,
    FIXTURES,
    LANE,
    NAME,
    NAMESPACE_SHAPES,
    PARAMS_NAME,
    PARAMS_NAMESPACE,
    POLICY_NAME,
    POOL,
    PREFIX,
    ROLE,
    RULES,
    SHAPE_LABEL,
    SUBMITTER,
    TEMPLATE_RULES,
    TRUST,
    Fixture,
    all_fixtures,
    namespace_labels,
    one_of_kind,
    read_fixture,
    template_of,
)
from poc_harness.admission_model import (
    AdmissionParams,
    all_broken_rules,
    broken_rules,
    extension_broken_rules,
)
from poc_harness.manifests import containers_of, load_manifests

Doc = dict[str, Any]


@pytest.fixture(scope="module")
def policy() -> Doc:
    return one_of_kind(ADMISSION / "policy.yaml", "ValidatingAdmissionPolicy")


@pytest.fixture(scope="module")
def binding() -> Doc:
    return one_of_kind(ADMISSION / "binding.yaml", "ValidatingAdmissionPolicyBinding")


@pytest.fixture(scope="module")
def extension_policies() -> dict[str, Doc]:
    docs = load_manifests(ADMISSION / "extension-policy.yaml")
    assert all(d["kind"] == "ValidatingAdmissionPolicy" for d in docs)
    return {d["metadata"]["name"]: d for d in docs}


@pytest.fixture(scope="module")
def extension_bindings() -> dict[str, Doc]:
    docs = load_manifests(ADMISSION / "extension-binding.yaml")
    assert all(d["kind"] == "ValidatingAdmissionPolicyBinding" for d in docs)
    return {d["metadata"]["name"]: d for d in docs}


@pytest.fixture(scope="module")
def all_policies(policy: Doc, extension_policies: dict[str, Doc]) -> list[Doc]:
    return [policy, *extension_policies.values()]


@pytest.fixture(scope="module")
def params() -> Doc:
    return one_of_kind(ADMISSION / "params.yaml", "ConfigMap")


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


def test_policy_has_only_the_params_check_and_rules_1_to_8(policy: Doc) -> None:
    """Criterion 7: "The admission check rejects an `untrusted` workload, and a third-party image,
    in the `sidecar` lane."

    Criterion 7: the trust policy holds rule 0 and rules 1 to 8, and no other rule."""
    assert sorted(_messages(policy)) == sorted(["0", *RULES])


@pytest.mark.parametrize("rule", ["0", *RULES])
def test_policy_has_every_rule_and_the_params_check(policy: Doc, rule: str) -> None:
    """Criterion 7: "The admission check rejects an `untrusted` workload, and a third-party image,
    in the `sidecar` lane."

    Criterion 7: trust rule `rule` is in the policy once, denies as Forbidden, and its dynamic
    message starts with the static one, so the kind test's match holds."""
    tag = f"trust rule {rule}: "

    found = [v for v in policy["spec"]["validations"] if v["message"].startswith(tag)]

    assert len(found) == 1, f"trust rule {rule}: found {len(found)} times in policy.yaml"
    (v,) = found
    assert v["reason"] == "Forbidden", f"trust rule {rule}"
    dynamic = v.get("messageExpression", "'" + v["message"]).strip()
    assert dynamic.startswith("'" + v["message"]), f"trust rule {rule}"


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


def test_the_selector_picks_exactly_the_workload_namespaces() -> None:
    labels = namespace_labels()
    labelled = {n for n, lab in labels.items() if lab.get(ENFORCE_LABEL[0]) == ENFORCE_LABEL[1]}
    assert labelled == ENFORCED_NAMESPACES
    assert {PARAMS_NAMESPACE, SUBMITTER[0], "poc05-smoke"} <= set(labels)


def test_each_enforced_namespace_names_one_pod_shape() -> None:
    """Rule 7a reads it through `namespaceObject` (review F3)."""
    labels = namespace_labels()
    assert {n: labels[n].get(SHAPE_LABEL) for n in ENFORCED_NAMESPACES} == NAMESPACE_SHAPES
    assert all(SHAPE_LABEL not in lab for n, lab in labels.items() if n not in NAMESPACE_SHAPES)


def test_params_live_in_the_platform_namespace_and_list_our_repositories(params: Doc) -> None:
    assert params["metadata"]["name"] == PARAMS_NAME
    assert params["metadata"]["namespace"] == PARAMS_NAMESPACE
    p = AdmissionParams.of(params)
    # A registry host no public registry serves (review F1): never a bare Docker Hub namespace.
    assert p.prefix == PREFIX
    assert p.prefix.split("/")[0].count(".") >= 1, "the prefix must start with a registry host"
    assert p.image_source == "pull-never"
    assert {PREFIX + "echo-python", PREFIX + "echo-typescript"} <= set(p.trusted)
    assert PREFIX + "hostile" not in p.trusted
    assert all(r.startswith(p.prefix) for r in p.trusted)
    assert all(":" not in r and "@" not in r for r in p.trusted), "repositories, not tags"


# --- fixtures ---------------------------------------------------------------------------------


ALL = all_fixtures() if FIXTURES.is_dir() else []
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
            assert read_fixture(f.twin).expect == "admitted"


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
        for c in containers_of(template_of(f.doc)["spec"])
    }
    assert "docker.io/agent-platform/echo-python:poc05" in images
    assert "agent-platform/echo-python:poc05" in images
    pulls = {
        c.get("imagePullPolicy")
        for f in REJECTED
        if f.rule == "8"
        for c in template_of(f.doc)["spec"]["containers"]
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


def test_c2_admits_the_empty_metadata_the_controller_writes() -> None:
    """The controller's UPDATE of a claim carries `additionalPodMetadata: {}` (kind, 2026-10-09).
    C2 must admit that, or no claim gets Ready; a label or an annotation in it stays refused."""
    base = {
        "warmPoolRef": {"name": POOL},
        "lifecycle": {"shutdownPolicy": "Delete", "shutdownTime": "2030-01-01T00:00:00Z"},
    }

    def claim(meta: Doc) -> Doc:
        return {"kind": "SandboxClaim", "spec": {**base, "additionalPodMetadata": meta}}

    assert extension_broken_rules(claim({})) == set()
    assert extension_broken_rules(claim({"labels": {}, "annotations": {}})) == set()
    assert extension_broken_rules(claim({"labels": {NAME: "litellm"}})) == {"C2"}
    assert extension_broken_rules(claim({"annotations": {"a": "b"}})) == {"C2"}


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
        if template_of(f.doc)["metadata"]["labels"].get(LANE) == "remote":
            continue
        for c in containers_of(template_of(f.doc)["spec"]):
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
    twin = read_fixture(fixture.twin)
    diff = _diff(fixture.doc, twin.doc)
    assert diff, f"{fixture.id}: identical to its twin"
    assert sorted(diff) == sorted(fixture.differs), f"{fixture.id}: differs in {diff}"
    for path in diff:
        assert re.fullmatch(READS[fixture.rule], _pod_path(path)), (
            f"{fixture.id}: {path} is not a field rule {fixture.rule} reads"
        )


@pytest.mark.parametrize("fixture", PODS, ids=[f.id for f in PODS])
def test_fixtures_meet_the_restricted_pod_security_standard(fixture: Fixture) -> None:
    """The enforced namespaces run PSA `restricted`: a fixture that broke it would be rejected by
    PSA on kind, and an admitted twin would then fail for the wrong reason."""
    spec = template_of(fixture.doc)["spec"]
    pod_sc = spec.get("securityContext", {})
    for c in containers_of(spec):
        sc = c.get("securityContext", {})
        assert sc.get("allowPrivilegeEscalation") is False, (fixture.id, c["name"])
        assert sc.get("capabilities", {}).get("drop") == ["ALL"], (fixture.id, c["name"])
        assert sc.get("runAsNonRoot", pod_sc.get("runAsNonRoot")) is True, (fixture.id, c["name"])
        seccomp = sc.get("seccompProfile", pod_sc.get("seccompProfile", {}))
        assert seccomp.get("type") == "RuntimeDefault", (fixture.id, c["name"])


# --- the rule model ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rule", "pinned"),
    [
        ("0", "576534e9e0cef6e0"),  # pragma: allowlist secret
        ("1", "597275553b86abd2"),  # pragma: allowlist secret
        ("2", "f64a89cf4cd13116"),  # pragma: allowlist secret
        ("3", "568074d253513a90"),  # pragma: allowlist secret
        ("4", "ed1db7cb5b6cb771"),
        ("5", "e90f7a249dc3490b"),  # pragma: allowlist secret
        ("6a", "06ca8856518fa312"),  # pragma: allowlist secret
        ("6b", "2b12dfb159202cb0"),
        ("6c", "a5113c46e8644e7b"),  # pragma: allowlist secret
        ("7a", "0583f09d78803e05"),
        ("7b", "ac07d2f204ef0003"),  # pragma: allowlist secret
        ("7c", "79e11318ba44d4e2"),  # pragma: allowlist secret
        ("7d", "9efa9e4543df63f6"),  # pragma: allowlist secret
        ("8", "7f98972336329322"),
        ("T1", "ca53815103a2c2af"),  # pragma: allowlist secret
        ("T2", "11f516c4a675f8e4"),  # pragma: allowlist secret
        ("T3", "06d8e73ceaac1d74"),  # pragma: allowlist secret
        ("T4", "8c86a51e84f6d3f6"),  # pragma: allowlist secret
        ("T5", "3e94decf932b3cf9"),  # pragma: allowlist secret
        ("C1", "52fb84bdc2365936"),  # pragma: allowlist secret
        ("C2", "4355e9e17f8aec66"),  # pragma: allowlist secret
        ("C3", "9ec23d4010e3a8a6"),  # pragma: allowlist secret
        ("C4", "7bf9639215cf6163"),  # pragma: allowlist secret
        ("C5", "8c86a51e84f6d3f6"),  # pragma: allowlist secret
    ],
)
def test_rule_expression_matches_its_pin_when_the_policy_loads(
    all_policies: list[Doc], rule: str, pinned: str
) -> None:
    """Criterion 7: "The admission check rejects an `untrusted` workload, and a third-party image,
    in the `sidecar` lane."

    Criterion 7: each rule's CEL in the loaded policy is the reviewed one, so a deleted or
    weakened rule fails offline. The model in `broken_rules` is not the policy; this pin is the
    offline link between them. After a reviewed change to a rule, change the model and the pin."""
    tag = re.compile(rf"(trust|template|claim) rule {rule}: ")
    found = [
        v["expression"]
        for p in all_policies
        for v in p["spec"]["validations"]
        if tag.match(v["message"])
    ]

    digests = [hashlib.sha256(" ".join(e.split()).encode()).hexdigest()[:16] for e in found]

    assert digests == [pinned], f"rule {rule} changed or is gone: {found}"


@pytest.mark.parametrize(
    ("name", "pinned"),
    [
        ("agent-trust-rule", "ce4ee3a01d617e67"),  # pragma: allowlist secret
        ("sandbox-template-rule", "e3b0c44298fc1c14"),  # pragma: allowlist secret
        ("sandbox-claim-rule", "e3b0c44298fc1c14"),  # pragma: allowlist secret
    ],
)
def test_policy_variables_match_their_pin_when_the_policy_loads(
    all_policies: list[Doc], name: str, pinned: str
) -> None:
    """Criterion 7: "The admission check rejects an `untrusted` workload, and a third-party image,
    in the `sidecar` lane."

    Criterion 7: the variables the rules read are the reviewed ones, so a rule cannot be
    weakened through a variable (say `isRemote`) while its own expression stays pinned."""
    (p,) = [p for p in all_policies if p["metadata"]["name"] == name]
    text = "\n".join(f"{v['name']}={v['expression']}" for v in p["spec"].get("variables", []))

    digest = hashlib.sha256(" ".join(text.split()).encode()).hexdigest()[:16]

    assert digest == pinned, f"{name}: a variable changed; review it, then the model and the pin"


@pytest.mark.parametrize("fixture", ALL, ids=[f.id for f in ALL])
def test_the_rule_model_agrees_with_each_fixture(fixture: Fixture, params: Doc) -> None:
    """A rejected fixture breaks exactly its own rule, so its message is the one the kind test
    sees; an admitted twin breaks none."""
    broken = all_broken_rules(fixture.doc, AdmissionParams.of(params))
    expected = {fixture.rule} if fixture.expect == "rejected" else set()
    assert broken == expected, f"{fixture.id}: breaks {sorted(broken)}"


def test_the_rule_model_fails_closed_on_empty_params() -> None:
    pod = {
        "kind": "Pod",
        "metadata": {"namespace": "poc05-tools", "labels": {TRUST: "trusted"}},
        "spec": {"containers": [{"name": "a", "image": PREFIX + "echo-python:1"}]},
    }
    assert "0" in broken_rules(pod, AdmissionParams(prefix="", trusted=()))
    assert "0" in broken_rules(pod, AdmissionParams(prefix=PREFIX, trusted=("x",), image_source=""))


def test_the_digest_mode_needs_a_digest_on_every_image() -> None:
    """The cloud setting of rule 8 (`imageSource: digest`), in the model only."""
    p = AdmissionParams(prefix=PREFIX, trusted=(PREFIX + "echo-python",), image_source="digest")
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
