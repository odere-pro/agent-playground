"""The stateless check (057 H-19) as processes, in the `sidecar` lane: a pod is a workload process
and a chassis process (`fake` profile), each with a read-only root (`readonly_root`).

`check_lane(pods, requests)` runs the requests once on fresh pods (a new pod per request) and once
on one reused pod, each in its own shuffled order, through the chassis's `/v1/run`. It fails with
a `StatelessError` when:

- **disk:** either process wrote to the file system; the message names the container and the path;
- **scratch:** `scratch_var` names a writable volume (the pod's `/tmp` emptyDir, which a CLI such as
  Claude Code needs for its `HOME`) and a file is still in it once a call is done;
- **kept data:** a request's `status` and `output` on the reused pod differ from a fresh pod; the
  message names the call and the calls before it. The reused pod reuses both containers.

The in-process check (`agent_testkit.stateless`) adds the module-global comparison; this one adds
the chassis container, real processes, and a CLI the workload starts.
"""

from __future__ import annotations

import random
import shutil
import tempfile
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_testkit.processes import (
    WORKLOAD_START_S,
    Chassis,
    ModelScript,
    start_chassis,
    start_workload,
)
from agent_testkit.settings import AgentRef, ChassisSource
from agent_testkit.stateless import DEFAULT_SEED, StatelessError, disk_error

CONTAINERS = ("workload", "chassis")
"""The two containers of a `sidecar` pod, both with a read-only root here."""
SCRATCH_SETTLE_S = 2.0
"""suggested: how long a workload may take, after its answer, to empty its scratch volume."""
POLL_S = 0.05


class ScratchLeftover(StatelessError):
    """A file stayed in the workload's scratch volume after a call. Its own type, so a known gap
    can be marked `xfail(raises=ScratchLeftover)` while every other finding still fails."""


@dataclass(frozen=True)
class LanePods:
    """How to start a `sidecar` pod: the agent, the chassis source, and the fake model's rules.

    - `env`: variables added to the workload process.
    - `scratch_var`: a variable the workload reads for its writable volume. Each pod gets a fresh
      empty folder there (and in `TMPDIR`), writes under it are allowed, and it must be empty
      after each call.
    - `defer_scratch`: a leftover in the scratch volume is removed and noted, and the check goes
      on; `ScratchLeftover` is raised only once the disk and kept-data checks have all passed.
    """

    ref: AgentRef | str
    source: ChassisSource
    model_script: ModelScript | None = None
    env: Mapping[str, str] | None = None
    scratch_var: str | None = None
    start_s: float = WORKLOAD_START_S
    defer_scratch: bool = False


@dataclass(frozen=True)
class LaneReport:
    """What the lane check ran: each order, each request's `status` and `output`, the containers
    that had a read-only root."""

    fresh_order: tuple[int, ...]
    reused_order: tuple[int, ...]
    outputs: tuple[dict[str, Any], ...]
    read_only: tuple[str, ...] = CONTAINERS


@dataclass(frozen=True)
class _Pod:
    chassis: Chassis
    scratch: Path | None
    scratch_var: str | None
    deferred: list[str] | None = None
    """Where a leftover's message goes instead of being raised (`defer_scratch`)."""

    def call(self, request: Mapping[str, Any], index: int) -> dict[str, Any]:
        """One `/v1/run`, then the disk and scratch checks. The comparable part of the answer."""
        envelope = self.chassis.run(request)
        self.check_disk()
        self.check_scratch(index)
        return {"status": envelope.get("status"), "output": envelope.get("output")}

    def check_disk(self) -> None:
        writes = [
            *self.chassis.workload.process.refused_writes(),
            *self.chassis.process.refused_writes(),
        ]
        if writes:
            raise disk_error(writes)

    def check_scratch(self, index: int) -> None:
        if self.scratch is None:
            return
        deadline = time.monotonic() + SCRATCH_SETTLE_S
        left = _files(self.scratch)
        while left and time.monotonic() < deadline:
            time.sleep(POLL_S)
            left = _files(self.scratch)
        if not left:
            return
        message = (
            f"stateless check, disk: container 'workload' left {', '.join(left)} in its "
            f"scratch volume (${self.scratch_var}) after call {index}. A writable volume "
            "holds only what one call needs; remove it before the call ends."
        )
        if self.deferred is None:
            raise ScratchLeftover(message)
        self.deferred.append(message)
        for child in self.scratch.iterdir():  # so the next call starts from an empty volume
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)


def _files(folder: Path) -> list[str]:
    return sorted(str(path) for path in folder.rglob("*"))


@contextmanager
def _pod(pods: LanePods, deferred: list[str] | None = None) -> Iterator[_Pod]:
    scratch = Path(tempfile.mkdtemp(prefix="atk-scratch-")) if pods.scratch_var else None
    env = dict(pods.env or {})
    if pods.scratch_var and scratch is not None:
        # In the pod the volume is /tmp, so a temp file lands in it too and must go as well.
        env.update({pods.scratch_var: str(scratch), "TMPDIR": str(scratch)})
    allow = (str(scratch),) if scratch is not None else ()
    try:
        with (
            start_workload(
                pods.ref, env=env, timeout_s=pods.start_s, read_only_root=True, allow=allow
            ) as workload,
            start_chassis(
                workload, pods.source, model_script=pods.model_script, read_only_root=True
            ) as chassis,
        ):
            pod = _Pod(chassis, scratch, pods.scratch_var, deferred)
            pod.check_disk()  # a write while starting counts too
            yield pod
            # A write while stopping (an atexit handler, a flush on SIGTERM) counts too: stop
            # both, then read their logs before the kit removes them with the socket folders.
            chassis.process.stop()
            workload.process.stop()
            pod.check_disk()
    finally:
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)


def _kept_error(
    requests: Sequence[Mapping[str, Any]],
    reused_order: Sequence[int],
    differed: Sequence[int],
    fresh: Mapping[int, dict[str, Any]],
    reused: Mapping[int, dict[str, Any]],
) -> StatelessError:
    lines = ["stateless check, kept data: the reused pod (workload and chassis) kept data."]
    for index in differed:
        before = list(reused_order[: reused_order.index(index)])
        lines.append(
            f"call {index} {dict(requests[index])!r} differed on the reused pod (after calls "
            f"{before}) from a fresh pod: {reused[index]!r} != {fresh[index]!r}"
        )
    lines.append("Keep what must outlive a call behind the chassis (StatePort).")
    return StatelessError("\n".join(lines))


def check_lane(
    pods: LanePods, requests: Sequence[Mapping[str, Any]], *, seed: int = DEFAULT_SEED
) -> LaneReport:
    """Run `requests` (each a `/v1/run` input) on fresh pods and on one reused pod, in shuffled
    orders, with a read-only root on both containers. See the module docstring for the checks."""
    rng = random.Random(seed)
    fresh_order = rng.sample(range(len(requests)), len(requests))
    reused_order = rng.sample(range(len(requests)), len(requests))
    fresh: dict[int, dict[str, Any]] = {}
    reused: dict[int, dict[str, Any]] = {}
    deferred: list[str] | None = [] if pods.defer_scratch else None
    for index in fresh_order:
        with _pod(pods, deferred) as pod:
            fresh[index] = pod.call(requests[index], index)
    with _pod(pods, deferred) as pod:
        for index in reused_order:
            reused[index] = pod.call(requests[index], index)
    differed = [index for index in reused_order if reused[index] != fresh[index]]
    if differed:
        raise _kept_error(requests, reused_order, differed, fresh, reused)
    if deferred:
        raise ScratchLeftover("\n".join(deferred))
    outputs = tuple(fresh[index] for index in range(len(requests)))
    return LaneReport(tuple(fresh_order), tuple(reused_order), outputs)
