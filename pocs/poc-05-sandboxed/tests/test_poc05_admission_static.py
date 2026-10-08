"""PoC-5 admission, offline: the trust rule's ValidatingAdmissionPolicy, its binding, its params,
the submitter's and deployer's RBAC, and the paired fixtures under `deploy/kind/poc05/admission/`
(plan `docs/plans/2026-10-02-poc-05-sandboxed.md`, section 2.5; ADR-005 decisions 4 and 5; security
review `notes/2026-10-02-review-security.md` F1 to F3: rules 6, 7, and 8).

Exit criterion 7 (offline part): "Admission rejects `untrusted` and a third-party image in
`sidecar`." A policy that fails to compile enforces nothing (spike question 5), so this file
checks as much as it can without a cluster:

- the policy, binding, params, and RBAC parse and carry the fields the design names;
- every CEL expression uses only the optional lookup form `[?'key']` (the spike found that
  `.?'key'` does not compile), has balanced delimiters, and names only variables defined above it;
- each rule (1 to 5, 6a to 6c, 7a to 7d, 8) has a rejected fixture and an admitted twin that
  differ only in the fields the rule reads, and each header names the rule's message;
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
SUBMITTER_NAMESPACES = {"poc05-remote", "poc05-tools"}
SHAPE_LABEL = "agents.platform/pod-shape"
NAMESPACE_SHAPES = {"poc05-agents": "chassis", "poc05-remote": "remote", "poc05-tools": "tool"}
TRUST = "agents.platform/trust"
LANE = "agents.platform/lane"
ROLE = "agents.platform/role"
NAME = "app.kubernetes.io/name"
PREFIX = "kind.local/agent-platform/"
RULES = ("1", "2", "3", "4", "5", "6a", "6b", "6c", "7a", "7b", "7c", "7d", "8")

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
    }
    matched: set[tuple[str, str]] = set()
    for rule in policy["spec"]["matchConstraints"]["resourceRules"]:
        assert set(rule["operations"]) == {"CREATE", "UPDATE"}
        matched |= {(g, r) for g in rule["apiGroups"] for r in rule["resources"]}
    assert wanted <= matched, f"not matched: {sorted(wanted - matched)}"


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


def test_cel_uses_only_the_optional_index_form_and_is_balanced(policy: Doc) -> None:
    """`[?'key/with-slash']` compiles; `.?'key'` does not, and `x['key']` errors when the key is
    missing, which with `failurePolicy: Fail` rejects for the wrong reason."""
    for where, expr in _expressions(policy):
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


def test_cel_variables_are_defined_before_use(policy: Doc) -> None:
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


def test_cel_strings_hold_no_apostrophe(policy: Doc) -> None:
    """A `'` inside prose would end a CEL string literal early (e.g. "pod's")."""
    for where, expr in _expressions(policy):
        assert not re.search(r"[A-Za-z]'[A-Za-z]", expr), f"{where}: apostrophe inside a string"


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


@pytest.mark.parametrize("account", [SUBMITTER, DEPLOYER], ids=["submitter", "deployer"])
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


def test_every_rule_has_a_rejected_fixture_and_an_admitted_twin() -> None:
    assert ALL, f"no fixtures under {FIXTURES}"
    for rule in RULES:
        rejected = [f for f in REJECTED if f.rule == rule]
        assert rejected, f"rule {rule}: no rejected fixture"
        for f in rejected:
            assert f.twin.is_file(), f"{f.id}: no admitted twin"
            assert _fixture(f.twin).expect == "admitted"


def test_workload_kinds_are_covered_too() -> None:
    kinds = {f.doc["kind"] for f in REJECTED}
    assert {"Pod", "Deployment", "CronJob", "Sandbox"} <= kinds


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


def test_fixture_headers_are_complete(policy: Doc) -> None:
    messages = _messages(policy)
    for f in ALL:
        assert f.expect in {"rejected", "admitted"}, f.id
        assert f.rule in RULES, f.id
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
        if f.expect != "admitted" or _template(f.doc)["metadata"]["labels"].get(LANE) == "remote":
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
    if kind == "Sandbox":
        return dict(doc["spec"]["podTemplate"])
    return dict(doc["spec"]["template"])


@pytest.mark.parametrize("fixture", ALL, ids=[f.id for f in ALL])
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


@pytest.mark.parametrize("fixture", ALL, ids=[f.id for f in ALL])
def test_the_rule_model_agrees_with_each_fixture(fixture: Fixture, params: Doc) -> None:
    """A rejected fixture breaks exactly its own rule, so its message is the one the kind test
    sees; an admitted twin breaks none."""
    broken = broken_rules(fixture.doc, _Params.of(params))
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
