"""PoC-5 on kind: `/dev/shm` in the remote pod `remote-echo`, under gVisor (security review
2026-10-09, the `/dev/shm` LOW; owner 055 CH-6).

Exit criterion 8, partly shown: the remote workload cannot fill memory past its bounds. The code
runner's half is `test_poc05_kind_code_runner.py` (`test_dev_shm_is_capped`, strict xfail, and
`test_dev_shm_is_bounded_by_the_per_call_pod`). Here the same question for the remote lane.

- The control: a 1 MiB write to `/dev/shm` works and reads back.
- A 32Mi cap (`suggested:`, the size of the remote's `/tmp` emptyDir) would refuse a write past it
  with ENOSPC. `remote-echo.yaml` mounts no `/dev/shm` volume, and runsc mounts its own sentry
  tmpfs there anyway, as the code runner showed. So the cap does not hold: strict xfail, accepted
  2026-10-09 with the code runner's case (055 CH-6).
- The bound that holds: the workload container's memory limit is set (256Mi), and the pod cgroup
  on the node has that `memory.max`, so a `/dev/shm` fill ends at the pod's limit.

Every write is checked from outside the pod: `kubectl exec` of echo-python's own Python (T10 is
dropped). The fill stops at 40 MiB and removes its files on every path. A kind test: marked
`network`, skipped unless `POC05_KIND=1` (`poc05_conftest.py`). Run:
`POC05_KIND=1 uv run pytest -m network <this file>`.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from poc05_kind import REMOTE_NS, WORKLOAD, kubectl, node_sh, pod

REMOTE = "remote-echo"
SHM_CAP_MIB = 32  # suggested: the remote's /tmp sizeLimit; no /dev/shm cap is set today
FILL_MIB = 40  # past the cap, well under the 256Mi pod limit (remote-echo idles near 113 MiB)
POD_MEMORY = "256Mi"
POD_MEMORY_BYTES = 256 * 1024 * 1024

# Writes one 1 MiB file (the control), then up to `limit` more 1 MiB files, stops at the first
# error, and removes every file it wrote. Prints one JSON line.
SHM_FILL = """
import errno, json, os
chunk = b"x" * (1 << 20)
tag = "poc05-shm-%d" % os.getpid()
made = []
def put(name):
    path = "/dev/shm/%s-%s" % (tag, name)
    try:
        with open(path, "wb") as f:
            made.append(path)
            f.write(chunk)
        return None
    except OSError as e:
        return errno.errorcode.get(e.errno, str(e.errno))
try:
    small = put("small")
    back = len(open(made[0], "rb").read()) if small is None else 0
    written, error = 0, None
    for i in range({limit}):
        error = put("fill-%d" % i)
        if error:
            break
        written += 1
finally:
    for path in made:
        try:
            os.remove(path)
        except OSError:
            pass
left = [n for n in os.listdir("/dev/shm") if n.startswith(tag)]
print(json.dumps({{"small": small, "read_back": back, "written_mib": written, "error": error,
                   "left": left}}))
"""


@pytest.fixture(scope="module")
def remote() -> dict[str, Any]:
    return pod(REMOTE_NS, REMOTE)


def shm_fill(remote: dict[str, Any], limit: int) -> dict[str, Any]:
    got = kubectl(
        "exec", "-n", REMOTE_NS, remote["metadata"]["name"], "-c", WORKLOAD, "--",
        "python", "-c", SHM_FILL.format(limit=limit),
    )  # fmt: skip
    assert got.returncode == 0, f"exec in {REMOTE} failed: {got.stderr[-400:]}"
    seen: dict[str, Any] = json.loads(got.stdout.strip().splitlines()[-1])
    return seen


def test_dev_shm_takes_a_small_write(remote: dict[str, Any]) -> None:
    """Criterion 8 (partly shown), the control: the remote's `/dev/shm` takes a 1 MiB write that
    reads back, and nothing is left behind."""
    assert remote["spec"]["runtimeClassName"] == "gvisor"
    seen = shm_fill(remote, 0)
    assert seen["small"] is None, seen
    assert seen["read_back"] == 1 << 20, seen
    assert seen["left"] == [], seen


@pytest.mark.xfail(
    strict=True,
    reason=(
        "runsc mounts its own sentry tmpfs at /dev/shm, and remote-echo.yaml sets no /dev/shm "
        "volume; the code runner on 2026-10-09 wrote 17 MiB past its 8Mi emptyDir with no error. "
        "Accepted 2026-10-09, owner 055 CH-6; the pod's memory limit bounds it."
    ),
)
def test_dev_shm_is_capped_at_32mi(remote: dict[str, Any]) -> None:
    """Criterion 8 (partly shown), the `/dev/shm` LOW for the remote lane: a write past 32 MiB
    fails with ENOSPC. The allowed control in the same call: a 1 MiB write works."""
    seen = shm_fill(remote, FILL_MIB)
    assert seen["small"] is None, seen
    assert seen["left"] == [], seen
    assert seen["error"] == "ENOSPC", seen
    assert SHM_CAP_MIB - 2 <= seen["written_mib"] <= SHM_CAP_MIB, seen


def test_remote_pod_memory_limit_bounds_dev_shm(remote: dict[str, Any]) -> None:
    """Criterion 8 (partly shown), the bound that holds: the workload container's memory limit is
    256Mi, and the pod cgroup on the node enforces it (`memory.max`). The control: the pod's
    `memory.current` is above zero and under that limit, so the sandbox runs inside it."""
    (workload,) = [c for c in remote["spec"]["containers"] if c["name"] == WORKLOAD]
    assert workload["resources"]["limits"]["memory"] == POD_MEMORY, workload["resources"]

    uid = remote["metadata"]["uid"].replace("-", "_")
    out = node_sh(
        "d=$(find /sys/fs/cgroup/kubelet.slice/kubelet-kubepods.slice -maxdepth 2 -type d "
        f'-name \'*-pod{uid}.slice\'); cat "$d/memory.max" "$d/memory.current"'
    )
    memory_max, memory_current = out.split()
    assert int(memory_max) == POD_MEMORY_BYTES, out
    assert 0 < int(memory_current) < int(memory_max), out
