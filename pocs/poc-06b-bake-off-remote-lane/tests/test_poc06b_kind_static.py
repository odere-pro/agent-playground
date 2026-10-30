"""PoC-6b on kind, offline: the manifests, scripts, and workflow of `deploy/kind/poc06/` and
`.github/workflows/poc06-kind.yml` (task T-LANES-B). The kind tests that run on them are in
`test_poc06b_kind_engines.py`, `..._remote_controls.py`, and `..._sidecar_controls.py`.

Exit criteria 1 and 4 (offline part): every new pod meets the PoC-5 controls. This file checks, for
the two sidecar pods, the four remote chassis, and the four remotes (smolagents, Claude, TypeScript,
kagent-adk):

- the hardening table (`fixtures/hardening_expect.yaml`, the shape of PoC-5's section 2.11):
  non-root with the distinct uids, read-only root, drop ALL, no privilege escalation, seccomp
  RuntimeDefault, cpu and memory limits, size-capped emptyDirs, no service account token, no host
  access, and the per-pod PID cap (the kubelet's `podPidsLimit` in PoC-5's `cluster.yaml`, which
  no pod here overrides);
- Secrets in the chassis container only; a remote holds its own token and nothing else (kagent-adk
  holds none, it uses the inbound bearer as its model key);
- every remote on gVisor, with TCP probes, a fixed proxy ClusterIP, and a model and tool URL that
  name it;
- every chassis config loads, and matches its pod;
- the NetworkPolicy edges equal `fixtures/netpol_edges.yaml`, both ways, with no `ipBlock`, no
  peerless egress, and no DNS for a remote;
- each admission rule (0 to 8; the template and claim rules T1 to T5 and C1 to C5 do not apply,
  and the folder has no template or claim) admits each object, by PoC-5's own Python model of the
  rules (`test_poc05_admission_static.broken_rules`), and refuses a mutated copy;
- the trust params differ from PoC-5's by the one repository the sidecar lane adds;
- the seed script, `run.sh`, the fake-model script, and the workflow.

Only the cluster proves the CEL itself (`test_poc05_kind_admission.py` runs it for PoC-5's
fixtures); `run.sh up` applies these objects as the deployer, so a refusal fails the job there.
"""

from __future__ import annotations

import copy
import ipaddress
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml
from chassis.server.config import ChassisConfig, load_config
from fake_model_server import Script
from poc06_harness import LOOKUP, SIMPLIFIER, SMOKE
from test_poc05_admission_static import _Params, broken_rules
from test_poc05_ci_wiring import PINNED, SHA256, _runs, _steps, _triggers, _workflow

ROOT = Path(__file__).resolve().parents[3]
POC05 = ROOT / "deploy/kind/poc05"
POC06 = ROOT / "deploy/kind/poc06"
TESTS = Path(__file__).resolve().parent
EXPECT = yaml.safe_load((TESTS / "fixtures/hardening_expect.yaml").read_text())
EDGES = yaml.safe_load((TESTS / "fixtures/netpol_edges.yaml").read_text())
WORKFLOW = ROOT / ".github/workflows/poc06-kind.yml"
REMOTE_LANE = ROOT / ".github/workflows/remote-lane.yml"
CI = ROOT / ".github/workflows/ci.yml"
RUN_SH = POC06 / "run.sh"
SEED_SH = POC06 / "seed.sh"
FOLDERS = ("remote", "agents", "kagent")

CHASSIS_REPO = "kind.local/agent-platform/chassis"
POD_KINDS = {"Deployment", "Sandbox"}
TRUST = "agents.platform/trust"
LANE = "agents.platform/lane"
NAME = "app.kubernetes.io/name"
NS_LABEL = "kubernetes.io/metadata.name"
TOKEN_SECRET = re.compile(r"remote-(?P<name>[a-z0-9]([-a-z0-9]*[a-z0-9])?)-token")
KAGENT_COMMIT = (
    "e324f6d844da1d99cd035124db856c3b74fae63b"  # pragma: allowlist secret (a git commit)
)

Doc = dict[str, Any]


def _load_all(path: Path) -> list[Doc]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


# --- loading the manifests --------------------------------------------------------------------


@dataclass(frozen=True)
class Obj:
    folder: str
    namespace: str
    doc: Doc

    @property
    def kind(self) -> str:
        return str(self.doc["kind"])

    @property
    def name(self) -> str:
        return str(self.doc["metadata"]["name"])


def _objects(folder: str) -> list[Obj]:
    """The folder's resources as written (not rendered), each with the kustomization's namespace
    or, in `kagent/`, its own."""
    kust = yaml.safe_load((POC06 / folder / "kustomization.yaml").read_text())
    out: list[Obj] = []
    for resource in kust["resources"]:
        for doc in _load_all(POC06 / folder / resource):
            ns = doc.get("metadata", {}).get("namespace", kust.get("namespace"))
            assert ns, (
                f"{folder}/{resource}: {doc['kind']} {doc['metadata']['name']} has no namespace"
            )
            out.append(Obj(folder, str(ns), doc))
    return out


ALL_OBJECTS = [o for f in FOLDERS for o in _objects(f)]


GVISOR_SECCOMP = {"type": "Localhost", "localhostProfile": "profiles/poc06-runsc-clone3.json"}
"""A gVisor pod's profile: the node's RuntimeDefault with clone3 allowed (run.sh `seccomp`; note
lanes-b-kind.md, "gVisor and clone3"). A runc pod keeps RuntimeDefault."""


@dataclass(frozen=True)
class Pod:
    obj: Obj

    @property
    def id(self) -> str:
        return self.obj.name

    @property
    def namespace(self) -> str:
        return self.obj.namespace

    @property
    def doc(self) -> Doc:
        return self.obj.doc

    @property
    def template(self) -> Doc:
        if self.obj.kind == "Sandbox":
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


PODS = [Pod(o) for o in ALL_OBJECTS if o.kind in POD_KINDS]
PIDS = [p.id for p in PODS]


def _pod(name: str) -> Pod:
    (found,) = [p for p in PODS if p.id == name]
    return found


def _is_chassis(c: Doc) -> bool:
    image = str(c.get("image", ""))
    return image == CHASSIS_REPO or image.startswith((CHASSIS_REPO + ":", CHASSIS_REPO + "@"))


def _volumes(pod: Pod) -> dict[str, Doc]:
    return {v["name"]: v for v in pod.spec.get("volumes", [])}


def _tmp_volume(pod: Pod, c: Doc) -> Doc | None:
    for m in c.get("volumeMounts", []):
        if m["mountPath"] == "/tmp":
            return _volumes(pod)[m["name"]]
    return None


def _secret_refs(c: Doc) -> Iterator[str]:
    for e in c.get("env", []):
        ref = e.get("valueFrom", {}).get("secretKeyRef")
        if ref:
            yield str(ref["name"])
    for f in c.get("envFrom", []):
        if "secretRef" in f:
            yield str(f["secretRef"]["name"])


def _flag(args: list[str], name: str) -> str | None:
    return args[args.index(name) + 1] if name in args else None


def _env(c: Doc) -> dict[str, Doc]:
    return {e["name"]: e for e in c.get("env", [])}


CONTAINERS = [(p, c) for p in PODS for c in p.containers()]
CONTAINER_IDS = [f"{p.id}/{c['name']}" for p, c in CONTAINERS]
GVISOR_CONTAINERS = [(p, c) for p, c in CONTAINERS if p.spec.get("runtimeClassName") == "gvisor"]
RUNC_CONTAINERS = [(p, c) for p, c in CONTAINERS if p.spec.get("runtimeClassName") != "gvisor"]
HOST_FIELDS = ("shareProcessNamespace", "hostNetwork", "hostPID", "hostIPC")
POD_VOLUMES = [(p, v) for p in PODS for v in p.spec.get("volumes", [])]
EMPTY_DIRS = [(p, v) for p, v in POD_VOLUMES if "emptyDir" in v]
YAML_PATHS = sorted(POC06.rglob("*.yaml"))


def _ids(pairs: list[tuple[Pod, Doc]]) -> list[str]:
    return [f"{p.id}/{c['name']}" for p, c in pairs]


def _volume_ids(pairs: list[tuple[Pod, Doc]]) -> list[str]:
    return [f"{p.id}/{v['name']}" for p, v in pairs]


# --- the table --------------------------------------------------------------------------------


def test_every_pod_is_in_the_table_and_the_table_is_all_there_is() -> None:
    """A pod added without a row in `hardening_expect.yaml` fails here, and so does a row with no
    pod."""
    found = {p.id: (p.namespace, p.obj.kind) for p in PODS}

    wanted = {n: (e["namespace"], e["kind"]) for n, e in EXPECT["pods"].items()}

    assert found == wanted


@pytest.mark.parametrize("pod", PODS, ids=PIDS)
def test_pod_containers_are_the_tables_when_the_pod_has_a_row(pod: Pod) -> None:
    """The pod's containers are the ones its table row names, no more and no fewer."""
    names = {c["name"] for c in pod.containers()}

    assert names == set(EXPECT["pods"][pod.id]["containers"]), pod.id


def test_nothing_but_pods_services_policies_accounts_and_configs() -> None:
    """No role or binding, no SandboxTemplate or SandboxClaim (so the extension rules T1 to T5
    and C1 to C5 have nothing to read)."""
    allowed = {"Deployment", "Sandbox", "Service", "ServiceAccount", "NetworkPolicy", "ConfigMap"}

    assert {o.kind for o in ALL_OBJECTS} <= allowed


@pytest.mark.parametrize("path", YAML_PATHS, ids=lambda p: str(p.relative_to(POC06)))
def test_the_manifest_holds_no_secret_when_it_is_in_git(path: Path) -> None:
    """No Secret in any file under deploy/kind/poc06 (the seed makes them)."""
    kinds = [doc.get("kind") for doc in _load_all(path)]

    assert "Secret" not in kinds, f"{path}: a Secret in git"


@pytest.mark.parametrize("pod", PODS, ids=PIDS)
def test_pod_has_no_token_and_no_host_access(pod: Pod) -> None:
    """The pod mounts no service account token and gets no service links."""
    spec = pod.spec

    assert spec.get("automountServiceAccountToken") is False, pod.id
    assert spec.get("enableServiceLinks") is False, pod.id


@pytest.mark.parametrize(
    ("pod", "field"),
    [(p, f) for p in PODS for f in HOST_FIELDS],
    ids=[f"{p.id}-{f}" for p in PODS for f in HOST_FIELDS],
)
def test_pod_shares_nothing_with_the_host_when_the_field_is_set(pod: Pod, field: str) -> None:
    """No host network, PID, or IPC namespace, and no shared process namespace."""
    value = pod.spec.get(field)

    assert not value, f"{pod.id}: {field}"


@pytest.mark.parametrize(("pod", "volume"), POD_VOLUMES, ids=_volume_ids(POD_VOLUMES))
def test_pod_volume_is_not_host_projected_or_secret(pod: Pod, volume: Doc) -> None:
    """No volume is a hostPath, a projected volume, or a Secret volume."""
    name = volume["name"]

    assert "hostPath" not in volume, f"{pod.id}: hostPath volume {name}"
    assert "projected" not in volume, f"{pod.id}: projected volume {name}"
    assert "secret" not in volume, f"{pod.id}: secret volume {name}"


@pytest.mark.parametrize("pod", PODS, ids=PIDS)
def test_pod_has_its_own_service_account_without_a_token(pod: Pod) -> None:
    """The pod names one ServiceAccount of its own, and that account mounts no token."""
    sa_name = pod.spec.get("serviceAccountName")

    accounts = [
        o.doc
        for o in ALL_OBJECTS
        if o.kind == "ServiceAccount" and o.name == sa_name and o.namespace == pod.namespace
    ]

    assert sa_name, f"{pod.id}: no serviceAccountName"
    assert len(accounts) == 1, f"{pod.id}: ServiceAccount {sa_name}"
    assert accounts[0].get("automountServiceAccountToken") is False, pod.id


@pytest.mark.parametrize(("pod", "c"), CONTAINERS, ids=CONTAINER_IDS)
def test_every_container_is_hardened(pod: Pod, c: Doc) -> None:
    """Given each container is its own case, when one drops a control, then that case fails."""
    pod_sc = pod.spec.get("securityContext", {})
    sc = c.get("securityContext", {})
    where = f"{pod.id}/{c['name']}"

    uid = sc.get("runAsUser", pod_sc.get("runAsUser"))
    limits = c.get("resources", {}).get("limits", {})

    assert sc.get("runAsNonRoot", pod_sc.get("runAsNonRoot")) is True, where
    assert isinstance(uid, int) and uid > 0, f"{where}: runAsUser {uid!r}"
    assert sc.get("readOnlyRootFilesystem") is True, where
    assert sc.get("allowPrivilegeEscalation") is False, where
    assert not sc.get("privileged"), where
    assert sc.get("capabilities", {}).get("drop") == ["ALL"], where
    assert not sc.get("capabilities", {}).get("add"), where
    assert limits.get("cpu") and limits.get("memory"), f"{where}: limits {limits}"
    assert c.get("resources", {}).get("requests"), f"{where}: no requests"


def _seccomp(pod: Pod, c: Doc) -> Doc:
    pod_sc = pod.spec.get("securityContext", {})
    return dict(
        c.get("securityContext", {}).get("seccompProfile", pod_sc.get("seccompProfile", {}))
    )


@pytest.mark.parametrize(("pod", "c"), GVISOR_CONTAINERS, ids=_ids(GVISOR_CONTAINERS))
def test_container_seccomp_is_the_clone3_profile_when_the_pod_runs_on_gvisor(
    pod: Pod, c: Doc
) -> None:
    """A gVisor pod's container uses the Localhost profile that allows clone3."""
    seccomp = _seccomp(pod, c)

    assert seccomp == GVISOR_SECCOMP, f"{pod.id}/{c['name']}"


@pytest.mark.parametrize(("pod", "c"), RUNC_CONTAINERS, ids=_ids(RUNC_CONTAINERS))
def test_container_seccomp_is_runtime_default_when_the_pod_runs_on_runc(pod: Pod, c: Doc) -> None:
    """A runc pod's container keeps the RuntimeDefault profile."""
    seccomp = _seccomp(pod, c)

    assert seccomp == {"type": "RuntimeDefault"}, f"{pod.id}/{c['name']}"


@pytest.mark.parametrize(("pod", "volume"), EMPTY_DIRS, ids=_volume_ids(EMPTY_DIRS))
def test_every_emptydir_is_size_capped_and_memory_backed(pod: Pod, volume: Doc) -> None:
    """Each emptyDir has a size limit and lives in memory, not on the node disk."""
    name = volume["name"]

    assert (volume["emptyDir"] or {}).get("sizeLimit"), f"{pod.id}: emptyDir {name} uncapped"
    assert volume["emptyDir"].get("medium") == "Memory", f"{pod.id}: {name} on the node disk"


@pytest.mark.parametrize(("pod", "c"), CONTAINERS, ids=CONTAINER_IDS)
def test_containers_match_the_table(pod: Pod, c: Doc) -> None:
    """The container's uid, gid, limits, and /tmp size are its table row's."""
    where = f"{pod.id}/{c['name']}"
    want = EXPECT["pods"][pod.id]["containers"][c["name"]]
    sc = c["securityContext"]

    tmp = _tmp_volume(pod, c)

    assert (sc.get("runAsUser"), sc.get("runAsGroup")) == (want["uid"], want["uid"]), where
    assert c["resources"]["limits"] == {"cpu": want["cpu"], "memory": want["memory"]}, where
    assert tmp is not None, f"{where}: no /tmp mount"
    assert tmp["emptyDir"]["sizeLimit"] == want["tmp"], where


@pytest.mark.parametrize("pod", PODS, ids=PIDS)
def test_pod_runtime_matches_the_table(pod: Pod) -> None:
    """The pod's runtime class (runc when unset) is its table row's."""
    runtime = pod.spec.get("runtimeClassName", "runc")

    assert runtime == EXPECT["pods"][pod.id]["runtime"], pod.id


@pytest.mark.parametrize("pod", PODS, ids=PIDS)
def test_pod_containers_have_distinct_uids(pod: Pod) -> None:
    """No two containers in one pod run as the same uid."""
    uids = [c["securityContext"]["runAsUser"] for c in pod.containers()]

    assert len(uids) == len(set(uids)), f"{pod.id}: uids {uids}"


@pytest.mark.parametrize("pod", PODS, ids=PIDS)
def test_pod_containers_share_no_tmp(pod: Pod) -> None:
    """Each container in a pod has its own /tmp volume."""
    tmps = [_tmp_volume(pod, c)["name"] for c in pod.containers()]  # type: ignore[index]

    assert len(tmps) == len(set(tmps)), f"{pod.id}: a /tmp is shared"


def test_the_pid_cap_is_the_kubelets_and_no_pod_lifts_it() -> None:
    """The per-pod PID limit is the kubelet's `podPidsLimit`, set once in PoC-5's `cluster.yaml`
    (256; PoC-5 shows on kind that the pod's pids cgroup carries it). A pod spec cannot raise it.
    This test requires the cap to be there and every pod to share the host's PID namespace with
    no one."""
    cluster = yaml.safe_load((POC05 / "cluster.yaml").read_text())
    patches = "\n".join(n.get("kubeadmConfigPatches", [""])[0] for n in cluster["nodes"])

    cap = re.search(r"podPidsLimit:\s*(\d+)", patches)

    assert cap and 0 < int(cap.group(1)) <= 256
    assert all(not p.spec.get("hostPID") for p in PODS)


# --- Secrets ----------------------------------------------------------------------------------


def _is_remote_workload_pod(pod: Pod) -> bool:
    return pod.labels.get(LANE) == "remote" and not any(_is_chassis(c) for c in pod.containers())


WORKLOADS = [(p, c) for p, c in CONTAINERS if not _is_chassis(c)]
REMOTE_WORKLOADS = [(p, c) for p, c in WORKLOADS if _is_remote_workload_pod(p)]
LOCAL_WORKLOADS = [(p, c) for p, c in WORKLOADS if not _is_remote_workload_pod(p)]


@pytest.mark.parametrize(("pod", "c"), LOCAL_WORKLOADS, ids=_ids(LOCAL_WORKLOADS))
def test_secrets_reach_the_chassis_container_only(pod: Pod, c: Doc) -> None:
    """A workload container that is not a remote never gets a Secret."""
    refs = set(_secret_refs(c))

    assert not refs, f"{pod.id}/{c['name']}: {sorted(refs)}"


@pytest.mark.parametrize(("pod", "c"), REMOTE_WORKLOADS, ids=_ids(REMOTE_WORKLOADS))
def test_a_remote_workload_gets_only_its_own_token_secret(pod: Pod, c: Doc) -> None:
    """A remote workload gets only its own token (and kagent-adk none: its bearer is its key)."""
    want = EXPECT["remotes"][pod.id]["secret"]

    refs = set(_secret_refs(c))

    assert refs == ({want} if want else set()), f"{pod.id}/{c['name']}: {sorted(refs)}"


REMOTE_PODS = [p for p in PODS if p.obj.kind == "Sandbox"]
REMOTE_PIDS = [p.id for p in REMOTE_PODS]
REMOTE_REFS = [
    (p, r)
    for p in REMOTE_PODS
    for r in sorted({r for c in p.containers() for r in _secret_refs(c)})
]
REMOTE_ENVS = [
    (p, e["name"]) for p in REMOTE_PODS for c in p.containers() for e in c.get("env", [])
]


@pytest.mark.parametrize("pod", REMOTE_PODS, ids=REMOTE_PIDS)
def test_a_remote_takes_its_own_token_only(pod: Pod) -> None:
    """The remote's name label is its own name, which its token Secret is keyed by."""
    name = pod.labels[NAME]

    assert name == pod.id


@pytest.mark.parametrize(("pod", "ref"), REMOTE_REFS, ids=[f"{p.id}-{r}" for p, r in REMOTE_REFS])
def test_a_remote_secret_ref_is_its_own_token(pod: Pod, ref: str) -> None:
    """Every Secret a remote names is its own `<name>-token`."""
    match = TOKEN_SECRET.fullmatch(ref)

    assert match and ref == f"{pod.id}-token", (pod.id, ref)


@pytest.mark.parametrize(("pod", "env"), REMOTE_ENVS, ids=[f"{p.id}-{e}" for p, e in REMOTE_ENVS])
def test_a_remote_env_name_is_not_a_credential(pod: Pod, env: str) -> None:
    """No remote env variable is named like a key, a secret, or a password."""
    suffixes = ("_API_KEY", "_SECRET", "_PASSWORD")

    assert not env.endswith(suffixes), env


def test_the_claude_pod_meets_the_cli_env_guard_and_has_a_scratch_volume() -> None:
    """The workload refuses to start a run when an ANTHROPIC_* or CLAUDE_* name is set that it
    does not override (`OVERRIDDEN` in its handle.py): the pod sets only CLAUDE_AGENT_HOME_BASE,
    which it allows, and that points at the /tmp emptyDir (the CLI writes HOME and the work dir)."""
    c = _pod("remote-claude-agent").containers()[0]
    env = _env(c)
    handle = (
        ROOT / "packages/workloads/echo-claude-agent/src/echo_claude_agent/handle.py"
    ).read_text()

    guarded = [n for n in env if n.startswith(("ANTHROPIC_", "CLAUDE_"))]
    tmp = _tmp_volume(_pod("remote-claude-agent"), c)

    assert guarded == ["CLAUDE_AGENT_HOME_BASE"], guarded
    assert env["CLAUDE_AGENT_HOME_BASE"]["value"] == "/tmp"
    assert tmp is not None and "emptyDir" in tmp and tmp["emptyDir"]["sizeLimit"] == "128Mi"
    assert '"CLAUDE_AGENT_HOME_BASE"' in handle or "HOME_BASE_VAR" in handle


# --- the chassis ------------------------------------------------------------------------------

CHASSIS = [(p, c) for p in PODS for c in p.containers() if _is_chassis(c)]
CHASSIS_IDS = [f"{p.id}/{c['name']}" for p, c in CHASSIS]
BAD_HOSTS = ("0.0.0.0", "127.0.0.1", "localhost", "::", "::1")


@pytest.mark.parametrize(("pod", "chassis"), CHASSIS, ids=CHASSIS_IDS)
def test_chassis_public_port_binds_the_pod_ip(pod: Pod, chassis: Doc) -> None:
    """The chassis binds `$(POD_IP)`, probes /health and /ready, and keeps the image entrypoint."""
    args = [str(a) for a in chassis.get("args", [])]

    pod_ip = _env(chassis)["POD_IP"]["valueFrom"]["fieldRef"]["fieldPath"]

    assert _flag(args, "--host") == "$(POD_IP)", pod.id
    assert _flag(args, "--remote-proxy-host") in {None, "$(POD_IP)"}, pod.id
    assert pod_ip == "status.podIP"
    assert chassis["startupProbe"]["httpGet"]["path"] == "/health", pod.id
    assert chassis["livenessProbe"]["httpGet"]["path"] == "/health", pod.id
    assert chassis["readinessProbe"]["httpGet"]["path"] == "/ready", pod.id
    assert not chassis.get("command"), "rule 6b: the image's entrypoint is `chassis serve`"


@pytest.mark.parametrize(
    ("pod", "chassis", "bad"),
    [(p, c, b) for p, c in CHASSIS for b in BAD_HOSTS],
    ids=[f"{i}-{b}" for i in CHASSIS_IDS for b in BAD_HOSTS],
)
def test_chassis_args_name_no_wildcard_or_loopback_host(pod: Pod, chassis: Doc, bad: str) -> None:
    """No chassis arg is a wildcard or a loopback address."""
    args = [str(a) for a in chassis.get("args", [])]

    assert bad not in args, f"{pod.id}: {bad} in args"


def _kustomization(folder: str) -> Doc:
    return dict(yaml.safe_load((POC06 / folder / "kustomization.yaml").read_text()))


def _chassis_config(pod: Pod, chassis: Doc) -> ChassisConfig:
    """Load the config the pod mounts at /etc/chassis, through the configMapGenerator."""
    args = [str(a) for a in chassis.get("args", [])]
    path = Path(_flag(args, "--config") or "")
    mount = next(m for m in chassis["volumeMounts"] if m["mountPath"] == str(path.parent))
    cm_name = _volumes(pod)[mount["name"]]["configMap"]["name"]
    generators = {
        g["name"]: g for g in _kustomization(pod.obj.folder).get("configMapGenerator", [])
    }
    files = dict(f.split("=", 1) for f in generators[cm_name]["files"])
    return load_config(POC06 / pod.obj.folder / files[path.name])


def _remote_of(pod: Pod) -> str:
    return pod.id.removeprefix("chassis-").removesuffix("-remote")


REMOTE_CHASSIS = [(p, c) for p, c in CHASSIS if p.labels.get(LANE) == "remote"]
SIDECAR_CHASSIS = [(p, c) for p, c in CHASSIS if p.labels.get(LANE) != "remote"]
PLAIN_CHASSIS = [(p, c) for p, c in REMOTE_CHASSIS if _remote_of(p) == "kagent-adk"]


@pytest.mark.parametrize(("pod", "chassis"), CHASSIS, ids=CHASSIS_IDS)
def test_chassis_config_loads_and_matches_the_pod(pod: Pod, chassis: Doc) -> None:
    """The config loads; its trust and connector are the pod's labels; its keys are Secrets."""
    env = _env(chassis)

    config = _chassis_config(pod, chassis)
    litellm = env["LITELLM_API_KEY"]["valueFrom"]["secretKeyRef"]

    assert config.spec.trust == pod.labels[TRUST], pod.id
    assert config.spec.engine.connector == pod.labels[LANE], pod.id
    assert config.spec.model.route == "fake-chat", pod.id
    assert litellm["name"].startswith("chassis-") and litellm["name"].endswith("-litellm")
    assert env["VALKEY_PASSWORD"]["valueFrom"]["secretKeyRef"]["name"] == "valkey-auth"


@pytest.mark.parametrize(("pod", "chassis"), REMOTE_CHASSIS, ids=_ids(REMOTE_CHASSIS))
def test_chassis_calls_its_remote_with_its_token_when_the_lane_is_remote(
    pod: Pod, chassis: Doc
) -> None:
    """A remote chassis binds its proxy on the pod IP and calls its own remote with its token."""
    args = [str(a) for a in chassis.get("args", [])]
    env = _env(chassis)
    remote = _remote_of(pod)

    config = _chassis_config(pod, chassis)
    auth = config.spec.engine.auth

    assert _flag(args, "--remote-proxy-host") == "$(POD_IP)", pod.id
    assert auth is not None, pod.id
    ref = env[auth.token_env]["valueFrom"]["secretKeyRef"]
    assert TOKEN_SECRET.fullmatch(ref["name"]), pod.id
    assert ref["name"] == f"remote-{remote}-token", pod.id
    assert config.spec.engine.url == f"http://remote-{remote}.poc05-remote.svc.cluster.local:9000"
    assert (config.spec.engine.protocol == "a2a") is (remote == "kagent-adk"), pod.id


@pytest.mark.parametrize(("pod", "chassis"), PLAIN_CHASSIS, ids=_ids(PLAIN_CHASSIS))
def test_chassis_reads_kagent_usage_and_omits_the_context_when_the_remote_is_plain_a2a(
    pod: Pod, chassis: Doc
) -> None:
    """The kagent-adk chassis reads usage from kagent's key and sends no context id."""
    config = _chassis_config(pod, chassis)

    a2a = config.spec.engine.a2a

    assert a2a is not None
    assert a2a.usage_key == "kagent.dev/a2a/usage"
    assert a2a.context_id == "omit"


@pytest.mark.parametrize(("pod", "chassis"), SIDECAR_CHASSIS, ids=_ids(SIDECAR_CHASSIS))
def test_chassis_calls_loopback_and_has_no_proxy_host_when_the_lane_is_sidecar(
    pod: Pod, chassis: Doc
) -> None:
    """A sidecar chassis has no remote proxy and calls its workload on 127.0.0.1."""
    args = [str(a) for a in chassis.get("args", [])]

    config = _chassis_config(pod, chassis)

    assert _flag(args, "--remote-proxy-host") is None, pod.id
    assert config.spec.engine.url == "http://127.0.0.1:9000"


SIDECAR_PODS = [p for p in PODS if p.labels.get(LANE) == "sidecar"]


@pytest.mark.parametrize("pod", SIDECAR_PODS, ids=[p.id for p in SIDECAR_PODS])
def test_the_sidecar_chassis_starts_before_its_workload(pod: Pod) -> None:
    """The chassis is the one native-sidecar init container; the workload gets only its URLs."""
    init = pod.spec.get("initContainers", [])

    workload = pod.spec["containers"][0]

    assert [c["name"] for c in init if _is_chassis(c)] == ["chassis"], pod.id
    assert init[0]["restartPolicy"] == "Always", pod.id
    assert set(_env(workload)) == {"HOST", "PORT", "CHASSIS_MODEL_URL", "CHASSIS_TOOL_URL"}


# --- the remote lane --------------------------------------------------------------------------

REMOTES = sorted(EXPECT["remotes"])
PROBES = ("startupProbe", "readinessProbe", "livenessProbe")
TOKEN_REMOTES = [n for n in REMOTES if n != "remote-kagent-adk"]


@pytest.mark.parametrize("name", REMOTES)
def test_every_remote_is_a_sandbox_on_gvisor_with_tcp_probes(name: str) -> None:
    """A remote is a Sandbox on gVisor, untrusted, in poc05-remote, with one workload container."""
    pod = _pod(name)

    containers = [c["name"] for c in pod.containers()]

    assert pod.doc["apiVersion"] == "agents.x-k8s.io/v1beta1"
    assert pod.spec.get("runtimeClassName") == "gvisor"
    assert pod.doc["spec"]["service"] is False
    assert pod.labels[LANE] == "remote" and pod.labels[TRUST] == "untrusted"
    assert pod.namespace == "poc05-remote"
    assert containers == ["workload"]


@pytest.mark.parametrize(
    ("name", "probe"),
    [(n, p) for n in REMOTES for p in PROBES],
    ids=[f"{n}-{p}" for n in REMOTES for p in PROBES],
)
def test_a_remote_probe_is_tcp_only(name: str, probe: str) -> None:
    """Each remote probe is a TCP socket check, never HTTP, exec, or gRPC."""
    c = _pod(name).containers()[0]

    kinds = set(c[probe])

    assert kinds & {"httpGet", "exec", "grpc"} == set(), (name, probe)
    assert "tcpSocket" in kinds, (name, probe)


@pytest.mark.parametrize("name", TOKEN_REMOTES)
def test_a_remote_requires_its_token_when_it_is_not_kagent(name: str) -> None:
    """The workload demands its token from the Secret and binds the pod IP."""
    c = _pod(name).containers()[0]

    env = _env(c)

    assert "--require-token-env" in [str(a) for a in c["args"]], name
    assert env["CHASSIS_API_TOKEN"]["valueFrom"]["secretKeyRef"]["key"] == "token"
    assert env["HOST"]["value"] == "$(POD_IP)"


def _service(folder: str, name: str) -> Doc:
    (found,) = [o.doc for o in _objects(folder) if o.kind == "Service" and o.name == name]
    return found


def _proxy_base(name: str) -> str:
    return f"http://{EXPECT['remotes'][name]['ip']}:8091"


@pytest.mark.parametrize("name", REMOTES)
def test_the_remote_reaches_its_chassis_at_the_fixed_service_ip(name: str) -> None:
    """The remote has no DNS: its chassis proxy is a fixed ClusterIP, on 8091. The IPs are inside
    the Service range, distinct from each other and from PoC-5's 10.96.85.91."""
    want = EXPECT["remotes"][name]
    folder = "kagent" if name == "remote-kagent-adk" else "agents"
    cluster = yaml.safe_load((POC05 / "cluster.yaml").read_text())

    svc = _service(folder, f"{want['chassis']}-proxy")
    ip = svc["spec"]["clusterIP"]

    assert ip == want["ip"]
    assert ipaddress.ip_address(ip) in ipaddress.ip_network(cluster["networking"]["serviceSubnet"])
    assert ip != "10.96.85.91"
    assert svc["spec"]["selector"] == {NAME: want["chassis"]}
    assert [p["port"] for p in svc["spec"]["ports"]] == [8091]


def test_the_kagent_model_url_names_the_fixed_service_ip() -> None:
    """kagent-adk's model goes to its chassis's remote listener and passes the bearer through."""
    cm = next(o.doc for o in _objects("kagent") if o.kind == "ConfigMap")

    model = json.loads(cm["data"]["config.json"])["model"]

    assert model["base_url"] == f"{_proxy_base('remote-kagent-adk')}/v1", (
        "the model goes to the chassis's remote listener"
    )
    assert model["api_key_passthrough"] is True
    assert "api_key" not in model and "api_key_secret" not in model


@pytest.mark.parametrize("name", TOKEN_REMOTES)
def test_the_remote_model_and_tool_urls_name_the_fixed_service_ip(name: str) -> None:
    """The workload's model and tool URLs are the proxy ClusterIP on 8091."""
    base = _proxy_base(name)

    env = _env(_pod(name).containers()[0])

    assert env["CHASSIS_MODEL_URL"]["value"] == f"{base}/v1"
    assert env["CHASSIS_TOOL_URL"]["value"] == f"{base}/mcp"


def test_the_proxy_addresses_are_unique_across_poc05_and_poc06() -> None:
    """No two remotes share a proxy IP, and PoC-5's remote does not use one of them."""
    ips = [e["ip"] for e in EXPECT["remotes"].values()]

    poc05 = (POC05 / "agents/chassis-echo-remote.yaml").read_text()

    assert len(ips) == len(set(ips))
    assert all(f"clusterIP: {ip}" not in poc05 for ip in ips)


def test_kagent_is_built_from_a_pinned_commit() -> None:
    """The image is not pulled: `run.sh build_kagent` clones kagent, checks out the commit in
    `kagent/source.commit`, and fails closed unless HEAD equals it. The pod runs the loaded image
    with `imagePullPolicy: Never`, in the remote lane only."""
    c = _pod("remote-kagent-adk").containers()[0]
    run = RUN_SH.read_text()

    commits = [
        ln for ln in (POC06 / "kagent/source.commit").read_text().splitlines()
        if re.fullmatch(r"[0-9a-f]{40}", ln)
    ]  # fmt: skip
    body = run[run.index("build_kagent() {") :].split("\n}\n")[0]

    assert c["image"] == "kind.local/agent-platform/kagent-adk:kind"
    assert c["imagePullPolicy"] == "Never"
    assert _pod("remote-kagent-adk").labels[LANE] == "remote"
    assert commits == [KAGENT_COMMIT]  # pragma: allowlist secret (a public git commit)
    assert not (POC06 / "kagent/image.sha256").exists()
    assert "git clone --quiet --filter=blob:none" in body
    assert 'checkout --quiet --detach "$commit"' in body
    assert '[[ $head == "$commit" ]] || die' in body
    assert body.index("rev-parse HEAD") < body.index("docker build")
    assert '-f "$dir/kagent/python/Dockerfile"' in body and '-t "$KAGENT_IMAGE"' in body
    assert "KAGENT_IMAGE=$REGISTRY/kagent-adk:$TAG" in run
    assert 'kind load docker-image "$KAGENT_IMAGE"' in run
    assert "apply_kagent" in run.split("apply_all() {")[1].split("}")[0]


def test_kagent_launcher_runs_the_image_venv_python() -> None:
    """The launcher compiles, builds the app locally, and runs on the image's venv Python."""
    cm = next(o.doc for o in _objects("kagent") if o.kind == "ConfigMap")
    c = _pod("remote-kagent-adk").containers()[0]

    compile(cm["data"]["run_kagent_adk.py"], "run_kagent_adk.py", "exec")

    assert "kapp.build(local=True)" in cm["data"]["run_kagent_adk.py"]
    assert c["command"] == ["/.kagent/.venv/bin/python"]
    assert c["args"][0] == "/config/run_kagent_adk.py"


@pytest.mark.parametrize(
    "name", ["OTEL_TRACES_EXPORTER", "OTEL_METRICS_EXPORTER", "OTEL_LOGS_EXPORTER"]
)
def test_kagent_launcher_turns_the_exporter_off(name: str) -> None:
    """Each OTel exporter of the kagent-adk pod is `none`."""
    env = _env(_pod("remote-kagent-adk").containers()[0])

    value = env[name]["value"]

    assert value == "none", name


# --- admission --------------------------------------------------------------------------------


def _params(path: Path) -> _Params:
    return _Params.of(_load_all(path)[0])


PARAMS5 = POC05 / "admission/params.yaml"
PARAMS6 = POC06 / "admission/params.yaml"
KAGENT_IMAGE = "kind.local/agent-platform/kagent-adk:kind"
BUILT = [(p, c) for p, c in CONTAINERS if c["image"] != KAGENT_IMAGE]


def test_the_trust_params_add_one_repository_and_change_nothing_else() -> None:
    """PoC-6's params are PoC-5's plus the echo-openai-agents repository at the end."""
    five, six = _load_all(PARAMS5)[0], _load_all(PARAMS6)[0]

    old = five["data"]["trustedRepositories"].split(",")
    new = six["data"]["trustedRepositories"].split(",")

    assert six["metadata"] == five["metadata"]
    assert {k: v for k, v in six["data"].items() if k != "trustedRepositories"} == {
        k: v for k, v in five["data"].items() if k != "trustedRepositories"
    }
    assert new[: len(old)] == old, "PoC-5's repositories stay, in order"
    assert new[len(old) :] == ["kind.local/agent-platform/echo-openai-agents"]


def test_the_kagent_image_is_built_by_run_sh() -> None:
    """run.sh names the kagent-adk image it builds from source."""
    run = RUN_SH.read_text()

    assert "KAGENT_IMAGE=$REGISTRY/kagent-adk:$TAG" in run


@pytest.mark.parametrize(("pod", "c"), BUILT, ids=_ids(BUILT))
def test_every_workload_image_is_loaded_by_run_sh_or_poc05(pod: Pod, c: Doc) -> None:
    """Every other image is a local one that run.sh or PoC-5 builds, never pulled (rule 8)."""
    image = c["image"]
    scripts = RUN_SH.read_text() + (POC05 / "run.sh").read_text()

    name = image.split("/")[-1].replace(":kind", ":$TAG")

    assert image.startswith("kind.local/agent-platform/"), image
    assert f'"$REGISTRY/{name}|' in scripts, f"{image}: nobody builds it"
    assert c["imagePullPolicy"] == "Never", f"{pod.id}: rule 8"


@pytest.mark.parametrize("pod", PODS, ids=PIDS)
def test_every_admission_rule_admits_every_poc06_object(pod: Pod) -> None:
    """Rules 0 to 8 by PoC-5's Python model of `admission/policy.yaml`, with the PoC-6 params. The
    object is as the deployer applies it; the namespace's pod-shape label is PoC-5's."""
    broken = broken_rules(pod.doc, _params(PARAMS6), pod.namespace)

    assert broken == set(), pod.id


def _mutations(pod: Pod) -> list[tuple[str, str, Doc]]:
    """Copies of `pod` that each break exactly one rule, for the control below."""
    out: list[tuple[str, str, Doc]] = []

    def template(doc: Doc) -> Doc:
        return dict(
            doc["spec"]["podTemplate"] if doc["kind"] == "Sandbox" else doc["spec"]["template"]
        )

    base = pod.doc
    if base["kind"] == "Sandbox":
        d = copy.deepcopy(base)
        del template(d)["spec"]["runtimeClassName"]
        out.append(("5", "no gVisor", d))
        d = copy.deepcopy(base)
        template(d)["spec"]["containers"][0].setdefault("env", []).append(
            {"name": "X", "valueFrom": {"secretKeyRef": {"name": "remote-other-token", "key": "t"}}}
        )
        out.append(("7c", "another remote's token", d))
    d = copy.deepcopy(base)
    del template(d)["metadata"]["labels"][TRUST]
    out.append(("1", "no trust label", d))
    return out


MUTATIONS = [(p, rule, why, doc) for p in PODS for rule, why, doc in _mutations(p)]


@pytest.mark.parametrize(
    ("pod", "rule", "why", "doc"),
    MUTATIONS,
    ids=[f"{p.id}-rule{r}" for p, r, _, _ in MUTATIONS],
)
def test_the_rule_model_refuses_a_mutated_copy(pod: Pod, rule: str, why: str, doc: Doc) -> None:
    """The control for the test above: the model is not a rubber stamp."""
    params = _params(PARAMS6)

    broken = broken_rules(doc, params, pod.namespace)

    assert rule in broken, (pod.id, why)


def test_a_sidecar_pod_with_an_unlisted_workload_image_breaks_rule_4() -> None:
    """An image not on the trusted list breaks rule 4, and PoC-5's list lacks the PoC-6 one."""
    pod = _pod("agent-openai-agents")
    doc = copy.deepcopy(pod.doc)
    doc["spec"]["template"]["spec"]["containers"][0]["image"] = (
        "kind.local/agent-platform/other:kind"
    )

    broken = broken_rules(doc, _params(PARAMS6), pod.namespace)

    assert "4" in broken
    assert "4" in broken_rules(pod.doc, _params(PARAMS5), pod.namespace), "PoC-5's list lacks it"


def _namespace_labels() -> dict[str, dict[str, str]]:
    return {
        d["metadata"]["name"]: d["metadata"].get("labels", {})
        for d in _load_all(POC05 / "base/namespaces.yaml")
        if d["kind"] == "Namespace"
    }


@pytest.mark.parametrize("pod", PODS, ids=PIDS)
def test_namespaces_are_poc05s_with_the_right_shapes(pod: Pod) -> None:
    """The pod's namespace is PoC-5's, enforcing, with the pod shape its kind needs."""
    want = "remote" if pod.obj.kind == "Sandbox" else "chassis"

    labels = _namespace_labels()[pod.namespace]

    assert labels["agents.platform/pod-shape"] == want, pod.id
    assert labels["agents.platform/admission"] == "enforce", pod.id


# --- NetworkPolicy ----------------------------------------------------------------------------

Edge = tuple[str, str, str]


def _sel(ns: str, labels: dict[str, str]) -> str:
    return f"{ns}/" + (",".join(f"{k}={v}" for k, v in sorted(labels.items())) or "*")


def _ports(raw: list[Doc]) -> list[str]:
    return [f"{p.get('protocol', 'TCP')}/{p['port']}" for p in raw]


POLICIES = [o for o in ALL_OBJECTS if o.kind == "NetworkPolicy"]


def computed_edges() -> tuple[set[Edge], set[Edge]]:
    egress: set[Edge] = set()
    ingress: set[Edge] = set()
    for pol in POLICIES:
        spec = pol.doc["spec"]
        own = _sel(pol.namespace, spec["podSelector"]["matchLabels"])
        for rule in spec.get("egress", []):
            for peer in rule["to"]:
                dst = _sel(
                    peer["namespaceSelector"]["matchLabels"][NS_LABEL],
                    peer.get("podSelector", {}).get("matchLabels", {}),
                )
                egress.update((own, dst, port) for port in _ports(rule["ports"]))
        for rule in spec.get("ingress", []):
            peers = rule.get("from")
            if peers is None:
                ingress.update(("any", own, port) for port in _ports(rule["ports"]))
                continue
            for peer in peers:
                src = _sel(
                    peer["namespaceSelector"]["matchLabels"][NS_LABEL],
                    peer.get("podSelector", {}).get("matchLabels", {}),
                )
                ingress.update((src, own, port) for port in _ports(rule["ports"]))
    return egress, ingress


def fixture_edges() -> tuple[set[Edge], set[Edge]]:
    egress: set[Edge] = set()
    ingress: set[Edge] = set()
    chassis = [*EDGES["sidecar_pods"], *(p["chassis"] for p in EDGES["remote_pairs"])]
    for name in chassis:
        own = _sel("poc05-agents", {NAME: name})
        for e in EDGES["chassis_egress"]:
            dst = _sel(e["dst"]["ns"], e["dst"]["labels"])
            egress.update((own, dst, port) for port in e["ports"])
        for e in EDGES["chassis_ingress"]:
            assert e["src"] == "any"
            ingress.update(("any", own, port) for port in e["ports"])
    for pair in EDGES["remote_pairs"]:
        side = {
            "remote": _sel("poc05-remote", {NAME: pair["remote"]}),
            "chassis": _sel("poc05-agents", {NAME: pair["chassis"]}),
        }
        for e in EDGES["pair_edges"]:
            for port in e["ports"]:
                egress.add((side[e["from"]], side[e["to"]], port))
                ingress.add((side[e["from"]], side[e["to"]], port))
    return egress, ingress


def test_network_policy_edges_equal_the_fixture() -> None:
    """Both ways: an edge the manifests add or drop fails until `netpol_edges.yaml` says so."""
    got_e, got_i = computed_edges()
    want_e, want_i = fixture_edges()

    diff = [
        line
        for name, got, want in (("egress", got_e, want_e), ("ingress", got_i, want_i))
        for line in (
            *(f"extra {name} (manifests only): {e}" for e in sorted(got - want)),
            *(f"missing {name} (fixture only): {e}" for e in sorted(want - got)),
        )
    ]

    assert not diff, "\n".join(diff)
    assert got_e and got_i


def _pol_id(pol: Obj) -> str:
    return f"{pol.namespace}/{pol.name}"


EGRESS_RULES = [(pol, rule) for pol in POLICIES for rule in pol.doc["spec"].get("egress", [])]
EGRESS_PEERS = [(pol, peer) for pol, rule in EGRESS_RULES for peer in rule.get("to") or []]
OPEN_INGRESS = [
    (pol, rule)
    for pol in POLICIES
    for rule in pol.doc["spec"].get("ingress", [])
    if "from" not in rule
]
REMOTE_POLICIES = [pol for pol in POLICIES if pol.namespace == "poc05-remote"]


@pytest.mark.parametrize("pol", POLICIES, ids=_pol_id)
def test_policies_are_label_based_and_deny_the_rest(pol: Obj) -> None:
    """Each policy covers both ways and has no `ipBlock`, so the metadata service and the API
    server are not named."""
    spec = pol.doc["spec"]

    assert set(spec["policyTypes"]) == {"Ingress", "Egress"}, pol.name
    assert "ipBlock" not in yaml.safe_dump(spec), pol.name


@pytest.mark.parametrize(
    ("pol", "rule"),
    EGRESS_RULES,
    ids=[f"{_pol_id(p)}-{i}" for i, (p, _) in enumerate(EGRESS_RULES)],
)
def test_an_egress_rule_names_a_destination(pol: Obj, rule: Doc) -> None:
    """No egress rule is peerless."""
    peers = rule.get("to")

    assert peers, f"{pol.name}: an egress rule with no destination"


@pytest.mark.parametrize(
    ("pol", "peer"),
    EGRESS_PEERS,
    ids=[f"{_pol_id(p)}-{i}" for i, (p, _) in enumerate(EGRESS_PEERS)],
)
def test_an_egress_peer_selects_pods_by_label(pol: Obj, peer: Doc) -> None:
    """No egress peer selects every pod in a namespace."""
    labels = peer.get("podSelector", {}).get("matchLabels")

    assert labels, f"{pol.name}: all pods"


@pytest.mark.parametrize(
    ("pol", "rule"),
    OPEN_INGRESS,
    ids=[f"{_pol_id(p)}-{i}" for i, (p, _) in enumerate(OPEN_INGRESS)],
)
def test_an_ingress_rule_without_a_source_opens_only_8080(pol: Obj, rule: Doc) -> None:
    """An ingress rule open to any source opens only the public port 8080."""
    ports = [p["port"] for p in rule["ports"]]

    assert ports == [8080], pol.name


def test_every_pod_has_a_policy_and_poc05_denies_the_rest() -> None:
    """A policy for every pod, and PoC-5's `default-deny` in each namespace refuses everything
    these do not allow."""
    covered = {pol.doc["spec"]["podSelector"]["matchLabels"][NAME] for pol in POLICIES}

    default_deny = [
        d["metadata"]["namespace"]
        for d in _load_all(POC05 / "base/default-deny.yaml")
        if d["metadata"]["name"] == "default-deny"
    ]

    assert covered == {p.id for p in PODS}
    assert {"poc05-agents", "poc05-remote"} <= set(default_deny)


@pytest.mark.parametrize("pol", REMOTE_POLICIES, ids=_pol_id)
def test_a_remote_has_no_dns_and_only_its_chassis_in_and_out(pol: Obj) -> None:
    """A remote's egress is port 8091 only (no DNS), and its only peer is its chassis."""
    spec = pol.doc["spec"]

    ports = [p["port"] for rule in spec["egress"] for p in rule["ports"]]
    peers = {
        peer["podSelector"]["matchLabels"][NAME]
        for rule in [*spec["egress"], *spec["ingress"]]
        for peer in rule.get("to", rule.get("from", []))
    }

    assert ports == [8091], pol.name
    assert len(peers) == 1 and next(iter(peers)).startswith("chassis-"), pol.name


@pytest.mark.parametrize("pod", PODS, ids=PIDS)
def test_the_chassis_pods_carry_the_role_litellm_and_valkey_admit(pod: Pod) -> None:
    """PoC-5's `litellm` and `valkey` policies admit `poc05-agents` pods with `role: chassis`. A
    PoC-6 chassis pod without it would get no answer; a pod with it that is not a chassis would."""
    has_chassis = any(_is_chassis(c) for c in pod.containers())

    role = pod.labels.get("agents.platform/role")

    assert (role == "chassis") is has_chassis, pod.id


# --- the seed, run.sh, the script -------------------------------------------------------------


def _array(text: str, name: str) -> list[str]:
    match = re.search(rf"(?ms)^{name}=\((.*?)\)$", text)
    assert match, f"no {name} array"
    return [w for w in re.sub(r"#.*", "", match.group(1)).split() if w]


def _code_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]


def test_the_seed_makes_every_secret_the_manifests_name() -> None:
    """The seed's arrays make exactly the Secrets the pods name, and the remotes' tokens."""
    seed = SEED_SH.read_text()
    services, remotes = _array(seed, "SERVICES"), _array(seed, "REMOTES")
    chassis_only = _array(seed, "CHASSIS_ONLY_REMOTES")

    made = {f"chassis-{s}-litellm" for s in services}
    made |= {f"remote-{r}-token" for r in [*remotes, *chassis_only]}
    named = {r for _, c in CONTAINERS for r in _secret_refs(c) if r != "valkey-auth"}

    assert named == made
    assert {f"remote-{r}-token" for r in remotes} == {
        e["secret"] for e in EXPECT["remotes"].values() if e["secret"]
    }


CREDENTIAL = re.compile(r"\$\{?(value|key|master|resp)\b")
PRINTED_CREDENTIAL = re.compile(r"\$\{?(value|key|master)\b")
PIPED_PREFIXES = ("printf", "unset", "[[", "die", "if !", "local")


def _in_a_pipe(ln: str) -> bool:
    return (
        ln.lstrip().startswith(PIPED_PREFIXES)
        or "| curl" in ln
        or "| jq" in ln
        or "| put_secret" in ln
    )


def test_the_seed_text_is_strict_pins_the_context_and_prints_no_credential() -> None:
    """The seed script's text: strict mode, a pinned context, no xtrace, kubectl only through
    `kctl`, and every credential variable kept inside a pipe."""
    text = SEED_SH.read_text()
    code = _code_lines(text)
    body = "\n".join(code)
    allowed = ('kctl() { kubectl --context "$CONTEXT" "$@"; }', "for tool in kubectl")

    kubectl = [ln for ln in code if re.search(r"(?<![\w-])kubectl\s", ln)]
    outside = [ln.strip() for ln in code if CREDENTIAL.search(ln) and not _in_a_pipe(ln)]
    printed = [
        ln.strip()
        for ln in code
        if ln.lstrip().startswith("printf") and PRINTED_CREDENTIAL.search(ln) and "|" not in ln
    ]
    echoed = [ln for ln in code if re.search(r"\becho\b.*\$\{?(value|key|master)\b", ln)]

    assert "set -euo pipefail" in body and "CONTEXT=kind-poc05" in body
    assert not re.search(r"\bset\s+-\w*x", body), "set -x would print a credential"
    assert all(any(a in ln for a in allowed) for ln in kubectl), kubectl
    assert "openssl rand -hex" in body
    assert not outside, f"a credential variable outside a pipe: {outside}"
    assert not printed, f"printf of a credential not into a pipe: {printed}"
    assert not echoed, echoed


def test_the_seed_text_grants_only_the_fake_tools() -> None:
    """The seed script's text scopes the keys to the fake MCP server and its two tools."""
    text = SEED_SH.read_text()

    assert 'object_permission: {mcp_servers: ["fake_tools"]' in text
    assert 'TOOLS="glossary_lookup,acronym_expand"' in text
    assert '"openai_routes", "mcp_inference_routes"' in text


RUN_VERBS = ("up", "build", "load", "seed", "apply", "test", "pods", "logs", "redact")


@pytest.mark.parametrize("verb", RUN_VERBS)
def test_the_run_sh_text_has_a_case_for_the_verb(verb: str) -> None:
    """run.sh's text has a `<verb>)` case for each verb it offers."""
    text = RUN_SH.read_text()

    case = re.search(rf"^\s*{verb}\) ", text, re.M)

    assert case, verb


def test_the_run_sh_text_reuses_poc05_and_pins_the_context() -> None:
    """run.sh's text: strict, PoC-5's cluster and context, PoC-5's up, delete, logs, and redact,
    kubectl only through `kctl`, logs only through redact, and applies as the deployer."""
    text = RUN_SH.read_text()
    code = _code_lines(text)

    kubectl = [ln for ln in code if re.search(r"(?<![\w-])kubectl\s", ln)]
    logs = [ln for ln in code if re.search(r"\bkctl\b.*\slogs\s", ln)]

    assert "set -euo pipefail" in text
    assert re.search(r"^CLUSTER=poc05$", text, re.M) and "CONTEXT=kind-$CLUSTER" in text
    assert re.search(r"^\s*delete \| down\) ", text, re.M)
    assert re.search(r'^\s*logs\) "\$P5" logs ;;', text, re.M)
    assert re.search(r'^\s*redact\) "\$P5" redact ;;', text, re.M)
    assert '"$P5" up' in text and '"$P5" delete' in text
    assert kubectl == ['kctl() { kubectl --context "$CONTEXT" "$@"; }'], kubectl
    assert logs and all(re.search(r'\|\s*"\$P5" redact', ln) for ln in logs), logs
    assert (
        '--as="$DEPLOYER"' in text
        and "DEPLOYER=system:serviceaccount:agent-platform-system:deployer" in text
    )


def test_the_run_sh_text_selects_the_kind_files_by_path_and_names_missing_ones() -> None:
    """run.sh's `KIND_TESTS` array lists the four kind files, each exists, and `run_tests`
    selects them by path, never by `-k`."""
    text = RUN_SH.read_text()

    listed = _array(text, "KIND_TESTS")
    missing = [f for f in listed if not (ROOT / f).is_file()]
    run_tests = text[text.index("run_tests() {") :].split("\n}\n")[0]

    assert listed == [
        "pocs/poc-06b-bake-off-remote-lane/tests/test_poc06b_kind_engines.py",
        "pocs/poc-06b-bake-off-remote-lane/tests/test_poc06b_kind_load.py",
        "pocs/poc-06b-bake-off-remote-lane/tests/test_poc06b_kind_remote_controls.py",
        "pocs/poc-06b-bake-off-remote-lane/tests/test_poc06b_kind_sidecar_controls.py",
    ]
    assert not missing, missing
    assert "POC06_KIND=1" in text and '"${KIND_TESTS[@]}"' in text and "-m kind" in text
    assert " -k " not in run_tests, "select by file path, not by -k"


def test_the_run_sh_text_writes_no_poc05_file() -> None:
    """It calls PoC-5's run.sh and patches live objects; it never writes under deploy/kind/poc05."""
    code = _code_lines(RUN_SH.read_text() + SEED_SH.read_text())

    writes = [ln for ln in code if "poc05/" in ln and re.search(r">|sed -i|tee", ln)]

    assert not writes


def _pick(script: Script, messages: list[dict[str, Any]]) -> Any:
    return script.pick(messages)


SCRIPT = Script.from_yaml(POC06 / "platform/fake-model-script.yaml")


def _ask(text: str) -> Any:
    return SCRIPT.pick([{"role": "user", "content": text}])


@pytest.mark.parametrize("marker", ["", " [text]"])
def test_the_fake_model_script_answers_smoke_and_simplifier_when_the_form_is_chat_or_text(
    marker: str,
) -> None:
    """The script is first-match-wins, so each task's text must reach the rule meant for it. In
    the chat form (no marker) and kagent-adk's ` [text]` form, smoke and simplifier are replies."""
    smoke = _ask(SMOKE.text + marker)

    simple = _ask(SIMPLIFIER.text + marker)

    assert smoke.reply == "Hello." and smoke.tool_call is None
    assert all(f in simple.reply for f in ("SLM", "Acme", "2026", "30 percent"))


def test_the_fake_model_script_answers_lookup_in_one_reply_when_the_form_is_text() -> None:
    """` [text]`: kagent-adk gets the lookup as one reply, with no tool call."""
    lookup = _ask(LOOKUP.text + " [text]")

    assert lookup.tool_call is None
    assert "small language model" in lookup.reply
    assert "retrieval-augmented generation" in lookup.reply


def test_the_fake_model_script_calls_both_tools_in_order_when_the_form_is_chat() -> None:
    """Plain form: OpenAI chat with tool calls by the gateway's names, glossary then acronym."""
    task = {"role": "user", "content": LOOKUP.text}

    lookup = _ask(LOOKUP.text)
    first = SCRIPT.pick([task, {"role": "tool", "content": "SLM is a small language model"}])
    last = SCRIPT.pick([task, {"role": "tool", "content": "RAG: retrieval-augmented generation"}])

    assert lookup.tool_call is not None
    assert (lookup.tool_call.name, lookup.tool_call.arguments) == (
        "fake_tools-glossary_lookup",
        {"term": "SLM"},
    )
    assert first.tool_call is not None
    assert (first.tool_call.name, first.tool_call.arguments) == (
        "fake_tools-acronym_expand",
        {"acronym": "RAG"},
    )
    assert last.tool_call is None and "retrieval-augmented generation" in last.reply


def test_the_fake_model_script_writes_python_when_the_form_is_code() -> None:
    """` [code]`: smolagents gets Python in a `<code>` block, observations as user messages."""
    glossary = {
        "role": "user",
        "content": "Execution logs: {'definition': 'a small language model'}",
    }
    acronym = {"role": "user", "content": "Execution logs: retrieval-augmented generation"}

    smoke = _ask(SMOKE.text + " [code]")
    simple = _ask(SIMPLIFIER.text + " [code]")
    lookup = _ask(LOOKUP.text + " [code]")

    assert 'final_answer("Hello.")' in smoke.reply
    assert all(f in simple.reply for f in ("Acme", "2026", "30 percent"))
    assert 'fake_tools_glossary_lookup(term="SLM")' in lookup.reply
    assert 'fake_tools_acronym_expand(acronym="RAG")' in SCRIPT.pick([glossary]).reply
    assert "final_answer" in SCRIPT.pick([acronym]).reply


def test_the_fake_model_patch_points_the_deployment_at_the_script() -> None:
    """The patch runs the fake model server on the mounted PoC-6 script, read-only."""
    patch = yaml.safe_load((POC06 / "platform/fake-model-server-patch.yaml").read_text())

    c = patch["spec"]["template"]["spec"]["containers"][0]

    assert c["name"] == "fake-model-server"
    assert c["args"] == ["--script", "/etc/fake-model/script.yaml"]
    assert c["volumeMounts"][0]["readOnly"] is True
    assert patch["spec"]["template"]["spec"]["volumes"][0]["configMap"]["name"] == (
        "fake-model-script-poc06"
    )
    assert "fake-model-script-poc06" in RUN_SH.read_text()


# --- the workflow -----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def workflow() -> dict[str, Any]:
    assert WORKFLOW.is_file(), "missing .github/workflows/poc06-kind.yml"
    return _workflow(WORKFLOW)


def test_the_workflow_runs_on_push_and_never_on_pull_request_target(
    workflow: dict[str, Any],
) -> None:
    """Triggers are push, pull_request, and dispatch; no line names pull_request_target."""
    code = _code_lines(WORKFLOW.read_text())

    triggers = _triggers(workflow)

    assert triggers == {"push", "pull_request", "workflow_dispatch"}
    assert not any("pull_request_target" in ln for ln in code)


def test_the_workflow_job_shape(workflow: dict[str, Any]) -> None:
    """One job on ubuntu-latest, at most 45 minutes, with read-only contents."""
    jobs = workflow["jobs"]

    job = next(iter(jobs.values()))

    assert len(jobs) == 1
    assert job["runs-on"] == "ubuntu-latest"
    assert isinstance(job.get("timeout-minutes"), int) and 0 < job["timeout-minutes"] <= 45
    assert workflow.get("permissions", job.get("permissions")) == {"contents": "read"}


def test_the_workflow_pins_every_action_by_sha_the_same_as_ci(workflow: dict[str, Any]) -> None:
    """Every action is pinned by a 40-hex SHA, the same one ci.yml pins, with a version comment."""
    ci_pins = {s["uses"].split("@")[0]: s["uses"] for s in _steps(_workflow(CI)) if "uses" in s}
    lines = WORKFLOW.read_text().splitlines()

    uses = [s["uses"] for s in _steps(workflow) if "uses" in s]
    unpinned = [ref for ref in uses if not PINNED.match(ref)]
    drifted = [ref for ref in uses if ref != ci_pins.get(ref.split("@")[0])]
    uncommented = [
        ln.strip()
        for ln in lines
        if "uses:" in ln and not ln.lstrip().startswith("#") and "# v" not in ln
    ]

    assert uses, "expected checkout and setup-uv"
    assert not unpinned, f"not pinned by a 40-hex SHA: {unpinned}"
    assert not drifted, f"pinned differently from ci.yml: {drifted}"
    assert not uncommented, f"no version comment: {uncommented}"


@pytest.mark.parametrize(
    "key", ["KIND_VERSION", "KUBECTL_VERSION", "KIND_SHA256", "KUBECTL_SHA256"]
)
def test_the_workflow_pins_kind_and_kubectl_like_remote_lane(
    workflow: dict[str, Any], key: str
) -> None:
    """Version and sha256 in the job env, the same values as remote-lane.yml."""
    env = next(iter(workflow["jobs"].values()))["env"]

    other = next(iter(_workflow(REMOTE_LANE)["jobs"].values()))["env"]

    assert env[key] == other[key], key


@pytest.mark.parametrize("key", ["KIND_SHA256", "KUBECTL_SHA256"])
def test_the_workflow_checks_the_sum_before_the_download_and_fails_closed(
    workflow: dict[str, Any], key: str
) -> None:
    """The sum is 64 hex, guarded fail-closed, checked with `sha256sum -c`, before the curl."""
    env = next(iter(workflow["jobs"].values()))["env"]
    runs = _runs(workflow)

    guard = runs.find(f"[[ ${{{key}}} =~ ^[0-9a-f]{{64}}$ ]] ||")
    check = runs.find(f'"${{{key}}}  ', guard)

    assert SHA256.match(str(env[key])), key
    assert guard >= 0, f"no fail-closed check on {key}"
    assert check > guard and "sha256sum -c" in runs[check : check + 80], key
    assert guard < runs.find("curl", guard), f"{key} is checked after the download"


def test_the_workflow_fetches_no_sum_from_the_release(workflow: dict[str, Any]) -> None:
    """No sum file is fetched from the same release as the binary."""
    runs = _runs(workflow)

    assert ".sha256sum" not in runs and "kubectl.sha256" not in runs


def test_the_workflow_downloads_nothing_unpinned(workflow: dict[str, Any]) -> None:
    """Only kind and kubectl are fetched, each verified; no `pip install`, no `curl | sh`."""
    runs = _runs(workflow)

    downloads = re.findall(r"curl [^\n]*", runs)

    assert "pip install" not in runs
    assert not re.search(r"\|\s*(sh|bash)\b", runs), "a download piped into a shell"
    assert len(downloads) == 2 and all("-fsSLo" in d for d in downloads), downloads
    assert runs.count("sha256sum -c") == 2


def test_the_workflow_steps_are_up_test_logs_delete(workflow: dict[str, Any]) -> None:
    """Up before test, logs on failure, delete always and last, and no bare kubectl logs."""
    steps = _steps(workflow)
    runs = [str(s.get("run", "")) for s in steps]

    up = next(i for i, r in enumerate(runs) if "deploy/kind/poc06/run.sh up" in r)
    test = next(i for i, r in enumerate(runs) if "deploy/kind/poc06/run.sh test" in r)
    logs = [s for s in steps if "run.sh logs" in str(s.get("run", ""))]
    deletes = [s for s in steps if "run.sh delete" in str(s.get("run", ""))]

    assert up < test
    assert logs and all(str(s.get("if", "")).strip() == "failure()" for s in logs)
    assert deletes and all(str(s.get("if", "")).strip() == "always()" for s in deletes)
    assert steps.index(deletes[-1]) == len(steps) - 1
    assert not re.search(r"\b(kubectl|kctl)\b.*\slogs\s", "\n".join(runs)), "a bare kubectl logs"


def test_the_workflow_text_uses_no_secret_and_leaves_remote_lane_alone() -> None:
    """The workflow's text names no secret or GITHUB_TOKEN, its head says why it never skips,
    and remote-lane.yml is still there."""
    text = WORKFLOW.read_text()

    head = text.split("\nname:", 1)[0]

    assert "secrets." not in text and "GITHUB_TOKEN" not in text
    assert "never skips" in head and "make check" in head
    assert (ROOT / ".github/workflows/remote-lane.yml").is_file()


NOTE_EXCEPTIONS = (
    "No Python in the Node image",
    "Claude lookup",
    "source.commit",
    "cannot see tool calls",
    "not a DNS name",
    "trustedRepositories",
)


@pytest.mark.parametrize("needle", NOTE_EXCEPTIONS)
def test_the_note_says_each_exception(needle: str) -> None:
    """The exceptions are on record, not only in code: a Node image with no Python, the Claude
    lookup, kagent-adk's missing digest and plain-mode limit, and the DNS-name deviation."""
    note = (TESTS.parent / "notes/2026-10-09-lanes-b-kind.md").read_text()

    assert needle in note, needle


# --- the Localhost profile is for the four gVisor pods only -----------------------------------

PROFILE_NAME = "poc06-runsc-clone3.json"
PROFILE_PODS = {
    "remote-typescript",
    "remote-smolagents",
    "remote-claude-agent",
    "remote-kagent-adk",
}


def _pod_docs_under_deploy() -> Iterator[tuple[Path, Doc]]:
    for path in sorted((ROOT / "deploy").rglob("*.y*ml")):
        for doc in _load_all(path):
            if isinstance(doc, dict):
                yield path, doc


def _where(path: Path, doc: Doc) -> str:
    return f"{path.relative_to(ROOT)}: {doc.get('kind')}/{doc.get('metadata', {}).get('name')}"


PROFILE_USERS = [(p, d) for p, d in _pod_docs_under_deploy() if PROFILE_NAME in json.dumps(d)]


@pytest.mark.parametrize(
    ("path", "doc"), PROFILE_USERS, ids=[_where(p, d) for p, d in PROFILE_USERS]
)
def test_only_the_four_gvisor_pods_reference_the_profile(path: Path, doc: Doc) -> None:
    """Scan every manifest under `deploy/` (not only poc06): each object that names the profile
    is one of the four PoC-6 gVisor remotes (no runc pod, no PoC-5 pod)."""
    where = _where(path, doc)

    assert "deploy/kind/poc06/" in path.as_posix(), where
    assert doc.get("kind") in POD_KINDS, where
    assert str(doc["metadata"]["name"]) in PROFILE_PODS, where
    template = doc["spec"]["podTemplate"] if doc["kind"] == "Sandbox" else doc["spec"]["template"]
    assert template["spec"].get("runtimeClassName") == "gvisor", where


def test_each_of_the_four_gvisor_pods_references_the_profile() -> None:
    """The four PoC-6 gVisor remotes all name the profile."""
    users = {str(d["metadata"]["name"]) for _, d in PROFILE_USERS}

    assert users == PROFILE_PODS, users


def _function(text: str, name: str) -> str:
    return text[text.index(f"{name}() {{") :].split("\n}\n")[0]


def test_the_hosted_sh_text_undoes_up_unless_it_finishes() -> None:
    """R6-27: `up` writes the provider Secret and the egress policy. From before the first write
    until its last line, an EXIT trap runs `down`; `up` clears it when it finishes."""
    text = (POC06 / "hosted.sh").read_text()
    arm, disarm = "trap up_undo EXIT", "trap stop_forward EXIT"

    up = _function(text, "up")
    undo = _function(text, "up_undo")

    assert arm in up and disarm in up
    assert up.index(arm) < up.index("put_secret") < up.index("network-policy.yaml")
    assert up.index("up done") < up.rindex(disarm)
    assert "trap - EXIT" in undo and "stop_forward" in undo
    assert re.search(r"^\s*\( down \) \|\| log", undo, re.M), "down runs, and its failure is logged"


def test_the_hosted_sh_text_scrubs_the_exact_provider_key() -> None:
    """R6-27: `scrub` removes the provider key's exact value, as poc06_mac.sh `redact` does, not
    only the `sk-`, `Bearer`, and 64-hex shapes. `up` keeps `key` until it finishes, so every
    scrub it calls sees it."""
    text = (POC06 / "hosted.sh").read_text()

    scrub = _function(text, "scrub")
    up = _function(text, "up")
    after_put = up[up.index("put_secret") :]

    assert '${text//"$key"/[redacted]}' in scrub
    assert after_put.count("unset key") == 1, "key stays set while up can still scrub"
    assert after_put.index("up done") < after_put.index("unset key")


@pytest.mark.parametrize(
    "revert",
    [
        "delete networkpolicy litellm-hosted-egress",
        '( unpatch_litellm "$orig" )',
        'delete secret "$PROVIDER_SECRET"',
    ],
)
def test_the_hosted_sh_text_runs_each_security_revert_in_down_when_one_before_fails(
    revert: str,
) -> None:
    """A failed revert in `down` is logged and marked, so the next revert still runs."""
    down = _function((POC06 / "hosted.sh").read_text(), "down")

    after = down[down.index(revert) :].split("\n", 2)

    assert after[0].endswith("||"), after[0]
    assert re.fullmatch(r'\s*\{ log "error: [^"]+"; reverted=0; \}', after[1]), after[1]


def test_the_hosted_sh_text_fails_down_when_a_security_revert_failed() -> None:
    """`down` returns 1 before "down done" when any security revert failed."""
    down = _function((POC06 / "hosted.sh").read_text(), "down")

    tail = down[down.index("if ((reverted == 0)); then") :]

    assert "reverted=1" in down.split("\n")[1]
    assert tail.index("return 1") < tail.index('log "down done"')


def test_the_run_sh_text_refuses_an_unpinned_kagent_base_before_the_build() -> None:
    """`build_kagent` stops on any kagent `FROM` base other than the two it pins and `base`."""
    run = RUN_SH.read_text()

    check = _function(run, "check_kagent_bases")
    build = _function(run, "build_kagent")

    assert "'ghcr.io/astral-sh/uv:${UV_VERSION}' | debian:bookworm-slim | base) ;;" in check
    assert "*) die \"kagent: unpinned base image '$image'" in check
    assert 'die "kagent: no FROM line' in check
    assert build.index("check_kagent_bases") < build.index("docker build")


def test_the_run_sh_text_pins_every_kagent_base_image_by_digest() -> None:
    """R6-28: kagent's python/Dockerfile names `ghcr.io/astral-sh/uv:${UV_VERSION}` and
    `debian:bookworm-slim` by tag. `build_kagent` overrides both with a multi-arch index digest,
    without editing the Dockerfile: a `UV_VERSION` build arg and a BuildKit named context."""
    run = RUN_SH.read_text()
    digest = r"@sha256:[0-9a-f]{64}"

    uv = re.search(r"^KAGENT_UV_VERSION=(\S+)$", run, re.M)
    debian = re.search(r"^KAGENT_DEBIAN=(\S+)$", run, re.M)
    body = _function(run, "build_kagent")

    assert uv and re.fullmatch(rf"0\.12\.3{digest}", uv.group(1))
    assert debian and re.fullmatch(rf"debian:bookworm-slim{digest}", debian.group(1))
    assert "DOCKER_BUILDKIT=1 docker build" in body
    assert '--build-arg "UV_VERSION=$KAGENT_UV_VERSION"' in body
    assert '--build-context "debian:bookworm-slim=docker-image://$KAGENT_DEBIAN"' in body
    assert "not pin its base images" not in run


TAGGED = [
    (p, c)
    for p, c in CONTAINERS
    if c["image"].startswith("kind.local/agent-platform/echo-") or "kagent" in c["image"]
]


def test_poc06_local_images_carry_the_kind_tag() -> None:
    """R6-29: one tag for the PoC-6 images, set once in run.sh; no file names a PoC number tag."""
    files = [RUN_SH, *sorted(POC06.rglob("*.yaml"))]

    hits = [f"{f.relative_to(ROOT)}" for f in files if ":poc06" in f.read_text()]

    assert re.search(r"^TAG=kind$", RUN_SH.read_text(), re.M)
    assert not hits, hits


@pytest.mark.parametrize(("pod", "c"), TAGGED, ids=_ids(TAGGED))
def test_a_poc06_image_ends_in_the_kind_or_poc05_tag(pod: Pod, c: Doc) -> None:
    """Each echo or kagent image is tagged `:kind` (or PoC-5's `:poc05`)."""
    image = c["image"]

    assert image.endswith((":kind", ":poc05")), image
