"""The rendered agent's Helm chart: it includes the platform's `agent-lib` and renders offline.

Backlog 025 H-10: "The generated repo carries a chart on `agent-lib`." The chart names the library
through the `.platform` link, as pyproject.toml does, so `helm dependency update --skip-refresh`
reads a `file://` path and never the network. Skipped when `helm` is not on PATH.
"""

from __future__ import annotations

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
LANES = [("langgraph", "sidecar"), ("smolagents", "remote")]


def _agent(tmp_path: Path, framework: str, connector: str) -> Path:
    answers = Answers(
        agent_name="my-agent",
        kind="transformer",
        framework=framework,
        connector=connector,
        platform_path=ROOT,
    )
    agent = render(answers, tmp_path)
    (agent / ".platform").symlink_to(ROOT)
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


@pytest.mark.parametrize(("framework", "connector"), LANES)
def test_the_chart_depends_on_agent_lib_through_the_platform_link(
    tmp_path: Path, framework: str, connector: str
) -> None:
    agent = _agent(tmp_path, framework, connector)
    chart = yaml.safe_load((agent / "deploy/helm/Chart.yaml").read_text())
    assert chart["name"] == "my-agent"
    assert chart["type"] == "application"
    assert [(d["name"], d["repository"]) for d in chart["dependencies"]] == [("agent-lib", LIBRARY)]
    values = yaml.safe_load((agent / "deploy/helm/values.yaml").read_text())
    assert values["chassis"]["config"]["spec"]["engine"]["connector"] == connector
    assert "image" not in values["chassis"], "the chassis tag is the library's (ADR-001)"


@NEEDS_HELM
@pytest.mark.parametrize(("framework", "connector"), LANES)
def test_the_chart_renders_the_chassis_from_the_library(
    tmp_path: Path, framework: str, connector: str
) -> None:
    docs = _render_chart(tmp_path, _agent(tmp_path, framework, connector))
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
    service = next(
        d for d in docs if d["kind"] == "Service" and d["metadata"]["name"] == "my-agent"
    )
    assert [p["port"] for p in service["spec"]["ports"]] == [CHASSIS_PORT]
