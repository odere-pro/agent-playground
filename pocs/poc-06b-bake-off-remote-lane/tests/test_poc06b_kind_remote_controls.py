"""PoC-6b on kind, the remote lane: the PoC-5 remote controls (`test_poc05_kind_remote_controls.py`,
plan sections 2.2, 2.3, 2.10, 2.11; threat model H17 to H21, H23, H28) for each new remote pod:
`remote-smolagents`, `remote-claude-agent`, `remote-typescript`, and `remote-kagent-adk`.

Exit criteria 1 and 4: the remote holds no credential but its own token (kagent-adk holds none),
reaches nothing but its chassis's listener, has no DNS, runs on a read-only root with a writable
`/tmp`, is held by the pids cap, and runs on gVisor next to a runc pod. Every refusal asserts its
paired allowed control in the same test.

Reuse: the probe, the pod and node helpers, and `secret_sources` come from PoC-5's tests unchanged.
What is new: the probe runs with Python or Node by the image (`bakeoff_kind.probe_in`), and the
listener checks cover `/v1/messages` too (the Claude CLI's path).

Recorded exceptions (notes/2026-10-09-lanes-b-kind.md):
- `remote-typescript` has no Python (the Node image). The probe is `node_probe.js`, the same check
  kinds in Node. It does not cover the PoC-5 `procs` scan (H26 is a sidecar check, not run here).
- `remote-kagent-adk`: its image may have no Python either; then `probe_python` marks the test
  `xfail` with the reason, and the Node fallback does not apply. Its other checks read the live
  pod spec and the node. Its image is built from source (`run.sh build_kagent`) and has the venv
  Python at /.kagent/.venv/bin/python, so the probe is expected to run.

A kind test: marked `KIND_BAKEOFF` (`poc_harness.drills`), so also `network`, and skipped unless
`KIND_DRILLS=1`.
Run: `deploy/kind/poc06/run.sh test`.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from poc_harness.bakeoff_kind import (
    MISSING_PROFILE_POD,
    PYTHONS,
    REMOTES,
    KindEngine,
    container_error_of,
    create_missing_profile_pod,
    delete_missing_profile_pod,
    probe_in,
)
from poc_harness.drills import KIND_BAKEOFF
from poc_harness.kind import (
    AGENTS_NS,
    CALLER_NS,
    CHASSIS,
    GATEWAY_NS,
    NOT_A_KEY,
    PLATFORM_NS,
    POLICY_DROPPED,
    REMOTE_NS,
    WORKLOAD,
    dns_check,
    gateway_caller,
    get_json,
    in_caller,
    kubectl,
    node_sh,
    pod,
    pods,
    probe,
    service_ip,
    tcp_check,
    unpoliced_caller,
)
from poc_harness.kind_controls import secret_sources, traceparent_headers

pytestmark = KIND_BAKEOFF

REPO = Path(__file__).resolve().parents[3]
CLUSTER_YAML = REPO / "deploy" / "kind" / "poc05" / "cluster.yaml"
TOKEN_ENV = "CHASSIS_API_TOKEN"
OUTSIDE_IP = "1.1.1.1"  # a literal public address: no DNS involved
POC05_PROXY_IP = "10.96.0.91"  # the PoC-5 remote's chassis proxy
NAME_LABEL = "app.kubernetes.io/name"
CHASSIS_CREDENTIALS = {"LITELLM_API_KEY", "VALKEY_PASSWORD", "REMOTE_TOKEN"}


FRONT_DOOR = "remote-run-dispatch"
# The front door's port per engine (deploy/kind/poc06/platform/remote-run-dispatch.yaml): each
# chassis may reach its own engine's port only.
DOOR_PORTS = {
    "remote-claude-agent": 9001,
    "remote-smolagents": 9002,
    "remote-typescript": 9003,
    "remote-kagent-adk": 9004,
}
CLAIM_LABEL = "agents.x-k8s.io/claim-uid"


def remote_pod(engine: KindEngine) -> dict[str, Any]:
    """One warm pod of the engine's pool: Running, not being deleted, not claimed by a run (one
    fresh pod per run, core review R6-11). Its spec is the template's."""
    warm = [
        p
        for p in pods(engine.namespace, engine.pod)
        if "deletionTimestamp" not in p["metadata"]
        and CLAIM_LABEL not in p["metadata"].get("labels", {})
    ]
    assert warm, f"{engine.namespace}/{engine.pod}: no warm pod"
    return warm[0]


def in_front_door(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    door = pod(PLATFORM_NS, FRONT_DOOR)
    return probe(PLATFORM_NS, door["metadata"]["name"], FRONT_DOOR, checks)


def chassis_pod(engine: KindEngine) -> dict[str, Any]:
    return pod(AGENTS_NS, engine.chassis)


def in_remote(
    engine: KindEngine, remote: dict[str, Any], checks: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    return probe_in(engine, remote["metadata"]["name"], checks)


def in_chassis(chassis: dict[str, Any], checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return probe(AGENTS_NS, chassis["metadata"]["name"], CHASSIS, checks)


@pytest.fixture(scope="module")
def caller() -> Iterator[str]:
    """A pod outside every PoC-5 namespace, with no egress policy."""
    with unpoliced_caller() as name:
        yield name


def post(url: str, **extra: Any) -> dict[str, Any]:
    body = {"model": "fake-chat", "messages": [{"role": "user", "content": "hello"}]}
    return {"kind": "http", "url": url, "body": body, **extra}


ENGINE_IDS = [e.id for e in REMOTES]


# --- the token --------------------------------------------------------------------------------


@pytest.mark.parametrize("engine", REMOTES, ids=ENGINE_IDS)
def test_remote_holds_one_secret_its_own_token(engine: KindEngine) -> None:
    """Criterion 4: the control refuses every other Secret. The pod spec takes exactly one value
    from a Secret, `CHASSIS_API_TOKEN` from `remote-<name>-token` in the workload container (none
    for kagent-adk); no `envFrom` Secret, no Secret or projected volume, no service account token
    (`automountServiceAccountToken: false`, the token directory absent). Its live env holds no
    chassis credential name. The allowed control: the live env does hold `CHASSIS_API_TOKEN`
    (names only), and the path check sees `/etc/hosts`."""
    remote = remote_pod(engine)
    spec = remote["spec"]
    want = [(WORKLOAD, TOKEN_ENV, engine.token_secret)] if engine.token_secret else []
    assert secret_sources(spec) == want, secret_sources(spec)
    assert spec["automountServiceAccountToken"] is False

    names, sa_dir, hosts = in_remote(
        engine,
        remote,
        [
            {"kind": "env_names"},
            {"kind": "path", "path": "/var/run/secrets/kubernetes.io"},
            {"kind": "path", "path": "/etc/hosts"},
        ],
    )
    if engine.token_secret:
        assert TOKEN_ENV in names["names"], names
    assert not CHASSIS_CREDENTIALS & set(names["names"]), names
    assert not [n for n in names["names"] if n.endswith(("_API_KEY", "_PASSWORD", "_SECRET"))]
    assert sa_dir == {"exists": False}, sa_dir
    assert hosts == {"exists": True}, hosts


@pytest.mark.parametrize("engine", REMOTES, ids=ENGINE_IDS)
def test_listener_answers_only_the_remote_token_inside_a_run(engine: KindEngine) -> None:
    """H17: the control refuses a model call to the remote listener (8091, from the remote pod) with
    no token (401), with a token it did not issue (401), on `/v1/chat/completions` and on
    `/v1/messages`, and refuses the remote's own token outside a run (403 `run_required`; not for
    kagent-adk, which holds no token in its env). The allowed control, a 200 inside a run with
    the same token, is `test_poc06b_kind_engines.py`: a run cannot end `ok` unless the listener
    answered the model calls."""
    remote = remote_pod(engine)
    assert engine.proxy_ip is not None
    trace = traceparent_headers(uuid.uuid4().hex)
    chat_url = f"http://{engine.proxy_ip}:8091/v1/chat/completions"
    messages_url = f"http://{engine.proxy_ip}:8091/v1/messages"
    stranger = {"Authorization": f"Bearer {NOT_A_KEY}"}
    checks = [
        post(chat_url, headers=trace),
        post(chat_url, headers={**trace, **stranger}),
        post(messages_url, headers=trace),
        post(messages_url, headers={**trace, **stranger}),
    ]
    if engine.token_secret:
        checks.append(post(chat_url, headers=trace, auth_env=TOKEN_ENV))
    results = in_remote(engine, remote, checks)
    for result in results[:4]:
        assert result["status"] == 401, result
    assert "remote_unauthenticated" in results[0]["head"], results[0]
    assert "remote_unauthenticated" in results[1]["head"], results[1]
    assert NOT_A_KEY not in results[1]["head"]
    if engine.token_secret:
        assert results[4]["status"] == 403 and "run_required" in results[4]["head"], results[4]


# --- who may reach whom -----------------------------------------------------------------------


@pytest.mark.parametrize("engine", REMOTES, ids=ENGINE_IDS)
def test_remote_reaches_no_other_chassis_listener(engine: KindEngine) -> None:
    """H17/H18/H30: the control refuses (a policy drop, a timeout) the remote pod's TCP connection
    to every other remote's chassis listener (8091 by ClusterIP) and to PoC-5's. The allowed
    control in the same call: its own chassis listener connects."""
    remote = remote_pod(engine)
    others = [e.proxy_ip for e in REMOTES if e is not engine] + [POC05_PROXY_IP]
    assert engine.proxy_ip is not None
    checks = [tcp_check(str(ip), 8091) for ip in others] + [tcp_check(engine.proxy_ip, 8091)]
    *refused, allowed = in_remote(engine, remote, checks)
    for check, result in zip(checks, refused, strict=False):
        assert result.get("error") == POLICY_DROPPED, (check, result)
    assert allowed.get("connected") is True, allowed


@pytest.mark.parametrize("engine", REMOTES, ids=ENGINE_IDS)
def test_public_port_takes_only_the_gateway(engine: KindEngine, caller: str) -> None:
    """H18 (core review R5a-07): given a pod in `default` with no egress policy, its TCP
    connection to the remote chassis's public port 8080 times out, while it still reaches kube-dns.
    The paired control: PoC-5's gateway peer (`agent-platform-system`,
    `agents.platform/role: gateway`) connects to the same address."""
    ip = chassis_pod(engine)["status"]["podIP"]

    public, own_egress = in_caller(caller, [tcp_check(ip, 8080), dns_check()])
    with gateway_caller() as gateway:
        (allowed,) = in_caller(gateway, [tcp_check(ip, 8080)], GATEWAY_NS)

    assert public == {"error": POLICY_DROPPED}, public
    assert own_egress.get("connected") is True, own_egress
    assert allowed.get("connected") is True, allowed


@pytest.mark.parametrize("engine", REMOTES, ids=ENGINE_IDS)
def test_remote_port_refuses_every_caller_but_the_front_door(
    engine: KindEngine, caller: str
) -> None:
    """H18: the control refuses a TCP connection to the remote pod's A2A port (9000, by the pod
    IP; there is no Service since core review R6-11) from a pod in `default`, from another
    remote's chassis, and from its own chassis, which now reaches the remote only through the
    front door. The allowed controls: the front door connects to the same port, the chassis
    reaches the front door on its own engine's port, and the caller reaches kube-dns on 53, so
    its own egress works."""
    remote = remote_pod(engine)
    chassis = chassis_pod(engine)
    remote_ip = remote["status"]["podIP"]
    door = service_ip(PLATFORM_NS, FRONT_DOOR)
    by_pod, own_egress = in_caller(caller, [tcp_check(remote_ip, 9000), dns_check()])
    assert by_pod.get("error") == POLICY_DROPPED, by_pod
    assert own_egress.get("connected") is True, own_egress

    other = next(e for e in REMOTES if e is not engine and e.name != "kagent-adk")
    (lateral,) = in_chassis(chassis_pod(other), [tcp_check(remote_ip, 9000)])
    assert lateral.get("error") == POLICY_DROPPED, lateral

    direct, to_door = in_chassis(
        chassis, [tcp_check(remote_ip, 9000), tcp_check(door, DOOR_PORTS[engine.pod])]
    )
    assert direct.get("error") == POLICY_DROPPED, direct
    assert to_door.get("connected") is True, to_door

    (from_door,) = in_front_door([tcp_check(remote_ip, 9000)])
    assert from_door.get("connected") is True, from_door


@pytest.mark.parametrize("engine", REMOTES, ids=ENGINE_IDS)
def test_a_chassis_reaches_only_its_own_engine_port_on_the_front_door(engine: KindEngine) -> None:
    """The front door routes by port, one per engine, and its policy lets each chassis reach its
    own port only. So `chassis-claude-agent-remote` times out on the kagent-adk port (kagent-adk
    checks no bearer, so this is its only inbound control). The paired control: the same chassis
    connects to its own port in the same probe."""
    chassis = chassis_pod(engine)
    door = service_ip(PLATFORM_NS, FRONT_DOOR)
    own = DOOR_PORTS[engine.pod]
    others = sorted(p for name, p in DOOR_PORTS.items() if name != engine.pod)
    own_check, *other_checks = in_chassis(
        chassis, [tcp_check(door, own), *(tcp_check(door, p) for p in others)]
    )
    assert own_check.get("connected") is True, own_check
    for port, got in zip(others, other_checks, strict=True):
        assert got.get("error") == POLICY_DROPPED, (port, got)


# --- no way out -------------------------------------------------------------------------------


@pytest.mark.parametrize("engine", REMOTES, ids=ENGINE_IDS)
def test_remote_reaches_no_outside_address(engine: KindEngine, caller: str) -> None:
    """H19: the control refuses the remote pod's TCP connection to a literal outside address
    (1.1.1.1:443 and :80; no DNS involved). The allowed controls: in the same call the remote
    reaches its listener on 8091, and the caller pod, which no egress policy holds, reaches the
    same outside address, so the address answers from this cluster."""
    remote = remote_pod(engine)
    assert engine.proxy_ip is not None
    https, http, allowed = in_remote(
        engine,
        remote,
        [tcp_check(OUTSIDE_IP, 443), tcp_check(OUTSIDE_IP, 80), tcp_check(engine.proxy_ip, 8091)],
    )
    assert https.get("error") == POLICY_DROPPED, https
    assert http.get("error") == POLICY_DROPPED, http
    assert allowed.get("connected") is True, allowed

    (outside,) = in_caller(caller, [tcp_check(OUTSIDE_IP, 443)])
    assert outside.get("connected") is True, outside


@pytest.mark.parametrize("engine", REMOTES, ids=ENGINE_IDS)
def test_remote_has_no_dns(engine: KindEngine) -> None:
    """H20: the remote lane has no DNS at all (its policy has no port 53 rule). The control
    refuses the remote's A query to kube-dns by its ClusterIP and by a CoreDNS pod IP, and to an
    outside resolver, and its own resolver finds no name. The allowed control: the same A query
    from its chassis (which may use kube-dns) is answered."""
    remote = remote_pod(engine)
    chassis = chassis_pod(engine)
    policy = get_json("networkpolicy", engine.pod, "-n", REMOTE_NS)["spec"]
    ports = [p.get("port") for rule in policy.get("egress", []) for p in rule.get("ports", [])]
    assert ports == [8091], ports

    kube_dns = service_ip("kube-system", "kube-dns")
    coredns = get_json("pods", "-n", "kube-system", "-l", "k8s-app=kube-dns")["items"][0]
    name = "litellm.poc05-platform.svc.cluster.local"
    query = {"kind": "dns", "name": name}
    by_svc, by_pod, outside, own = in_remote(
        engine,
        remote,
        [
            {**query, "server": kube_dns},
            {**query, "server": coredns["status"]["podIP"]},
            {**query, "server": OUTSIDE_IP},
            {"kind": "resolve", "name": name},
        ],
    )
    for result in (by_svc, by_pod, outside):
        assert result.get("error") == "TimeoutError", result
    assert "addresses" not in own, own

    (allowed,) = in_chassis(chassis, [{**query, "server": kube_dns}])
    assert allowed.get("rcode") == 0 and allowed.get("answers", 0) >= 1, allowed


# --- the sandbox ------------------------------------------------------------------------------


@pytest.mark.parametrize("engine", REMOTES, ids=ENGINE_IDS)
def test_root_is_read_only_and_tmp_is_writable(engine: KindEngine) -> None:
    """H21: `/proc/mounts` shows `/` mounted `ro`, and the control refuses a write into `/var/tmp`
    (mode 1777 on the root file system, so permissions would allow it) with EROFS. The allowed
    control: the same write to `/tmp` works. For `remote-claude-agent` that `/tmp` is the scratch
    volume the CLI's HOME and work dir live on."""
    remote = remote_pod(engine)
    spec = remote["spec"]["containers"][0]["securityContext"]
    assert spec["readOnlyRootFilesystem"] is True, spec
    mounts, root_write, tmp_write = in_remote(
        engine,
        remote,
        [
            {"kind": "read", "path": "/proc/mounts"},
            {"kind": "write", "path": "/var/tmp/poc06-h21"},
            {"kind": "write", "path": "/tmp/poc06-h21"},
        ],
    )
    table = {f[1]: f[3].split(",") for f in (line.split() for line in mounts["text"].splitlines())}
    assert "ro" in table["/"], table["/"]
    assert "rw" in table["/tmp"], table["/tmp"]
    assert root_write == {"error": "EROFS"}, root_write
    assert tmp_write == {"written": True}, tmp_write


@pytest.mark.parametrize("engine", REMOTES, ids=ENGINE_IDS)
def test_pids_cap_is_the_configured_limit(engine: KindEngine) -> None:
    """H23: the remote pod's pids cgroup on the node has `pids.max` equal to the kubelet's
    `podPidsLimit`, which is the value `cluster.yaml` configures (256). The cap is never exhausted
    here (under gVisor that would end the sandbox). The allowed control: `pids.current` is above
    zero and under the cap, so the sandbox's processes run inside it."""
    remote = remote_pod(engine)
    cluster = yaml.safe_load(CLUSTER_YAML.read_text())
    patches = "\n".join(n.get("kubeadmConfigPatches", [""])[0] for n in cluster["nodes"])
    found = re.search(r"podPidsLimit:\s*(\d+)", patches)
    assert found is not None
    configured = int(found.group(1))
    kubelet = node_sh("grep -E '^podPidsLimit:' /var/lib/kubelet/config.yaml")
    assert int(kubelet.split()[1]) == configured

    uid = remote["metadata"]["uid"].replace("-", "_")
    out = node_sh(
        "d=$(find /sys/fs/cgroup/kubelet.slice/kubelet-kubepods.slice -maxdepth 2 -type d "
        f'-name \'*-pod{uid}.slice\'); cat "$d/pids.max" "$d/pids.current"'
    )
    pids_max, pids_current = out.split()
    assert int(pids_max) == configured == 256, out
    assert 0 < int(pids_current) < int(pids_max), out


@pytest.mark.parametrize("engine", REMOTES, ids=ENGINE_IDS)
def test_remote_runs_on_gvisor_next_to_a_runc_pod(engine: KindEngine) -> None:
    """H28: the remote pod has `runtimeClassName: gvisor`, the RuntimeClass's handler is `runsc`,
    the node's containerd ran its sandbox with `runsc`, and the kernel it sees is gVisor's
    (`/proc/version`). The control: the workload of `agent-openai-agents`, a PoC-6 sidecar pod on
    the default runtime, sees the node's real kernel and its sandbox handler is not `runsc`."""
    remote = remote_pod(engine)
    assert remote["spec"]["runtimeClassName"] == "gvisor"
    assert get_json("runtimeclass", "gvisor")["handler"] == "runsc"
    sidecar = pod(AGENTS_NS, "agent-openai-agents")
    assert sidecar["spec"].get("runtimeClassName") is None

    def handler(name: str) -> str:
        pods = json.loads(node_sh(f"crictl pods --name '^{name}' --state ready -o json"))["items"]
        assert len(pods) == 1, [p["metadata"]["name"] for p in pods]
        found: str = pods[0].get("runtimeHandler", "")
        return found

    assert handler(remote["metadata"]["name"]) == "runsc"
    assert handler(sidecar["metadata"]["name"]) != "runsc"

    (gvisor,) = in_remote(engine, remote, [{"kind": "read", "path": "/proc/version"}])
    (runc,) = probe(
        AGENTS_NS,
        sidecar["metadata"]["name"],
        WORKLOAD,
        [{"kind": "read", "path": "/proc/version"}],
    )
    assert "gvisor" in gvisor["text"], gvisor
    assert "gvisor" not in runc["text"] and runc["text"].startswith("Linux version"), runc


STATUS = "/proc/self/status"
THREAD_PY = (
    "import threading; t=threading.Thread(target=lambda: None); t.start(); t.join(); print('ok')"
)
THREAD_NODE = (
    "const {Worker}=require('node:worker_threads');"
    "const w=new Worker('process.exit(0)',{eval:true});"
    "w.on('exit',()=>console.log('ok'));w.on('error',e=>{console.error(e);process.exit(1)})"
)


@pytest.mark.parametrize("engine", REMOTES, ids=ENGINE_IDS)
def test_remote_can_start_a_thread_and_its_seccomp_filter_is_on(engine: KindEngine) -> None:
    """The gVisor pods use the Localhost profile that allows `clone3` (run.sh `seccomp`; note
    lanes-b-kind.md, "gVisor and clone3"). Under RuntimeDefault, runsc turns the ENOSYS for
    `clone3` into EPERM and `pthread_create` fails. The check: a thread starts in the pod (Python
    `threading`, or a Node worker), and `/proc/self/status` still says `Seccomp:\t2`, so the
    filter is on."""
    remote = remote_pod(engine)
    name = remote["metadata"]["name"]
    exes = ["node"] if engine.language == "node" else list(PYTHONS)
    for exe in exes:
        if exe == "node":
            argvs = [
                ["node", "-e", THREAD_NODE],
                [
                    "node",
                    "-e",
                    "process.stdout.write(require('fs').readFileSync('/proc/self/status','utf8'))",
                ],
            ]
        else:
            argvs = [
                [exe, "-c", THREAD_PY],
                [exe, "-c", f"print(open({STATUS!r}).read())"],
            ]
        first = kubectl("exec", "-n", REMOTE_NS, name, "-c", WORKLOAD, "--", *argvs[0])
        if first.returncode != 0 and "not found" in first.stderr.lower() and exe != exes[-1]:
            continue
        assert first.returncode == 0, f"{engine.id}: thread failed: {first.stderr[-400:]}"
        assert first.stdout.strip().endswith("ok"), first.stdout[-200:]
        second = kubectl("exec", "-n", REMOTE_NS, name, "-c", WORKLOAD, "--", *argvs[1])
        assert second.returncode == 0, f"{engine.id}: status failed: {second.stderr[-400:]}"
        assert "Seccomp:\t2" in second.stdout, f"{engine.id}: the seccomp filter is off"
        return
    pytest.fail(f"{engine.id}: no interpreter found")


NEGATIVE_PY = r"""
import ctypes, json, os, platform
libc = ctypes.CDLL(None, use_errno=True)
NEWUSER = 0x10000000
SYS_CLONE = {"x86_64": 56, "aarch64": 220}.get(platform.machine())
out = {}
def call(name, fn):
    ctypes.set_errno(0)
    ret = fn()
    out[name] = [ret, ctypes.get_errno()]
    return ret
call("unshare", lambda: libc.unshare(NEWUSER))
call("mount", lambda: libc.mount(b"none", b"/tmp", b"tmpfs", 0, None))
if SYS_CLONE is not None:
    ret = call("clone", lambda: libc.syscall(SYS_CLONE, NEWUSER | 17, 0, 0, 0, 0))
    if ret == 0:  # the call made a child: never let it run on
        os._exit(0)
print(json.dumps(out))
"""


@pytest.mark.parametrize("engine", [e for e in REMOTES if e.language != "node"], ids=lambda e: e.id)
def test_remote_still_refuses_namespaces_and_mounts(engine: KindEngine) -> None:
    """The Localhost profile allows `clone3` and nothing else new. In the guest, `unshare(
    CLONE_NEWUSER)`, a `mount`, and a raw `clone` with CLONE_NEWUSER must still fail with EPERM
    (errno 1). The raw `clone` is the masked RuntimeDefault rule that blocks CLONE_NEW*; the
    child guard (`os._exit`) keeps a surprise success from running on. The thread test above is
    the paired allowed control (a thread start works)."""
    name = remote_pod(engine)["metadata"]["name"]
    last = ""
    for exe in PYTHONS:
        got = kubectl("exec", "-n", REMOTE_NS, name, "-c", WORKLOAD, "--", exe, "-c", NEGATIVE_PY)
        if got.returncode != 0 and "not found" in got.stderr.lower():
            last = got.stderr
            continue
        assert got.returncode == 0, f"{engine.id}: probe failed: {got.stderr[-400:]}"
        results = json.loads(got.stdout.strip().splitlines()[-1])
        assert {"unshare", "mount"} <= set(results), results
        for call, (ret, err) in results.items():
            assert ret == -1 and err == 1, f"{engine.id}: {call} gave {ret}, errno {err}"
        if "clone" not in results:
            pytest.skip("raw clone: no syscall number for this architecture (checked the rest)")
        return
    pytest.fail(f"{engine.id}: no interpreter found: {last[-200:]}")


SECCOMP_NODE_FILE = "/var/lib/kubelet/seccomp/profiles/poc06-runsc-clone3.json"


def test_the_profile_file_is_the_same_on_every_node() -> None:
    """`run.sh seccomp` writes one file to every node; the sha256 must agree across nodes."""
    docker = shutil.which("docker")
    assert docker is not None, "docker is not on PATH"
    nodes = get_json("nodes")["items"]
    assert nodes, "no nodes"
    sums: dict[str, str] = {}
    for node in nodes:
        n = node["metadata"]["name"]  # a kind node's name is its Docker container's name
        got = subprocess.run(
            [docker, "exec", n, "sha256sum", SECCOMP_NODE_FILE],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert got.returncode == 0, f"{n}: {got.stderr[-300:]}"
        sums[n] = got.stdout.split()[0]
    assert len(set(sums.values())) == 1, sums


@pytest.fixture(scope="module")
def missing_profile_pod() -> Iterator[subprocess.CompletedProcess[str]]:
    """The result of creating the pod with a missing profile. Deleted after, if it was created."""
    made = create_missing_profile_pod()
    yield made
    if made.returncode == 0:
        delete_missing_profile_pod()


def test_a_refused_pod_with_a_missing_profile_was_denied_by_admission(
    missing_profile_pod: subprocess.CompletedProcess[str],
) -> None:
    """Fail closed: admission may refuse the pod before scheduling. Accepted, as long as the
    refusal is a denial and not another `kubectl create` failure. A pod that was admitted is the
    case of `test_a_pod_with_a_missing_profile_never_runs`, so this one skips."""
    if missing_profile_pod.returncode == 0:
        pytest.skip("admission let the pod in: the never-Running test decides")
    stderr = missing_profile_pod.stderr
    assert "denied" in stderr.lower() or "forbidden" in stderr.lower(), stderr


def test_a_pod_with_a_missing_profile_never_runs(
    missing_profile_pod: subprocess.CompletedProcess[str],
) -> None:
    """Fail closed: a Localhost profile the node does not have must stop the container, not run it
    unfiltered. The pod stays out of Running with a container waiting in CreateContainerError or
    CreateContainerConfigError. A pod that admission refused is the case of the test above, so
    this one skips."""
    if missing_profile_pod.returncode != 0:
        pytest.skip("admission refused the pod: the denial test decides")
    reason = container_error_of(CALLER_NS, MISSING_PROFILE_POD)
    assert reason, "no container reached CreateContainerError or CreateContainerConfigError"


def test_the_remote_pods_are_the_ones_the_manifests_name() -> None:
    """A guard for the tests above: a warm pool per remote, each pod with its own name label."""
    for engine in REMOTES:
        assert remote_pod(engine)["metadata"]["labels"][NAME_LABEL] == engine.pod
    got = kubectl("get", "sandboxwarmpool", "-n", REMOTE_NS, "-o", "name")
    assert got.returncode == 0, got.stderr
