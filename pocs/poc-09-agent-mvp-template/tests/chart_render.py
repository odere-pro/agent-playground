"""Render the charts under `deploy/helm` for the PoC-9 chart tests.

The charts are copied to a temp dir and the `file://` dependency is built there, so the repo stays
clean and nothing touches the network. `helm template` output is parsed to documents. The readers
the chart tests share live here too. Skipped, with the reason, when `helm` is not on PATH;
`.github/workflows/poc09-helm.yml` installs a pinned helm and sets `POC09_HELM_REQUIRED=1`, which
turns that skip into a failure.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml
from poc_harness.paths import ROOT

HELM_DIR = ROOT / "deploy/helm"
HELM = shutil.which("helm")
NO_HELM = (
    "helm is not on PATH; .github/workflows/poc09-helm.yml installs a pinned helm and runs this"
)
if HELM is None and os.environ.get("POC09_HELM_REQUIRED") == "1":
    pytest.fail(NO_HELM, pytrace=False)
NEEDS_HELM = pytest.mark.skipif(HELM is None, reason=NO_HELM)

CHASSIS_PORT = 8080
WORKLOAD_PORT = 9000
REMOTE_PROXY_PORT = 8091
CHASSIS_UID = 10001
WORKLOAD_UID = 10002
RELEASE = "example-agent"
NAMESPACE = "agents"
REMOTE_NAMESPACE = "agents-remote"
ADMISSION_RELEASE = "agent-admission"
PLATFORM_NAMESPACE = "agent-platform-system"
CHARTS = ("agent-lib", "example-agent", "agent-admission")

# The remote lane: the chassis pod in the release namespace, the workload pod under gVisor in its
# own. The token Secret is not a value: the chart names it after the workload (admission rule 7c).
REMOTE_VALUES: dict[str, Any] = {
    "chassis": {"config": {"spec": {"trust": "untrusted", "engine": {"connector": "remote"}}}},
    "remote": {"namespace": REMOTE_NAMESPACE, "proxyClusterIP": "10.96.85.92"},
}

Doc = dict[str, Any]


@dataclass(frozen=True)
class Charts:
    """The charts copied to a temp dir, and a helm env that reads no user config."""

    example: Path
    admission: Path
    env: dict[str, str]

    def helm(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        assert HELM is not None
        proc = subprocess.run(
            [HELM, *args], capture_output=True, text=True, timeout=60, check=False, env=self.env
        )
        if check and proc.returncode != 0:
            pytest.fail(
                f"helm {' '.join(args)} exited {proc.returncode}:\n{proc.stdout}\n{proc.stderr}"
            )
        return proc

    def render(
        self, chart: Path, release: str, namespace: str, values: Path | None, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        args = ["template", release, str(chart), "--namespace", namespace]
        if values is not None:
            args += ["-f", str(values)]
        return self.helm(*args, check=check)

    def template(self, values: Path | None = None) -> list[Doc]:
        """`example-agent` rendered: the service chart."""
        proc = self.render(self.example, RELEASE, NAMESPACE, values)
        return [d for d in yaml.safe_load_all(proc.stdout) if d]

    def template_admission(self, values: Path | None = None) -> list[Doc]:
        """`agent-admission` rendered: the platform's chart, in the platform namespace."""
        proc = self.render(self.admission, ADMISSION_RELEASE, PLATFORM_NAMESPACE, values)
        return [d for d in yaml.safe_load_all(proc.stdout) if d]


def make_charts(base: Path) -> Charts:
    for name in CHARTS:
        shutil.copytree(
            HELM_DIR / name, base / name, ignore=shutil.ignore_patterns("charts", "Chart.lock")
        )
    env = {k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "TMPDIR", "LANG"}} | {
        "HELM_CACHE_HOME": str(base / ".cache"),
        "HELM_CONFIG_HOME": str(base / ".config"),
        "HELM_DATA_HOME": str(base / ".data"),
    }
    charts = Charts(example=base / "example-agent", admission=base / "agent-admission", env=env)
    # A file:// dependency only: no repo, no index, no network.
    for chart in (charts.example, charts.admission):
        charts.helm("dependency", "update", "--skip-refresh", str(chart))
    return charts


def values_file(tmp_path: Path, values: dict[str, Any]) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "values.yaml"
    path.write_text(yaml.safe_dump(values), encoding="utf-8")
    return path


def one(docs: list[Doc], kind: str, name: str) -> Doc:
    found = [d for d in docs if d["kind"] == kind and d["metadata"]["name"] == name]
    assert len(found) == 1, f"want one {kind}/{name}, got {[d['metadata']['name'] for d in docs]}"
    return found[0]


def containers(pod: Doc) -> dict[str, Doc]:
    every = [*pod.get("initContainers", []), *pod.get("containers", [])]
    return {c["name"]: c for c in every}


def walk(node: Any) -> Iterator[tuple[str, Any]]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield key, value
            yield from walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from walk(item)


def secret_names(container: Doc, pod: Doc) -> set[str]:
    """Every Secret the container can read: env, envFrom, and mounted secret volumes."""
    names = {v["name"] for k, v in walk(container) if k in {"secretKeyRef", "secretRef"}}
    secret_volumes = {
        v["name"]: v["secret"]["secretName"] for v in pod.get("volumes", []) if "secret" in v
    }
    for mount in container.get("volumeMounts", []):
        if mount["name"] in secret_volumes:
            names.add(secret_volumes[mount["name"]])
    return names


def pod_of(docs: list[Doc], name: str) -> Doc:
    """The pod template spec of Deployment `name`."""
    spec: Doc = one(docs, "Deployment", name)["spec"]["template"]["spec"]
    return spec
