"""The PoC-5 admission objects as files: where they are, what the design names, the model of their
RBAC, and the paired fixtures under `admission/fixtures/`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from poc_harness.manifests import Doc, load_manifests
from poc_harness.paths import KIND_POC05

ADMISSION = KIND_POC05 / "admission"
FIXTURES = ADMISSION / "fixtures"
NAMESPACES = KIND_POC05 / "base/namespaces.yaml"
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
        "additionalPodMetadata.labels",
        "additionalPodMetadata.annotations",
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


def one_of_kind(path: Path, kind: str) -> Doc:
    """The one object of `kind` in the file `path`."""
    docs = [d for d in load_manifests(path) if d.get("kind") == kind]
    assert len(docs) == 1, f"{path.name}: expected one {kind}, found {len(docs)}"
    return docs[0]


def namespace_labels() -> dict[str, dict[str, str]]:
    return {
        d["metadata"]["name"]: dict(d["metadata"].get("labels", {}))
        for d in load_manifests(NAMESPACES)
        if d.get("kind") == "Namespace"
    }


def bindings_to(rbac: list[Doc], account: tuple[str, str]) -> list[Doc]:
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


def roles_of(rbac: list[Doc]) -> dict[tuple[str, str], Doc]:
    return {
        (d["kind"], d["metadata"]["name"]): d for d in rbac if d["kind"] in {"Role", "ClusterRole"}
    }


def role_of(rbac: list[Doc], binding: Doc) -> Doc:
    return roles_of(rbac)[(binding["roleRef"]["kind"], binding["roleRef"]["name"])]


def can_i(
    rbac: list[Doc], account: tuple[str, str], verb: str, resource: str, group: str, ns: str
) -> bool:
    """`kubectl auth can-i` for the RoleBindings in rbac.yaml (no ClusterRoleBinding is used)."""
    return any(
        group in rule["apiGroups"] and resource in rule["resources"] and verb in rule["verbs"]
        for b in bindings_to(rbac, account)
        if b["metadata"].get("namespace") == ns
        for rule in role_of(rbac, b)["rules"]
    )


def resources_of(rbac: list[Doc], account: tuple[str, str]) -> set[str]:
    roles = roles_of(rbac)
    return {
        r
        for b in bindings_to(rbac, account)
        for rule in roles[(b["roleRef"]["kind"], b["roleRef"]["name"])]["rules"]
        for r in rule["resources"]
    }


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


def read_fixture(path: Path) -> Fixture:
    fields: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if not line.startswith("#"):
            break
        m = HEADER.match(line)
        if m:
            fields[m.group(1)] = m.group(2)
    docs = load_manifests(path)
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


def all_fixtures() -> list[Fixture]:
    return [read_fixture(p) for p in sorted(FIXTURES.glob("*/*.yaml"))]


def template_of(doc: Doc) -> Doc:
    kind = doc["kind"]
    if kind == "Pod":
        return doc
    if kind == "CronJob":
        return dict(doc["spec"]["jobTemplate"]["spec"]["template"])
    if kind in {"Sandbox", "SandboxTemplate"}:
        return dict(doc["spec"]["podTemplate"])
    return dict(doc["spec"]["template"])
