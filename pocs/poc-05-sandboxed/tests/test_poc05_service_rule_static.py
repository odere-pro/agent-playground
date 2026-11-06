"""PoC-5 Service rule S1, offline (platform-security review of the core-review fixes, HIGH).

Exit criterion 5 (offline part): "an agent pod has no route to the internal services except
through the chassis", and the chassis's public port takes only the gateway (core review R5a-07).
A NodePort or LoadBalancer Service on a chassis's 8080 is reached from the node, with the node as
the source, past that ingress rule; `externalIPs` takes any address (CVE-2020-8554). The platform
deployer may write Services, so admission refuses every type but ClusterIP
(`deploy/kind/poc05/admission/service-policy.yaml`).

A small Python model of S1 is checked against the paired fixtures in
`admission/fixtures/ruleS1-service-type/`, and every Service the repo ships under
`deploy/kind/poc05` and `deploy/kind/poc06` passes it. The CEL itself compiles and refuses only on
kind (`test_poc05_kind_admission.py`).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from poc_harness.admission_manifests import ENFORCED_NAMESPACES, read_fixture
from poc_harness.manifests import load_manifests
from poc_harness.paths import ROOT

POC05 = ROOT / "deploy/kind/poc05"
POC06 = ROOT / "deploy/kind/poc06"
ADMISSION = POC05 / "admission"
FIXTURES = ADMISSION / "fixtures" / "ruleS1-service-type"
POLICY_NAME = "agent-service-rule"
ENFORCE = {"agents.platform/admission": "enforce"}
FIELDS = ("externalIPs", "loadBalancerIP", "externalName")


def s1_problems(service: dict[str, Any]) -> list[str]:
    """What breaks rule S1 in `service`: a type other than ClusterIP, or an outside address."""
    spec = service.get("spec") or {}
    problems = []
    if spec.get("type", "ClusterIP") != "ClusterIP":
        problems.append(f"type {spec['type']}")
    problems.extend(f"sets {field}" for field in FIELDS if spec.get(field))
    return problems


def _policy() -> dict[str, Any]:
    return dict(yaml.safe_load((ADMISSION / "service-policy.yaml").read_text()))


def _message() -> str:
    (validation,) = _policy()["spec"]["validations"]
    return str(validation["message"])


FIXTURE_FILES = sorted(FIXTURES.glob("*.yaml"))
REJECTED = [p for p in FIXTURE_FILES if read_fixture(p).expect == "rejected"]


def test_poc05_service_rule_policy_fails_closed_on_services() -> None:
    """Criterion 5: the policy matches every Service write, fails closed, and reads the four
    fields its message names."""
    spec = _policy()["spec"]
    (rule,) = spec["matchConstraints"]["resourceRules"]
    (validation,) = spec["validations"]

    assert _policy()["metadata"]["name"] == POLICY_NAME
    assert spec["failurePolicy"] == "Fail"
    assert rule == {
        "apiGroups": [""],
        "apiVersions": ["v1"],
        "operations": ["CREATE", "UPDATE"],
        "resources": ["services"],
    }
    assert "paramKind" not in spec
    assert validation["message"].startswith("service rule S1: ")
    assert "object.spec.type == 'ClusterIP'" in validation["expression"]
    assert "!has(object.spec.type)" in validation["expression"], "absent must count as ClusterIP"
    for field in FIELDS:
        assert f"object.spec.{field}" in validation["expression"], field
        assert field in validation["message"], field


def test_poc05_service_rule_binding_denies_in_the_workload_namespaces() -> None:
    """Criterion 5: the binding has binding.yaml's selector and actions, and the selector picks
    exactly poc05-agents, poc05-remote, and poc05-tools."""
    (binding,) = load_manifests(ADMISSION / "service-binding.yaml")
    spec = binding["spec"]
    namespaces = load_manifests(POC05 / "base/namespaces.yaml")
    labelled = {
        n["metadata"]["name"]
        for n in namespaces
        if n.get("kind") == "Namespace"
        and ENFORCE.items() <= (n["metadata"].get("labels") or {}).items()
    }

    assert spec["policyName"] == POLICY_NAME == binding["metadata"]["name"]
    assert spec["validationActions"] == ["Deny", "Audit"]
    assert spec["matchResources"] == {"namespaceSelector": {"matchLabels": ENFORCE}}
    assert "paramRef" not in spec
    assert labelled == ENFORCED_NAMESPACES


def test_poc05_service_rule_fixtures_cover_each_exposure_with_an_admitted_twin() -> None:
    """Criterion 5: a rejected NodePort, LoadBalancer, and externalIPs fixture, each refused by the
    model and naming the policy's message; the admitted ClusterIP twin passes."""
    twin = read_fixture(FIXTURES / "admitted.yaml")
    exposures = set()
    for path in REJECTED:
        fixture = read_fixture(path)
        assert fixture.rule == "S1" and fixture.message == _message(), fixture.id
        assert fixture.doc["kind"] == "Service", fixture.id
        assert fixture.differs, fixture.id
        exposures.update(s1_problems(fixture.doc))

    assert {"type NodePort", "type LoadBalancer", "sets externalIPs"} <= exposures
    assert twin.expect == "admitted" and twin.message is None
    assert s1_problems(twin.doc) == []


@pytest.mark.parametrize("path", REJECTED, ids=[p.stem for p in REJECTED])
def test_poc05_service_rule_rejected_fixture_differs_from_its_twin_only_there(path: Path) -> None:
    """Criterion 5: each rejected fixture breaks S1 and nothing else differs from its twin's spec
    except the fields it names, so the kind refusal is for S1 alone."""
    fixture = read_fixture(path)
    twin = read_fixture(FIXTURES / "admitted.yaml").doc
    named = {d.removeprefix(".spec.") for d in fixture.differs}
    spec, twin_spec = fixture.doc["spec"], twin["spec"]

    assert s1_problems(fixture.doc), fixture.id
    assert fixture.doc["metadata"]["namespace"] in ENFORCED_NAMESPACES
    assert fixture.doc["metadata"]["namespace"] == twin["metadata"]["namespace"]
    for key in set(spec) | set(twin_spec):
        if key not in named:
            assert spec.get(key) == twin_spec.get(key), (fixture.id, key)


def _shipped_services() -> list[tuple[str, dict[str, Any]]]:
    out = []
    for root in (POC05, POC06):
        for path in sorted(root.rglob("*.yaml")):
            if "fixtures" in path.parts or "agent-sandbox" in path.parts:
                continue
            out.extend(
                (path.relative_to(ROOT).as_posix(), doc)
                for doc in load_manifests(path)
                if doc.get("kind") == "Service"
            )
    return out


def test_poc05_every_shipped_service_passes_rule_s1() -> None:
    """Criterion 5: every Service under deploy/kind/poc05 and deploy/kind/poc06 is a plain
    ClusterIP, so the rule refuses none of the platform's own (the paired control)."""
    services = _shipped_services()
    broken = [(where, doc["metadata"]["name"], s1_problems(doc)) for where, doc in services]

    assert len(services) >= 5, "the Services were not read"
    assert [b for b in broken if b[2]] == []


def test_poc05_run_sh_applies_the_service_rule_policy_first_with_a_canary() -> None:
    """Criterion 5: `run.sh admission` applies the policy, waits for its type check, then the
    binding, then refuses the NodePort fixture next to its admitted twin."""
    text = (POC05 / "run.sh").read_text()
    match = re.search(r"(?ms)^admission\(\) \{\n(.*?)^\}", text)
    assert match, "run.sh has no admission()"
    body = match.group(1)
    order = [
        '"$dir/service-policy.yaml"',
        "type_checked agent-service-rule",
        '"$dir/service-binding.yaml"',
        "canary agent-service-rule",
    ]

    assert all(body.find(s) >= 0 for s in order), body
    assert [body.find(s) for s in order] == sorted(body.find(s) for s in order)
    assert '"$fx/ruleS1-service-type/rejected.yaml"' in body
    assert '"$fx/ruleS1-service-type/admitted.yaml" "service rule S1"' in body
