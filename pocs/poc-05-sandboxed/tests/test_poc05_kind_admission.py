"""PoC-5 admission on kind: the real ValidatingAdmissionPolicy `agent-trust-rule` against every
fixture under `deploy/kind/poc05/admission/fixtures/` (plan section 2.5; ADR-005 decisions 4, 5).

Exit criterion 7: "The admission check rejects an `untrusted` workload, and a third-party image, in
the `sidecar` lane." Each rule (1 to 5, and 6a to 8 from the security review's F1 to F3) is proved
with a rejected object and its admitted twin, sent with `kubectl apply --dry-run=server`, so nothing
is created. Each fixture goes as the identity that may create it, so RBAC never refuses it for the
wrong reason: a `SandboxClaim` as the code-runner dispatcher (the deployer has no claim rights);
anything else in `poc05-agents` or `poc05-tools` as the platform deployer (the submitter has no
rights there: review F2, and the per-call sandbox plan, "RBAC", dropped its `poc05-tools` binding);
the rest as the tenant's submitter. A rejected fixture must name its rule's policy, not an RBAC
refusal. The per-call sandbox adds the template rules T1 to T5 (`sandbox-template-rule`) and the
claim rules C1 to C5 (`sandbox-claim-rule`). A policy that fails to compile enforces nothing (spike
question 5); the admitted twins alone would then pass, so the rejected ones are what proves the
policy is live. Neither identity can write the platform's params, and with the params gone every
request is denied.

The core review adds rules 7e and 7f (fixtures, R5a-01 and R5a-05), a projected-token case for 7d
(R5a-06), and rule 7g (R5a-16), which has no fixture file: a Pod may not carry ephemeral containers
at create, so `test_ephemeral_container_is_refused_next_to_its_twin` patches
`pods/ephemeralcontainers` on the live `agent-echo` pod as the cluster admin, with --dry-run=server.

The platform-security review of the core-review fixes adds the Service rule S1
(`agent-service-rule`, fixtures `ruleS1-service-type/`): the deployer's NodePort, LoadBalancer, or
externalIPs Service is refused, its ClusterIP twin admitted.

The per-call plan's `can-i` lists (`CAN_I`, in `poc_harness.admission_manifests`, so the static and
kind tests never drift) and the check that no agent-sandbox object owns a NetworkPolicy close the
security review's required changes 1, 2, and 4 on the cluster.

A kind test: marked `network` and skipped unless `KIND_DRILLS=1` and the `kind-poc05` context exists
(`poc_harness.drills`). Needs `deploy/kind/poc05/base/` and `admission/` applied (T19).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml
from poc_harness.admission_manifests import CAN_I, VERSION_POLICY_NAME, VERSION_RULES
from poc_harness.admission_manifests import DEPLOYER as DEPLOYER_ACCOUNT
from poc_harness.admission_manifests import DISPATCHER as DISPATCHER_ACCOUNT
from poc_harness.admission_manifests import SUBMITTER as SUBMITTER_ACCOUNT
from poc_harness.drills import KIND_SANDBOX
from poc_harness.kind import AGENTS_NS, SIDECAR_POD, get_json, pod
from poc_harness.manifests import load_manifests

pytestmark = KIND_SANDBOX

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
    "service rule": "agent-service-rule",
}
RBAC_REFUSAL = re.compile(r"cannot \w+ resource")
KIND = re.compile(r"(?m)^kind: (\S+)$")
PARAMS = ("agent-platform-system", "agent-trust-params")
HEADER = re.compile(r"^#\s*(expect|rule|message|namespace):\s*(.*?)\s*$")
NAMESPACE = re.compile(r"(?m)^  namespace: (\S+)$")
TIMEOUT_S = 60


@dataclass(frozen=True)
class Fixture:
    path: Path
    expect: str
    rule: str
    message: str | None
    namespace: str
    kind: str

    @property
    def identity(self) -> str | None:
        """Who sends the fixture. None is the cluster admin: a claim in poc05-remote is the PoC-6
        front door's, whose Role only `deploy/kind/poc06/run.sh` applies. The admission binding
        holds every identity, so the admin's dry run still meets the claim rule."""
        if self.kind == "SandboxClaim":
            return DISPATCHER if self.namespace == "poc05-tools" else None
        return DEPLOYER if self.namespace in DEPLOYER_NAMESPACES else SUBMITTER

    @property
    def policy(self) -> str:
        """The policy that holds the fixture's rule. Rule 9 keeps the `trust rule` prefix but
        lives in `agent-chassis-version`, so its binding can warn first (ADR-001 item 9)."""
        assert self.message is not None
        if self.rule in VERSION_RULES:
            return VERSION_POLICY_NAME
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
        rule=fields.get("rule", ""),
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


@pytest.mark.parametrize("name", ["agent-trust-rule", VERSION_POLICY_NAME, "agent-service-rule"])
def test_policy_and_binding_are_installed(name: str) -> None:
    for kind in ("validatingadmissionpolicy", "validatingadmissionpolicybinding"):
        got = kubectl("get", kind, name, "-o", "name")
        assert got.returncode == 0, got.stderr
    params = kubectl("get", "configmap", PARAMS[1], "-n", PARAMS[0], "-o", "name")
    assert params.returncode == 0, params.stderr


REJECTED = [f for f in ALL if f.expect == "rejected"]
ADMITTED = [f for f in ALL if f.expect == "admitted"]


def _twin(fixture: Fixture) -> Fixture:
    """The admitted twin in the rejected fixture's folder."""
    return _fixture(fixture.path.with_name("admitted.yaml"))


def _dry_run(fixture: Fixture) -> subprocess.CompletedProcess[str]:
    return kubectl("apply", "--dry-run=server", "-f", str(fixture.path), as_user=fixture.identity)


def test_every_fixture_is_rejected_or_admitted() -> None:
    """Criterion 7: "The admission check rejects an `untrusted` workload, and a third-party image,
    in the `sidecar` lane."

    Exit criterion 7: the two lists below cover every fixture, so none is skipped."""
    assert len(REJECTED) + len(ADMITTED) == len(ALL) > 0


@pytest.mark.parametrize("fixture", REJECTED, ids=[f.id for f in REJECTED])
def test_rejected_fixture_is_refused_with_its_rule_message(fixture: Fixture) -> None:
    """Criterion 7: "The admission check rejects an `untrusted` workload, and a third-party image,
    in the `sidecar` lane."

    Exit criterion 7: a rejected fixture is refused by its rule's policy with its message, not
    by RBAC. The paired control: its admitted twin, sent the same way, is admitted."""
    got = _dry_run(fixture)
    twin = _dry_run(_twin(fixture))

    err = _one_line(got.stderr)
    assert got.returncode != 0, f"{fixture.id}: admitted: {got.stdout}"
    assert not RBAC_REFUSAL.search(err), f"{fixture.id}: refused by RBAC, not admission: {err}"
    assert fixture.message is not None
    assert f"'{fixture.policy}'" in err, f"{fixture.id}: refused by something else: {err}"
    assert fixture.message in err, f"{fixture.id}: wrong message: {err}"
    assert twin.returncode == 0, f"{fixture.id}: twin refused: {twin.stderr}"


@pytest.mark.parametrize("fixture", ADMITTED, ids=[f.id for f in ADMITTED])
def test_admitted_twin_is_admitted(fixture: Fixture) -> None:
    """Criterion 7: "The admission check rejects an `untrusted` workload, and a third-party image,
    in the `sidecar` lane."

    Exit criterion 7: the admitted twin of each rule's fixture passes a server dry run as the
    identity that may create it, so the rejected fixtures are refused for their rule alone."""
    got = _dry_run(fixture)

    assert got.returncode == 0, f"{fixture.id}: refused: {got.stderr}"
    assert "(server dry run)" in got.stdout


VERSION_BINDING = ADMISSION / "chassis-version-binding.yaml"
RULE9_OLD_TAG = FIXTURES / "rule9-chassis-version" / "rejected-old-tag.yaml"
WARN_RING = json.dumps({"spec": {"validationActions": ["Warn", "Audit"]}})
# A binding change reaches the API server's admission informer within seconds; retry that long.
RING_SETTLE_TRIES = 15


def _dry_run_until(path: Path, admitted: bool) -> subprocess.CompletedProcess[str]:
    """`kubectl apply --dry-run=server` of `path` as the deployer, retried until it is admitted
    (or refused) as asked, so a binding patched a moment ago has reached admission."""
    for _ in range(RING_SETTLE_TRIES):
        got = kubectl("apply", "--dry-run=server", "-f", str(path), as_user=DEPLOYER)
        if (got.returncode == 0) == admitted:
            return got
        time.sleep(1)
    return got


def test_a_chassis_below_the_minimum_is_admitted_with_a_warning_in_the_warn_ring() -> None:
    """ADR-001 item 9: "it warns first, then enforces." With `agent-chassis-version`'s binding
    on [Warn, Audit] (the ring cycle after a new minimum, 026 CH-4), the pod on the old chassis
    tag is admitted and the request carries rule 9's message as a warning; the binding restored
    from its file ([Deny, Audit]) refuses the same pod again. The trust rule's binding is not
    touched: the paired control is that a rule-1 fixture stays refused while rule 9 warns."""
    rule1 = FIXTURES / "rule1-trust-label" / "rejected.yaml"
    before = kubectl(
        "get", "validatingadmissionpolicybinding", VERSION_POLICY_NAME,
        "-o", "jsonpath={.spec.validationActions}",
    )  # fmt: skip
    assert before.returncode == 0, before.stderr
    assert json.loads(before.stdout) == ["Deny", "Audit"], (
        f"the binding is not in the enforce ring before the test: {before.stdout}; a killed run "
        "left it on Warn; restore it with `deploy/kind/poc05/run.sh admission`"
    )
    patched = kubectl(
        "patch", "validatingadmissionpolicybinding", VERSION_POLICY_NAME, "--type", "merge",
        "-p", WARN_RING,
    )  # fmt: skip
    assert patched.returncode == 0, patched.stderr
    try:
        warned = _dry_run_until(RULE9_OLD_TAG, admitted=True)
        still_denied = kubectl("apply", "--dry-run=server", "-f", str(rule1), as_user=DEPLOYER)
    finally:
        restored = kubectl("apply", "-f", str(VERSION_BINDING))
    if restored.returncode != 0:
        pytest.fail(f"the binding was not restored from {VERSION_BINDING.name}: {restored.stderr}")
    denied_again = _dry_run_until(RULE9_OLD_TAG, admitted=False)

    warning = _one_line(warned.stderr)
    assert warned.returncode == 0, f"warn ring refused the pod: {warning}"
    assert "Warning:" in warning and f"'{VERSION_POLICY_NAME}'" in warning, warning
    assert "trust rule 9:" in warning, warning
    assert still_denied.returncode != 0 and "trust rule 1:" in _one_line(still_denied.stderr)
    assert denied_again.returncode != 0, "the binding did not go back to Deny"
    assert "trust rule 9:" in _one_line(denied_again.stderr), denied_again.stderr


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


WORKLOAD_NAMESPACES = ("poc05-agents", "poc05-remote", "poc05-tools")


def admin_can_i(*args: str) -> str:
    """`can-i` as the kind cluster admin (the context's own user, no `--as`)."""
    return kubectl("auth", "can-i", *args).stdout.strip()


@pytest.mark.parametrize("ns", WORKLOAD_NAMESPACES)
@pytest.mark.parametrize("user", [SUBMITTER, DEPLOYER], ids=["submitter", "deployer"])
def test_nobody_but_the_admin_reads_secrets(user: str, ns: str) -> None:
    """Criterion 4: "No provider key or internal credential is visible in any workload container or
    remote pod, and neither holds a service account token."

    Exit criterion 4 (only the chassis holds credentials): neither the submitter nor the
    deployer may get Secrets in a workload namespace. The paired control: the cluster admin, who
    runs `seed.sh`, may."""
    denied = can_i(user, "get", "secrets", "-n", ns)
    allowed = admin_can_i("get", "secrets", "-n", ns)

    assert denied == "no", ns
    assert allowed == "yes", ns


@pytest.mark.parametrize("resource", ["networkpolicies", "services"])
def test_submitter_cannot_open_network_edges(resource: str) -> None:
    """Review F3: NetworkPolicies and Services select by labels; only the platform writes them.
    The paired control: the platform deployer (`agent-deployer` in `rbac.yaml`) may create a
    Service in the same namespace, and the cluster admin a NetworkPolicy (core review R5a-02: the
    deployer has no NetworkPolicy rights; `run.sh` applies each `network-policy.yaml` as admin)."""
    denied = can_i(SUBMITTER, "create", resource, "-n", "poc05-remote")
    if resource == "networkpolicies":
        allowed = admin_can_i("create", resource, "-n", "poc05-remote")
        deployer = can_i(DEPLOYER, "create", resource, "-n", "poc05-remote")
        assert deployer == "no", "core review R5a-02: the deployer writes no NetworkPolicy"
    else:
        allowed = can_i(DEPLOYER, "create", resource, "-n", "poc05-remote")

    assert denied == "no", resource
    assert allowed == "yes", resource


SERVICE_FIXTURES = FIXTURES / "ruleS1-service-type"


@pytest.mark.parametrize("ns", ["poc05-agents", "poc05-tools"])
def test_deployer_nodeport_service_is_refused_by_rule_s1(ns: str) -> None:
    """Criterion 5: "an agent pod has no route to the internal services except through the
    chassis" (platform-security review of the core-review fixes, HIGH).

    Exit criterion 5: the deployer may write Services, so a NodePort on a chassis's 8080 would
    reach it from the node, past the gateway-only ingress (R5a-07). As the deployer, with
    --dry-run=server, the NodePort fixture is refused by agent-service-rule and not by RBAC. The
    paired control: the ClusterIP twin, sent the same way, is admitted."""
    rejected = SERVICE_FIXTURES / "rejected.yaml"
    twin = SERVICE_FIXTURES / "admitted.yaml"

    got = _dry_run_text(_in_namespace(rejected, ns))
    allowed = _dry_run_text(_in_namespace(twin, ns))

    err = _one_line(got.stderr)
    assert got.returncode != 0, got.stdout
    assert not RBAC_REFUSAL.search(err), err
    assert "'agent-service-rule'" in err and "service rule S1" in err, err
    assert allowed.returncode == 0, allowed.stderr


def _in_namespace(path: Path, ns: str) -> str:
    doc = yaml.safe_load(path.read_text())
    doc["metadata"]["namespace"] = ns
    return yaml.safe_dump(doc)


def _dry_run_text(manifest: str) -> subprocess.CompletedProcess[str]:
    exe = shutil.which("kubectl")
    assert exe is not None, "kubectl is not on PATH"
    command = [exe, "--context", CONTEXT, "apply", "--dry-run=server", "-f", "-"]
    command.append(f"--as={DEPLOYER}")
    return subprocess.run(
        command, input=manifest, capture_output=True, text=True, timeout=TIMEOUT_S, check=False
    )


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


# Rule 8 in its cloud mode: a made-up digest; a dry run never pulls the image.
DIGEST = "@sha256:" + "0" * 64
RULE8_TWIN = FIXTURES / "rule8-pull-policy" / "admitted.yaml"
RULE8_MESSAGE = "trust rule 8"


def _pinned(image: str) -> str:
    # The tag stays next to the digest (`repo:tag@sha256:...`), as the library chart renders it:
    # rule 9 reads the chassis tag, and a digest alone has none.
    return image + DIGEST


def _with_digests(pod: dict[str, object]) -> dict[str, object]:
    """A copy of `pod` with every image pinned by `DIGEST` next to its tag."""
    spec = pod["spec"]
    assert isinstance(spec, dict)
    pinned = {
        key: [{**c, "image": _pinned(c["image"])} for c in spec[key]]
        for key in ("initContainers", "containers")
        if key in spec
    }
    return {**pod, "spec": {**spec, **pinned}}


def test_digest_mode_refuses_a_tag_and_admits_its_digest_twin(tmp_path: Path) -> None:
    """Criterion 7: "The admission check rejects an `untrusted` workload, and a third-party image,
    in the `sidecar` lane."

    Exit criterion 7, rule 8 with `imageSource: digest` (the cloud mode, review R5a-15): with
    the params patched to `digest`, a sidecar pod whose images carry only a tag is refused by
    `agent-trust-rule` with 'trust rule 8', and its twin pinned by `@sha256` is admitted. The
    params are restored after, also on failure, and the tag-only pod is admitted again."""
    tagged = yaml.safe_load(RULE8_TWIN.read_text())
    pinned_path = tmp_path / "rule8-digest.yaml"
    pinned_path.write_text(yaml.safe_dump(_with_digests(tagged)))
    ns, name = PARAMS
    params = str(ADMISSION / "params.yaml")
    # Rule 9 in digest mode reads the chassis digest, so the made-up one is listed for the patch.
    patch = f'{{"data":{{"imageSource":"digest","supportedChassisDigests":"{DIGEST[1:]}"}}}}'
    patched = kubectl("patch", "configmap", name, "-n", ns, "--type", "merge", "-p", patch)
    assert patched.returncode == 0, patched.stderr
    try:
        refused = kubectl("apply", "--dry-run=server", "-f", str(RULE8_TWIN), as_user=DEPLOYER)
        admitted = kubectl("apply", "--dry-run=server", "-f", str(pinned_path), as_user=DEPLOYER)
    finally:
        restored = kubectl("apply", "-f", params)
        assert restored.returncode == 0, restored.stderr
    again = kubectl("apply", "--dry-run=server", "-f", str(RULE8_TWIN), as_user=DEPLOYER)

    err = _one_line(refused.stderr)
    assert refused.returncode != 0, refused.stdout
    assert "'agent-trust-rule'" in err and RULE8_MESSAGE in err, err
    assert admitted.returncode == 0, admitted.stderr
    assert again.returncode == 0, again.stderr


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
    """Criterion 7: "The admission check rejects an `untrusted` workload, and a third-party image,
    in the `sidecar` lane."

    Exit criterion 7 (admission and its RBAC), per-call plan "RBAC"; security review of the
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
    """Criterion 7: "The admission check rejects an `untrusted` workload, and a third-party image,
    in the `sidecar` lane."

    Exit criterion 7, security review of the design, required change 4: after `run.sh
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
        for d in load_manifests(path)
        if d.get("kind") == "NetworkPolicy"
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
    """Criterion 8: "The hostile suites pass in both lanes."

    Exit criterion 8 (code runs sandboxed with no egress); template rule T1 and the security
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


# Review R5a-16: ephemeral containers on the live `agent-echo` pod, sent as the cluster admin
# (who holds `pods/ephemeralcontainers`; RBAC is not what refuses) with --dry-run=server, so the
# pod never changes. Each refused case sits next to an admitted twin that differs in one field.
DEBUG_IMAGE = "kind.local/agent-platform/echo-python:poc05"
OUTSIDE_IMAGE = "docker.io/library/busybox:1.36"
# (case id, refused image and target, admitted image and target, message the refusal carries)
EPHEMERAL_CASES = [
    ("image", (OUTSIDE_IMAGE, "workload"), (DEBUG_IMAGE, "workload"), "trust rule 3"),
    ("target", (DEBUG_IMAGE, "chassis"), (DEBUG_IMAGE, "workload"), "trust rule 7g"),
]


def _debug_container(name: str, image: str, target: str) -> dict[str, object]:
    """A restricted-profile ephemeral container that also meets rules 7f and 8."""
    return {
        "name": name,
        "image": image,
        "imagePullPolicy": "Never",
        "targetContainerName": target,
        "command": ["sleep", "1"],
        "securityContext": {
            "allowPrivilegeEscalation": False,
            "capabilities": {"drop": ["ALL"]},
            "readOnlyRootFilesystem": True,
            "runAsNonRoot": True,
            "runAsUser": 10003,
            "runAsGroup": 10003,
            "seccompProfile": {"type": "RuntimeDefault"},
        },
    }


def _add_debug(
    pod_name: str, name: str, image: str, target: str
) -> subprocess.CompletedProcess[str]:
    patch = {"spec": {"ephemeralContainers": [_debug_container(name, image, target)]}}
    return kubectl(
        "patch", "pod", pod_name, "-n", AGENTS_NS, "--subresource=ephemeralcontainers",
        "--type=strategic", "-p", json.dumps(patch), "--dry-run=server",
    )  # fmt: skip


@pytest.mark.parametrize(
    ("refused", "admitted", "message"),
    [c[1:] for c in EPHEMERAL_CASES],
    ids=[c[0] for c in EPHEMERAL_CASES],
)
def test_ephemeral_container_is_refused_next_to_its_twin(
    refused: tuple[str, str], admitted: tuple[str, str], message: str
) -> None:
    """The image case, criterion 7: "The admission check rejects an `untrusted` workload, and a
    third-party image, in the `sidecar` lane." The target case, ADR-001 hard requirement 1: "Every
    internal service requires a credential that only the chassis holds." A debug container joined
    to the chassis's process namespace could read its credentials.

    Exit criterion 7 and hard requirement 1 (review R5a-16): given the live `agent-echo` pod, an
    ephemeral container with an image outside the platform prefix is refused by
    `agent-trust-rule` with 'trust rule 3', and one with `targetContainerName: chassis` with
    'trust rule 7g'. The paired control: the same container with our image, aimed at the
    workload, is admitted. All three are `--dry-run=server`; the pod is not changed."""
    name = pod(AGENTS_NS, SIDECAR_POD)["metadata"]["name"]

    got = _add_debug(name, "poc05-debug", *refused)
    twin = _add_debug(name, "poc05-debug", *admitted)

    err = _one_line(got.stderr)
    assert got.returncode != 0, f"admitted: {got.stdout}"
    assert "'agent-trust-rule'" in err and message in err, err
    assert twin.returncode == 0, twin.stderr
