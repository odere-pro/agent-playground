"""PoC-5 container hardening and the agent, remote, and tool pods, offline (plan
`docs/plans/2026-10-02-poc-05-sandboxed.md`, sections 2.2, 2.3, 2.10, 2.11; tasks T13, T14, T15).

Exit criterion 4 (offline part): "the remote pod holds no provider key, no internal credential,
and no service account token": the remote Sandbox runs on gVisor, takes no Secret but its own
`remote-<name>-token`, mounts no token, and its egress is its own chassis's port 8091 only.
Exit criterion 5 (offline part): the hardening table of section 2.11 holds for every pod template
in `deploy/kind/poc05/{agents,remote,tools,platform}`: non-root with the distinct uids, read-only
root, drop ALL, no privilege escalation, seccomp RuntimeDefault, cpu and memory limits, a
size-capped `/tmp`, no service account token, no host namespaces or host paths, Secrets in the
chassis container only, the chassis public port on the pod IP, every chassis config loads, every
pod passes the admission rules, and no NetworkPolicy under `deploy/kind/poc05/` uses an `ipBlock`
but the dispatcher's one block to the API server (the netpol test pins it down).
Exit criterion 8 (offline part): the code runner is a `SandboxTemplate` on gVisor behind a warm
pool, a fresh pod per call, with no egress and only the dispatcher in; the dispatcher
(`platform/code-runner-dispatch.yaml`) gets the section 2.11 checks, and its projected token is in
its one container only (`docs/plans/2026-10-09-poc-05-per-call-sandbox.md`).

Not scanned for pod fields: `base/agent-sandbox/` (the upstream controller), `smoke/` (throwaway
pods `run.sh smoke` deletes), and `admission/fixtures/` (their own test). The packets are tested
on kind (T19, T21).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml
from chassis.server.config import ChassisConfig, load_config
from test_poc05_admission_static import _Params, broken_rules

ROOT = Path(__file__).resolve().parents[3]
POC05 = ROOT / "deploy/kind/poc05"
WORKLOAD_DIRS = ("agents", "remote", "tools")
SCANNED_DIRS = (*WORKLOAD_DIRS, "platform")
EXPECT = Path(__file__).resolve().parent / "fixtures/hardening_expect.yaml"
PARAMS = POC05 / "admission/params.yaml"

CHASSIS_REPO = "kind.local/agent-platform/chassis"
POD_IP = "$(POD_IP)"
TRUST = "agents.platform/trust"
TOKEN_SECRET = re.compile(r"remote-(?P<name>[a-z0-9]([-a-z0-9]*[a-z0-9])?)-token")
POD_KINDS = {"Deployment", "StatefulSet", "DaemonSet", "Job", "Pod", "Sandbox", "SandboxTemplate"}
POD_TEMPLATE_KINDS = {"Sandbox", "SandboxTemplate"}  # the pod template is `spec.podTemplate`
DISPATCHER = "code-runner-dispatch"
EXTENSIONS = "extensions.agents.x-k8s.io/v1beta1"

Doc = dict[str, Any]


def _load_all(path: Path) -> list[Doc]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


@dataclass(frozen=True)
class Pod:
    """One pod template: the object that holds it, its folder, and its namespace."""

    folder: str
    namespace: str
    doc: Doc

    @property
    def name(self) -> str:
        return str(self.doc["metadata"]["name"])

    @property
    def id(self) -> str:
        return f"{self.folder}/{self.doc['kind']}/{self.name}"

    @property
    def template(self) -> Doc:
        kind = self.doc["kind"]
        if kind == "Pod":
            return self.doc
        if kind in POD_TEMPLATE_KINDS:
            return dict(self.doc["spec"]["podTemplate"])
        return dict(self.doc["spec"]["template"])

    @property
    def spec(self) -> Doc:
        return dict(self.template["spec"])

    @property
    def labels(self) -> dict[str, str]:
        return dict(self.template.get("metadata", {}).get("labels", {}))

    def containers(self) -> list[Doc]:
        return [*self.spec.get("initContainers", []), *self.spec["containers"]]


def _kustomization(folder: str) -> Doc:
    return dict(yaml.safe_load((POC05 / folder / "kustomization.yaml").read_text()))


def _objects(folder: str) -> list[tuple[str, Doc]]:
    """The folder's resources as written (not rendered), each with the kustomization's namespace."""
    kust = _kustomization(folder)
    namespace = kust["namespace"]
    out: list[tuple[str, Doc]] = []
    for resource in kust["resources"]:
        out.extend(
            (doc.get("metadata", {}).get("namespace", namespace), doc)
            for doc in _load_all(POC05 / folder / resource)
        )
    return out


def _pods(folders: tuple[str, ...]) -> list[Pod]:
    return [
        Pod(folder, ns, doc)
        for folder in folders
        for ns, doc in _objects(folder)
        if doc["kind"] in POD_KINDS
    ]


ALL_PODS = _pods(SCANNED_DIRS)
WORKLOAD_PODS = _pods(WORKLOAD_DIRS)
EXPECTED: Doc = yaml.safe_load(EXPECT.read_text())


def _ids(pods: list[Pod]) -> list[str]:
    return [p.id for p in pods]


def _is_chassis(container: Doc) -> bool:
    image = str(container.get("image", ""))
    return image == CHASSIS_REPO or image.startswith((CHASSIS_REPO + ":", CHASSIS_REPO + "@"))


def _role(pod: Pod, container: Doc) -> Doc:
    role = EXPECTED["pods"][pod.name]["containers"][container["name"]]
    return dict(EXPECTED["roles"][role])


def _volumes(pod: Pod) -> dict[str, Doc]:
    return {v["name"]: v for v in pod.spec.get("volumes", [])}


def _tmp_volume(pod: Pod, container: Doc) -> Doc | None:
    for m in container.get("volumeMounts", []):
        if m["mountPath"] == "/tmp":
            return _volumes(pod)[m["name"]]
    return None


def _secret_refs(container: Doc) -> Iterator[str]:
    for e in container.get("env", []):
        ref = e.get("valueFrom", {}).get("secretKeyRef")
        if ref is not None:
            yield str(ref.get("name"))
    for f in container.get("envFrom", []):
        if "secretRef" in f:
            yield str(f["secretRef"].get("name"))


def _flag(args: list[str], name: str) -> str | None:
    """The value after `name` in `args`, or the part after `name=`."""
    for i, a in enumerate(args):
        if a == name and i + 1 < len(args):
            return args[i + 1]
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return None


def _walk(node: Any) -> Iterator[tuple[str, Any]]:
    if isinstance(node, dict):
        for k, v in node.items():
            yield k, v
            yield from _walk(v)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


# --- the inventory ----------------------------------------------------------------------------


def test_every_workload_pod_is_in_the_table() -> None:
    """A pod added to agents, remote, or tools without a row in the fixture fails here."""
    found = {p.name: (p.namespace, p.doc["kind"]) for p in WORKLOAD_PODS}
    wanted = {n: (e["namespace"], e["kind"]) for n, e in EXPECTED["pods"].items()}
    assert found == wanted
    for pod in WORKLOAD_PODS:
        names = {c["name"] for c in pod.containers()}
        assert names == set(EXPECTED["pods"][pod.name]["containers"]), pod.id


def test_the_scan_sees_the_platform_too() -> None:
    names = {p.name for p in ALL_PODS if p.folder == "platform"}
    assert {"litellm", "valkey", "minio", "postgres"} <= names


# --- pod-level fields -------------------------------------------------------------------------


@pytest.mark.parametrize("pod", ALL_PODS, ids=_ids(ALL_PODS))
def test_pod_has_no_token_and_no_host_access(pod: Pod) -> None:
    """No automounted token anywhere. A projected token only in the dispatcher (next tests)."""
    spec = pod.spec
    assert spec.get("automountServiceAccountToken") is False, pod.id
    for field in ("shareProcessNamespace", "hostNetwork", "hostPID", "hostIPC"):
        assert not spec.get(field), f"{pod.id}: {field}"
    for v in spec.get("volumes", []):
        assert "hostPath" not in v, f"{pod.id}: hostPath volume {v['name']}"
        if pod.id == f"platform/Deployment/{DISPATCHER}":
            continue
        for s in v.get("projected", {}).get("sources", []):
            assert "serviceAccountToken" not in s, f"{pod.id}: projected token in {v['name']}"


@pytest.mark.parametrize("pod", WORKLOAD_PODS, ids=_ids(WORKLOAD_PODS))
def test_workload_pod_has_its_own_service_account_without_a_token(pod: Pod) -> None:
    """Section 2.11: its own ServiceAccount, with no RBAC binding and no token."""
    sa_name = pod.spec.get("serviceAccountName")
    assert sa_name, f"{pod.id}: no serviceAccountName"
    accounts = [
        d
        for _, d in _objects(pod.folder)
        if d["kind"] == "ServiceAccount" and d["metadata"]["name"] == sa_name
    ]
    assert len(accounts) == 1, f"{pod.id}: ServiceAccount {sa_name} not in {pod.folder}/"
    assert accounts[0].get("automountServiceAccountToken") is False, pod.id
    bindings = [
        d for _, d in _objects(pod.folder) if d["kind"] in {"RoleBinding", "ClusterRoleBinding"}
    ]
    assert not bindings, f"{pod.folder}/ binds a role"


# --- container-level fields -------------------------------------------------------------------


@pytest.mark.parametrize("pod", ALL_PODS, ids=_ids(ALL_PODS))
def test_every_container_is_hardened(pod: Pod) -> None:
    pod_sc = pod.spec.get("securityContext", {})
    for c in pod.containers():
        where = f"{pod.id}/{c['name']}"
        sc = c.get("securityContext", {})
        assert sc.get("runAsNonRoot", pod_sc.get("runAsNonRoot")) is True, where
        uid = sc.get("runAsUser", pod_sc.get("runAsUser"))
        assert isinstance(uid, int) and uid > 0, f"{where}: runAsUser {uid!r}"
        assert sc.get("readOnlyRootFilesystem") is True, where
        assert sc.get("allowPrivilegeEscalation") is False, where
        assert not sc.get("privileged"), where
        assert sc.get("capabilities", {}).get("drop") == ["ALL"], where
        assert not sc.get("capabilities", {}).get("add"), where
        seccomp = sc.get("seccompProfile", pod_sc.get("seccompProfile", {}))
        assert seccomp.get("type") == "RuntimeDefault", where
        limits = c.get("resources", {}).get("limits", {})
        assert limits.get("cpu") and limits.get("memory"), f"{where}: limits {limits}"


@pytest.mark.parametrize("pod", ALL_PODS, ids=_ids(ALL_PODS))
def test_every_emptydir_is_size_capped(pod: Pod) -> None:
    for name, v in _volumes(pod).items():
        if "emptyDir" in v:
            assert (v["emptyDir"] or {}).get("sizeLimit"), f"{pod.id}: emptyDir {name} uncapped"
    for c in pod.containers():
        tmp = _tmp_volume(pod, c)
        if tmp is not None:
            assert "emptyDir" in tmp, f"{pod.id}/{c['name']}: /tmp is not an emptyDir"


@pytest.mark.parametrize("pod", WORKLOAD_PODS, ids=_ids(WORKLOAD_PODS))
def test_containers_match_the_section_2_11_table(pod: Pod) -> None:
    runtimes = set()
    tmp_volumes: list[str] = []
    for c in pod.containers():
        where = f"{pod.id}/{c['name']}"
        want = _role(pod, c)
        sc = c["securityContext"]
        assert (sc.get("runAsUser"), sc.get("runAsGroup")) == (want["uid"], want["gid"]), where
        assert c["resources"]["limits"] == {"cpu": want["cpu"], "memory": want["memory"]}, where
        tmp = _tmp_volume(pod, c)
        assert tmp is not None, f"{where}: no /tmp mount"
        assert tmp["emptyDir"] == {"medium": "Memory", "sizeLimit": want["tmp"]}, where
        tmp_volumes.append(tmp["name"])
        runtimes.add(want["runtime"])
    assert len(tmp_volumes) == len(set(tmp_volumes)), f"{pod.id}: a /tmp is shared"
    assert len(runtimes) == 1, pod.id
    expected = runtimes.pop()
    assert pod.spec.get("runtimeClassName", "runc") == expected, pod.id


@pytest.mark.parametrize("pod", ALL_PODS, ids=_ids(ALL_PODS))
def test_containers_in_one_pod_run_as_distinct_uids(pod: Pod) -> None:
    """H26: the workload cannot read the chassis's files, even through a volume shared by
    mistake."""
    uids = [c.get("securityContext", {}).get("runAsUser") for c in pod.containers()]
    assert len(uids) == len(set(uids)), f"{pod.id}: uids {uids}"


# --- Secrets ----------------------------------------------------------------------------------


@pytest.mark.parametrize("pod", WORKLOAD_PODS, ids=_ids(WORKLOAD_PODS))
def test_secrets_reach_the_chassis_container_only(pod: Pod) -> None:
    """A workload container never gets a Secret; the remote workload gets only its own token."""
    for v in pod.spec.get("volumes", []):
        assert "secret" not in v, f"{pod.id}: a Secret volume can be mounted anywhere"
        assert "projected" not in v, f"{pod.id}: projected volume"
    lane = pod.labels.get("agents.platform/lane")
    for c in pod.containers():
        refs = set(_secret_refs(c))
        if _is_chassis(c):
            continue
        if lane == "remote":
            assert refs == {f"{pod.name}-token"}, f"{pod.id}/{c['name']}: {sorted(refs)}"
        else:
            assert not refs, f"{pod.id}/{c['name']}: {sorted(refs)}"


def test_the_remote_token_is_the_only_secret_in_the_remote_namespace() -> None:
    for pod in WORKLOAD_PODS:
        if pod.namespace != "poc05-remote":
            continue
        refs = {r for c in pod.containers() for r in _secret_refs(c)}
        assert refs == {f"{pod.name}-token"}, pod.id
        assert all(TOKEN_SECRET.fullmatch(r) for r in refs), pod.id


# --- the chassis ------------------------------------------------------------------------------


def _chassis_pods() -> list[tuple[Pod, Doc]]:
    return [(p, c) for p in WORKLOAD_PODS for c in p.containers() if _is_chassis(c)]


CHASSIS = _chassis_pods()
CHASSIS_IDS = [f"{p.id}/{c['name']}" for p, c in CHASSIS]


def _env(container: Doc) -> dict[str, Doc]:
    return {e["name"]: e for e in container.get("env", [])}


@pytest.mark.parametrize(("pod", "chassis"), CHASSIS, ids=CHASSIS_IDS)
def test_chassis_public_port_binds_the_pod_ip(pod: Pod, chassis: Doc) -> None:
    """Never 0.0.0.0 and never loopback: loopback carries only the proxies (section 2.3)."""
    args = [str(a) for a in chassis.get("args", [])]
    assert _flag(args, "--host") == POD_IP, pod.id
    remote_host = _flag(args, "--remote-proxy-host")
    assert remote_host in {None, POD_IP}, pod.id
    for bad in ("0.0.0.0", "127.0.0.1", "localhost", "::", "::1"):
        assert bad not in args, f"{pod.id}: {bad} in args"
    pod_ip = _env(chassis)["POD_IP"]
    assert pod_ip["valueFrom"]["fieldRef"]["fieldPath"] == "status.podIP", pod.id


@pytest.mark.parametrize(("pod", "chassis"), CHASSIS, ids=CHASSIS_IDS)
def test_chassis_probes_health_and_ready(pod: Pod, chassis: Doc) -> None:
    assert chassis["startupProbe"]["httpGet"]["path"] == "/health", pod.id
    assert chassis["livenessProbe"]["httpGet"]["path"] == "/health", pod.id
    assert chassis["readinessProbe"]["httpGet"]["path"] == "/ready", pod.id


def test_the_sidecar_chassis_starts_before_its_workload() -> None:
    """The chassis binds its proxies first, so the workload cannot squat 127.0.0.1:8090."""
    for pod in WORKLOAD_PODS:
        if pod.labels.get("agents.platform/lane") != "sidecar":
            continue
        init = pod.spec.get("initContainers", [])
        assert [c["name"] for c in init if _is_chassis(c)] == ["chassis"], pod.id
        assert init[0]["restartPolicy"] == "Always", pod.id


def _chassis_config(pod: Pod, chassis: Doc) -> ChassisConfig:
    """Load the chassis config the pod mounts at /etc/chassis, through the configMapGenerator."""
    args = [str(a) for a in chassis.get("args", [])]
    path = Path(_flag(args, "--config") or "")
    mount = next(m for m in chassis["volumeMounts"] if m["mountPath"] == str(path.parent))
    cm_name = _volumes(pod)[mount["name"]]["configMap"]["name"]
    generators = {g["name"]: g for g in _kustomization(pod.folder).get("configMapGenerator", [])}
    files = dict(f.split("=", 1) for f in generators[cm_name]["files"])
    return load_config(POC05 / pod.folder / files[path.name])


@pytest.mark.parametrize(("pod", "chassis"), CHASSIS, ids=CHASSIS_IDS)
def test_chassis_config_loads_and_matches_the_pod(pod: Pod, chassis: Doc) -> None:
    config = _chassis_config(pod, chassis)
    assert config.spec.trust == pod.labels[TRUST], pod.id
    assert config.spec.engine.connector == pod.labels["agents.platform/lane"], pod.id
    assert config.spec.model.route == "fake-chat", pod.id
    env = _env(chassis)
    args = [str(a) for a in chassis.get("args", [])]
    if config.spec.engine.connector == "remote":
        assert _flag(args, "--remote-proxy-host") == POD_IP, pod.id
        auth = config.spec.engine.auth
        assert auth is not None, pod.id
        ref = env[auth.token_env]["valueFrom"]["secretKeyRef"]
        assert TOKEN_SECRET.fullmatch(ref["name"]), pod.id
    else:
        assert _flag(args, "--remote-proxy-host") is None, pod.id


# --- the remote lane and the Sandboxes ---------------------------------------------------------


@pytest.mark.parametrize("pod", WORKLOAD_PODS, ids=_ids(WORKLOAD_PODS))
def test_every_sandbox_runs_on_gvisor(pod: Pod) -> None:
    if pod.doc["kind"] in POD_TEMPLATE_KINDS:
        want = "agents.x-k8s.io/v1beta1" if pod.doc["kind"] == "Sandbox" else EXTENSIONS
        assert pod.doc["apiVersion"] == want, pod.id
        assert pod.spec.get("runtimeClassName") == "gvisor", pod.id
        assert pod.spec.get("enableServiceLinks") is False, pod.id


def _one(folder: str, kind: str, name: str) -> Doc:
    found = [d for _, d in _objects(folder) if d["kind"] == kind and d["metadata"]["name"] == name]
    assert len(found) == 1, f"{folder}/: {kind} {name}"
    return found[0]


def test_the_code_runner_is_a_template_and_a_warm_pool_only() -> None:
    """A fresh sandbox per call: no shared `Sandbox` and no Service in tools/; the template lets
    the controller write no NetworkPolicy, inject no env, and add no volume claim."""
    kinds = {(d["kind"], d["metadata"]["name"]) for _, d in _objects("tools")}
    assert ("Sandbox", "code-runner") not in kinds
    assert not {k for k in kinds if k[0] == "Service"}, kinds
    template = _one("tools", "SandboxTemplate", "code-runner")
    spec = template["spec"]
    assert template["apiVersion"] == EXTENSIONS
    assert spec["networkPolicyManagement"] == "Unmanaged"
    assert spec["envVarsInjectionPolicy"] == "Disallowed"
    assert spec["volumeClaimTemplatesPolicy"] == "Disallowed"
    assert spec["service"] is False
    assert not spec.get("networkPolicy") and not spec.get("volumeClaimTemplates")
    pool = _one("tools", "SandboxWarmPool", "code-runner")
    assert pool["apiVersion"] == EXTENSIONS
    assert pool["spec"]["sandboxTemplateRef"] == {"name": "code-runner"}
    assert pool["spec"]["replicas"] == 2


def test_the_per_call_pod_has_no_liveness_probe_and_a_capped_dev_shm() -> None:
    """A per-call pod lives seconds: no silent restart mid-call. `/dev/shm` is a capped emptyDir
    (security review LOW), and memory request equals the limit."""
    (pod,) = [p for p in WORKLOAD_PODS if p.doc["kind"] == "SandboxTemplate"]
    (c,) = pod.containers()
    assert "livenessProbe" not in c, pod.id
    shm = [m for m in c["volumeMounts"] if m["mountPath"] == "/dev/shm"]
    assert len(shm) == 1, pod.id
    assert _volumes(pod)[shm[0]["name"]]["emptyDir"] == {"medium": "Memory", "sizeLimit": "8Mi"}
    resources = c["resources"]
    assert resources["requests"]["memory"] == resources["limits"]["memory"], resources
    assert pod.labels["app.kubernetes.io/name"] == "code-runner"


def _service(folder: str, name: str) -> Doc:
    found = [
        d for _, d in _objects(folder) if d["kind"] == "Service" and d["metadata"]["name"] == name
    ]
    assert len(found) == 1, f"{folder}/: Service {name}"
    return found[0]


@pytest.mark.parametrize("proxy", sorted(EXPECTED["remote_proxies"]))
def test_the_remote_reaches_its_chassis_at_the_fixed_service_ip(proxy: str) -> None:
    """The remote has no DNS: its model and tool URLs name the fixed ClusterIP, on 8091."""
    want = EXPECTED["remote_proxies"][proxy]
    svc = _service("agents", proxy)
    ip = svc["spec"]["clusterIP"]
    assert re.fullmatch(r"10\.96\.\d+\.\d+", ip), ip
    assert svc["spec"]["selector"] == {"app.kubernetes.io/name": want["chassis"]}
    assert [p["port"] for p in svc["spec"]["ports"]] == [want["port"]]
    remote = next(p for p in WORKLOAD_PODS if p.name == want["remote"])
    env = _env(remote.containers()[0])
    base = f"http://{ip}:{want['port']}"
    assert env["CHASSIS_MODEL_URL"]["value"] == f"{base}/v1"
    assert env["CHASSIS_TOOL_URL"]["value"] == f"{base}/mcp"
    assert "--require-token-env" in [str(a) for a in remote.containers()[0]["args"]]


def test_remote_probes_are_tcp() -> None:
    """The bearer check covers every HTTP path, and the kubelet has no token."""
    for pod in WORKLOAD_PODS:
        if pod.namespace != "poc05-remote":
            continue
        for c in pod.containers():
            for probe in ("startupProbe", "readinessProbe", "livenessProbe"):
                assert set(c[probe]) & {"httpGet", "exec", "grpc"} == set(), (pod.id, probe)
                assert "tcpSocket" in c[probe], (pod.id, probe)


# --- admission --------------------------------------------------------------------------------


@pytest.mark.parametrize("pod", WORKLOAD_PODS, ids=_ids(WORKLOAD_PODS))
def test_every_workload_pod_passes_the_admission_rules(pod: Pod) -> None:
    """The Python model of `admission/policy.yaml` (test_poc05_admission_static.py)."""
    params = _Params.of(_load_all(PARAMS)[0])
    assert broken_rules(_as_admitted(pod), params, pod.namespace) == set(), pod.id


def _as_admitted(pod: Pod) -> Doc:
    """A template is admitted through the `Sandbox` objects its warm pool makes from it."""
    if pod.doc["kind"] != "SandboxTemplate":
        return pod.doc
    return {
        "apiVersion": "agents.x-k8s.io/v1beta1",
        "kind": "Sandbox",
        "metadata": dict(pod.doc["metadata"]),
        "spec": {"podTemplate": pod.template},
    }


# --- NetworkPolicy ----------------------------------------------------------------------------


def _policies() -> list[tuple[Path, Doc]]:
    return [
        (path, doc)
        for path in sorted(POC05.rglob("*.yaml"))
        for doc in _load_all(path)
        if doc.get("kind") == "NetworkPolicy"
    ]


def test_no_network_policy_uses_an_ip_block_but_the_dispatchers() -> None:
    """The one ipBlock is the dispatcher's API egress (test_poc05_netpol_static.py pins it)."""
    policies = _policies()
    assert len(policies) >= 10
    with_block = []
    for path, doc in policies:
        keys = {k for k, _ in _walk(doc)}
        if "ipBlock" in keys:
            with_block.append((path.relative_to(POC05).as_posix(), doc["metadata"]["name"]))
    assert with_block == [("platform/network-policy.yaml", DISPATCHER)]


def _policy(folder: str, name: str) -> Doc:
    found = [
        d
        for _, d in _objects(folder)
        if d["kind"] == "NetworkPolicy" and d["metadata"]["name"] == name
    ]
    assert len(found) == 1, f"{folder}/: NetworkPolicy {name}"
    return found[0]


def test_the_remote_egress_is_its_chassis_port_8091_only() -> None:
    spec = _policy("remote", "remote-echo")["spec"]
    assert spec["policyTypes"] == ["Ingress", "Egress"]
    (rule,) = spec["egress"]
    assert rule["ports"] == [{"port": 8091, "protocol": "TCP"}]
    (peer,) = rule["to"]
    assert peer["podSelector"] == {"matchLabels": {"app.kubernetes.io/name": "chassis-echo-remote"}}
    (ingress,) = spec["ingress"]
    assert ingress["ports"] == [{"port": 9000, "protocol": "TCP"}]


def test_only_the_remote_reaches_port_8091() -> None:
    spec = _policy("agents", "chassis-echo-remote")["spec"]
    for rule in spec["ingress"]:
        if {"port": 8091, "protocol": "TCP"} in rule.get("ports", []):
            (peer,) = rule["from"]
            assert peer["namespaceSelector"] == {
                "matchLabels": {"kubernetes.io/metadata.name": "poc05-remote"}
            }
            assert peer["podSelector"] == {"matchLabels": {"app.kubernetes.io/name": "remote-echo"}}
    assert [r for r in spec["ingress"] if not r.get("ports")] == [], "an ingress rule on all ports"


def test_the_code_runner_has_no_egress_and_only_the_dispatcher_in() -> None:
    spec = _policy("tools", "code-runner")["spec"]
    assert spec["podSelector"] == {"matchLabels": {"app.kubernetes.io/name": "code-runner"}}
    assert "Egress" in spec["policyTypes"]
    assert spec.get("egress", []) == []
    (rule,) = spec["ingress"]
    assert rule["ports"] == [{"port": 8000, "protocol": "TCP"}]
    (peer,) = rule["from"]
    assert peer["podSelector"] == {"matchLabels": {"app.kubernetes.io/name": DISPATCHER}}
    assert peer["namespaceSelector"] == {
        "matchLabels": {"kubernetes.io/metadata.name": "poc05-platform"}
    }


# --- the dispatcher (platform/code-runner-dispatch.yaml) ----------------------------------------


def _dispatcher() -> Pod:
    (pod,) = [p for p in ALL_PODS if p.id == f"platform/Deployment/{DISPATCHER}"]
    return pod


def test_the_dispatcher_runs_on_runc_with_the_section_2_11_hardening() -> None:
    """Non-root, read-only root, drop ALL, seccomp, limits (the generic tests above), plus: runc
    (no `runtimeClassName`), its own uid, the local image never pulled, and a /health liveness
    that tolerates a slow API server."""
    pod = _dispatcher()
    assert pod.namespace == "poc05-platform"
    assert "runtimeClassName" not in pod.spec, pod.id
    assert pod.spec["serviceAccountName"] == DISPATCHER
    assert pod.spec["enableServiceLinks"] is False
    (c,) = pod.containers()
    assert c["image"] == "kind.local/agent-platform/code-runner:poc05"
    assert c["imagePullPolicy"] == "Never"
    assert c["securityContext"]["runAsUser"] == c["securityContext"]["runAsGroup"] == 10003
    assert c["resources"]["limits"] == {"cpu": "500m", "memory": "128Mi"}
    assert c["command"][0] == "code-runner" and c["args"][0] == "dispatch"
    args = [str(a) for a in c["args"]]
    assert args[args.index("--pool") + 1] == "code-runner"
    assert args[args.index("--namespace") + 1] == "poc05-tools"
    live = c["livenessProbe"]
    assert live["httpGet"]["path"] == "/health"
    assert (live["timeoutSeconds"], live["failureThreshold"]) == (5, 6)
    assert not c.get("env") and not c.get("envFrom"), "the dispatcher takes no Secret or env"
    tmp = _tmp_volume(pod, c)
    assert tmp is not None and tmp["emptyDir"]["medium"] == "Memory"


def test_the_dispatcher_token_is_projected_into_its_one_container_only() -> None:
    """Its one credential: a projected, expiring token and the cluster CA, read-only, in its one
    container. No automount, on the pod or on its ServiceAccount."""
    pod = _dispatcher()
    assert pod.spec["automountServiceAccountToken"] is False
    sa = _one("platform", "ServiceAccount", DISPATCHER)
    assert sa.get("automountServiceAccountToken") is False
    projected = {n: v for n, v in _volumes(pod).items() if "projected" in v}
    (name,) = projected
    sources = projected[name]["projected"]["sources"]
    tokens = [s["serviceAccountToken"] for s in sources if "serviceAccountToken" in s]
    assert tokens == [{"path": "token", "expirationSeconds": 3600}]
    assert {
        "configMap": {"name": "kube-root-ca.crt", "items": [{"key": "ca.crt", "path": "ca.crt"}]}
    } in sources
    assert len(sources) == 2, sources
    mounts = [
        (c["name"], m)
        for c in pod.containers()
        for m in c.get("volumeMounts", [])
        if m["name"] == name
    ]
    assert len(mounts) == 1, mounts
    assert mounts[0][1]["readOnly"] is True
    assert mounts[0][1]["mountPath"] == "/var/run/secrets/kubernetes.io/serviceaccount"


def test_the_dispatcher_is_reached_only_by_litellm() -> None:
    spec = _policy("platform", DISPATCHER)["spec"]
    assert spec["podSelector"] == {"matchLabels": {"app.kubernetes.io/name": DISPATCHER}}
    (rule,) = spec["ingress"]
    assert rule["ports"] == [{"port": 8000, "protocol": "TCP"}]
    assert rule["from"] == [{"podSelector": {"matchLabels": {"app.kubernetes.io/name": "litellm"}}}]
