"""PoC-9 exit criterion 5: "The library chart renders a pod with both containers, secrets in the
chassis container only, and the Service on the chassis port."
(docs/planning/poc/009-PoC-9-agent-mvp-template.md; backlog 024 CH-3)

Renders `deploy/helm/example-agent`, which includes the library chart `deploy/helm/agent-lib`,
with `helm template`, parses the YAML, and checks:

- Default values (the sidecar lane): one pod with exactly two containers, `chassis` (the native
  sidecar, an init container with `restartPolicy: Always`, as PoC-5) and `workload`. Every Secret
  reference (`secretKeyRef`, `secretRef`, a `secret` volume) is on the chassis; the workload has
  none, and no service account token. The Service's `targetPort` is the chassis port, never the
  workload port. Both containers run non-root with the uid table's uids, a read-only root, all
  capabilities dropped, no privilege escalation, and seccomp `RuntimeDefault`. The paired control:
  the example values do give the chassis a Secret, so "none on the workload" is not vacuous.
- `connector: remote` with `trust: untrusted` (ADR-005): the remote lane is two pods, as on kind
  in PoC-5. The chassis pod holds every internal credential; the workload pod runs under the
  gVisor RuntimeClass with one Secret, its own bearer token (ADR-005 decision 1), and may reach
  only its chassis's remote proxy port.
- Values that try to give the workload a Secret (or break the trust rule) fail the render with
  the chart's own `agent-lib:` message.

`helm lint` runs on both charts. The charts are copied to a temp dir and the `file://`
dependency is built there, so the repo stays clean and nothing touches the network. Skipped,
with the reason, when `helm` is not on PATH; `.github/workflows/poc09-helm.yml` installs a pinned
helm and runs this file.
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

ROOT = Path(__file__).resolve().parents[3]
HELM_DIR = ROOT / "deploy/helm"
HELM = shutil.which("helm")
NO_HELM = (
    "helm is not on PATH; .github/workflows/poc09-helm.yml installs a pinned helm and runs this"
)

# The CI job sets POC09_HELM_REQUIRED=1, so a missing helm there fails instead of skipping.
if HELM is None and os.environ.get("POC09_HELM_REQUIRED") == "1":
    pytest.fail(NO_HELM, pytrace=False)

pytestmark = pytest.mark.skipif(HELM is None, reason=NO_HELM)

CHASSIS_PORT = 8080
WORKLOAD_PORT = 9000
REMOTE_PROXY_PORT = 8091
CHASSIS_UID = 10001
WORKLOAD_UID = 10002
RELEASE = "example-agent"
NAMESPACE = "agents"
REMOTE_NAMESPACE = "agents-remote"

REMOTE_VALUES: dict[str, Any] = {
    "chassis": {"config": {"spec": {"trust": "untrusted", "engine": {"connector": "remote"}}}},
    "remote": {
        "namespace": REMOTE_NAMESPACE,
        "proxyClusterIP": "10.96.85.92",
        "tokenSecret": {"name": "example-agent-remote-token", "key": "token"},
    },
}

Doc = dict[str, Any]


@dataclass(frozen=True)
class Charts:
    """The two charts copied to a temp dir, and a helm env that reads no user config."""

    example: Path
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

    def template(self, values: Path | None = None) -> list[Doc]:
        args = ["template", RELEASE, str(self.example), "--namespace", NAMESPACE]
        if values is not None:
            args += ["-f", str(values)]
        return [d for d in yaml.safe_load_all(self.helm(*args).stdout) if d]


@pytest.fixture(scope="module")
def chart(tmp_path_factory: pytest.TempPathFactory) -> Charts:
    base = tmp_path_factory.mktemp("helm")
    for name in ("agent-lib", "example-agent"):
        shutil.copytree(
            HELM_DIR / name, base / name, ignore=shutil.ignore_patterns("charts", "Chart.lock")
        )
    env = {k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "TMPDIR", "LANG"}} | {
        "HELM_CACHE_HOME": str(base / ".cache"),
        "HELM_CONFIG_HOME": str(base / ".config"),
        "HELM_DATA_HOME": str(base / ".data"),
    }
    charts = Charts(example=base / "example-agent", env=env)
    # A file:// dependency only: no repo, no index, no network.
    charts.helm("dependency", "update", "--skip-refresh", str(charts.example))
    return charts


def _values_file(tmp_path: Path, values: dict[str, Any]) -> Path:
    path = tmp_path / "values.yaml"
    path.write_text(yaml.safe_dump(values), encoding="utf-8")
    return path


def _one(docs: list[Doc], kind: str, name: str) -> Doc:
    found = [d for d in docs if d["kind"] == kind and d["metadata"]["name"] == name]
    assert len(found) == 1, f"want one {kind}/{name}, got {[d['metadata']['name'] for d in docs]}"
    return found[0]


def _containers(pod: Doc) -> dict[str, Doc]:
    every = [*pod.get("initContainers", []), *pod.get("containers", [])]
    return {c["name"]: c for c in every}


def _walk(node: Any) -> Iterator[tuple[str, Any]]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield key, value
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _secret_names(container: Doc, pod: Doc) -> set[str]:
    """Every Secret the container can read: env, envFrom, and mounted secret volumes."""
    names = {v["name"] for k, v in _walk(container) if k in {"secretKeyRef", "secretRef"}}
    secret_volumes = {
        v["name"]: v["secret"]["secretName"] for v in pod.get("volumes", []) if "secret" in v
    }
    for mount in container.get("volumeMounts", []):
        if mount["name"] in secret_volumes:
            names.add(secret_volumes[mount["name"]])
    return names


def _assert_hardened(container: Doc, uid: int) -> None:
    sc = container["securityContext"]
    assert sc["runAsNonRoot"] is True
    assert sc["runAsUser"] == uid and sc["runAsGroup"] == uid
    assert sc["readOnlyRootFilesystem"] is True
    assert sc["allowPrivilegeEscalation"] is False
    assert sc["capabilities"] == {"drop": ["ALL"]}
    assert sc["seccompProfile"] == {"type": "RuntimeDefault"}
    assert "command" not in container, "the chassis image's ENTRYPOINT runs; admission rule 6b"
    assert container["resources"]["limits"] and container["resources"]["requests"]


def _assert_pod_closed(pod: Doc) -> None:
    assert pod["automountServiceAccountToken"] is False
    assert pod["enableServiceLinks"] is False
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert pod["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
    assert "hostNetwork" not in pod and "hostPID" not in pod


# --- lint -------------------------------------------------------------------------------------


def test_helm_lint_passes_on_the_library_and_the_example(chart: Charts) -> None:
    lib = chart.helm("lint", str(chart.example.parent / "agent-lib"))
    app = chart.helm("lint", str(chart.example))
    assert "0 chart(s) failed" in lib.stdout + lib.stderr
    assert "0 chart(s) failed" in app.stdout + app.stderr


# --- the sidecar lane (default values) --------------------------------------------------------


def test_sidecar_lane_renders_one_pod_with_the_chassis_and_the_workload(chart: Charts) -> None:
    docs = chart.template()
    pod = _one(docs, "Deployment", RELEASE)["spec"]["template"]["spec"]
    containers = _containers(pod)
    assert sorted(containers) == ["chassis", "workload"]
    # The chassis is the native sidecar (PoC-5 bind order, contract v4 H14).
    assert [c["name"] for c in pod["initContainers"]] == ["chassis"]
    assert pod["initContainers"][0]["restartPolicy"] == "Always"
    assert [c["name"] for c in pod["containers"]] == ["workload"]
    assert "runtimeClassName" not in pod
    image = containers["chassis"]["image"]
    assert image.startswith("kind.local/agent-platform/chassis:") and not image.endswith(":latest")
    assert not [d for d in docs if d["kind"] == "HorizontalPodAutoscaler"], "HPA is off by default"


def test_sidecar_lane_secrets_are_on_the_chassis_container_only(chart: Charts) -> None:
    docs = chart.template()
    pod = _one(docs, "Deployment", RELEASE)["spec"]["template"]["spec"]
    containers = _containers(pod)
    # Paired control: the example values give the chassis secrets, so the check below has teeth.
    assert _secret_names(containers["chassis"], pod) >= {"example-agent-litellm"}
    assert _secret_names(containers["workload"], pod) == set()
    assert not [k for k, _ in _walk(containers["workload"]) if k in {"valueFrom", "envFrom"}]
    assert {e["name"] for e in containers["workload"]["env"]} >= {"HOST", "PORT"}
    sa = _one(docs, "ServiceAccount", RELEASE)
    assert sa["automountServiceAccountToken"] is False
    _assert_pod_closed(pod)


def test_sidecar_lane_service_targets_the_chassis_port_only(chart: Charts) -> None:
    docs = chart.template()
    pod = _one(docs, "Deployment", RELEASE)["spec"]["template"]["spec"]
    chassis = _containers(pod)["chassis"]
    assert [p["containerPort"] for p in chassis["ports"]] == [CHASSIS_PORT]
    assert "ports" not in _containers(pod)["workload"], "the workload listens on 127.0.0.1"
    services = [d for d in docs if d["kind"] == "Service"]
    assert [s["metadata"]["name"] for s in services] == [RELEASE]
    ports = services[0]["spec"]["ports"]
    assert [p["targetPort"] for p in ports] == [CHASSIS_PORT]
    assert WORKLOAD_PORT not in {p["port"] for p in ports}
    workload_env = {e["name"]: e["value"] for e in _containers(pod)["workload"]["env"]}
    assert workload_env["HOST"] == "127.0.0.1"
    assert workload_env["PORT"] == str(WORKLOAD_PORT)
    assert "$(POD_IP)" in chassis["args"], "the public port binds the pod IP (H13)"


def test_sidecar_lane_containers_are_hardened(chart: Charts) -> None:
    pod = _one(chart.template(), "Deployment", RELEASE)["spec"]["template"]["spec"]
    containers = _containers(pod)
    _assert_hardened(containers["chassis"], CHASSIS_UID)
    _assert_hardened(containers["workload"], WORKLOAD_UID)
    # The workload's liveness restarts the pod on a hang without waiting for the pod's grace.
    assert containers["workload"]["livenessProbe"]["terminationGracePeriodSeconds"] == 10


def test_sidecar_lane_network_policy_admits_the_chassis_port_only(chart: Charts) -> None:
    docs = chart.template()
    policy = _one(docs, "NetworkPolicy", RELEASE)
    assert policy["spec"]["policyTypes"] == ["Ingress", "Egress"]
    ingress_ports = {p["port"] for rule in policy["spec"]["ingress"] for p in rule["ports"]}
    assert ingress_ports == {CHASSIS_PORT}
    assert not [k for k, _ in _walk(policy) if k == "ipBlock"]
    config = yaml.safe_load(_one(docs, "ConfigMap", f"{RELEASE}-chassis")["data"]["config.yaml"])
    assert config["spec"]["engine"] == {
        "connector": "sidecar",
        "url": f"http://127.0.0.1:{WORKLOAD_PORT}",
    }
    assert config["spec"]["trust"] == "trusted"


def test_hpa_renders_when_enabled(chart: Charts, tmp_path: Path) -> None:
    values = _values_file(tmp_path, {"hpa": {"enabled": True, "minReplicas": 2, "maxReplicas": 4}})
    hpa = _one(chart.template(values), "HorizontalPodAutoscaler", RELEASE)
    assert hpa["spec"]["scaleTargetRef"] == {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "name": RELEASE,
    }
    assert (hpa["spec"]["minReplicas"], hpa["spec"]["maxReplicas"]) == (2, 4)


# --- the remote lane --------------------------------------------------------------------------


def test_remote_lane_renders_the_chassis_pod_and_a_gvisor_workload_pod(
    chart: Charts, tmp_path: Path
) -> None:
    docs = chart.template(_values_file(tmp_path, REMOTE_VALUES))
    chassis_pod = _one(docs, "Deployment", RELEASE)["spec"]["template"]["spec"]
    workload_dep = _one(docs, "Deployment", f"{RELEASE}-workload")
    workload_pod = workload_dep["spec"]["template"]["spec"]
    assert sorted(_containers(chassis_pod)) == ["chassis"]
    assert sorted(_containers(workload_pod)) == ["workload"]
    assert "runtimeClassName" not in chassis_pod, "the chassis stays outside the sandbox"
    assert workload_pod["runtimeClassName"] == "gvisor"
    assert workload_dep["metadata"]["namespace"] == REMOTE_NAMESPACE
    assert workload_dep["spec"]["replicas"] == 1
    labels = workload_dep["spec"]["template"]["metadata"]["labels"]
    assert labels["agents.platform/lane"] == "remote"
    assert labels["agents.platform/trust"] == "untrusted"
    for pod in (chassis_pod, workload_pod):
        _assert_pod_closed(pod)
    _assert_hardened(_containers(chassis_pod)["chassis"], CHASSIS_UID)
    _assert_hardened(_containers(workload_pod)["workload"], WORKLOAD_UID)


def test_remote_lane_the_workload_holds_only_its_own_token(chart: Charts, tmp_path: Path) -> None:
    docs = chart.template(_values_file(tmp_path, REMOTE_VALUES))
    chassis_pod = _one(docs, "Deployment", RELEASE)["spec"]["template"]["spec"]
    workload_pod = _one(docs, "Deployment", f"{RELEASE}-workload")["spec"]["template"]["spec"]
    chassis = _containers(chassis_pod)["chassis"]
    workload = _containers(workload_pod)["workload"]
    token = "example-agent-remote-token"
    assert _secret_names(chassis, chassis_pod) >= {"example-agent-litellm", token}
    assert _secret_names(workload, workload_pod) == {token}
    by_name = {e["name"]: e for e in workload["env"]}
    assert by_name["CHASSIS_API_TOKEN"]["valueFrom"]["secretKeyRef"]["name"] == token
    secret_env = [e["name"] for e in workload["env"] if "secretKeyRef" in e.get("valueFrom", {})]
    assert secret_env == ["CHASSIS_API_TOKEN"]
    assert "--remote-proxy-host" in chassis["args"]
    config = yaml.safe_load(_one(docs, "ConfigMap", f"{RELEASE}-chassis")["data"]["config.yaml"])
    assert config["spec"]["engine"]["url"] == (
        f"http://{RELEASE}-workload.{REMOTE_NAMESPACE}.svc.cluster.local:{WORKLOAD_PORT}"
    )
    assert config["spec"]["engine"]["auth"] == {"scheme": "bearer", "token_env": "REMOTE_TOKEN"}


def test_remote_lane_services_and_policies(chart: Charts, tmp_path: Path) -> None:
    docs = chart.template(_values_file(tmp_path, REMOTE_VALUES))
    public = _one(docs, "Service", RELEASE)
    assert [p["targetPort"] for p in public["spec"]["ports"]] == [CHASSIS_PORT]
    proxy = _one(docs, "Service", f"{RELEASE}-remote-proxy")
    assert proxy["spec"]["clusterIP"] == "10.96.85.92"
    assert [p["targetPort"] for p in proxy["spec"]["ports"]] == [REMOTE_PROXY_PORT]
    policy = _one(docs, "NetworkPolicy", f"{RELEASE}-workload")
    assert policy["metadata"]["namespace"] == REMOTE_NAMESPACE
    egress = policy["spec"]["egress"]
    assert len(egress) == 1, "no DNS, no other peer: only the chassis's remote proxy"
    assert [p["port"] for p in egress[0]["ports"]] == [REMOTE_PROXY_PORT]
    ingress = policy["spec"]["ingress"]
    assert len(ingress) == 1 and [p["port"] for p in ingress[0]["ports"]] == [WORKLOAD_PORT]
    for doc in docs:
        assert not [k for k, _ in _walk(doc) if k == "ipBlock"], doc["metadata"]["name"]


# --- refusals ---------------------------------------------------------------------------------

REFUSED: dict[str, tuple[dict[str, Any], str]] = {
    "chassis arg moves the listener": (
        {"chassis": {"extraArgs": ["--host", "0.0.0.0"]}},
        "is not allowed",
    ),
    "chassis arg abbreviated": ({"chassis": {"extraArgs": ["--ho=0.0.0.0"]}}, "is not allowed"),
    "workload arg disables remote auth": (
        {"workload": {"args": ["--require-token-env", ""]}},
        "is not allowed",
    ),
    "workload secretEnv": (
        {"workload": {"secretEnv": [{"name": "X", "secretName": "s", "key": "k"}]}},
        "workload.secretEnv",
    ),
    "workload envFrom": (
        {"workload": {"envFrom": [{"secretRef": {"name": "s"}}]}},
        "workload.envFrom",
    ),
    "workload secret volume": (
        {"workload": {"volumes": [{"name": "v", "secret": {"secretName": "s"}}]}},
        "workload.volumes",
    ),
    "workload env valueFrom": (
        {
            "workload": {
                "env": [{"name": "X", "valueFrom": {"secretKeyRef": {"name": "s", "key": "k"}}}]
            }
        },
        "valueFrom",
    ),
    "workload env that looks like a credential": (
        {"workload": {"env": [{"name": "OPENAI_API_KEY", "value": "x"}]}},
        "OPENAI_API_KEY",
    ),
    "workload env the chart owns": (
        {"workload": {"env": [{"name": "CHASSIS_MODEL_URL", "value": "http://evil"}]}},
        "CHASSIS_MODEL_URL",
    ),
    "chassis env valueFrom outside secretEnv": (
        {
            "chassis": {
                "env": [{"name": "X", "valueFrom": {"secretKeyRef": {"name": "s", "key": "k"}}}]
            }
        },
        "chassis.secretEnv",
    ),
    "chassis command": ({"chassis": {"command": ["sh"]}}, "chassis.command"),
    "untrusted in the sidecar lane": (
        {"chassis": {"config": {"spec": {"trust": "untrusted"}}}},
        "untrusted",
    ),
    "chassis tag latest": ({"chassis": {"image": {"tag": "latest"}}}, "latest"),
    "egress ipBlock": (
        {"networkPolicy": {"chassisEgress": [{"to": [{"ipBlock": {"cidr": "0.0.0.0/0"}}]}]}},
        "ipBlock",
    ),
    "remote lane with two replicas": (REMOTE_VALUES | {"replicas": 2}, "one replica"),
    "remote lane without a token Secret": (
        REMOTE_VALUES | {"remote": {"proxyClusterIP": "10.96.85.92"}},
        "remote.tokenSecret.name",
    ),
}


@pytest.mark.parametrize("case", sorted(REFUSED))
def test_the_render_fails_on_values_that_break_the_boundary(
    chart: Charts, tmp_path: Path, case: str
) -> None:
    values, needle = REFUSED[case]
    path = _values_file(tmp_path, values)
    proc = chart.helm("template", RELEASE, str(chart.example), "-f", str(path), check=False)
    assert proc.returncode != 0, f"{case}: rendered, want a refusal:\n{proc.stdout}"
    assert "agent-lib:" in proc.stderr and needle in proc.stderr, proc.stderr
