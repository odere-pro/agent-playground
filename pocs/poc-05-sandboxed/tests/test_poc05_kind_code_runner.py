"""PoC-5 on kind: the code runner's per-call sandbox (T22, per-call sandbox plan task 6; plan
sections 2.8, 2.10, 2.11; threat model H19, H23, H28, B12 for the tool lane; security review
2026-10-09, the HIGH and the file-leak MEDIUM).

Exit criterion 8, partly shown: the code-execution tool runs on gVisor with no egress, in a fresh
sandbox per call, and a call cannot start processes past its per-call limit. LiteLLM registers the
dispatcher (`code-runner-dispatch`, runc, `poc05-platform`) as `code_runner`. Per call it claims a
sandbox from the warm pool `code-runner` in `poc05-tools`, runs the code there, and deletes the
claim. Every check uses the tool's own API, `code_runner-run_python` through LiteLLM's MCP gateway
with the chassis's key (`McpGatewayTools`, the adapter the chassis uses), with ordinary
standard-library code. Each refusal sits next to its allowed control in the same test.

- runsc: the template and every pool pod have `runtimeClassName: gvisor`; the code ran in one of
  them (its hostname) and sees gVisor's kernel.
- No egress (`egress: []`): a policy drop is a timeout. The code's TCP to a literal outside
  address, to LiteLLM, to the dispatcher, and to the API server times out; its own server on
  127.0.0.1:8000 connects. Each target is up from an allowed peer in the same test (an unpoliced
  pod in `default` for the outside address).
- Only the dispatcher reaches a sandbox: LiteLLM (the old caller) and the unpoliced pod are
  dropped; the dispatcher connects.
- The process limit (`RLIMIT_NPROC`, the uid's tasks at the call's start plus 32): forking
  children that `exec /bin/cat` on a pipe, a call is refused with EAGAIN after about 31; a call
  that starts 5 works.
- The burst (the HIGH): about 31 forked Python children in one call. Whatever happens to that
  sandbox, the dispatcher does not restart, and a concurrent call and the next call work.
- Nothing crosses calls (B12): files call A leaves in `/tmp` and `/dev/shm` are not in call B.
- `/dev/shm` is an 8Mi memory `emptyDir`: a write past it fails with ENOSPC.
- The claim for a call is gone, with its pod, within 10 s (`suggested:`) of the call's end.

A kind test: marked `network`, skipped unless `POC05_KIND=1` (`poc05_conftest.py`). It needs the
gateway URL and the chassis's key in the environment. Run:
`POC05_KIND=1 deploy/kind/poc05/run.sh with-gateway uv run pytest -m network <this file>`.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from chassis.adapters.mcp.gateway import McpGatewayTools
from poc05_kind import (
    AGENTS_NS,
    CONTEXT,
    PLATFORM_NS,
    POLICY_DROPPED,
    SIDECAR_POD,
    TOOLS_NS,
    WORKLOAD,
    get_json,
    in_caller,
    kubectl,
    node_ip,
    pod,
    pods,
    probe,
    service_ip,
    tcp,
    unpoliced_caller,
    warm_sandboxes,
)

URL_VAR = "POC05_GATEWAY_MCP_URL"
KEY_VAR = "POC05_CHASSIS_VIRTUAL_KEY"
TOOL = "code_runner-run_python"
APP = "code-runner"
DISPATCH = "code-runner-dispatch"
NPROC_ALLOWANCE = 32  # code_runner.isolation.NPROC_ALLOWANCE
UNDER_LIMIT = 5
BURST = 31  # the security review's count: about 31 Python children reach 256Mi
OUTSIDE_IP = "1.1.1.1"
API_PORT = 6443
TIMEOUT_S = 10  # the tool's ceiling (code_runner Limits.max_timeout_s)
GONE_WITHIN_S = 10  # suggested: per-call sandbox plan, "Tests"
SHM_CAP_MIB = 8  # the template's /dev/shm sizeLimit
POD_MEMORY = "256Mi"  # the per-call pod's memory limit and request
LOST = "sandbox_lost"

# Forks up to `want` children that block on a pipe, stops at the first refusal, then closes the
# pipe so every child exits, and reaps them. Prints one JSON line. Each child is `cat` on the
# pipe, not Python (module docstring).
FORKS = """
import errno, json, os
want = {want}
r, w = os.pipe()
children, refused = [], None
for _ in range(want):
    try:
        pid = os.fork()
    except OSError as e:
        refused = errno.errorcode.get(e.errno, str(e.errno))
        break
    if pid == 0:
        os.close(w)
        os.dup2(r, 0)
        os.execv("/bin/cat", ["cat"])
    children.append(pid)
os.close(w)
for pid in children:
    os.waitpid(pid, 0)
print(json.dumps({{"started": len(children), "refused": refused}}))
"""

# Forks `want` Python children that block on a pipe (no exec: each is a copy of the
# interpreter), stops at the first refusal, then closes the pipe so every child exits.
PY_FORKS = """
import errno, json, os
want = {want}
r, w = os.pipe()
children, refused = [], None
for _ in range(want):
    try:
        pid = os.fork()
    except OSError as e:
        refused = errno.errorcode.get(e.errno, str(e.errno))
        break
    if pid == 0:
        os.close(w)
        os.read(r, 1)
        os._exit(0)
    children.append(pid)
os.close(w)
for pid in children:
    os.waitpid(pid, 0)
print(json.dumps({{"started": len(children), "refused": refused}}))
"""

# The connects run at once in one thread (the runner's limits leave no room for threads): each
# gets 3 s, under the call's 10 s. A connect still open at the end is a TimeoutError.
NETWORK = """
import json, os, select, socket, time
targets = {{"outside": ("{outside}", 443), "litellm": ("{litellm}", 4000),
           "dispatch": ("{dispatch}", 8000), "api_node": ("{node}", {api_port}),
           "api_vip": ("{vip}", 443), "own_server": ("127.0.0.1", 8000)}}
socks, seen = {{}}, {{}}
for name, address in targets.items():
    s = socket.socket()
    s.setblocking(False)
    s.connect_ex(address)
    socks[s] = name
deadline = time.monotonic() + 3
while socks and time.monotonic() < deadline:
    _, ready, _ = select.select([], list(socks), [], deadline - time.monotonic())
    for s in ready:
        err = s.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
        seen[socks.pop(s)] = "connected" if err == 0 else type(OSError(err, "")).__name__
        s.close()
for s, name in socks.items():
    seen[name] = "TimeoutError"
    s.close()
seen.update(kernel=open("/proc/version").read(), host=socket.gethostname())
print(json.dumps(seen))
"""

WRITE_FILES = """
import json, socket
seen = {{}}
for path in ("/tmp/{name}", "/dev/shm/{name}"):
    with open(path, "w") as f:
        f.write("call-a")
    seen[path] = open(path).read()
print(json.dumps({{"read_back": seen, "host": socket.gethostname()}}))
"""

LOOK_FILES = """
import json, os, socket
print(json.dumps({{"exists": [os.path.exists(p) for p in ("/tmp/{name}", "/dev/shm/{name}")],
                  "host": socket.gethostname()}}))
"""

# A 1 MiB control write, then 1 MiB files (each under RLIMIT_FSIZE) until one fails.
SHM_FILL = """
import errno, json
chunk = b"x" * (1 << 20)
def put(path):
    try:
        with open(path, "wb") as f:
            f.write(chunk)
        return None
    except OSError as e:
        return errno.errorcode.get(e.errno, str(e.errno))
small = put("/dev/shm/small")
written, error = 1, None
for i in range({limit}):
    error = put("/dev/shm/fill-%d" % i)
    if error:
        break
    written += 1
print(json.dumps({{"small": small, "written_mib": written, "error": error}}))
"""

HOST = """
import json, socket
print(json.dumps({"host": socket.gethostname()}))
"""


async def open_port() -> McpGatewayTools:
    url, key = os.environ.get(URL_VAR), os.environ.get(KEY_VAR)
    if not url or not key:
        pytest.skip(f"set {URL_VAR} and {KEY_VAR} (run.sh with-gateway exports them)")
    port = McpGatewayTools(url, key)
    await port.refresh()
    return port


@pytest.fixture
async def tools() -> AsyncIterator[McpGatewayTools]:
    port = await open_port()
    try:
        yield port
    finally:
        await port.aclose()


@pytest.fixture
async def other_caller() -> AsyncIterator[McpGatewayTools]:
    """A second, separate gateway session: another caller as far as the dispatcher can tell."""
    port = await open_port()
    try:
        yield port
    finally:
        await port.aclose()


@pytest.fixture(scope="module")
def caller() -> Iterator[str]:
    with unpoliced_caller() as name:
        yield name


async def call(tools: McpGatewayTools, code: str) -> Any:
    """One call of the tool with a fresh key; the raw `ToolResult`."""
    return await tools.call(
        TOOL, {"code": code, "timeout_s": TIMEOUT_S}, idempotency_key=uuid.uuid4().hex
    )


async def run_python(tools: McpGatewayTools, code: str) -> dict[str, Any]:
    """One call that must succeed; the code's last stdout line, parsed."""
    result = await call(tools, code)
    assert result.is_error is False, result
    content = result.content
    assert content["exit_code"] == 0, content
    assert content["timed_out"] is False, content
    parsed: dict[str, Any] = json.loads(content["stdout"].strip().splitlines()[-1])
    return parsed


def warm_pool_ready() -> list[dict[str, Any]]:
    """Wait until the pool is settled: its replicas Ready, no claim left, and every Running pod
    unclaimed and Ready (a pod still torn down after an earlier call is not picked). Its pods."""
    deadline = time.monotonic() + 60
    while True:
        pool = get_json("sandboxwarmpool", APP, "-n", TOOLS_NS)
        want, ready = pool["spec"]["replicas"], pool["status"].get("readyReplicas", 0)
        claims = get_json("sandboxclaims", "-n", TOOLS_NS)["items"]
        warm, running = warm_sandboxes(), pods(TOOLS_NS, APP)
        if ready >= want and not claims and len(warm) == len(running) == want:
            return warm
        assert time.monotonic() < deadline, (
            f"pool not settled after 60 s: {ready}/{want} Ready, {len(claims)} claims, "
            f"{len(warm)} warm of {len(running)} Running"
        )
        time.sleep(1)


def in_platform(name: str, checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Run `checks` in the one pod of the platform app `name` (its container has that name)."""
    return probe(PLATFORM_NS, pod(PLATFORM_NS, name)["metadata"]["name"], name, checks)


def dispatcher_state() -> tuple[str, int]:
    """The dispatcher pod's uid and its container's restart count."""
    found = pod(PLATFORM_NS, DISPATCH)
    (status,) = found["status"]["containerStatuses"]
    return found["metadata"]["uid"], int(status["restartCount"])


# --- runsc, egress, ingress --------------------------------------------------------------------


async def test_code_runs_on_gvisor_with_no_egress(caller: str, tools: McpGatewayTools) -> None:
    """Criterion 8 (partly shown), H28 and H19 for the tool lane: the `SandboxTemplate` and every
    pool pod have `runtimeClassName: gvisor`; the code ran in a pool pod (its hostname) and sees
    gVisor's kernel. The control drops (a timeout) the code's TCP to a literal outside address,
    to LiteLLM, to the dispatcher, and to the API server (node IP:6443 and the Service VIP). The
    allowed controls: in the same call the code connects to its own server on 127.0.0.1:8000;
    and each target is up from an allowed peer: the unpoliced caller reaches the outside address,
    `agent-echo`'s workload reaches LiteLLM, LiteLLM reaches the dispatcher, and the dispatcher
    reaches the API by both addresses."""
    template = get_json("sandboxtemplate", APP, "-n", TOOLS_NS)
    assert template["spec"]["podTemplate"]["spec"]["runtimeClassName"] == "gvisor"
    pool = warm_pool_ready()
    assert pool and all(p["spec"]["runtimeClassName"] == "gvisor" for p in pool), pool

    targets = {
        "outside": tcp(OUTSIDE_IP, 443),
        "litellm": tcp(service_ip(PLATFORM_NS, "litellm"), 4000),
        "dispatch": tcp(pod(PLATFORM_NS, DISPATCH)["status"]["podIP"], 8000),
        "api_node": tcp(node_ip(), API_PORT),
        "api_vip": tcp(service_ip("default", "kubernetes"), 443),
    }
    seen = await run_python(
        tools,
        NETWORK.format(
            outside=OUTSIDE_IP,
            litellm=targets["litellm"]["host"],
            dispatch=targets["dispatch"]["host"],
            node=targets["api_node"]["host"],
            api_port=API_PORT,
            vip=targets["api_vip"]["host"],
        ),
    )
    assert seen["host"] in {p["metadata"]["name"] for p in pool}, seen["host"]
    assert "gvisor" in seen["kernel"], seen
    for name in targets:
        assert seen[name] == POLICY_DROPPED, (name, seen)
    assert seen["own_server"] == "connected", seen

    sidecar = pod(AGENTS_NS, SIDECAR_POD)["metadata"]["name"]
    up = [
        *in_caller(caller, [targets["outside"]]),
        *probe(AGENTS_NS, sidecar, WORKLOAD, [targets["litellm"]]),
        *in_platform("litellm", [targets["dispatch"]]),
        *in_platform(DISPATCH, [targets["api_node"], targets["api_vip"]]),
    ]
    for name, result in zip(targets, up, strict=True):
        assert result.get("connected") is True, (name, result)


def test_only_the_dispatcher_reaches_a_sandbox(caller: str) -> None:
    """Criterion 8 (partly shown), H30 for the tool lane: the live policy on the pool pods admits
    ingress only from the dispatcher, on 8000. The control drops (a timeout) a TCP connection to
    a warm sandbox's pod IP:8000 from LiteLLM (the caller before the per-call layout) and from an
    unpoliced pod in `default`. The allowed control: the dispatcher connects to the same address
    in the same test."""
    policy = get_json("networkpolicy", APP, "-n", TOOLS_NS)["spec"]
    assert policy["podSelector"] == {"matchLabels": {"app.kubernetes.io/name": APP}}, policy
    (rule,) = policy["ingress"]
    (peer,) = rule["from"]
    assert peer["podSelector"] == {"matchLabels": {"app.kubernetes.io/name": DISPATCH}}, peer
    assert peer["namespaceSelector"] == {
        "matchLabels": {"kubernetes.io/metadata.name": PLATFORM_NS}
    }, peer
    assert rule["ports"] == [{"port": 8000, "protocol": "TCP"}], rule
    assert not policy.get("egress"), policy

    target = warm_pool_ready()[0]
    sandbox = tcp(target["status"]["podIP"], 8000)
    (from_dispatch,) = in_platform(DISPATCH, [sandbox])
    (from_litellm,) = in_platform("litellm", [sandbox])
    (from_caller,) = in_caller(caller, [sandbox])
    assert from_dispatch.get("connected") is True, from_dispatch
    assert from_litellm.get("error") == POLICY_DROPPED, from_litellm
    assert from_caller.get("error") == POLICY_DROPPED, from_caller
    still = {p["metadata"]["uid"] for p in warm_sandboxes()}
    assert target["metadata"]["uid"] in still, "the target pod changed during the test"


# --- processes ---------------------------------------------------------------------------------


async def test_process_limit_refuses_forks_past_the_allowance(tools: McpGatewayTools) -> None:
    """Criterion 8 (partly shown), H23 for the tool lane: the control refuses a new process with
    EAGAIN once a call has started about 31 (the uid's tasks at the call's start plus 32,
    `RLIMIT_NPROC`). The allowed control: a call that starts 5 children starts all 5, no
    refusal."""
    under = await run_python(tools, FORKS.format(want=UNDER_LIMIT))
    assert under == {"started": UNDER_LIMIT, "refused": None}, under

    over = await run_python(tools, FORKS.format(want=NPROC_ALLOWANCE * 2))
    assert over["refused"] == "EAGAIN", over
    assert NPROC_ALLOWANCE - 4 <= over["started"] < NPROC_ALLOWANCE, over


async def test_python_child_burst_leaves_the_dispatcher_up(
    tools: McpGatewayTools, other_caller: McpGatewayTools
) -> None:
    """Criterion 8 (partly shown), the security review's HIGH (054 H-16), H23 for the tool lane:
    one call forks about 31 Python children (ordinary code, inside the per-call limits), which
    took the old shared runner to its memory limit and a restart for every caller. The burst
    either returns a result or `sandbox_lost`; it cannot reach anyone else. The allowed controls:
    another caller's call run at the same time works, the next call by that caller (5 children)
    works, and the dispatcher pod is the same pod with the same restart count."""
    pod_uid, restarts = dispatcher_state()
    warm_pool_ready()

    async def concurrent() -> dict[str, Any]:
        await asyncio.sleep(1)  # the burst has its sandbox and is forking
        return await run_python(other_caller, HOST)

    burst, during = await asyncio.gather(call(tools, PY_FORKS.format(want=BURST)), concurrent())
    if burst.is_error:
        assert LOST in json.dumps(burst.content), burst
    else:
        assert {"exit_code", "timed_out"} <= set(burst.content), burst
    assert during["host"].startswith(f"{APP}-"), during

    warm_pool_ready()  # the burst's sandbox is torn down before the next call claims one
    after = await run_python(other_caller, PY_FORKS.format(want=UNDER_LIMIT))
    assert after == {"started": UNDER_LIMIT, "refused": None}, after
    assert dispatcher_state() == (pod_uid, restarts)


# --- nothing survives a call -------------------------------------------------------------------


async def test_files_do_not_cross_calls(tools: McpGatewayTools) -> None:
    """Criterion 8 (partly shown), B12 for the code runner (the security review's file-leak
    MEDIUM): call A writes `/tmp/<name>` and `/dev/shm/<name>`; call B, run after it, sees
    neither, and ran on another host. The allowed control: call A reads both files back inside
    its own call."""
    name = f"poc05-{uuid.uuid4().hex[:8]}"
    first = await run_python(tools, WRITE_FILES.format(name=name))
    assert first["read_back"] == {f"/tmp/{name}": "call-a", f"/dev/shm/{name}": "call-a"}, first

    second = await run_python(tools, LOOK_FILES.format(name=name))
    assert second["exists"] == [False, False], second
    assert second["host"] != first["host"], (first["host"], second["host"])


async def test_dev_shm_is_bounded_by_the_per_call_pod(tools: McpGatewayTools) -> None:
    """Criterion 8 (partly shown), the security review's `/dev/shm` LOW as accepted on
    2026-10-09 (platform-security, owner 055 CH-6): under gVisor the 8Mi cap does not hold
    (`test_dev_shm_is_capped`), so the bound is the per-call pod. A call that writes to
    `/dev/shm` ran in a pool pod whose spec sets memory limit and request to 256Mi. The second
    half, a file call A writes to `/dev/shm` is not seen by call B, is
    `test_files_do_not_cross_calls`. The allowed control: the call reads its file back."""
    pool = {p["metadata"]["name"]: p for p in warm_pool_ready()}
    name = f"poc05-{uuid.uuid4().hex[:8]}"
    seen = await run_python(tools, WRITE_FILES.format(name=name))
    assert seen["read_back"][f"/dev/shm/{name}"] == "call-a", seen
    assert seen["host"] in pool, (seen["host"], sorted(pool))
    (runner,) = pool[seen["host"]]["spec"]["containers"]
    resources = runner["resources"]
    assert resources["limits"]["memory"] == POD_MEMORY, resources
    assert resources["requests"]["memory"] == POD_MEMORY, resources


@pytest.mark.xfail(
    strict=True,
    reason=(
        "2026-10-09 on kind: runsc mounts its own sentry tmpfs at /dev/shm (/proc/mounts "
        "'none /dev/shm tmpfs rw', df 4062256 KiB), not the 8Mi emptyDir; a call wrote 17 MiB "
        "with no error. Accepted 2026-10-09, owner 055 CH-6; the per-call pod's memory limit "
        "bounds it."
    ),
)
async def test_dev_shm_is_capped(tools: McpGatewayTools) -> None:
    """Criterion 8 (partly shown), the security review's `/dev/shm` LOW: `/dev/shm` in a sandbox
    is an 8Mi memory `emptyDir`. The control refuses a write past the cap with ENOSPC after
    about 8 MiB. The allowed control in the same call: a 1 MiB write works."""
    seen = await run_python(tools, SHM_FILL.format(limit=SHM_CAP_MIB * 2))
    assert seen["small"] is None, seen
    assert seen["error"] == "ENOSPC", seen
    assert SHM_CAP_MIB - 2 <= seen["written_mib"] <= SHM_CAP_MIB, seen


def watch_claims() -> subprocess.Popen[str]:
    exe = shutil.which("kubectl")
    assert exe is not None, "kubectl is not on PATH"
    return subprocess.Popen(
        [exe, "--context", CONTEXT, "get", "sandboxclaims", "-n", TOOLS_NS, "--watch",
         "--output-watch-events", "-o", "json"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )  # fmt: skip


def watched_events(text: str) -> list[dict[str, Any]]:
    """The watch's events: JSON objects one after another. A partial last one is dropped."""
    decoder, events, at = json.JSONDecoder(), [], 0
    while (at := len(text) - len(text[at:].lstrip())) < len(text):
        try:
            event, at = decoder.raw_decode(text, at)
        except json.JSONDecodeError:
            break
        events.append(event)
    return events


def gone(kind: str, name: str) -> bool:
    got = kubectl("get", kind, name, "-n", TOOLS_NS, "--ignore-not-found", "-o", "name")
    assert got.returncode == 0, got.stderr
    return got.stdout.strip() == ""


async def test_claim_is_gone_after_the_call(tools: McpGatewayTools) -> None:
    """Criterion 8 (partly shown), the per-call sandbox plan: the claim a call makes, and its
    sandbox pod, are gone within 10 s (`suggested:`) of the call's end. The allowed control: a
    watch on claims during the call saw the claim created and bound to the pod the code ran in
    (its hostname)."""
    warm_pool_ready()
    watch = watch_claims()
    try:
        await asyncio.sleep(2)  # the watch is open
        host = (await run_python(tools, HOST))["host"]
        ended = time.monotonic()
        await asyncio.sleep(1)  # the watch sees the claim's last update
    finally:
        watch.terminate()
        out, _ = watch.communicate(timeout=10)
    events = watched_events(out)
    added = {e["object"]["metadata"]["name"] for e in events if e["type"] == "ADDED"}
    bound = {
        e["object"]["metadata"]["name"]
        for e in events
        if ((e["object"].get("status") or {}).get("sandbox") or {}).get("name") == host
    }
    assert bound and bound <= added, (
        host,
        [(e["type"], e["object"]["metadata"]["name"]) for e in events],
    )

    while left := [c for c in sorted(added) if not gone("sandboxclaim", c)] + (
        [] if gone("pod", host) else [f"pod/{host}"]
    ):
        assert time.monotonic() - ended < GONE_WITHIN_S, f"left after {GONE_WITHIN_S} s: {left}"
        await asyncio.sleep(0.5)
