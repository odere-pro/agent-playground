"""059 H-15 box 2 on kind: a generated `--trust untrusted` chart runs as a `remote`.

`agentctl new my-agent --kind transformer --trust untrusted` (LangGraph, the default framework;
no `--connector`, so the lane is `remote`) renders a repo. The test builds its workload image
with the repo's own Dockerfile, loads it and the chassis image into the PoC-5 cluster `poc05`
(the one cluster here with gVisor), and installs the repo's chart with `helm install`. The chart
is used as rendered; the drill values only point it at this cluster:

- the workload image built here, and `pullPolicy: Never` for both images (kind loads them);
- the chassis's fake model (`spec.adapters.model: fake`) and no LiteLLM Secret or egress, so the
  run needs no key and no platform service;
- a free fixed ClusterIP for the remote proxy (the default, 10.96.0.92, is PoC-6 smolagents').

The chassis image is this checkout's PoC-5 build, tagged `poc09`, the library chart's tag. The
release goes in two namespaces of its own, `h15-agents` and `my-agent-remote`, with Pod Security
`restricted`. They do not carry `agents.platform/admission: enforce`, so PoC-5's admission rules
do not apply; the box is about the chart's pods, not admission. The test makes the token Secret
`my-agent-workload-token` (two random keys, one per direction) in both namespaces through
stdin, never in argv or output, and removes the release and both namespaces after.

Marked `network` and skipped unless `KIND_DRILLS=1` (`poc_harness.drills`). It needs
`deploy/kind/poc05/run.sh up`, then:

    KIND_DRILLS=1 uv run pytest -m network \\
        pocs/poc-09-agent-mvp-template/tests/test_poc09_kind_untrusted_remote.py -q -rs

Every kubectl and helm call pins `--context kind-poc05`.
"""

from __future__ import annotations

import base64
import json
import re
import secrets
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from poc_harness.drills import KIND_SANDBOX
from poc_harness.kind import CONTEXT, POLICY_DROPPED, kubectl, probe, service_ip, tcp_check

pytestmark = KIND_SANDBOX

ROOT = Path(__file__).resolve().parents[3]
AGENT = "my-agent"
CHASSIS_NS = "h15-agents"
REMOTE_NS = f"{AGENT}-remote"
"""The generated values' `remote.namespace` (`<name>-remote`)."""
WORKLOAD_DEPLOY = f"{AGENT}-workload"
TOKEN_SECRET = f"{AGENT}-workload-token"  # pragma: allowlist secret (a Secret name)
WORKLOAD_IMAGE = f"kind.local/agent-platform/{AGENT}:h15"
CHASSIS_BUILT = "kind.local/agent-platform/chassis:poc05"
CHASSIS_IMAGE = "kind.local/agent-platform/chassis:poc09"
"""The library chart's chassis tag (`agent-lib.defaults`)."""
PROXY_IP = "10.96.0.97"
"""suggested: a fixed ClusterIP no PoC-5 or PoC-6 Service takes (they use .91 to .95)."""
OUTSIDE_IP = "1.1.1.1"  # a literal public address: no DNS involved
GVISOR = "gvisor"
DRILL_VALUES: dict[str, Any] = {
    "workload": {"image": WORKLOAD_IMAGE, "pullPolicy": "Never"},
    "chassis": {
        "image": {"pullPolicy": "Never"},
        "env": [],
        "secretEnv": [],
        "config": {"spec": {"adapters": {"model": "fake"}}},
    },
    "remote": {"proxyClusterIP": PROXY_IP},
    "networkPolicy": {"chassisEgress": []},
}
RUN_PY = r"""
import json, os, urllib.request
req = urllib.request.Request("http://%s:8080/v1/run" % os.environ["POD_IP"],
                             json.dumps({"input": {"text": "hello"}}).encode(),
                             {"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=60) as r:
    out = json.loads(r.read())
    print(json.dumps({"status": r.status, "run": out["status"], "output": out["output"]}))
"""
STEP_TIMEOUT_S = 600


def _run(*argv: str, stdin: str | None = None, cwd: Path | None = None) -> str:
    done = subprocess.run(
        list(argv),
        input=stdin,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=STEP_TIMEOUT_S,
        check=False,
    )
    assert done.returncode == 0, f"{argv[0]} {argv[1]}: {done.stderr[-800:]}"
    return done.stdout


def _tool(name: str) -> str:
    exe = shutil.which(name)
    assert exe is not None, f"{name} is not on PATH"
    return exe


def _helm(*args: str, cwd: Path | None = None) -> str:
    return _run(_tool("helm"), "--kube-context", CONTEXT, *args, cwd=cwd)


def _kubectl_ok(*args: str, stdin: str | None = None) -> str:
    got = kubectl(*args, stdin=stdin, timeout=STEP_TIMEOUT_S)
    assert got.returncode == 0, got.stderr[-400:]
    return got.stdout


def _generate(dest: Path) -> Path:
    """`agentctl new` with `--trust untrusted` and no `--connector`, pinned to this checkout."""
    _run(
        sys.executable, "-m", "agentctl", "new", AGENT, "--kind", "transformer",
        "--trust", "untrusted", "--dest", str(dest), "--platform-path", str(ROOT),
    )  # fmt: skip
    agent = dest / AGENT
    answers = yaml.safe_load((agent / ".copier-answers.yml").read_text())
    assert (answers["trust"], answers["connector"]) == ("untrusted", "remote"), answers
    return agent


def _images(agent: Path) -> None:
    """Build the workload with the repo's Dockerfile; load it and the chassis into the node."""
    _run(
        _tool("docker"), "buildx", "build",
        "--build-context", f"workload-a2a={ROOT / 'packages/workload-a2a'}",
        "-t", WORKLOAD_IMAGE, "--load", str(agent),
    )  # fmt: skip
    _run(_tool("docker"), "tag", CHASSIS_BUILT, CHASSIS_IMAGE)
    _run(_tool("kind"), "load", "docker-image", "--name", "poc05", CHASSIS_IMAGE, WORKLOAD_IMAGE)


def _token_secrets() -> str:
    """The two-key token Secret in both namespaces, as one List manifest for stdin."""
    keys = {
        "chassis-to-workload": secrets.token_hex(32),
        "workload-to-chassis": secrets.token_hex(32),
    }
    data = {k: base64.b64encode(v.encode()).decode() for k, v in keys.items()}
    items = [
        {
            "apiVersion": "v1",
            "kind": "Secret",
            "type": "Opaque",
            "metadata": {"name": TOKEN_SECRET, "namespace": ns},
            "data": data,
        }
        for ns in (CHASSIS_NS, REMOTE_NS)
    ]
    return json.dumps({"apiVersion": "v1", "kind": "List", "items": items})


def _namespaces() -> None:
    for ns in (CHASSIS_NS, REMOTE_NS):
        _kubectl_ok("create", "namespace", ns)
        _kubectl_ok("label", "namespace", ns, "pod-security.kubernetes.io/enforce=restricted")
    _kubectl_ok("apply", "-f", "-", stdin=_token_secrets())


def _cleanup() -> None:
    kubectl("delete", "namespace", CHASSIS_NS, REMOTE_NS, "--ignore-not-found", "--wait=true")


def _one_pod(namespace: str, name: str) -> dict[str, Any]:
    items = json.loads(
        _kubectl_ok(
            "get", "pods", "-n", namespace, "-l", f"app.kubernetes.io/name={name}", "-o", "json"
        )
    )["items"]
    assert len(items) == 1, f"{namespace}/{name}: want one pod, got {len(items)}"
    found: dict[str, Any] = items[0]
    return found


@pytest.fixture(scope="module")
def release(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, dict[str, Any]]]:
    """The generated chart installed and Ready: {"chassis": pod, "workload": pod}."""
    work = tmp_path_factory.mktemp("h15")
    agent = _generate(work)
    _images(agent)
    values = work / "drill-values.yaml"
    values.write_text(yaml.safe_dump(DRILL_VALUES))
    _cleanup()
    _namespaces()
    try:
        _helm("dependency", "update", "--skip-refresh", "deploy/helm", cwd=agent)
        _helm(
            "install", AGENT, "deploy/helm", "-n", CHASSIS_NS, "-f", str(values),
            "--wait", "--timeout", "180s", cwd=agent,
        )  # fmt: skip
        yield {
            "chassis": _one_pod(CHASSIS_NS, AGENT),
            "workload": _one_pod(REMOTE_NS, WORKLOAD_DEPLOY),
        }
    finally:
        subprocess.run(
            [_tool("helm"), "--kube-context", CONTEXT, "uninstall", AGENT, "-n", CHASSIS_NS],
            capture_output=True,
            timeout=STEP_TIMEOUT_S,
            check=False,
        )
        _cleanup()


def _proc_version(pod: dict[str, Any], container: str) -> str:
    meta = pod["metadata"]
    return _kubectl_ok(
        "exec", "-n", meta["namespace"], meta["name"], "-c", container, "--", "cat", "/proc/version"
    )


def _in_workload(pod: dict[str, Any], checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return probe(REMOTE_NS, pod["metadata"]["name"], "workload", checks)


def _logs(pod: dict[str, Any], container: str) -> str:
    meta = pod["metadata"]
    return _kubectl_ok("logs", "-n", meta["namespace"], meta["name"], "-c", container)


def _run_through_chassis(chassis: dict[str, Any]) -> dict[str, Any]:
    out = _kubectl_ok(
        "exec", "-n", CHASSIS_NS, chassis["metadata"]["name"], "-c", "chassis", "--",
        "python", "-c", RUN_PY,
    )  # fmt: skip
    result: dict[str, Any] = json.loads(out.strip().splitlines()[-1])
    return result


def test_untrusted_chart_runs_the_workload_alone_in_a_gvisor_pod(
    release: dict[str, dict[str, Any]],
) -> None:
    """`--trust untrusted` produces a `remote` chart: the workload runs in its own pod with the
    gVisor RuntimeClass, and the chassis pod stays outside the sandbox.

    Both pods are Ready, in different namespaces. The workload pod holds the workload alone, sets
    `runtimeClassName: gvisor`, and its kernel is gVisor's. The paired control: the chassis pod
    holds the chassis alone, sets no RuntimeClass, and its kernel is the node's."""
    chassis, workload = release["chassis"], release["workload"]

    w_kernel = _proc_version(workload, "workload")
    c_kernel = _proc_version(chassis, "chassis")

    assert [c["name"] for c in workload["spec"]["containers"]] == ["workload"]
    assert workload["spec"]["runtimeClassName"] == GVISOR
    assert GVISOR in w_kernel, w_kernel
    assert [c["name"] for c in chassis["spec"]["containers"]] == ["chassis"]
    assert "runtimeClassName" not in chassis["spec"]
    assert GVISOR not in c_kernel, c_kernel
    assert workload["metadata"]["namespace"] == REMOTE_NS != chassis["metadata"]["namespace"]


def test_untrusted_chart_workload_has_default_deny_egress(
    release: dict[str, dict[str, Any]],
) -> None:
    """`--trust untrusted` produces a `remote` chart: the workload runs in its own pod with the
    gVisor RuntimeClass and default-deny egress.

    The control refuses the workload's TCP connection to a literal outside address (1.1.1.1:443)
    and its A query to kube-dns. The allowed control: in the same call the workload reaches its
    chassis's remote proxy on the fixed ClusterIP, port 8091."""
    query = {"kind": "dns", "name": "kubernetes.default.svc.cluster.local"}

    outside, dns, proxy = _in_workload(
        release["workload"],
        [
            tcp_check(OUTSIDE_IP, 443),
            {**query, "server": service_ip("kube-system", "kube-dns")},
            tcp_check(PROXY_IP, 8091),
        ],
    )

    assert outside.get("error") == POLICY_DROPPED, outside
    assert dns.get("error") == POLICY_DROPPED, dns
    assert proxy.get("connected") is True, proxy


def test_untrusted_chart_chassis_calls_the_workload_over_the_remote_lane(
    release: dict[str, dict[str, Any]],
) -> None:
    """`--trust untrusted` produces a `remote` chart: the chassis reaches the workload in its own
    pod over A2A, and the workload reaches the chassis only through its remote proxy.

    One run through the chassis's public port. The workload's access log shows the chassis pod's
    A2A call answered 200, and the chassis's log shows the workload pod's MCP calls to the
    remote listener answered 200, so the bearer keys matched in both directions."""
    chassis, workload = release["chassis"], release["workload"]
    chassis_ip = re.escape(chassis["status"]["podIP"])
    workload_ip = re.escape(workload["status"]["podIP"])

    run = _run_through_chassis(chassis)

    assert run["status"] == 200, run
    assert re.search(rf'{chassis_ip}:\d+ - \\"POST / HTTP/1.1\\" 200', _logs(workload, "workload"))
    assert re.search(
        rf'{workload_ip}:\d+ - \\"POST /mcp HTTP/1.1\\" 200', _logs(chassis, "chassis")
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "the LangGraph workload cannot start a thread under gVisor with the chart's RuntimeDefault "
        "seccomp profile (runsc turns clone3's ENOSYS rule into EPERM; PoC-6 'gVisor and "
        "clone3'). agent-lib has no value for the remote pod's seccomp profile yet. "
        "owner: 059 H-15 box 2, platform-security (agent-lib seccomp value)"
    ),
)
def test_untrusted_chart_run_ends_ok(release: dict[str, dict[str, Any]]) -> None:
    """`--trust untrusted` produces a `remote` chart that serves a run: one run through the
    chassis ends `ok` with the fake model's reply."""
    run = _run_through_chassis(release["chassis"])

    assert run == {"status": 200, "run": "ok", "output": {"text": "ok"}}, run
