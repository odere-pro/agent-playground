"""PoC-5 on kind: the runtime hardening of each lane's untrusted container, read live (review
R5a-08; plan section 2.11; threat model H28; ADR-005).

Exit criterion 8, partly shown: the sidecar workload (`agent-echo`, runc), the remote pod
(`remote-echo`, gVisor), and a warm code-runner sandbox (gVisor) each run with no effective
capability, `no_new_privs` set, a seccomp filter (mode 2), and a non-root uid. The static tests
check the pod specs; this reads what the kernel (or gVisor's sentry) applied, with `kubectl exec`
of the image's own Python: `/proc/self/status` for `CapEff` and `Seccomp`, `os.geteuid()`, and
`prctl(PR_GET_NO_NEW_PRIVS)` for `no_new_privs` (gVisor's status file may omit that line). The
paired control: the same fields read on the kind node, a privileged root process, show a non-zero
`CapEff` and uid 0, so the reader can see what it refuses.

Pod Security: a root pod sent with `--dry-run=server` to each workload namespace is refused by
PodSecurity `restricted`, next to its admitted non-root twin (the admission fixture the root pod
is built from). Both go as the cluster admin, so RBAC refuses neither; the root pod differs from
its twin only in its user, so the trust rule admits it and PodSecurity alone refuses it. Nothing
is created.

A kind test: marked `network`, skipped unless `POC05_KIND=1` (`poc05_conftest.py`). Run:
`POC05_KIND=1 uv run pytest -m network <this file>`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml
from poc05_kind import (
    AGENTS_NS,
    REMOTE_NS,
    SIDECAR_POD,
    TOOLS_NS,
    WORKLOAD,
    kubectl,
    node_sh,
    pod,
    warm_sandboxes,
)

REPO = Path(__file__).resolve().parents[3]
FIXTURES = REPO / "deploy" / "kind" / "poc05" / "admission" / "fixtures"
REMOTE = "remote-echo"
CODE_RUNNER = "code-runner"
PSA_REFUSAL = 'violates PodSecurity "restricted'

# Reads the hardening fields of its own process; prints one JSON line. Names and numbers only.
STATUS_PY = r"""
import ctypes, json, os
fields = dict(line.split(":", 1) for line in open("/proc/self/status").read().splitlines()
              if ":" in line)
libc = ctypes.CDLL(None, use_errno=True)
PR_GET_NO_NEW_PRIVS = 39
print(json.dumps({"cap_eff": int(fields["CapEff"].strip(), 16),
                  "seccomp": int(fields["Seccomp"].strip()),
                  "uid": os.geteuid(),
                  "no_new_privs": libc.prctl(PR_GET_NO_NEW_PRIVS, 0, 0, 0, 0)}))
"""
NODE_STATUS = "grep -E '^(CapEff|Uid):' /proc/self/status"


@dataclass(frozen=True)
class Lane:
    id: str
    namespace: str
    container: str
    app: str


LANES = [
    Lane("sidecar-workload", AGENTS_NS, WORKLOAD, SIDECAR_POD),
    Lane("remote", REMOTE_NS, WORKLOAD, REMOTE),
    Lane("code-runner-sandbox", TOOLS_NS, CODE_RUNNER, CODE_RUNNER),
]


def lane_pod(lane: Lane) -> dict[str, Any]:
    """The lane's pod: the one Running pod of its app, or a warm code-runner sandbox."""
    if lane.app == CODE_RUNNER:
        sandboxes = warm_sandboxes()
        assert sandboxes, "no Running code-runner pod (the warm pool is empty)"
        return sandboxes[0]
    return pod(lane.namespace, lane.app)


def spec_uid(found: dict[str, Any], container: str) -> int:
    """The `runAsUser` the pod spec gives `container`."""
    (c,) = [c for c in found["spec"]["containers"] if c["name"] == container]
    uid: int = c["securityContext"]["runAsUser"]
    return uid


def status_in(found: dict[str, Any], lane: Lane) -> dict[str, int]:
    got = kubectl(
        "exec", "-n", lane.namespace, found["metadata"]["name"], "-c", lane.container, "--",
        "python", "-c", STATUS_PY,
    )  # fmt: skip
    assert got.returncode == 0, f"{lane.id}: exec failed: {got.stderr[-400:]}"
    status: dict[str, int] = json.loads(got.stdout.strip().splitlines()[-1])
    return status


def node_status() -> dict[str, int]:
    """`CapEff` and the effective uid of a root shell on the kind node."""
    fields = dict(line.split(":", 1) for line in node_sh(NODE_STATUS).splitlines())
    return {"cap_eff": int(fields["CapEff"].strip(), 16), "uid": int(fields["Uid"].split()[1])}


@pytest.mark.parametrize("lane", LANES, ids=[lane.id for lane in LANES])
def test_untrusted_container_runs_with_no_capability_no_new_privs_and_seccomp(lane: Lane) -> None:
    """Criterion 8 (partly shown), review R5a-08: in the lane's untrusted container,
    `/proc/self/status` shows `CapEff` all zero and `Seccomp` 2 (a filter), `no_new_privs` is 1,
    and the uid is the pod spec's non-zero `runAsUser`. The paired control: the same fields on the
    kind node, a privileged root process, show a non-zero `CapEff` and uid 0."""
    found = lane_pod(lane)
    want_uid = spec_uid(found, lane.container)

    seen = status_in(found, lane)
    node = node_status()

    assert seen["cap_eff"] == 0, (lane.id, seen)
    assert seen["no_new_privs"] == 1, (lane.id, seen)
    assert seen["seccomp"] == 2, (lane.id, seen)
    assert seen["uid"] == want_uid != 0, (lane.id, seen, want_uid)
    assert node["cap_eff"] != 0 and node["uid"] == 0, node


# Per workload namespace, the admitted fixture the root pod is built from (its non-root twin).
TWINS = {
    AGENTS_NS: FIXTURES / "rule8-pull-policy" / "admitted.yaml",
    REMOTE_NS: FIXTURES / "rule5-remote-shape" / "admitted.yaml",
    TOOLS_NS: FIXTURES / "rule7d-tool-gvisor" / "admitted.yaml",
}


def as_root(manifest: dict[str, Any]) -> dict[str, Any]:
    """A copy of the pod `manifest` that runs as root: uid 0 in the pod and every container, and
    `runAsNonRoot` off. Nothing else changes."""
    spec = manifest["spec"]
    root = {"runAsUser": 0}
    containers = {
        key: [{**c, "securityContext": {**c.get("securityContext", {}), **root}} for c in spec[key]]
        for key in ("initContainers", "containers")
        if key in spec
    }
    pod_context = {**spec.get("securityContext", {}), **root, "runAsNonRoot": False}
    return {**manifest, "spec": {**spec, **containers, "securityContext": pod_context}}


def dry_run(path: Path) -> tuple[int, str]:
    got = kubectl("apply", "--dry-run=server", "-f", str(path))
    return got.returncode, " ".join((got.stdout + got.stderr).split())


@pytest.mark.parametrize("namespace", sorted(TWINS))
def test_root_pod_is_refused_by_pod_security_next_to_its_twin(
    namespace: str, tmp_path: Path
) -> None:
    """Criterion 8 (partly shown), review R5a-08: a root pod sent with `--dry-run=server` to a
    workload namespace is refused by PodSecurity `restricted`. The paired control: its non-root
    twin, the same pod as the admission fixture, is admitted."""
    twin = TWINS[namespace]
    manifest = yaml.safe_load(twin.read_text())
    root_path = tmp_path / "root-pod.yaml"
    root_path.write_text(yaml.safe_dump(as_root(manifest)))

    refused_rc, refused = dry_run(root_path)
    admitted_rc, admitted = dry_run(twin)

    assert manifest["metadata"]["namespace"] == namespace
    assert refused_rc != 0 and PSA_REFUSAL in refused, refused
    assert admitted_rc == 0 and "(server dry run)" in admitted, admitted
