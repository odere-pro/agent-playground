"""PoC-6b on kind, the remote lane: the PoC-5 remote controls (`test_poc05_kind_remote_controls.py`,
plan sections 2.2, 2.3, 2.10, 2.11; threat model H17 to H21, H23, H28) for each new remote pod:
`remote-smolagents`, `remote-claude-agent`, `remote-typescript`, and `remote-kagent-adk`.

Exit criteria 1 and 4: the remote holds no credential but its own token (kagent-adk holds none),
reaches nothing but its chassis's listener, has no DNS, runs on a read-only root with a writable
`/tmp`, is held by the pids cap, and runs on gVisor next to a runc pod. Every refusal asserts its
paired allowed control in the same test.

Reuse: the probe, the pod and node helpers, and `secret_sources` come from PoC-5's tests unchanged.
What is new: the probe runs with Python or Node by the image (`poc06b_kind.probe_in`), and the
listener checks cover `/v1/messages` too (the Claude CLI's path).

Recorded exceptions (notes/2026-10-09-lanes-b-kind.md):
- `remote-typescript` has no Python (the Node image). The probe is `node_probe.js`, the same check
  kinds in Node. It does not cover the PoC-5 `procs` scan (H26 is a sidecar check, not run here).
- `remote-kagent-adk`: its image may have no Python either; then `probe_python` marks the test
  `xfail` with the reason, and the Node fallback does not apply. Its other checks read the live
  pod spec and the node. It runs only when `kagent/image.sha256` holds a digest.

A kind test: marked `kind` (and `network`), skipped unless `POC06_KIND=1` (`poc06b_conftest.py`).
Run: `deploy/kind/poc06/run.sh test`.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from poc05_kind import (
    AGENTS_NS,
    CHASSIS,
    NOT_A_KEY,
    POLICY_DROPPED,
    REMOTE_NS,
    WORKLOAD,
    get_json,
    in_caller,
    kubectl,
    node_sh,
    pod,
    probe,
    service_ip,
    tcp,
    unpoliced_caller,
)
from poc06b_kind import (
    KAGENT_XFAIL,
    REMOTES,
    KindEngine,
    kagent_enabled,
    probe_in,
)
from test_poc05_kind_remote_controls import secret_sources, traceparent

pytestmark = pytest.mark.kind

REPO = Path(__file__).resolve().parents[3]
CLUSTER_YAML = REPO / "deploy" / "kind" / "poc05" / "cluster.yaml"
TOKEN_ENV = "CHASSIS_API_TOKEN"
OUTSIDE_IP = "1.1.1.1"  # a literal public address: no DNS involved
POC05_PROXY_IP = "10.96.85.91"  # the PoC-5 remote's chassis proxy
CHASSIS_CREDENTIALS = {"LITELLM_API_KEY", "VALKEY_PASSWORD", "REMOTE_TOKEN"}


def remote_pod(engine: KindEngine) -> dict[str, Any]:
    if engine.name == "kagent-adk" and not kagent_enabled():
        pytest.xfail(KAGENT_XFAIL)
    return pod(engine.namespace, engine.pod)


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
    trace = traceparent(uuid.uuid4().hex)
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
    checks = [tcp(str(ip), 8091) for ip in others] + [tcp(engine.proxy_ip, 8091)]
    *refused, allowed = in_remote(engine, remote, checks)
    for check, result in zip(checks, refused, strict=False):
        assert result.get("error") == POLICY_DROPPED, (check, result)
    assert allowed.get("connected") is True, allowed


@pytest.mark.parametrize("engine", REMOTES, ids=ENGINE_IDS)
def test_remote_port_refuses_every_caller_but_its_chassis(engine: KindEngine, caller: str) -> None:
    """H18: the control refuses a TCP connection to the remote's A2A port (9000, by the pod IP and
    the Service) from a pod in `default` and from another remote's chassis. The allowed control:
    its own chassis connects to the same port by both addresses, and the caller reaches another
    pod's open port (its chassis's 8080)."""
    remote = remote_pod(engine)
    chassis = chassis_pod(engine)
    remote_ip, svc = remote["status"]["podIP"], service_ip(REMOTE_NS, engine.pod)
    by_pod, by_svc, public = in_caller(
        caller, [tcp(remote_ip, 9000), tcp(svc, 9000), tcp(chassis["status"]["podIP"], 8080)]
    )
    assert by_pod.get("error") == POLICY_DROPPED, by_pod
    assert by_svc.get("error") == POLICY_DROPPED, by_svc
    assert public.get("connected") is True, public

    other = next(e for e in REMOTES if e is not engine and e.name != "kagent-adk")
    other_chassis = chassis_pod(other)
    (lateral,) = in_chassis(other_chassis, [tcp(remote_ip, 9000)])
    assert lateral.get("error") == POLICY_DROPPED, lateral

    own_pod, own_svc = in_chassis(chassis, [tcp(remote_ip, 9000), tcp(svc, 9000)])
    assert own_pod.get("connected") is True, own_pod
    assert own_svc.get("connected") is True, own_svc


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
        engine, remote, [tcp(OUTSIDE_IP, 443), tcp(OUTSIDE_IP, 80), tcp(engine.proxy_ip, 8091)]
    )
    assert https.get("error") == POLICY_DROPPED, https
    assert http.get("error") == POLICY_DROPPED, http
    assert allowed.get("connected") is True, allowed

    (outside,) = in_caller(caller, [tcp(OUTSIDE_IP, 443)])
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


def test_the_remote_pods_are_the_ones_the_manifests_name() -> None:
    """A guard for the tests above: the live cluster has one Running pod per remote that was
    applied (kagent-adk only with a digest), each with its own name label and no sibling."""
    for engine in REMOTES:
        if engine.name == "kagent-adk" and not kagent_enabled():
            continue
        items = get_json("pods", "-n", REMOTE_NS, "-l", f"app.kubernetes.io/name={engine.pod}")
        assert len(items["items"]) == 1, engine.pod
    got = kubectl("get", "sandbox", "-n", REMOTE_NS, "-o", "name")
    assert got.returncode == 0, got.stderr
