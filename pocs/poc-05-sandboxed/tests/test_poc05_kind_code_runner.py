"""PoC-5 on kind: the code runner's sandbox (T22; plan sections 2.8, 2.10, 2.11; threat model H19,
H23, H28 for the tool lane).

Exit criterion 8, partly shown: the code-execution tool runs on gVisor with no egress, and a call
cannot start processes past its per-call limit. Every check uses the code runner's own API, the
`code_runner-run_python` tool through LiteLLM's MCP gateway with the chassis's key
(`McpGatewayTools`, the same adapter the chassis uses), with ordinary standard-library code. Each
refusal sits next to its allowed control in the same call.

- runsc: the Sandbox pod has `runtimeClassName: gvisor`, and the code's own `/proc/version` is
  gVisor's.
- No egress (`egress: []`): the control refuses the code's TCP connection to a literal outside
  address and to LiteLLM; the allowed control is the code's connection to its own server on
  127.0.0.1:8000, so sockets work in the sandbox and the refusal is the network's.
- The process limit (`RLIMIT_NPROC`, the uid's task count at the call's start plus
  `NPROC_ALLOWANCE`, 32): forking children that wait on a pipe, the control refuses a new process
  with EAGAIN after about 31 (bring-up note, item 8); a call that starts 5 works. The code stops
  at the first refusal and lets every child exit; the server's sweep kills anything left.
  The pod's own pids cap is never reached (31 children, about 85 guest processes allowed).
  Each child execs `/bin/cat` on the pipe: a forked Python child costs 5 to 8 MiB under gVisor,
  and 31 of them hit the pod's 256Mi limit, where each fork takes seconds (bring-up note,
  2026-10-09).

A kind test: marked `network`, skipped unless `POC05_KIND=1` (`poc05_conftest.py`). It needs the
gateway URL and the chassis's key in the environment. Run:
`POC05_KIND=1 deploy/kind/poc05/run.sh with-gateway uv run pytest -m network <this file>`.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from chassis.adapters.mcp.gateway import McpGatewayTools
from poc05_kind import PLATFORM_NS, TOOLS_NS, pod, service_ip

URL_VAR = "POC05_GATEWAY_MCP_URL"
KEY_VAR = "POC05_CHASSIS_VIRTUAL_KEY"
TOOL = "code_runner-run_python"
NPROC_ALLOWANCE = 32  # code_runner.isolation.NPROC_ALLOWANCE
UNDER_LIMIT = 5
OUTSIDE_IP = "1.1.1.1"
TIMEOUT_S = 10  # the tool's ceiling (code_runner Limits.max_timeout_s)

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

NETWORK = """
import json, socket
def tcp(host, port):
    try:
        socket.create_connection((host, port), timeout=3).close()
        return "connected"
    except OSError as e:
        return type(e).__name__
print(json.dumps({{"outside": tcp("{outside}", 443), "litellm": tcp("{litellm}", 4000),
                  "own_server": tcp("127.0.0.1", 8000),
                  "kernel": open("/proc/version").read()}}))
"""


@pytest.fixture
async def tools() -> AsyncIterator[McpGatewayTools]:
    url, key = os.environ.get(URL_VAR), os.environ.get(KEY_VAR)
    if not url or not key:
        pytest.skip(f"set {URL_VAR} and {KEY_VAR} (run.sh with-gateway exports them)")
    port = McpGatewayTools(url, key)
    await port.refresh()
    try:
        yield port
    finally:
        await port.aclose()


async def run_python(tools: McpGatewayTools, code: str) -> dict[str, Any]:
    """One call of the tool with a fresh key; the code's last stdout line, parsed."""
    result = await tools.call(
        TOOL, {"code": code, "timeout_s": TIMEOUT_S}, idempotency_key=uuid.uuid4().hex
    )
    assert result.is_error is False, result
    content = result.content
    assert content["exit_code"] == 0, content
    assert content["timed_out"] is False, content
    parsed: dict[str, Any] = json.loads(content["stdout"].strip().splitlines()[-1])
    return parsed


async def test_code_runs_on_gvisor_with_no_egress(tools: McpGatewayTools) -> None:
    """Criterion 8 (partly shown), H28 and H19 for the tool lane: the code runner's pod has
    `runtimeClassName: gvisor` and the code sees gVisor's kernel; the control refuses the code's
    TCP connection to a literal outside address and to LiteLLM. The allowed control in the same
    call: the code connects to its own server on 127.0.0.1:8000."""
    runner = pod(TOOLS_NS, "code-runner")
    assert runner["spec"]["runtimeClassName"] == "gvisor"

    litellm = service_ip(PLATFORM_NS, "litellm")
    seen = await run_python(tools, NETWORK.format(outside=OUTSIDE_IP, litellm=litellm))
    assert "gvisor" in seen["kernel"], seen
    assert seen["outside"] in {"TimeoutError", "ConnectionRefusedError", "OSError"}, seen
    assert seen["litellm"] in {"TimeoutError", "ConnectionRefusedError", "OSError"}, seen
    assert seen["own_server"] == "connected", seen


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
