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
SENTINEL = "__KAGENT_ADK_SHA256__"

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


# --- the table --------------------------------------------------------------------------------


def test_every_pod_is_in_the_table_and_the_table_is_all_there_is() -> None:
    """A pod added without a row in `hardening_expect.yaml` fails here, and so does a row with no
    pod."""
    found = {p.id: (p.namespace, p.obj.kind) for p in PODS}
    wanted = {n: (e["namespace"], e["kind"]) for n, e in EXPECT["pods"].items()}
    assert found == wanted
    for pod in PODS:
        names = {c["name"] for c in pod.containers()}
        assert names == set(EXPECT["pods"][pod.id]["containers"]), pod.id


def test_nothing_but_pods_services_policies_accounts_and_configs() -> None:
    """No Secret (the seed makes them), no role or binding, no SandboxTemplate or SandboxClaim
    (so the extension rules T1 to T5 and C1 to C5 have nothing to read)."""
    allowed = {"Deployment", "Sandbox", "Service", "ServiceAccount", "NetworkPolicy", "ConfigMap"}
    assert {o.kind for o in ALL_OBJECTS} <= allowed
    for path in sorted(POC06.rglob("*.yaml")):
        for doc in _load_all(path):
            assert doc.get("kind") != "Secret", f"{path}: a Secret in git"


@pytest.mark.parametrize("pod", PODS, ids=PIDS)
def test_pod_has_no_token_and_no_host_access(pod: Pod) -> None:
    spec = pod.spec
    assert spec.get("automountServiceAccountToken") is False, pod.id
    assert spec.get("enableServiceLinks") is False, pod.id
    for field in ("shareProcessNamespace", "hostNetwork", "hostPID", "hostIPC"):
        assert not spec.get(field), f"{pod.id}: {field}"
    for v in spec.get("volumes", []):
        assert "hostPath" not in v, f"{pod.id}: hostPath volume {v['name']}"
        assert "projected" not in v, f"{pod.id}: projected volume {v['name']}"
        assert "secret" not in v, f"{pod.id}: secret volume {v['name']}"


@pytest.mark.parametrize("pod", PODS, ids=PIDS)
def test_pod_has_its_own_service_account_without_a_token(pod: Pod) -> None:
    sa_name = pod.spec.get("serviceAccountName")
    assert sa_name, f"{pod.id}: no serviceAccountName"
    accounts = [
        o.doc
        for o in ALL_OBJECTS
        if o.kind == "ServiceAccount" and o.name == sa_name and o.namespace == pod.namespace
    ]
    assert len(accounts) == 1, f"{pod.id}: ServiceAccount {sa_name}"
    assert accounts[0].get("automountServiceAccountToken") is False, pod.id


@pytest.mark.parametrize("pod", PODS, ids=PIDS)
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
        assert c.get("resources", {}).get("requests"), f"{where}: no requests"


@pytest.mark.parametrize("pod", PODS, ids=PIDS)
def test_every_emptydir_is_size_capped_and_memory_backed(pod: Pod) -> None:
    for name, v in _volumes(pod).items():
        if "emptyDir" in v:
            assert (v["emptyDir"] or {}).get("sizeLimit"), f"{pod.id}: emptyDir {name} uncapped"
            assert v["emptyDir"].get("medium") == "Memory", f"{pod.id}: {name} on the node disk"


@pytest.mark.parametrize("pod", PODS, ids=PIDS)
def test_containers_match_the_table(pod: Pod) -> None:
    row = EXPECT["pods"][pod.id]
    for c in pod.containers():
        where = f"{pod.id}/{c['name']}"
        want = row["containers"][c["name"]]
        sc = c["securityContext"]
        assert (sc.get("runAsUser"), sc.get("runAsGroup")) == (want["uid"], want["uid"]), where
        assert c["resources"]["limits"] == {"cpu": want["cpu"], "memory": want["memory"]}, where
        tmp = _tmp_volume(pod, c)
        assert tmp is not None, f"{where}: no /tmp mount"
        assert tmp["emptyDir"]["sizeLimit"] == want["tmp"], where
    assert pod.spec.get("runtimeClassName", "runc") == row["runtime"], pod.id
    uids = [c["securityContext"]["runAsUser"] for c in pod.containers()]
    assert len(uids) == len(set(uids)), f"{pod.id}: uids {uids}"
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


@pytest.mark.parametrize("pod", PODS, ids=PIDS)
def test_secrets_reach_the_chassis_container_only(pod: Pod) -> None:
    """A workload container never gets a Secret; a remote workload gets only its own token (and
    kagent-adk none: its inbound bearer is its model key)."""
    remote = pod.labels.get(LANE) == "remote" and not any(_is_chassis(c) for c in pod.containers())
    for c in pod.containers():
        refs = set(_secret_refs(c))
        if _is_chassis(c):
            continue
        if remote:
            want = EXPECT["remotes"][pod.id]["secret"]
            assert refs == ({want} if want else set()), f"{pod.id}/{c['name']}: {sorted(refs)}"
        else:
            assert not refs, f"{pod.id}/{c['name']}: {sorted(refs)}"


@pytest.mark.parametrize("pod", PODS, ids=PIDS)
def test_a_remote_takes_its_own_token_only(pod: Pod) -> None:
    if pod.obj.kind != "Sandbox":
        pytest.skip("not a remote")
    assert pod.labels[NAME] == pod.id
    refs = {r for c in pod.containers() for r in _secret_refs(c)}
    for ref in refs:
        match = TOKEN_SECRET.fullmatch(ref)
        assert match and ref == f"{pod.id}-token", (pod.id, ref)
    for env in (e for c in pod.containers() for e in c.get("env", [])):
        assert not env["name"].endswith(("_API_KEY", "_SECRET", "_PASSWORD")), env["name"]


def test_the_claude_pod_meets_the_cli_env_guard_and_has_a_scratch_volume() -> None:
    """The workload refuses to start a run when an ANTHROPIC_* or CLAUDE_* name is set that it
    does not override (`OVERRIDDEN` in its handle.py): the pod sets only CLAUDE_AGENT_HOME_BASE,
    which it allows, and that points at the /tmp emptyDir (the CLI writes HOME and the work dir)."""
    c = _pod("remote-claude-agent").containers()[0]
    env = _env(c)
    guarded = [n for n in env if n.startswith(("ANTHROPIC_", "CLAUDE_"))]
    assert guarded == ["CLAUDE_AGENT_HOME_BASE"], guarded
    assert env["CLAUDE_AGENT_HOME_BASE"]["value"] == "/tmp"
    tmp = _tmp_volume(_pod("remote-claude-agent"), c)
    assert tmp is not None and "emptyDir" in tmp and tmp["emptyDir"]["sizeLimit"] == "128Mi"
    handle = (
        ROOT / "packages/workloads/echo-claude-agent/src/echo_claude_agent/handle.py"
    ).read_text()
    assert '"CLAUDE_AGENT_HOME_BASE"' in handle or "HOME_BASE_VAR" in handle


# --- the chassis ------------------------------------------------------------------------------

CHASSIS = [(p, c) for p in PODS for c in p.containers() if _is_chassis(c)]
CHASSIS_IDS = [f"{p.id}/{c['name']}" for p, c in CHASSIS]


@pytest.mark.parametrize(("pod", "chassis"), CHASSIS, ids=CHASSIS_IDS)
def test_chassis_public_port_binds_the_pod_ip(pod: Pod, chassis: Doc) -> None:
    args = [str(a) for a in chassis.get("args", [])]
    assert _flag(args, "--host") == "$(POD_IP)", pod.id
    assert _flag(args, "--remote-proxy-host") in {None, "$(POD_IP)"}, pod.id
    for bad in ("0.0.0.0", "127.0.0.1", "localhost", "::", "::1"):
        assert bad not in args, f"{pod.id}: {bad} in args"
    assert _env(chassis)["POD_IP"]["valueFrom"]["fieldRef"]["fieldPath"] == "status.podIP"
    assert chassis["startupProbe"]["httpGet"]["path"] == "/health", pod.id
    assert chassis["livenessProbe"]["httpGet"]["path"] == "/health", pod.id
    assert chassis["readinessProbe"]["httpGet"]["path"] == "/ready", pod.id
    assert not chassis.get("command"), "rule 6b: the image's entrypoint is `chassis serve`"


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


@pytest.mark.parametrize(("pod", "chassis"), CHASSIS, ids=CHASSIS_IDS)
def test_chassis_config_loads_and_matches_the_pod(pod: Pod, chassis: Doc) -> None:
    config = _chassis_config(pod, chassis)
    assert config.spec.trust == pod.labels[TRUST], pod.id
    assert config.spec.engine.connector == pod.labels[LANE], pod.id
    assert config.spec.model.route == "fake-chat", pod.id
    env = _env(chassis)
    args = [str(a) for a in chassis.get("args", [])]
    litellm = env["LITELLM_API_KEY"]["valueFrom"]["secretKeyRef"]
    assert litellm["name"].startswith("chassis-") and litellm["name"].endswith("-litellm")
    assert env["VALKEY_PASSWORD"]["valueFrom"]["secretKeyRef"]["name"] == "valkey-auth"
    if config.spec.engine.connector == "remote":
        assert _flag(args, "--remote-proxy-host") == "$(POD_IP)", pod.id
        auth = config.spec.engine.auth
        assert auth is not None, pod.id
        ref = env[auth.token_env]["valueFrom"]["secretKeyRef"]
        assert TOKEN_SECRET.fullmatch(ref["name"]), pod.id
        remote = pod.id.removeprefix("chassis-").removesuffix("-remote")
        assert ref["name"] == f"remote-{remote}-token", pod.id
        assert (
            config.spec.engine.url == f"http://remote-{remote}.poc05-remote.svc.cluster.local:9000"
        )
        plain = remote == "kagent-adk"
        assert (config.spec.engine.protocol == "a2a") is plain, pod.id
        if plain:
            assert config.spec.engine.a2a is not None
            assert config.spec.engine.a2a.usage_key == "kagent.dev/a2a/usage"
            assert config.spec.engine.a2a.context_id == "omit"
    else:
        assert _flag(args, "--remote-proxy-host") is None, pod.id
        assert config.spec.engine.url == "http://127.0.0.1:9000"


def test_the_sidecar_chassis_starts_before_its_workload() -> None:
    for pod in PODS:
        if pod.labels.get(LANE) != "sidecar":
            continue
        init = pod.spec.get("initContainers", [])
        assert [c["name"] for c in init if _is_chassis(c)] == ["chassis"], pod.id
        assert init[0]["restartPolicy"] == "Always", pod.id
        workload = pod.spec["containers"][0]
        assert set(_env(workload)) == {"HOST", "PORT", "CHASSIS_MODEL_URL", "CHASSIS_TOOL_URL"}


# --- the remote lane --------------------------------------------------------------------------

REMOTES = sorted(EXPECT["remotes"])


@pytest.mark.parametrize("name", REMOTES)
def test_every_remote_is_a_sandbox_on_gvisor_with_tcp_probes(name: str) -> None:
    pod = _pod(name)
    assert pod.doc["apiVersion"] == "agents.x-k8s.io/v1beta1"
    assert pod.spec.get("runtimeClassName") == "gvisor"
    assert pod.doc["spec"]["service"] is False
    assert pod.labels[LANE] == "remote" and pod.labels[TRUST] == "untrusted"
    assert pod.namespace == "poc05-remote"
    assert [c["name"] for c in pod.containers()] == ["workload"]
    c = pod.containers()[0]
    for probe in ("startupProbe", "readinessProbe", "livenessProbe"):
        assert set(c[probe]) & {"httpGet", "exec", "grpc"} == set(), (name, probe)
        assert "tcpSocket" in c[probe], (name, probe)
    if name != "remote-kagent-adk":
        assert "--require-token-env" in [str(a) for a in c["args"]], name
        assert _env(c)["CHASSIS_API_TOKEN"]["valueFrom"]["secretKeyRef"]["key"] == "token"
        assert _env(c)["HOST"]["value"] == "$(POD_IP)"


def _service(folder: str, name: str) -> Doc:
    (found,) = [o.doc for o in _objects(folder) if o.kind == "Service" and o.name == name]
    return found


@pytest.mark.parametrize("name", REMOTES)
def test_the_remote_reaches_its_chassis_at_the_fixed_service_ip(name: str) -> None:
    """The remote has no DNS: its model and tool URLs name the fixed ClusterIP, on 8091. The IPs
    are inside the Service range, distinct from each other and from PoC-5's 10.96.85.91."""
    want = EXPECT["remotes"][name]
    folder = "kagent" if name == "remote-kagent-adk" else "agents"
    svc = _service(folder, f"{want['chassis']}-proxy")
    ip = svc["spec"]["clusterIP"]
    assert ip == want["ip"]
    cluster = yaml.safe_load((POC05 / "cluster.yaml").read_text())
    assert ipaddress.ip_address(ip) in ipaddress.ip_network(cluster["networking"]["serviceSubnet"])
    assert ip != "10.96.85.91"
    assert svc["spec"]["selector"] == {NAME: want["chassis"]}
    assert [p["port"] for p in svc["spec"]["ports"]] == [8091]
    base = f"http://{ip}:8091"
    c = _pod(name).containers()[0]
    if name == "remote-kagent-adk":
        cm = next(o.doc for o in _objects("kagent") if o.kind == "ConfigMap")
        model = json.loads(cm["data"]["config.json"])["model"]
        assert model["base_url"] == f"{base}/v1", "the model goes to the chassis's remote listener"
        assert model["api_key_passthrough"] is True
        assert "api_key" not in model and "api_key_secret" not in model
    else:
        assert _env(c)["CHASSIS_MODEL_URL"]["value"] == f"{base}/v1"
        assert _env(c)["CHASSIS_TOOL_URL"]["value"] == f"{base}/mcp"


def test_the_proxy_addresses_are_unique_across_poc05_and_poc06() -> None:
    ips = [e["ip"] for e in EXPECT["remotes"].values()]
    assert len(ips) == len(set(ips))
    poc05 = (POC05 / "agents/chassis-echo-remote.yaml").read_text()
    assert all(f"clusterIP: {ip}" not in poc05 for ip in ips)


def test_kagent_is_pinned_by_digest_filled_from_one_file() -> None:
    """A third-party image is allowed in the remote lane only, and only pinned. The manifest holds
    a sentinel; `run.sh` fills it from `kagent/image.sha256` and applies nothing while that file
    holds no digest."""
    image = _pod("remote-kagent-adk").containers()[0]["image"]
    assert image == f"ghcr.io/kagent-dev/kagent/kagent-adk@sha256:{SENTINEL}"
    assert _pod("remote-kagent-adk").labels[LANE] == "remote"
    digest = [
        ln for ln in (POC06 / "kagent/image.sha256").read_text().splitlines() if SHA256.match(ln)
    ]
    assert len(digest) <= 1
    run = RUN_SH.read_text()
    assert f"KAGENT_PLACEHOLDER={SENTINEL}" in run and "skipping the remote" in run
    kagent_docs = "\n".join(p.read_text() for p in (POC06 / "kagent").glob("*.yaml"))
    assert kagent_docs.count(SENTINEL) == 3  # the header comment, the image, and the comment on it


# --- admission --------------------------------------------------------------------------------


def _params(path: Path) -> _Params:
    return _Params.of(_load_all(path)[0])


PARAMS5 = POC05 / "admission/params.yaml"
PARAMS6 = POC06 / "admission/params.yaml"


def test_the_trust_params_add_one_repository_and_change_nothing_else() -> None:
    five, six = _load_all(PARAMS5)[0], _load_all(PARAMS6)[0]
    assert six["metadata"] == five["metadata"]
    assert {k: v for k, v in six["data"].items() if k != "trustedRepositories"} == {
        k: v for k, v in five["data"].items() if k != "trustedRepositories"
    }
    old = five["data"]["trustedRepositories"].split(",")
    new = six["data"]["trustedRepositories"].split(",")
    assert new[: len(old)] == old, "PoC-5's repositories stay, in order"
    assert new[len(old) :] == ["kind.local/agent-platform/echo-openai-agents"]


def test_every_workload_image_is_loaded_by_run_sh_or_poc05() -> None:
    poc05 = (POC05 / "run.sh").read_text()
    run = RUN_SH.read_text()
    for pod in PODS:
        for c in pod.containers():
            image = c["image"]
            if image.startswith("ghcr.io/"):
                assert pod.id == "remote-kagent-adk", image
                continue
            assert image.startswith("kind.local/agent-platform/"), image
            assert f'"$REGISTRY/{image.split("/")[-1]}|' in run + poc05, (
                f"{image}: nobody builds it"
            )
            assert c["imagePullPolicy"] == "Never", f"{pod.id}: rule 8"


@pytest.mark.parametrize("pod", PODS, ids=PIDS)
def test_every_admission_rule_admits_every_poc06_object(pod: Pod) -> None:
    """Rules 0 to 8 by PoC-5's Python model of `admission/policy.yaml`, with the PoC-6 params. The
    object is as the deployer applies it; the namespace's pod-shape label is PoC-5's."""
    assert broken_rules(pod.doc, _params(PARAMS6), pod.namespace) == set(), pod.id


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


@pytest.mark.parametrize("pod", PODS, ids=PIDS)
def test_the_rule_model_refuses_a_mutated_copy(pod: Pod) -> None:
    """The control for the test above: the model is not a rubber stamp."""
    params = _params(PARAMS6)
    for rule, why, doc in _mutations(pod):
        assert rule in broken_rules(doc, params, pod.namespace), (pod.id, why)


def test_a_sidecar_pod_with_an_unlisted_workload_image_breaks_rule_4() -> None:
    pod = _pod("agent-openai-agents")
    doc = copy.deepcopy(pod.doc)
    doc["spec"]["template"]["spec"]["containers"][0]["image"] = (
        "kind.local/agent-platform/other:poc06"
    )
    assert "4" in broken_rules(doc, _params(PARAMS6), pod.namespace)
    assert "4" in broken_rules(pod.doc, _params(PARAMS5), pod.namespace), "PoC-5's list lacks it"


def test_namespaces_are_poc05s_with_the_right_shapes() -> None:
    labels = {
        d["metadata"]["name"]: d["metadata"].get("labels", {})
        for d in _load_all(POC05 / "base/namespaces.yaml")
        if d["kind"] == "Namespace"
    }
    for pod in PODS:
        want = "remote" if pod.obj.kind == "Sandbox" else "chassis"
        assert labels[pod.namespace]["agents.platform/pod-shape"] == want, pod.id
        assert labels[pod.namespace]["agents.platform/admission"] == "enforce", pod.id


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
    diff = []
    for name, got, want in (("egress", got_e, want_e), ("ingress", got_i, want_i)):
        diff += [f"extra {name} (manifests only): {e}" for e in sorted(got - want)]
        diff += [f"missing {name} (fixture only): {e}" for e in sorted(want - got)]
    assert not diff, "\n".join(diff)
    assert got_e and got_i


def test_policies_are_label_based_and_deny_the_rest() -> None:
    """No `ipBlock` (so the metadata service and the API server are not named), no egress rule
    without a destination, no peer that selects every pod, and a policy for every pod, both ways.
    PoC-5's `default-deny` in each namespace refuses everything these do not allow."""
    for pol in POLICIES:
        spec = pol.doc["spec"]
        assert set(spec["policyTypes"]) == {"Ingress", "Egress"}, pol.name
        assert "ipBlock" not in yaml.safe_dump(spec), pol.name
        for rule in spec.get("egress", []):
            assert rule.get("to"), f"{pol.name}: an egress rule with no destination"
            for peer in rule["to"]:
                assert peer.get("podSelector", {}).get("matchLabels"), f"{pol.name}: all pods"
        for rule in spec.get("ingress", []):
            if "from" not in rule:
                assert [p["port"] for p in rule["ports"]] == [8080], pol.name
    covered = {pol.doc["spec"]["podSelector"]["matchLabels"][NAME] for pol in POLICIES}
    assert covered == {p.id for p in PODS}
    default_deny = [
        d["metadata"]["namespace"]
        for d in _load_all(POC05 / "base/default-deny.yaml")
        if d["metadata"]["name"] == "default-deny"
    ]
    assert {"poc05-agents", "poc05-remote"} <= set(default_deny)


def test_a_remote_has_no_dns_and_only_its_chassis_in_and_out() -> None:
    for pol in POLICIES:
        if pol.namespace != "poc05-remote":
            continue
        spec = pol.doc["spec"]
        ports = [p["port"] for rule in spec["egress"] for p in rule["ports"]]
        assert ports == [8091], pol.name
        peers = {
            peer["podSelector"]["matchLabels"][NAME]
            for rule in [*spec["egress"], *spec["ingress"]]
            for peer in rule.get("to", rule.get("from", []))
        }
        assert len(peers) == 1 and next(iter(peers)).startswith("chassis-"), pol.name


def test_the_chassis_pods_carry_the_role_litellm_and_valkey_admit() -> None:
    """PoC-5's `litellm` and `valkey` policies admit `poc05-agents` pods with `role: chassis`. A
    PoC-6 chassis pod without it would get no answer; a pod with it that is not a chassis would."""
    for pod in PODS:
        has_chassis = any(_is_chassis(c) for c in pod.containers())
        assert (pod.labels.get("agents.platform/role") == "chassis") is has_chassis, pod.id


# --- the seed, run.sh, the script -------------------------------------------------------------


def _array(text: str, name: str) -> list[str]:
    match = re.search(rf"(?ms)^{name}=\((.*?)\)$", text)
    assert match, f"no {name} array"
    return [w for w in re.sub(r"#.*", "", match.group(1)).split() if w]


def test_the_seed_makes_every_secret_the_manifests_name() -> None:
    seed = SEED_SH.read_text()
    services, remotes = _array(seed, "SERVICES"), _array(seed, "REMOTES")
    chassis_only = _array(seed, "CHASSIS_ONLY_REMOTES")
    made = {f"chassis-{s}-litellm" for s in services}
    made |= {f"remote-{r}-token" for r in [*remotes, *chassis_only]}
    named: set[str] = set()
    for pod in PODS:
        for c in pod.containers():
            named.update(r for r in _secret_refs(c) if r != "valkey-auth")
    assert named == made
    assert {f"remote-{r}-token" for r in remotes} == {
        e["secret"] for e in EXPECT["remotes"].values() if e["secret"]
    }


def test_the_seed_is_strict_pins_the_context_and_prints_no_credential() -> None:
    text = SEED_SH.read_text()
    code = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    body = "\n".join(code)
    assert "set -euo pipefail" in body and "CONTEXT=kind-poc05" in body
    assert not re.search(r"\bset\s+-\w*x", body), "set -x would print a credential"
    kubectl = [ln for ln in code if re.search(r"(?<![\w-])kubectl\s", ln)]
    allowed = ('kctl() { kubectl --context "$CONTEXT" "$@"; }', "for tool in kubectl")
    assert all(any(a in ln for a in allowed) for ln in kubectl), kubectl
    assert "openssl rand -hex" in body
    for ln in code:
        if re.search(r"\$\{?(value|key|master|resp)\b", ln):
            ok = (
                ln.lstrip().startswith(("printf", "unset", "[[", "die", "if !", "local"))
                or "| curl" in ln
                or "| jq" in ln
                or "| put_secret" in ln
            )
            assert ok, f"a credential variable outside a pipe: {ln.strip()}"
    for ln in code:
        if ln.lstrip().startswith("printf") and re.search(r"\$\{?(value|key|master)\b", ln):
            assert "|" in ln, f"printf of a credential not into a pipe: {ln.strip()}"
        assert not re.search(r"\becho\b.*\$\{?(value|key|master)\b", ln), ln
    assert 'object_permission: {mcp_servers: ["fake_tools"]' in text
    assert 'TOOLS="glossary_lookup,acronym_expand"' in text
    assert '"openai_routes", "mcp_inference_routes"' in text


def test_run_sh_reuses_poc05_and_pins_the_context() -> None:
    text = RUN_SH.read_text()
    assert "set -euo pipefail" in text
    assert re.search(r"^CLUSTER=poc05$", text, re.M) and "CONTEXT=kind-$CLUSTER" in text
    for verb in ("up", "build", "load", "seed", "apply", "test", "pods", "logs", "redact"):
        assert re.search(rf"^\s*{verb}\) ", text, re.M), verb
    assert re.search(r"^\s*delete \| down\) ", text, re.M)
    assert re.search(r'^\s*logs\) "\$P5" logs ;;', text, re.M)
    assert re.search(r'^\s*redact\) "\$P5" redact ;;', text, re.M)
    assert '"$P5" up' in text and '"$P5" delete' in text
    code = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    kubectl = [ln for ln in code if re.search(r"(?<![\w-])kubectl\s", ln)]
    assert kubectl == ['kctl() { kubectl --context "$CONTEXT" "$@"; }'], kubectl
    logs = [ln for ln in code if re.search(r"\bkctl\b.*\slogs\s", ln)]
    assert logs and all(re.search(r'\|\s*"\$P5" redact', ln) for ln in logs), logs
    assert (
        '--as="$DEPLOYER"' in text
        and "DEPLOYER=system:serviceaccount:agent-platform-system:deployer" in text
    )


def test_run_sh_test_selects_the_kind_files_by_path_and_names_missing_ones() -> None:
    text = RUN_SH.read_text()
    listed = _array(text, "KIND_TESTS")
    assert listed == [
        "pocs/poc-06b-bake-off-remote-lane/tests/test_poc06b_kind_engines.py",
        "pocs/poc-06b-bake-off-remote-lane/tests/test_poc06b_kind_remote_controls.py",
        "pocs/poc-06b-bake-off-remote-lane/tests/test_poc06b_kind_sidecar_controls.py",
    ]
    missing = [f for f in listed if not (ROOT / f).is_file()]
    assert not missing, missing
    assert "POC06_KIND=1" in text and '"${KIND_TESTS[@]}"' in text and "-m kind" in text
    run_tests = text[text.index("run_tests() {") :].split("\n}\n")[0]
    assert " -k " not in run_tests, "select by file path, not by -k"


def test_run_sh_edits_no_poc05_file() -> None:
    """It calls PoC-5's run.sh and patches live objects; it never writes under deploy/kind/poc05."""
    text = RUN_SH.read_text() + SEED_SH.read_text()
    code = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    assert not [ln for ln in code if "poc05/" in ln and re.search(r">|sed -i|tee", ln)]


def _pick(script: Script, messages: list[dict[str, Any]]) -> Any:
    return script.pick(messages)


SCRIPT = Script.from_yaml(POC06 / "platform/fake-model-script.yaml")


@pytest.mark.parametrize("marker", ["", " [text]", " [code]"])
def test_the_fake_model_script_answers_each_task_for_each_engine_form(marker: str) -> None:
    """The script is first-match-wins, so each task's text must reach the rule meant for it. Plain
    forms (no marker): OpenAI chat with tool calls by the gateway's names. ` [text]`: kagent-adk,
    one reply. ` [code]`: smolagents, Python in a `<code>` block, observations as user messages."""
    smoke = SCRIPT.pick([{"role": "user", "content": SMOKE.text + marker}])
    simple = SCRIPT.pick([{"role": "user", "content": SIMPLIFIER.text + marker}])
    lookup = SCRIPT.pick([{"role": "user", "content": LOOKUP.text + marker}])
    if marker == " [code]":
        assert 'final_answer("Hello.")' in smoke.reply
        assert all(f in simple.reply for f in ("Acme", "2026", "30 percent"))
        assert 'fake_tools_glossary_lookup(term="SLM")' in lookup.reply
        glossary = {
            "role": "user",
            "content": "Execution logs: {'definition': 'a small language model'}",
        }
        assert 'fake_tools_acronym_expand(acronym="RAG")' in SCRIPT.pick([glossary]).reply
        acronym = {"role": "user", "content": "Execution logs: retrieval-augmented generation"}
        assert "final_answer" in SCRIPT.pick([acronym]).reply
        return
    assert smoke.reply == "Hello." and smoke.tool_call is None
    assert all(f in simple.reply for f in ("SLM", "Acme", "2026", "30 percent"))
    if marker == " [text]":
        assert lookup.tool_call is None
        assert "small language model" in lookup.reply
        assert "retrieval-augmented generation" in lookup.reply
        return
    assert lookup.tool_call is not None
    assert (lookup.tool_call.name, lookup.tool_call.arguments) == (
        "fake_tools-glossary_lookup",
        {"term": "SLM"},
    )
    task = {"role": "user", "content": LOOKUP.text}
    first = SCRIPT.pick([task, {"role": "tool", "content": "SLM is a small language model"}])
    assert first.tool_call is not None
    assert (first.tool_call.name, first.tool_call.arguments) == (
        "fake_tools-acronym_expand",
        {"acronym": "RAG"},
    )
    last = SCRIPT.pick([task, {"role": "tool", "content": "RAG: retrieval-augmented generation"}])
    assert last.tool_call is None and "retrieval-augmented generation" in last.reply


def test_the_fake_model_patch_points_the_deployment_at_the_script() -> None:
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
    assert _triggers(workflow) == {"push", "pull_request", "workflow_dispatch"}
    code = [ln for ln in WORKFLOW.read_text().splitlines() if not ln.lstrip().startswith("#")]
    assert not any("pull_request_target" in ln for ln in code)


def test_the_workflow_job_shape(workflow: dict[str, Any]) -> None:
    jobs = workflow["jobs"]
    assert len(jobs) == 1
    job = next(iter(jobs.values()))
    assert job["runs-on"] == "ubuntu-latest"
    assert isinstance(job.get("timeout-minutes"), int) and 0 < job["timeout-minutes"] <= 45
    assert workflow.get("permissions", job.get("permissions")) == {"contents": "read"}


def test_the_workflow_pins_every_action_by_sha_the_same_as_ci(workflow: dict[str, Any]) -> None:
    uses = [s["uses"] for s in _steps(workflow) if "uses" in s]
    assert uses, "expected checkout and setup-uv"
    ci_pins = {s["uses"].split("@")[0]: s["uses"] for s in _steps(_workflow(CI)) if "uses" in s}
    for ref in uses:
        assert PINNED.match(ref), f"not pinned by a 40-hex SHA: {ref}"
        assert ref == ci_pins[ref.split("@")[0]], f"{ref} pinned differently from ci.yml"
    for line in WORKFLOW.read_text().splitlines():
        if "uses:" in line and not line.lstrip().startswith("#"):
            assert "# v" in line, f"no version comment: {line.strip()}"


def test_the_workflow_pins_kind_and_kubectl_like_remote_lane(workflow: dict[str, Any]) -> None:
    """Version and sha256 in the job env, the same values as remote-lane.yml, checked before the
    download and failing closed; no sum fetched from the same release."""
    env = next(iter(workflow["jobs"].values()))["env"]
    other = next(iter(_workflow(REMOTE_LANE)["jobs"].values()))["env"]
    runs = _runs(workflow)
    for key in ("KIND_VERSION", "KUBECTL_VERSION", "KIND_SHA256", "KUBECTL_SHA256"):
        assert env[key] == other[key], key
    for key in ("KIND_SHA256", "KUBECTL_SHA256"):
        assert SHA256.match(str(env[key])), key
        guard = runs.find(f"[[ ${{{key}}} =~ ^[0-9a-f]{{64}}$ ]] ||")
        assert guard >= 0, f"no fail-closed check on {key}"
        check = runs.find(f'"${{{key}}}  ', guard)
        assert check > guard and "sha256sum -c" in runs[check : check + 80], key
        assert guard < runs.find("curl", guard), f"{key} is checked after the download"
    assert ".sha256sum" not in runs and "kubectl.sha256" not in runs


def test_the_workflow_downloads_nothing_unpinned(workflow: dict[str, Any]) -> None:
    """Only kind and kubectl are fetched, each verified; no `pip install`, no `curl | sh`."""
    runs = _runs(workflow)
    assert "pip install" not in runs
    assert not re.search(r"\|\s*(sh|bash)\b", runs), "a download piped into a shell"
    downloads = re.findall(r"curl [^\n]*", runs)
    assert len(downloads) == 2 and all("-fsSLo" in d for d in downloads), downloads
    assert runs.count("sha256sum -c") == 2


def test_the_workflow_steps_are_up_test_logs_delete(workflow: dict[str, Any]) -> None:
    steps = _steps(workflow)
    runs = [str(s.get("run", "")) for s in steps]
    up = next(i for i, r in enumerate(runs) if "deploy/kind/poc06/run.sh up" in r)
    test = next(i for i, r in enumerate(runs) if "deploy/kind/poc06/run.sh test" in r)
    assert up < test
    logs = [s for s in steps if "run.sh logs" in str(s.get("run", ""))]
    assert logs and all(str(s.get("if", "")).strip() == "failure()" for s in logs)
    deletes = [s for s in steps if "run.sh delete" in str(s.get("run", ""))]
    assert deletes and all(str(s.get("if", "")).strip() == "always()" for s in deletes)
    assert steps.index(deletes[-1]) == len(steps) - 1
    assert not re.search(r"\b(kubectl|kctl)\b.*\slogs\s", "\n".join(runs)), "a bare kubectl logs"


def test_the_workflow_uses_no_secret_and_leaves_remote_lane_alone() -> None:
    text = WORKFLOW.read_text()
    assert "secrets." not in text and "GITHUB_TOKEN" not in text
    head = text.split("\nname:", 1)[0]
    assert "never skips" in head and "make check" in head
    assert (ROOT / ".github/workflows/remote-lane.yml").is_file()


def test_the_record_names_each_exception() -> None:
    """The exceptions are on record, not only in code: a Node image with no Python, the Claude
    lookup, kagent-adk's missing digest and plain-mode limit, and the DNS-name deviation."""
    note = (TESTS.parent / "notes/2026-10-09-lanes-b-kind.md").read_text()
    for needle in (
        "No Python in the Node image",
        "Claude lookup",
        "image.sha256",
        "cannot see tool calls",
        "not a DNS name",
        "trustedRepositories",
    ):
        assert needle in note, needle
