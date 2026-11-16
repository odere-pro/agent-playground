"""139 CH-19: `make platform-up`, the reference stack on kind, checked offline.

The stack is seven parts on the PoC-5 cluster `poc05`: LiteLLM, its MCP gateway, Valkey, Kafka
(PoC-5's `up` and `kafka`), the OTel Collector and Jaeger (deploy/kind/platform), and the example
agent from deploy/helm. `deploy/kind/platform/run.sh` drives it. These tests run its dry run, which
prints every command and calls none (stand-ins for kind, kubectl, helm, and docker fail if called),
parse its manifests, and render the example agent with the platform values. The live run is not
here: it needs Docker and kind, and its evidence is a dated note (139 boxes 1 to 3).
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
HERE = ROOT / "deploy/kind/platform"
RUN = HERE / "run.sh"
OBSERVABILITY = HERE / "observability.yaml"
POLICIES = HERE / "network-policy.yaml"
PARAMS = HERE / "admission/params.yaml"
VALUES = HERE / "example-agent-values.yaml"
POC05_PARAMS = ROOT / "deploy/kind/poc05/admission/params.yaml"
POC07_COMPOSE = ROOT / "deploy/compose/poc07/docker-compose.yaml"
HELM_DIR = ROOT / "deploy/helm"
CONTEXT = "kind-poc05"
PLATFORM_NS = "poc05-platform"
AGENTS_NS = "poc05-agents"
COLLECTOR_PORT = 4318
JAEGER_OTLP_PORT = 4317
SEVEN = ("litellm", "mcp-gateway", "valkey", "kafka", "otel-collector", "jaeger", "example-agent")
HELM = shutil.which("helm")
NEEDS_HELM = pytest.mark.skipif(HELM is None, reason="helm is not on PATH")

Doc = dict[str, Any]


def _docs(path: Path) -> list[Doc]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


@pytest.fixture(scope="module")
def plan(tmp_path_factory: pytest.TempPathFactory) -> list[str]:
    """The dry run of `up smoke down`, with kind, kubectl, helm, and docker that fail if called."""
    bin_dir = tmp_path_factory.mktemp("bin")
    for tool in ("kind", "kubectl", "helm", "docker", "openssl"):
        stub = bin_dir / tool
        stub.write_text(f"#!/bin/sh\necho '{tool} was called' >&2\nexit 97\n")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    keep = {k: v for k, v in os.environ.items() if k in {"HOME", "TMPDIR", "LANG"}}
    env = {**keep, "PATH": f"{bin_dir}:/usr/bin:/bin", "PLATFORM_DRY_RUN": "1"}
    proc = subprocess.run(
        ["bash", str(RUN), "up", "smoke", "down"],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    assert "was called" not in proc.stderr, proc.stderr
    return proc.stdout.splitlines()


def _steps(plan: list[str], tool: str) -> list[str]:
    return [s for s in plan if s.startswith(f"+ {tool} ")]


# --- the dry run -------------------------------------------------------------------------------


def test_up_reuses_the_poc05_stack_for_litellm_the_gateway_valkey_and_kafka(
    plan: list[str],
) -> None:
    poc05 = [s for s in plan if s.startswith("+ ") and "deploy/kind/poc05/run.sh" in s]
    assert poc05[0].endswith("deploy/kind/poc05/run.sh up kafka"), poc05
    assert poc05[-1].endswith("deploy/kind/poc05/run.sh delete"), "down removes the cluster"


def test_every_cluster_call_is_pinned_to_the_poc05_context(plan: list[str]) -> None:
    kubectl, helm, kind = _steps(plan, "kubectl"), _steps(plan, "helm"), _steps(plan, "kind")
    assert kubectl and helm and kind
    assert all(f"--context {CONTEXT}" in s for s in kubectl), kubectl
    assert all(f"--kube-context {CONTEXT}" in s for s in helm if " template " not in s), helm
    assert all("--name poc05" in s for s in kind), kind
    assert not [s for s in plan if "use-context" in s or "current-context" in s]


def test_up_installs_the_example_agent_from_deploy_helm_with_the_platform_values(
    plan: list[str],
) -> None:
    (install,) = [s for s in _steps(plan, "helm") if " upgrade --install example-agent " in s]
    assert f"--namespace {AGENTS_NS}" in install and "--wait" in install
    assert "/example-agent/values-kind.yaml" in install
    assert str(VALUES.relative_to(ROOT)) in install
    # The chart comes from a scratch copy of deploy/helm, so no charts/ lands in the checkout.
    assert "deploy/helm/example-agent " not in install


def test_up_loads_the_chart_images_under_the_tags_the_chart_names(plan: list[str]) -> None:
    builds = [s for s in _steps(plan, "docker") if " build " in s]
    loads = [s for s in _steps(plan, "kind") if " load docker-image " in s]
    for image in (
        "kind.local/agent-platform/chassis:v0.1.0",
        "kind.local/agent-platform/echo-python:poc09",
    ):
        assert any(f"-t {image} " in s for s in builds), (image, builds)
        assert any(f" {image} " in f"{s} " for s in loads), (image, loads)


def test_policies_go_in_by_path_and_the_rest_as_the_deployer(plan: list[str]) -> None:
    applies = [s for s in _steps(plan, "kubectl") if " apply " in s and "--dry-run" not in s]
    (policy,) = [s for s in applies if str(POLICIES.relative_to(ROOT)) in s]
    assert f"-n {PLATFORM_NS}" in policy and "--as=" not in policy
    assert any("--as=system:serviceaccount:agent-platform-system:deployer" in s for s in applies)


def test_the_otlp_token_is_made_in_the_cluster_and_never_printed(plan: list[str]) -> None:
    secrets = [s for s in plan if "create secret" in s]
    assert secrets, "up makes the collector's token Secret and the chassis's header Secret"
    assert all("/dev/stdin" in s for s in secrets), "the value goes through a pipe"
    assert not [s for s in plan if "--from-literal" in s]


def test_smoke_checks_all_seven_parts_then_runs_the_example_agent_demo(plan: list[str]) -> None:
    checked = [s.split("== smoke: ", 1)[1].split()[0] for s in plan if s.startswith("== smoke: ")]
    assert tuple(checked[: len(SEVEN)]) == SEVEN, checked
    calls = " ".join(s for s in plan if s.startswith("+ curl "))
    for route in ("/v1/run", "/v1/chat/completions", "/v1/mcp", "/api/traces"):
        assert route in calls, route


# --- the manifests -----------------------------------------------------------------------------


def _pod_specs(node: Any) -> Iterator[Doc]:
    if isinstance(node, dict):
        if isinstance(node.get("containers"), list):
            yield node
        for value in node.values():
            yield from _pod_specs(value)
    elif isinstance(node, list):
        for item in node:
            yield from _pod_specs(item)


def _poc07_pins() -> set[str]:
    services = yaml.safe_load(POC07_COMPOSE.read_text())["services"]
    return {s["image"] for s in services.values() if "image" in s}


def test_the_collector_and_jaeger_run_the_poc07_pins_hardened() -> None:
    pods = [p for d in _docs(OBSERVABILITY) for p in _pod_specs(d)]
    images = {c["image"] for p in pods for c in p["containers"]}
    assert len(pods) == 2 and images <= _poc07_pins(), images
    for pod in pods:
        assert pod["automountServiceAccountToken"] is False
        assert pod["securityContext"]["runAsNonRoot"] is True
        for c in pod["containers"]:
            sc = c["securityContext"]
            assert sc["readOnlyRootFilesystem"] is True and sc["allowPrivilegeEscalation"] is False
            assert sc["capabilities"] == {"drop": ["ALL"]}
            assert sc["seccompProfile"] == {"type": "RuntimeDefault"}
            assert c["resources"]["limits"] and c["resources"]["requests"]


def test_the_collector_needs_a_bearer_token_it_reads_from_a_secret() -> None:
    (config_map,) = [d for d in _docs(OBSERVABILITY) if d["kind"] == "ConfigMap"]
    config = yaml.safe_load(config_map["data"]["config.yaml"])
    http = config["receivers"]["otlp"]["protocols"]["http"]
    assert http["auth"]["authenticator"] == "bearertokenauth"
    assert config["extensions"]["bearertokenauth"]["token"] == "${env:OTLP_CHASSIS_TOKEN}"
    (deployment,) = [
        d
        for d in _docs(OBSERVABILITY)
        if d["kind"] == "Deployment" and d["metadata"]["name"] == "otel-collector"
    ]
    (container,) = deployment["spec"]["template"]["spec"]["containers"]
    (env,) = [e for e in container["env"] if e["name"] == "OTLP_CHASSIS_TOKEN"]
    assert "secretKeyRef" in env["valueFrom"]
    assert config["service"]["pipelines"]["traces"]["exporters"] == ["otlp/jaeger"]


def test_only_chassis_pods_reach_the_collector_and_only_the_collector_reaches_jaeger() -> None:
    policies = {d["metadata"]["name"]: d for d in _docs(POLICIES)}
    assert set(policies) == {"otel-collector", "jaeger"}
    assert all(d["kind"] == "NetworkPolicy" for d in policies.values())
    assert "ipBlock" not in yaml.safe_dump(list(policies.values()))
    (collector_in,) = policies["otel-collector"]["spec"]["ingress"]
    (peer,) = collector_in["from"]
    assert peer["namespaceSelector"]["matchLabels"] == {"kubernetes.io/metadata.name": AGENTS_NS}
    assert peer["podSelector"]["matchLabels"] == {"agents.platform/role": "chassis"}
    assert collector_in["ports"] == [{"port": COLLECTOR_PORT, "protocol": "TCP"}]
    (jaeger_in,) = policies["jaeger"]["spec"]["ingress"]
    (peer,) = jaeger_in["from"]
    assert peer == {"podSelector": {"matchLabels": {"app.kubernetes.io/name": "otel-collector"}}}
    assert jaeger_in["ports"] == [{"port": JAEGER_OTLP_PORT, "protocol": "TCP"}]


def test_the_admission_params_add_only_the_chart_chassis_tag() -> None:
    (mine,) = _docs(PARAMS)
    (poc05,) = _docs(POC05_PARAMS)
    assert mine["metadata"] == poc05["metadata"]
    assert {k: v for k, v in mine["data"].items() if k != "supportedChassisTags"} == {
        k: v for k, v in poc05["data"].items() if k != "supportedChassisTags"
    }
    assert mine["data"]["supportedChassisTags"].split(",") == ["poc05", "v0.1.0"]


def test_the_makefile_has_the_platform_targets() -> None:
    text = (ROOT / "Makefile").read_text()
    for target, verb in (("platform-up", "up"), ("platform-down", "down")):
        assert f"\n{target}: ##" in text, target
        assert f"\tdeploy/kind/platform/run.sh {verb}" in text, target


# --- the example agent with the platform values ------------------------------------------------


@pytest.fixture(scope="module")
def rendered(tmp_path_factory: pytest.TempPathFactory) -> list[Doc]:
    assert HELM is not None
    base = tmp_path_factory.mktemp("helm")
    for name in ("agent-lib", "example-agent"):
        shutil.copytree(
            HELM_DIR / name, base / name, ignore=shutil.ignore_patterns("charts", "Chart.lock")
        )
    env = {
        "PATH": "/usr/bin:/bin",
        "HELM_CACHE_HOME": str(base / ".cache"),
        "HELM_CONFIG_HOME": str(base / ".config"),
        "HELM_DATA_HOME": str(base / ".data"),
    }

    def helm(*args: str) -> str:
        proc = subprocess.run(
            [str(HELM), *args], capture_output=True, text=True, timeout=60, check=False, env=env
        )
        assert proc.returncode == 0, f"helm {' '.join(args)}:\n{proc.stderr}"
        return proc.stdout

    helm("dependency", "update", "--skip-refresh", str(base / "example-agent"))
    out = helm(
        "template",
        "example-agent",
        str(base / "example-agent"),
        "--namespace",
        AGENTS_NS,
        "-f",
        str(base / "example-agent/values-kind.yaml"),
        "-f",
        str(VALUES),
    )
    return [d for d in yaml.safe_load_all(out) if d]


def _pod(docs: list[Doc]) -> Doc:
    (deployment,) = [d for d in docs if d["kind"] == "Deployment"]
    pod: Doc = deployment["spec"]["template"]["spec"]
    return pod


@NEEDS_HELM
def test_the_platform_chassis_exports_traces_to_the_collector(rendered: list[Doc]) -> None:
    pod = _pod(rendered)
    (chassis,) = [c for c in pod["containers"] if c["name"] == "chassis"]
    env = {e["name"]: e for e in chassis["env"]}
    endpoint = f"http://otel-collector.{PLATFORM_NS}.svc.cluster.local:{COLLECTOR_PORT}"
    assert env["OTEL_EXPORTER_OTLP_ENDPOINT"]["value"] == endpoint
    assert "secretKeyRef" in env["OTEL_EXPORTER_OTLP_HEADERS"]["valueFrom"]
    (config_map,) = [d for d in rendered if d["kind"] == "ConfigMap"]
    config = yaml.safe_load(next(iter(config_map["data"].values())))
    assert config["spec"]["adapters"]["telemetry"] == "otel"


@NEEDS_HELM
def test_the_workload_gets_no_secret_and_the_chassis_reaches_only_the_collector(
    rendered: list[Doc],
) -> None:
    pod = _pod(rendered)
    (workload,) = [c for c in pod.get("initContainers", []) if c["name"] == "workload"]
    assert "secretKeyRef" not in yaml.safe_dump(workload)
    egress = [
        d
        for d in rendered
        if d["kind"] == "NetworkPolicy" and d["metadata"]["name"] == "example-agent"
    ]
    rules = yaml.safe_dump(egress)
    assert "otel-collector" in rules and "ipBlock" not in rules
