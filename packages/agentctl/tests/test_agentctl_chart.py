"""The rendered agent's Helm chart: it includes the platform's `agent-lib` and renders offline.

Backlog 025 H-10: "The generated repo carries a chart on `agent-lib`." The chart names the library
through the `.platform` link, as pyproject.toml does, so `helm dependency update --skip-refresh`
reads a `file://` path and never the network. Skipped when `helm` is not on PATH.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from agentctl.scaffold import Answers, render

ROOT = Path(__file__).resolve().parents[3]
HELM = shutil.which("helm")
NEEDS_HELM = pytest.mark.skipif(HELM is None, reason="helm is not on PATH")
LIBRARY = "file://../../.platform/deploy/helm/agent-lib"
CHASSIS_PORT = 8080
LANES = [("langgraph", "sidecar", None), ("smolagents", "remote", None)]
"""(framework, connector, --trust). None leaves trust to the framework (ADR-006 decision 2)."""
UNTRUSTED = ("langgraph", "remote", "untrusted")
"""059 H-15 box 2: `--trust untrusted` on a trusted framework renders the remote chart."""
ALL_LANES = [*LANES, UNTRUSTED]
GVISOR = "gvisor"
_LIBRARY_DIGEST = re.search(
    r"^    digest: (sha256:[0-9a-f]{64})$",
    (ROOT / "deploy/helm/agent-lib/templates/_values.tpl").read_text(),
    re.M,
)
assert _LIBRARY_DIGEST is not None, "the library chart pins its chassis by digest"
ZERO_DIGEST = "sha256:" + "0" * 64
PUBLISHED_STAND_IN = "sha256:" + "e" * 64
"""The chart refuses the all-zero placeholder (139 CH-19), so before the first push the test's
platform copy holds this stand-in for the digest scripts/publish_chassis.sh writes."""
_DIGEST = _LIBRARY_DIGEST.group(1)
_DIGEST = PUBLISHED_STAND_IN if _DIGEST == ZERO_DIGEST else _DIGEST
LIBRARY_CHASSIS_IMAGE = f"ghcr.io/odere-pro/agent-chassis:v0.1.0@{_DIGEST}"
"""The default in deploy/helm/agent-lib/templates/_values.tpl (`agent-lib.defaults`): the
published image, pinned by digest."""


def _platform(tmp_path: Path) -> Path:
    """The platform the agent links to: the library chart only, with the published digest."""
    lib = tmp_path / "platform/deploy/helm/agent-lib"
    shutil.copytree(ROOT / "deploy/helm/agent-lib", lib, dirs_exist_ok=True)
    values = lib / "templates/_values.tpl"
    values.write_text(values.read_text().replace(ZERO_DIGEST, PUBLISHED_STAND_IN))
    return tmp_path / "platform"


def _agent(tmp_path: Path, framework: str, connector: str, trust: str | None = None) -> Path:
    answers = Answers(
        agent_name="my-agent",
        kind="transformer",
        framework=framework,
        connector=connector,
        platform_path=ROOT,
        trust=trust,
    )
    agent = render(answers, tmp_path)
    (agent / ".platform").symlink_to(_platform(tmp_path))
    return agent


def _helm(tmp_path: Path, *args: str) -> str:
    assert HELM is not None
    home = tmp_path / "helm-home"
    env = {
        "PATH": "/usr/bin:/bin",
        "HELM_CACHE_HOME": str(home / "cache"),
        "HELM_CONFIG_HOME": str(home / "config"),
        "HELM_DATA_HOME": str(home / "data"),
    }
    proc = subprocess.run(
        [HELM, *args], capture_output=True, text=True, timeout=60, check=False, env=env
    )
    assert proc.returncode == 0, f"helm {' '.join(args)}:\n{proc.stdout}\n{proc.stderr}"
    return proc.stdout


def _render_chart(tmp_path: Path, agent: Path) -> list[dict[str, Any]]:
    chart = agent / "deploy/helm"
    _helm(tmp_path, "dependency", "update", "--skip-refresh", str(chart))
    out = _helm(tmp_path, "template", "my-agent", str(chart), "--namespace", "agents")
    return [doc for doc in yaml.safe_load_all(out) if doc]


@pytest.mark.parametrize(("framework", "connector", "trust"), ALL_LANES)
def test_the_chart_depends_on_agent_lib_through_the_platform_link(
    tmp_path: Path, framework: str, connector: str, trust: str | None
) -> None:
    agent = _agent(tmp_path, framework, connector, trust)
    chart = yaml.safe_load((agent / "deploy/helm/Chart.yaml").read_text())
    assert chart["name"] == "my-agent"
    assert chart["type"] == "application"
    assert [(d["name"], d["repository"]) for d in chart["dependencies"]] == [("agent-lib", LIBRARY)]
    values = yaml.safe_load((agent / "deploy/helm/values.yaml").read_text())
    assert values["chassis"]["config"]["spec"]["engine"]["connector"] == connector
    assert "image" not in values["chassis"], "the chassis tag is the library's (ADR-001)"


@NEEDS_HELM
@pytest.mark.parametrize(("framework", "connector", "trust"), ALL_LANES)
def test_the_chart_renders_the_chassis_from_the_library(
    tmp_path: Path, framework: str, connector: str, trust: str | None
) -> None:
    docs = _render_chart(tmp_path, _agent(tmp_path, framework, connector, trust))
    kinds = {doc["kind"] for doc in docs}
    assert {"Deployment", "Service", "ConfigMap", "NetworkPolicy"} <= kinds
    # Sidecar: the workload is a native sidecar (an init container) in the chassis pod. Remote:
    # it is its own Deployment.
    containers = [
        c["name"]
        for doc in docs
        if doc["kind"] == "Deployment"
        for key in ("initContainers", "containers")
        for c in doc["spec"]["template"]["spec"].get(key, [])
    ]
    assert "chassis" in containers
    assert "workload" in containers
    # 059 H-15 box 5: the chassis image and tag come from the library chart, not the agent.
    chassis_images = {
        c["image"]
        for doc in docs
        if doc["kind"] == "Deployment"
        for key in ("initContainers", "containers")
        for c in doc["spec"]["template"]["spec"].get(key, [])
        if c["name"] == "chassis"
    }
    assert chassis_images == {LIBRARY_CHASSIS_IMAGE}
    service = next(
        d for d in docs if d["kind"] == "Service" and d["metadata"]["name"] == "my-agent"
    )
    assert [p["port"] for p in service["spec"]["ports"]] == [CHASSIS_PORT]


def _pod_specs(docs: list[dict[str, Any]]) -> dict[str, tuple[str, dict[str, Any]]]:
    """Deployment name -> (namespace, pod spec)."""
    return {
        d["metadata"]["name"]: (d["metadata"]["namespace"], d["spec"]["template"]["spec"])
        for d in docs
        if d["kind"] == "Deployment"
    }


def _container_names(pod: dict[str, Any]) -> set[str]:
    return {c["name"] for key in ("initContainers", "containers") for c in pod.get(key, [])}


@NEEDS_HELM
def test_trust_untrusted_renders_the_workload_in_its_own_gvisor_pod(tmp_path: Path) -> None:
    """059 H-15 box 2: `--trust untrusted` gives a `remote` chart. The workload runs in its own pod
    under the gVisor RuntimeClass, with default-deny egress in its namespace; the chassis pod stays
    outside the sandbox. A render (`helm template`), not a cluster run."""
    framework, connector, trust = UNTRUSTED
    agent = _agent(tmp_path, framework, connector, trust)
    values = yaml.safe_load((agent / "deploy/helm/values.yaml").read_text())
    assert values["chassis"]["config"]["spec"]["trust"] == "untrusted"
    assert values["chassis"]["config"]["spec"]["engine"]["connector"] == "remote"
    docs = _render_chart(tmp_path, agent)
    pods = _pod_specs(docs)
    assert set(pods) == {"my-agent", "my-agent-workload"}, "two pods: the chassis, the workload"
    chassis_ns, chassis = pods["my-agent"]
    workload_ns, workload = pods["my-agent-workload"]
    assert _container_names(chassis) == {"chassis"}
    assert "runtimeClassName" not in chassis, "the chassis pod stays outside the sandbox"
    assert _container_names(workload) == {"workload"}
    assert workload["runtimeClassName"] == GVISOR
    assert workload_ns == "my-agent-remote" != chassis_ns
    deny = [
        d["spec"]
        for d in docs
        if d["kind"] == "NetworkPolicy"
        and d["metadata"].get("namespace") == workload_ns
        and d["spec"]["podSelector"] == {}
    ]
    assert len(deny) == 1, "one default-deny policy in the workload's namespace"
    assert "Egress" in deny[0]["policyTypes"] and not deny[0].get("egress")
