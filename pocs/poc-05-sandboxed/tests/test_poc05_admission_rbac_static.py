"""PoC-5 admission, offline: the submitter's, deployer's, and dispatcher's RBAC in
`deploy/kind/poc05/admission/rbac.yaml` (plan `docs/plans/2026-10-02-poc-05-sandboxed.md`, section
2.5; per-call sandbox plan, section "RBAC"). A model of the RBAC (`poc_harness.admission_manifests`)
answers the design's `kubectl auth can-i` lists as the kind test expects. Split from
`test_poc05_admission_static.py`, which holds the policies and the fixtures.
"""

from __future__ import annotations

import pytest
from poc_harness.admission_manifests import (
    ADMISSION,
    CAN_I,
    DEPLOYER,
    DISPATCHER,
    ENFORCED_NAMESPACES,
    EXT_GROUP,
    PARAMS_NAME,
    PARAMS_NAMESPACE,
    SUBMITTER,
    SUBMITTER_NAMESPACES,
    TOOLS,
    bindings_to,
    can_i,
    resources_of,
    role_of,
    roles_of,
)
from poc_harness.manifests import Doc, load_manifests


@pytest.fixture(scope="module")
def rbac() -> list[Doc]:
    return load_manifests(ADMISSION / "rbac.yaml")


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
    found = bindings_to(rbac, SUBMITTER)
    assert all(b["kind"] == "RoleBinding" for b in found), "no cluster-wide grant"
    assert {b["metadata"]["namespace"] for b in found} == SUBMITTER_NAMESPACES
    assert "poc05-agents" not in SUBMITTER_NAMESPACES


def test_deployer_deploys_every_workload_namespace(rbac: list[Doc]) -> None:
    found = bindings_to(rbac, DEPLOYER)
    assert all(b["kind"] == "RoleBinding" for b in found), "no cluster-wide grant"
    assert {b["metadata"]["namespace"] for b in found} == ENFORCED_NAMESPACES
    needed = {"pods", "configmaps", "services", "serviceaccounts", "deployments", "sandboxes"}
    assert needed | {"networkpolicies"} <= resources_of(rbac, DEPLOYER)


@pytest.mark.parametrize(
    "account", [SUBMITTER, DEPLOYER, DISPATCHER], ids=["submitter", "deployer", "dispatcher"]
)
def test_tenant_and_deployer_cannot_touch_params_policy_rbac_or_secrets(
    rbac: list[Doc], account: tuple[str, str]
) -> None:
    roles = roles_of(rbac)
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
    for b in bindings_to(rbac, account):
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
    assert not {"networkpolicies", "services"} & resources_of(rbac, SUBMITTER)


def test_deployer_applies_templates_and_pools_but_never_claims(rbac: list[Doc]) -> None:
    """`run.sh workloads` applies tools/ as the deployer; claims are the dispatcher's alone."""
    for ns in ENFORCED_NAMESPACES:
        for resource in ("sandboxtemplates", "sandboxwarmpools"):
            assert can_i(rbac, DEPLOYER, "create", resource, EXT_GROUP, ns), (resource, ns)
        assert not can_i(rbac, DEPLOYER, "create", "sandboxclaims", EXT_GROUP, ns), ns


def test_the_dispatcher_role_is_create_get_delete_on_claims_in_tools(rbac: list[Doc]) -> None:
    (b,) = bindings_to(rbac, DISPATCHER)
    assert b["kind"] == "RoleBinding"
    assert b["metadata"]["namespace"] == "poc05-tools"
    role = role_of(rbac, b)
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
