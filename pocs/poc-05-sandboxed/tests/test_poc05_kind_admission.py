"""PoC-5 admission on kind: the real ValidatingAdmissionPolicy `agent-trust-rule` against every
fixture under `deploy/kind/poc05/admission/fixtures/` (plan section 2.5; ADR-005 decisions 4, 5).

Exit criterion 7: "Admission rejects `untrusted` and a third-party image in `sidecar`." Each rule
(1 to 5, and 6a to 8 from the security review's F1 to F3) is proved with a rejected object and its
admitted twin, sent with `kubectl apply --dry-run=server`, so nothing is created. Fixtures in
`poc05-agents` go as the platform deployer (the submitter has no rights there, review F2, so RBAC
would refuse them for the wrong reason); the others go as the tenant's submitter. A policy that
fails to compile enforces nothing (spike question 5); the admitted twins alone would then pass, so
the rejected ones are what proves the policy is live. Neither identity can write the platform's
params, and with the params gone every request is denied.

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

ROOT = Path(__file__).resolve().parents[3]
ADMISSION = ROOT / "deploy/kind/poc05/admission"
FIXTURES = ADMISSION / "fixtures"
CONTEXT = "kind-poc05"
SUBMITTER = "system:serviceaccount:poc05-tenant:submitter"
DEPLOYER = "system:serviceaccount:agent-platform-system:deployer"
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

    @property
    def identity(self) -> str:
        return DEPLOYER if self.namespace == "poc05-agents" else SUBMITTER

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
    m = NAMESPACE.search(path.read_text())
    assert m, f"{path}: no metadata.namespace"
    return Fixture(
        path=path, expect=fields["expect"], message=fields.get("message"), namespace=m.group(1)
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
    assert "agent-trust-rule" in err, f"{fixture.id}: refused by something else: {err}"
    assert fixture.message is not None
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
