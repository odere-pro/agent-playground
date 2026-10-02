"""PoC-4 kind manifests, read offline: read-only root, probes, and container roles per variant.

Exit criteria covered (docs/planning/poc/004-PoC-4-stateless-scalable.md):

- 5. "Both containers run with a read-only root file system on every engine, or the engine is
  flagged." The manifest half: every container in every variant and engine has
  `readOnlyRootFilesystem: true` and the rest of the hardening. The live half is the kind drill.
- 9. "A hung workload restarts the pod, and the chosen container roles fail no request under a
  rolling restart." The manifest half: the workload has an exec liveness probe on the agent card
  (it listens on 127.0.0.1 only, so the kubelet's `httpGet` cannot reach it), the chassis is ready
  on `/ready`, and each variant puts the two containers in the roles it names. The drills
  themselves are `test_kind.py` (`kind`, needs a cluster).

Two layers. The raw files under `deploy/kind/poc04/` are parsed with PyYAML and always checked.
The rendered output of `kubectl kustomize` (a local render: no cluster, no socket) is checked
when `kubectl` is on PATH, for all four variant and engine pairs.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
KIND = ROOT / "deploy/kind"
POC04 = KIND / "poc04"
VARIANTS = ("native-sidecar", "prestop")
ENGINES = ("python", "typescript")
CARD = "http://127.0.0.1:9000/.well-known/agent-card.json"
CHASSIS_PORT = 8080
NODE_PORT = 30080
HOST_PORT = 18081

Doc = dict[str, Any]


def _load_all(path: Path) -> list[Doc]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def _overlay(variant: str, engine: str) -> Path:
    return POC04 / variant if engine == "python" else POC04 / "typescript" / variant


def _render(path: Path) -> list[Doc]:
    kubectl = shutil.which("kubectl")
    if kubectl is None:
        pytest.skip("kubectl is not on PATH: the rendered check needs `kubectl kustomize`")
    out = subprocess.run(
        [kubectl, "kustomize", str(path)], check=True, capture_output=True, text=True
    ).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def _one(docs: list[Doc], kind: str, name: str) -> Doc:
    found = [d for d in docs if d["kind"] == kind and d["metadata"]["name"] == name]
    assert len(found) == 1, f"expected one {kind}/{name}, found {len(found)}"
    return found[0]


def _pod_spec(deployment: Doc) -> Doc:
    spec: Doc = deployment["spec"]["template"]["spec"]
    return spec


def _containers(pod: Doc) -> Iterator[Doc]:
    yield from pod.get("initContainers", [])
    yield from pod.get("containers", [])


def _by_name(pod: Doc, name: str) -> tuple[Doc, str]:
    """The container named `name` and the list it sits in: `initContainers` or `containers`."""
    for field in ("initContainers", "containers"):
        for c in pod.get(field, []):
            if c["name"] == name:
                return c, field
    raise AssertionError(f"no container {name!r}")


def _flat(cmd: list[str]) -> str:
    return " ".join(cmd)


# --- checks shared by the raw and the rendered layer ---------------------------------------------


def check_hardening(pod: Doc) -> None:
    assert pod.get("automountServiceAccountToken") is False
    tmp_volumes = {v["name"] for v in pod["volumes"] if "emptyDir" in v}
    for c in _containers(pod):
        sc = c.get("securityContext", {})
        name = c["name"]
        assert sc.get("readOnlyRootFilesystem") is True, f"{name}: root file system not read-only"
        assert sc.get("runAsNonRoot") is True, name
        assert isinstance(sc.get("runAsUser"), int) and sc["runAsUser"] != 0, name
        assert sc.get("allowPrivilegeEscalation") is False, name
        assert sc.get("capabilities", {}).get("drop") == ["ALL"], name
        assert "add" not in sc.get("capabilities", {}), name
        assert sc.get("privileged") in (None, False), name
        tmp = [m for m in c.get("volumeMounts", []) if m["mountPath"] == "/tmp"]
        assert len(tmp) == 1 and tmp[0]["name"] in tmp_volumes, f"{name}: no emptyDir at /tmp"
    # Only emptyDir volumes are writable; a config or secret volume is mounted read-only.
    for c in _containers(pod):
        for m in c.get("volumeMounts", []):
            if m["name"] not in tmp_volumes:
                assert m.get("readOnly") is True, f"{c['name']}: {m['mountPath']} is writable"
    # Each container gets its own /tmp: a shared writable volume would be a side channel.
    tmp_names = [
        m["name"] for c in _containers(pod) for m in c["volumeMounts"] if m["mountPath"] == "/tmp"
    ]
    assert len(tmp_names) == len(set(tmp_names))


def check_workload(pod: Doc, engine: str) -> None:
    workload, _ = _by_name(pod, "workload")
    env = {e["name"]: e.get("value") for e in workload.get("env", [])}
    assert env.get("HOST") == "127.0.0.1"
    assert env.get("PORT") == "9000"
    assert env.get("CHASSIS_MODEL_URL") == "http://127.0.0.1:8090/v1"
    assert not workload.get("envFrom"), "the workload gets no secret and no env_file"
    for e in workload.get("env", []):
        assert "valueFrom" not in e, f"the workload gets no secret: {e['name']}"
        assert not e["name"].endswith(("_KEY", "_PASSWORD", "_TOKEN")), e["name"]
    assert not workload.get("ports"), "the workload publishes no port"
    for probe in ("livenessProbe", "startupProbe"):
        p = workload[probe]
        assert "httpGet" not in p, f"{probe}: the kubelet cannot reach 127.0.0.1 in the pod"
        command = _flat(p["exec"]["command"])
        assert CARD in command, f"{probe} does not GET the agent card"
        assert command.startswith("python" if engine == "python" else "node"), command
    assert workload["livenessProbe"]["periodSeconds"] == 10
    assert workload["livenessProbe"]["failureThreshold"] == 3
    # A liveness kill must not wait for the preStop sleep or the pod's grace: a stopped process
    # ignores SIGTERM, so only the probe's own short grace gets the restart inside 60 s.
    assert workload["livenessProbe"]["terminationGracePeriodSeconds"] == 10
    image = workload["image"]
    assert image == f"agent-platform/echo-{engine}:poc04", image
    if engine == "typescript":
        assert env.get("DRAIN_TIMEOUT_MS") == "30000"
    else:
        assert "--drain-timeout-s" in workload.get("args", [])


def check_chassis(pod: Doc) -> None:
    chassis, field = _by_name(pod, "chassis")
    assert field == "containers", "the chassis is the main container in both variants"
    assert chassis["image"] == "agent-platform/chassis:poc04"
    ready = chassis["readinessProbe"]["httpGet"]
    assert ready["path"] == "/ready" and ready["port"] == CHASSIS_PORT
    assert chassis["readinessProbe"]["periodSeconds"] == 2
    live = chassis["livenessProbe"]["httpGet"]
    assert live["path"] == "/health" and live["port"] == CHASSIS_PORT
    assert chassis["resources"]["requests"] == {"cpu": "100m", "memory": "128Mi"}
    assert "cpu" not in chassis["resources"].get("limits", {})
    args = _flat(chassis.get("command", []) + chassis.get("args", []))
    assert "--drain-delay-s" in args and "--drain-timeout-s" in args
    # The public port binds the pod IP, so loopback carries only the proxies (deploy/CLAUDE.md).
    assert "0.0.0.0" not in args
    assert "POD_IP" in args
    pod_ip = [e for e in chassis["env"] if e["name"] == "POD_IP"]
    assert pod_ip and pod_ip[0]["valueFrom"]["fieldRef"]["fieldPath"] == "status.podIP"
    mounts = {m["mountPath"]: m for m in chassis["volumeMounts"]}
    assert mounts["/etc/chassis"]["readOnly"] is True


def check_variant(deployment: Doc, variant: str) -> None:
    spec = deployment["spec"]
    assert spec["replicas"] == 3
    strategy = spec["strategy"]
    assert strategy["type"] == "RollingUpdate"
    assert strategy["rollingUpdate"] == {"maxUnavailable": 0, "maxSurge": 1}
    pod = _pod_spec(deployment)
    workload, field = _by_name(pod, "workload")
    chassis, _ = _by_name(pod, "chassis")
    args = _flat(chassis.get("command", []) + chassis.get("args", []))
    if variant == "native-sidecar":
        assert field == "initContainers"
        assert workload["restartPolicy"] == "Always", "a native sidecar"
        assert [c["name"] for c in pod["containers"]] == ["chassis"]
        assert pod["terminationGracePeriodSeconds"] == 50
        assert "--drain-delay-s 10" in args
        assert "lifecycle" not in chassis and "lifecycle" not in workload
    else:
        assert field == "containers"
        assert "restartPolicy" not in workload
        assert not pod.get("initContainers")
        assert sorted(c["name"] for c in pod["containers"]) == ["chassis", "workload"]
        assert pod["terminationGracePeriodSeconds"] == 50
        assert "--drain-delay-s 0" in args
        # The built-in sleep action: no `sleep` binary needed in a read-only image.
        assert chassis["lifecycle"]["preStop"] == {"sleep": {"seconds": 5}}
        assert workload["lifecycle"]["preStop"] == {"sleep": {"seconds": 35}}
    assert "--drain-timeout-s 30" in args


# --- the raw files ------------------------------------------------------------------------------


def test_cluster_config_names_poc04_and_maps_loopback_only() -> None:
    (cluster,) = _load_all(KIND / "cluster.yaml")
    assert cluster["kind"] == "Cluster"
    assert cluster["name"] == "poc04"
    (node,) = cluster["nodes"]
    assert "@sha256:" in node["image"], "the node image is pinned by digest"
    (mapping,) = node["extraPortMappings"]
    assert mapping == {
        "containerPort": NODE_PORT,
        "hostPort": HOST_PORT,
        "listenAddress": "127.0.0.1",
        "protocol": "TCP",
    }


@pytest.mark.parametrize("variant", VARIANTS)
def test_raw_variant_deployment(variant: str) -> None:
    deployment = _one(_load_all(POC04 / variant / "deployment.yaml"), "Deployment", "agent")
    pod = _pod_spec(deployment)
    check_hardening(pod)
    check_chassis(pod)
    check_workload(pod, "python")
    check_variant(deployment, variant)


def test_raw_base_service_exposes_the_chassis_port_only() -> None:
    service = _one(_load_all(POC04 / "base" / "agent-service.yaml"), "Service", "agent")
    assert service["spec"]["type"] == "NodePort"
    (port,) = service["spec"]["ports"]
    assert port["nodePort"] == NODE_PORT
    assert port["targetPort"] == CHASSIS_PORT


def test_run_script_pins_the_context() -> None:
    script = (KIND / "run.sh").read_text()
    assert "kind-poc04" in script
    for line in script.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if stripped.startswith("kubectl ") or " kubectl " in stripped:
            assert "--context" in stripped or "$KUBECTL" in stripped or "kctl" in stripped, line
    for command in ("create", "build", "load", "apply", "drill-rolling", "drill-hung", "delete"):
        assert f"{command})" in script, f"run.sh has no `{command}` command"


# --- the rendered output ------------------------------------------------------------------------


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("variant", VARIANTS)
def test_rendered_variant(variant: str, engine: str) -> None:
    docs = _render(_overlay(variant, engine))
    for d in docs:
        if d["kind"] != "Namespace":
            assert d["metadata"].get("namespace") == "poc04", d["metadata"]["name"]
    deployment = _one(docs, "Deployment", "agent")
    check_hardening(_pod_spec(deployment))
    check_chassis(_pod_spec(deployment))
    check_workload(_pod_spec(deployment), engine)
    check_variant(deployment, variant)
    for name in ("fake-model-server", "valkey"):
        check_hardening(_pod_spec(_one(docs, "Deployment", name)))
    config = _one(docs, "ConfigMap", "chassis-bootstrap")
    bootstrap = yaml.safe_load(config["data"]["config.yaml"])
    assert bootstrap["spec"]["adapters"]["config"] == "memory"
    assert bootstrap["spec"]["adapters"]["state"] == "valkey"
    assert bootstrap["spec"]["engine"] == {"connector": "sidecar", "url": "http://127.0.0.1:9000"}
    # No secret value in any rendered manifest: run.sh creates the Secret.
    assert not [d for d in docs if d["kind"] == "Secret"]
