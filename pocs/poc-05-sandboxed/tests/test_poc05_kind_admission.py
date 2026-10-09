"""PoC-5 admission on kind: the real ValidatingAdmissionPolicy `agent-trust-rule` against every
fixture under `deploy/kind/poc05/admission/fixtures/` (plan section 2.5; ADR-005 decisions 4, 5).

Exit criterion 7: "Admission rejects `untrusted` and a third-party image in `sidecar`." Each rule
(1 to 5, and 6a to 8 from the security review's F1 to F3) is proved with a rejected object and its
admitted twin, sent with `kubectl apply --dry-run=server`, so nothing is created. Each fixture goes
as the identity that may create it, so RBAC never refuses it for the wrong reason: a
`SandboxClaim` as the code-runner dispatcher (the deployer has no claim rights); anything else in
`poc05-agents` or `poc05-tools` as the platform deployer (the submitter has no rights there:
review F2, and the per-call sandbox plan, "RBAC", dropped its `poc05-tools` binding); the rest as
the tenant's submitter. A rejected fixture must name its rule's policy, not an RBAC refusal. The
per-call sandbox adds the template rules T1 to T5 (`sandbox-template-rule`) and the claim rules C1
to C5 (`sandbox-claim-rule`). A policy that fails to compile enforces nothing (spike question 5);
the admitted twins alone would then pass, so the rejected ones are what proves the policy is live.
Neither identity can write the platform's params, and with the params gone every request is
denied.

The per-call plan's `can-i` lists (`CAN_I`, imported from the static test, so the two never drift)
and the check that no agent-sandbox object owns a NetworkPolicy close the security review's
required changes 1, 2, and 4 on the cluster.

A kind test: marked `network` and skipped unless `POC05_KIND=1` and the `kind-poc05` context
exists (`poc05_conftest.py`). Needs `deploy/kind/poc05/base/` and `admission/` applied (T19).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml
from poc05_kind import get_json
from test_poc05_admission_static import CAN_I
from test_poc05_admission_static import DEPLOYER as DEPLOYER_ACCOUNT
from test_poc05_admission_static import DISPATCHER as DISPATCHER_ACCOUNT
from test_poc05_admission_static import SUBMITTER as SUBMITTER_ACCOUNT

ROOT = Path(__file__).resolve().parents[3]
ADMISSION = ROOT / "deploy/kind/poc05/admission"
FIXTURES = ADMISSION / "fixtures"
CONTEXT = "kind-poc05"


def sa_user(account: tuple[str, str]) -> str:
    """The `--as` name of a service account `(namespace, name)`."""
    return f"system:serviceaccount:{account[0]}:{account[1]}"


SUBMITTER = sa_user(SUBMITTER_ACCOUNT)
DEPLOYER = sa_user(DEPLOYER_ACCOUNT)
DISPATCHER = sa_user(DISPATCHER_ACCOUNT)
# The namespaces only the platform deployer writes (review F2; per-call plan, "RBAC").
DEPLOYER_NAMESPACES = {"poc05-agents", "poc05-tools"}
# A rule message's prefix names the policy that holds the rule.
POLICY_OF_PREFIX = {
    "trust rule": "agent-trust-rule",
    "template rule": "sandbox-template-rule",
    "claim rule": "sandbox-claim-rule",
}
RBAC_REFUSAL = re.compile(r"cannot \w+ resource")
KIND = re.compile(r"(?m)^kind: (\S+)$")
PARAMS = ("agent-platform-system", "agent-trust-params")
HEADER = re.compile(r"^#\s*(expect|message|namespace):\s*(.*?)\s*$")
NAMESPACE = re.compile(r"(?m)^  namespace: (\S+)$")
TIMEOUT_S = 60


@dataclass(frozen=True)
class Fixture:
    path: Path
    expect: str
    message: str | None
    namespace: str
    kind: str

    @property
    def identity(self) -> str:
        if self.kind == "SandboxClaim":
            return DISPATCHER
        return DEPLOYER if self.namespace in DEPLOYER_NAMESPACES else SUBMITTER

    @property
    def policy(self) -> str:
        assert self.message is not None
        prefix = self.message.split(" ", 2)[:2]
        return POLICY_OF_PREFIX[" ".join(prefix)]

    @property
    def id(self) -> str:
        return f"{self.path.parent.name}/{self.path.stem}"


def _fixture(path: Path) -> Fixture:
    fields: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if not line.startswith("#"):
            break
        m = HEADER.match(line)
        if m:
            fields[m.group(1)] = m.group(2)
    text = path.read_text()
    m = NAMESPACE.search(text)
    assert m, f"{path}: no metadata.namespace"
    k = KIND.search(text)
    assert k, f"{path}: no kind"
    return Fixture(
        path=path,
        expect=fields["expect"],
        message=fields.get("message"),
        namespace=m.group(1),
        kind=k.group(1),
    )


ALL = [_fixture(p) for p in sorted(FIXTURES.glob("*/*.yaml"))]


def kubectl(*args: str, as_user: str | None = None) -> subprocess.CompletedProcess[str]:
    exe = shutil.which("kubectl")
    assert exe is not None, "kubectl is not on PATH"
    command = [exe, "--context", CONTEXT, *args]
    if as_user is not None:
        command.append(f"--as={as_user}")
    return subprocess.run(command, capture_output=True, text=True, timeout=TIMEOUT_S, check=False)


def _one_line(text: str) -> str:
    return " ".join(text.split())


def test_policy_and_binding_are_installed() -> None:
    for kind in ("validatingadmissionpolicy", "validatingadmissionpolicybinding"):
        got = kubectl("get", kind, "agent-trust-rule", "-o", "name")
        assert got.returncode == 0, got.stderr
    params = kubectl("get", "configmap", PARAMS[1], "-n", PARAMS[0], "-o", "name")
    assert params.returncode == 0, params.stderr


@pytest.mark.parametrize("fixture", ALL, ids=[f.id for f in ALL])
def test_fixture_outcome_matches_its_header(fixture: Fixture) -> None:
    got = kubectl("apply", "--dry-run=server", "-f", str(fixture.path), as_user=fixture.identity)
    if fixture.expect == "admitted":
        assert got.returncode == 0, f"{fixture.id}: refused: {got.stderr}"
        assert "(server dry run)" in got.stdout
        return
    assert got.returncode != 0, f"{fixture.id}: admitted: {got.stdout}"
    err = _one_line(got.stderr)
    assert not RBAC_REFUSAL.search(err), f"{fixture.id}: refused by RBAC, not admission: {err}"
    assert fixture.message is not None
    assert f"'{fixture.policy}'" in err, f"{fixture.id}: refused by something else: {err}"
    assert fixture.message in err, f"{fixture.id}: wrong message: {err}"


def can_i(user: str, *args: str) -> str:
    return kubectl("auth", "can-i", *args, as_user=user).stdout.strip()


@pytest.mark.parametrize("verb", ["update", "patch", "delete"])
@pytest.mark.parametrize("user", [SUBMITTER, DEPLOYER], ids=["submitter", "deployer"])
def test_tenant_and_deployer_cannot_write_the_params(verb: str, user: str) -> None:
    ns, name = PARAMS
    assert can_i(user, verb, f"configmap/{name}", "-n", ns) == "no"


def test_submitter_may_write_configmaps_in_its_own_namespaces() -> None:
    """The paired control: the same verb works where the submitter's role is bound."""
    assert can_i(SUBMITTER, "create", "configmaps", "-n", "poc05-remote") == "yes"


def test_submitter_cannot_create_pods_where_the_chassis_secrets_live() -> None:
    """Review F2, with its controls: the deployer may, and the submitter may in poc05-remote."""
    assert can_i(SUBMITTER, "create", "pods", "-n", "poc05-agents") == "no"
    assert can_i(DEPLOYER, "create", "pods", "-n", "poc05-agents") == "yes"
    assert can_i(SUBMITTER, "create", "pods", "-n", "poc05-remote") == "yes"


@pytest.mark.parametrize("user", [SUBMITTER, DEPLOYER], ids=["submitter", "deployer"])
def test_nobody_but_the_admin_reads_secrets(user: str) -> None:
    for ns in ("poc05-agents", "poc05-remote", "poc05-tools"):
        assert can_i(user, "get", "secrets", "-n", ns) == "no", ns


def test_submitter_cannot_open_network_edges() -> None:
    """Review F3: NetworkPolicies and Services select by labels; only the platform writes them."""
    for resource in ("networkpolicies", "services"):
        assert can_i(SUBMITTER, "create", resource, "-n", "poc05-remote") == "no", resource


def test_missing_params_deny_everything() -> None:
    """`parameterNotFoundAction: Deny`: with the params gone even an admitted twin is refused;
    restored, it is admitted again. Deletes and re-applies the real params ConfigMap."""
    twin = FIXTURES / "rule1-trust-label" / "admitted.yaml"
    ns, name = PARAMS
    params = str(ADMISSION / "params.yaml")
    deleted = kubectl("delete", "configmap", name, "-n", ns)
    assert deleted.returncode == 0, deleted.stderr
    try:
        denied = kubectl("apply", "--dry-run=server", "-f", str(twin), as_user=DEPLOYER)
        assert denied.returncode != 0, denied.stdout
        assert "agent-trust-rule" in _one_line(denied.stderr), denied.stderr
    finally:
        restored = kubectl("apply", "-f", params)
        assert restored.returncode == 0, restored.stderr
    admitted = kubectl("apply", "--dry-run=server", "-f", str(twin), as_user=DEPLOYER)
    assert admitted.returncode == 0, admitted.stderr


def test_submitter_apply_of_the_params_is_forbidden() -> None:
    params = str(ADMISSION / "params.yaml")
    denied = kubectl("apply", "--dry-run=server", "-f", params, as_user=SUBMITTER)
    assert denied.returncode != 0
    assert "forbidden" in denied.stderr.lower(), denied.stderr
    allowed = kubectl("apply", "--dry-run=server", "-f", params)
    assert allowed.returncode == 0, allowed.stderr


CanI = tuple[tuple[str, str], str, str, str, str, bool]


def _resource(resource: str, group: str) -> str:
    return f"{resource}.{group}" if group else resource


def _control(account: tuple[str, str]) -> CanI:
    """The identity's first `yes` line in `CAN_I`: the same identity may do something."""
    return next(c for c in CAN_I if c[0] == account and c[5])


@pytest.mark.parametrize("case", CAN_I, ids=[f"{a[1]}-{v}-{r}-{ns}" for a, v, r, _, ns, _ in CAN_I])
def test_can_i_on_kind_matches_the_design(case: CanI) -> None:
    """Exit criterion 7 (admission and its RBAC), per-call plan "RBAC"; security review of the
    design, required changes 2 and 4. The dispatcher may only create, get, and delete claims in
    `poc05-tools`; the submitter may create no pod in `poc05-tools` and no claim, template, or pool
    in any `poc05-*` namespace. Every `no` is checked next to its identity's `yes` control (the
    dispatcher creates claims in `poc05-tools`; the submitter creates pods in `poc05-remote`)."""
    account, verb, resource, group, ns, allowed = case
    control = _control(account)
    _, c_verb, c_resource, c_group, c_ns, _ = control
    assert can_i(sa_user(account), c_verb, _resource(c_resource, c_group), "-n", c_ns) == "yes"
    want = "yes" if allowed else "no"
    assert can_i(sa_user(account), verb, _resource(resource, group), "-n", ns) == want


def test_submitter_cannot_create_pods_in_tools_after_admission() -> None:
    """Exit criterion 7, security review of the design, required change 4: after `run.sh
    admission` the submitter has no binding in `poc05-tools`, so it cannot fill the shared quota or
    start a pod next to the warm pool. Controls: the deployer may, and the submitter may in
    `poc05-remote`."""
    assert can_i(SUBMITTER, "create", "pods", "-n", "poc05-tools") == "no"
    assert can_i(SUBMITTER, "patch", "pods", "-n", "poc05-tools") == "no"
    assert can_i(DEPLOYER, "create", "pods", "-n", "poc05-tools") == "yes"
    assert can_i(SUBMITTER, "create", "pods", "-n", "poc05-remote") == "yes"


AGENT_SANDBOX_GROUPS = {"agents.x-k8s.io", "extensions.agents.x-k8s.io"}
# The platform's own NetworkPolicies: each folder's file, in the folder's kustomize namespace,
# and the default deny in every namespace.
PLATFORM_POLICY_FILES = {
    "poc05-agents": ROOT / "deploy/kind/poc05/agents/network-policy.yaml",
    "poc05-platform": ROOT / "deploy/kind/poc05/platform/network-policy.yaml",
    "poc05-remote": ROOT / "deploy/kind/poc05/remote/network-policy.yaml",
    "poc05-tools": ROOT / "deploy/kind/poc05/tools/network-policy.yaml",
}
DEFAULT_DENY = ROOT / "deploy/kind/poc05/base/default-deny.yaml"


def _policies_in(path: Path, namespace: str | None = None) -> set[tuple[str, str]]:
    return {
        (namespace or d["metadata"]["namespace"], d["metadata"]["name"])
        for d in yaml.safe_load_all(path.read_text())
        if d and d.get("kind") == "NetworkPolicy"
    }


def _platform_policies() -> set[tuple[str, str]]:
    found = {p for ns, f in PLATFORM_POLICY_FILES.items() for p in _policies_in(f, ns)}
    deny = {p for p in _policies_in(DEFAULT_DENY) if p[0] in PLATFORM_POLICY_FILES}
    return found | deny


def _agent_sandbox_owned(policy: dict[str, object]) -> bool:
    meta = policy["metadata"]
    assert isinstance(meta, dict)
    owners = meta.get("ownerReferences") or []
    return any(o["apiVersion"].split("/")[0] in AGENT_SANDBOX_GROUPS for o in owners)


def test_no_network_policy_is_owned_by_an_agent_sandbox_object() -> None:
    """Exit criterion 8 (code runs sandboxed with no egress); template rule T1 and the security
    review of the design, required change 1: a `SandboxTemplate` left `Managed` makes the
    controller write a NetworkPolicy that allows internet egress (B2). No NetworkPolicy in any
    `poc05-*` namespace has an owner from `agents.x-k8s.io` or `extensions.agents.x-k8s.io`.
    Control: the same listing holds every policy the platform ships, so an empty or wrong
    listing cannot pass."""
    items = get_json("networkpolicies", "-A")["items"]
    live = [p for p in items if p["metadata"]["namespace"].startswith("poc05-")]
    names = {(p["metadata"]["namespace"], p["metadata"]["name"]) for p in live}
    missing = _platform_policies() - names
    assert not missing, f"platform NetworkPolicies not listed: {sorted(missing)}"
    owned = sorted(
        f"{p['metadata']['namespace']}/{p['metadata']['name']}"
        for p in live
        if _agent_sandbox_owned(p)
    )
    assert not owned, f"NetworkPolicies owned by an agent-sandbox object: {owned}"
