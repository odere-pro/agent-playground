"""Backlog 024 CH-3, offline: the library chart's drill on `poc05` has what it needs.

`test_poc09_kind_chart.py` runs the drill on kind (the tag rollout, the workload port, the
Secrets, the liveness restart). It installs `example-agent` twice into `poc05-chart` and reads
images and a Secret that `deploy/kind/poc05/run.sh` and `seed.sh` make. This file holds the two
sides together with no cluster: the images run.sh builds are the ones the chart names, the
namespace exists with Pod Security and a default deny, the seed makes the Secret through a pipe,
and the drill values put that Secret on the chassis container only.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from chart_render import NEEDS_HELM, Charts, containers, pod_of, secret_names, values_file
from kind_chart import (
    CHASSIS_PORT,
    CHASSIS_REPOSITORY,
    CURRENT_TAG,
    DRILL_IMAGES,
    NAMESPACE,
    NEXT_TAG,
    SECRET,
    SECRET_KEY,
    WORKLOAD_IMAGE,
    drill_values,
)
from poc_harness.paths import ROOT

POC05 = ROOT / "deploy/kind/poc05"
RUN_SH = POC05 / "run.sh"
SEED_SH = POC05 / "platform/seed.sh"
LIBRARY_VALUES = ROOT / "deploy/helm/agent-lib/templates/_values.tpl"
VALUES_KIND = ROOT / "deploy/helm/example-agent/values-kind.yaml"
KIND_TEST = "pocs/poc-09-agent-mvp-template/tests/test_poc09_kind_chart.py"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _function(script: str, name: str) -> str:
    """The body of the shell function `name` in `script`, up to its closing brace."""
    found = re.search(rf"^{name}\(\) \{{\n(.*?)^\}}", script, re.MULTILINE | re.DOTALL)
    assert found, f"run.sh has no function {name}"
    return found.group(1)


def _docs(path: Path) -> list[dict[str, Any]]:
    return [d for d in yaml.safe_load_all(_text(path)) if d]


def test_the_drill_tags_are_the_ones_the_charts_name() -> None:
    """The library's chassis tag and example-agent's kind workload image are the drill's. A
    library bump moves CURRENT_TAG here and in run.sh, or the drill installs an image the node
    does not hold."""
    defaults = re.search(
        r"^  image:\n    repository: (\S+)\n    tag: (\S+)$", _text(LIBRARY_VALUES), re.M
    )
    assert defaults, "agent-lib.defaults moved its chassis image"
    assert (defaults.group(1), defaults.group(2)) == (CHASSIS_REPOSITORY, CURRENT_TAG)
    assert yaml.safe_load(_text(VALUES_KIND))["workload"]["image"] == WORKLOAD_IMAGE
    assert NEXT_TAG != CURRENT_TAG


@pytest.mark.parametrize("image", DRILL_IMAGES)
def test_run_sh_names_every_drill_image(image: str) -> None:
    assert image.replace("kind.local/agent-platform", "$REGISTRY") in _text(RUN_SH)


def test_run_sh_builds_and_loads_the_drill_images() -> None:
    script = _text(RUN_SH)
    assert "chart_images" in _function(script, "build")
    assert "CHART_IMAGES" in _function(script, "load")


def test_the_next_chassis_release_differs_from_the_current_one_by_a_label() -> None:
    """The bump must change the running chassis's image id, or a rollout to it shows nothing."""
    body = _function(_text(RUN_SH), "chart_images")
    assert re.search(r"LABEL [a-z./-]+=", body), "the next release adds no label"
    assert "docker build" in body and ' -t "$CHART_NEXT"' in body


def test_run_sh_has_a_test_chart_verb_that_runs_the_kind_file() -> None:
    script = _text(RUN_SH)
    assert re.search(r"^    test-chart\) run_tests_chart ;;$", script, re.M)
    assert KIND_TEST in _function(script, "run_tests_chart")


def test_the_chart_namespace_is_restricted_with_a_default_deny_and_no_admission() -> None:
    """Pod Security `restricted` and the platform's own default deny, like every PoC-5
    namespace. No admission label: the chart passing admission is 026 CH-4, not this drill."""
    (ns,) = [d for d in _docs(POC05 / "base/namespaces.yaml") if d["metadata"]["name"] == NAMESPACE]
    labels = ns["metadata"]["labels"]
    assert labels["pod-security.kubernetes.io/enforce"] == "restricted"
    assert "agents.platform/admission" not in labels
    denies = [
        d["spec"]
        for d in _docs(POC05 / "base/default-deny.yaml")
        if d["metadata"]["namespace"] == NAMESPACE
    ]
    assert denies == [{"podSelector": {}, "policyTypes": ["Ingress", "Egress"]}]


def test_seed_makes_the_drill_secret_through_a_pipe_in_the_first_pass() -> None:
    script = _text(SEED_SH)
    assert f"CHART={NAMESPACE}" in script
    base = _function(script, "seed_base")
    assert f'printf \'%s\' "$value" | put_secret "$CHART" {SECRET} {SECRET_KEY}' in base
    assert '"$CHART"' in _function(script, "status")


@pytest.fixture
def drill_render(chart: Charts, tmp_path: Path) -> list[dict[str, Any]]:
    """What the kind test installs for one release: the kind values plus the drill values."""
    drill = values_file(tmp_path, drill_values())
    proc = chart.helm(
        "template", "chart-a", str(chart.example), "--namespace", NAMESPACE,
        "-f", str(chart.example / "values-kind.yaml"), "-f", str(drill),
    )  # fmt: skip
    return [d for d in yaml.safe_load_all(proc.stdout) if d]


@NEEDS_HELM
def test_the_drill_values_put_the_secret_on_the_chassis_only(
    drill_render: list[dict[str, Any]],
) -> None:
    pod = pod_of(drill_render, "chart-a")
    named = containers(pod)
    assert secret_names(named["chassis"], pod) == {SECRET}
    assert secret_names(named["workload"], pod) == set()
    assert pod["automountServiceAccountToken"] is False


@NEEDS_HELM
def test_the_drill_policies_allow_dns_out_only_and_hold_no_ip_block(
    drill_render: list[dict[str, Any]],
) -> None:
    """helm installs the chart's policies as cluster admin, past `policies.sh`: they open no
    edge besides DNS out and the chassis port in (security review, 2026-11-15)."""
    policies = {
        d["metadata"]["name"]: d["spec"] for d in drill_render if d["kind"] == "NetworkPolicy"
    }
    assert "ipBlock" not in yaml.safe_dump(policies)
    assert sorted(policies) == ["chart-a", "chart-a-default-deny"]
    out = [port["port"] for rule in policies["chart-a"]["egress"] for port in rule["ports"]]
    assert out == [53, 53]
    ins = [port["port"] for rule in policies["chart-a"]["ingress"] for port in rule["ports"]]
    assert ins == [CHASSIS_PORT]


def test_no_role_binding_reaches_the_chart_namespace() -> None:
    """Only the cluster admin may put pods in poc05-chart, which has no admission label."""
    bindings = [
        d
        for path in sorted(POC05.rglob("*.yaml"))
        for d in _docs(path)
        if isinstance(d, dict) and d.get("kind") in {"RoleBinding", "ClusterRoleBinding"}
    ]
    assert bindings, "the PoC-5 RBAC moved"
    assert NAMESPACE not in yaml.safe_dump(bindings)
