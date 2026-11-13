"""The stateless check (057 H-19), in process: no agent keeps data on disk or in memory between
calls.

Two checks, each failing with a `StatelessError` that names the container:

- **Disk.** Every call runs under a `WriteGuard`: a process-wide audit hook (`sys.addaudithook`)
  refuses each write to the file system, as a read-only root file system with no writable volume
  would, and records the path. A call that wrote anything fails, even when the agent caught the
  `OSError`. Writes from any thread of the process count. Bytecode caches (`__pycache__`) and
  `/dev/null`, `/dev/stdout`, `/dev/stderr` are not writes to the root.
- **Kept data.** The same request set runs once on fresh pods (a new pod per request) and once
  on one reused pod, each in its own shuffled order. Outputs must match per request, and no
  plain-data module global of the agent's packages may move on the reused pod. In process, a pod
  is a fresh import of the agent's packages (`Pods`); `sys.modules` is put back afterwards.

What it does not cover: the chassis container and the real read-only root, which need the
process run in the `sidecar` lane; state kept in an object whose `repr` does not change.
The PoC-4 scenario this starts from: `pocs/poc-04-stateless-scalable/tests/test_hidden_state.py`.
"""

from __future__ import annotations

import collections
import errno
import importlib
import os
import random
import sys
from collections.abc import AsyncIterator, Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType, TracebackType
from typing import Any

WireHandle = Callable[[dict[str, Any], dict[str, Any]], AsyncIterator[dict[str, Any]]]
"""`handle(input, ctx)`: plain dicts in, event dicts out (the wire form of the contract)."""

WORKLOAD = "workload"
"""The default container name: the agent's own container in the pod."""
NOT_THE_ROOT = frozenset({"/dev/null", "/dev/stdout", "/dev/stderr"})
"""Paths a container may open for writing on a read-only root."""
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
PATH_EVENTS = {
    "os.mkdir": (0,),
    "os.rmdir": (0,),
    "os.remove": (0,),
    "os.rename": (0, 1),
    "os.replace": (0, 1),
    "os.symlink": (1,),
    "os.link": (1,),
    "os.truncate": (0,),
    "os.chmod": (0,),
    "os.chown": (0,),
    "os.utime": (0,),
    "sqlite3.connect": (0,),
}
"""Audit events that change the file system, and the positions of their path arguments."""
KEPT_TYPES = (dict, list, set, bytearray, int, float, str, bytes, tuple, collections.deque)
"""Module globals of these types are compared before and after the reused pod's calls."""
DEFAULT_SEED = 0


class StatelessError(AssertionError):
    """An agent kept data between calls; the message names the container and what it kept."""


@dataclass(frozen=True)
class Write:
    """One refused write: the container, the audit event, and the path."""

    container: str
    event: str
    path: str

    def __str__(self) -> str:
        return f"container {self.container!r} wrote {self.path} ({self.event})"


class _Slot:
    """The guard that is on, if any. Process-wide, so a write from any thread is seen."""

    guard: WriteGuard | None = None
    hooked: bool = False


def _as_path(value: Any) -> str | None:
    if isinstance(value, int) or value is None:
        return None  # a file descriptor: its open was audited already
    try:
        path = os.fsdecode(os.fspath(value))
    except TypeError:
        return None
    return str(Path(path).absolute()) if path != ":memory:" else None


def _written(event: str, args: tuple[Any, ...]) -> list[str]:
    """The paths an audit event writes to; `[]` when it writes nothing."""
    if event == "open":
        flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
        path = _as_path(args[0]) if flags & WRITE_FLAGS else None
        return [path] if path else []
    paths = (_as_path(args[i]) for i in PATH_EVENTS.get(event, ()) if i < len(args))
    return [path for path in paths if path]


def _counts(path: str) -> bool:
    return path not in NOT_THE_ROOT and "__pycache__" not in Path(path).parts


def _audit(event: str, args: tuple[Any, ...]) -> None:
    guard = _Slot.guard
    if guard is None or (event != "open" and event not in PATH_EVENTS):
        return
    paths = [path for path in _written(event, args) if _counts(path)]
    if paths:
        guard.refuse(event, paths)


class WriteGuard:
    """Refuse and record every file system write while on, as a read-only root would.

    `with WriteGuard("workload") as guard: ...`; then `guard.writes`. One guard at a time.
    """

    def __init__(self, container: str = WORKLOAD) -> None:
        self.container = container
        self.writes: list[Write] = []

    def refuse(self, event: str, paths: Sequence[str]) -> None:
        self.writes.extend(Write(self.container, event, path) for path in paths)
        message = f"Read-only file system (stateless check, container {self.container!r})"
        raise OSError(errno.EROFS, message, paths[0])

    def __enter__(self) -> WriteGuard:
        if _Slot.guard is not None:
            raise RuntimeError("a WriteGuard is already on")
        if not _Slot.hooked:
            sys.addaudithook(_audit)
            _Slot.hooked = True
        _Slot.guard = self
        return self

    def __exit__(
        self, kind: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        _Slot.guard = None


def _disk_error(writes: Sequence[Write]) -> StatelessError:
    listed = "; ".join(str(write) for write in writes)
    return StatelessError(
        f"stateless check, disk: {listed}. The root file system is read-only and the pod has "
        "no writable volume: keep data behind the chassis (StatePort), not on local disk."
    )


async def run_call(
    handle: WireHandle,
    input: dict[str, Any],
    ctx: dict[str, Any],
    *,
    container: str = WORKLOAD,
) -> list[dict[str, Any]]:
    """Run one call under a `WriteGuard` and return its events. A write fails it."""
    guard = WriteGuard(container)
    try:
        with guard:
            events = [event async for event in handle(input, ctx)]
    except Exception as exc:
        if guard.writes:
            raise _disk_error(guard.writes) from exc
        raise
    if guard.writes:
        raise _disk_error(guard.writes)
    return events


@dataclass(frozen=True)
class Pods:
    """How to start a pod in process: a fresh import of the agent's packages.

    - `handle`: `module:attribute`, the agent's `handle`.
    - `packages`: the top-level packages that are the agent's own code (default: the handle's).
    - `setup`: runs after each fresh import, before the handle is taken; it points the fresh
      modules at the fake model adapter (an ASGI transport, a tool stub).
    """

    handle: str
    packages: tuple[str, ...] = ()
    setup: Callable[[], None] | None = None
    container: str = WORKLOAD

    @property
    def own(self) -> tuple[str, ...]:
        return self.packages or (self.handle.split(":")[0].split(".")[0],)

    def start(self) -> WireHandle:
        """A fresh pod: the agent's modules imported anew, so no module global carries over."""
        for name in [name for name in sys.modules if _top(name) in self.own]:
            del sys.modules[name]
        module_name, _, attr = self.handle.partition(":")
        module = importlib.import_module(module_name)
        if self.setup is not None:
            self.setup()
        handle: WireHandle = getattr(module, attr or "handle")
        return handle


def _top(name: str) -> str:
    return name.split(".")[0]


@contextmanager
def _modules_put_back(packages: Sequence[str]) -> Iterator[None]:
    """Put the packages' entries of `sys.modules` back as they were, once the pods are done."""
    saved = {name: m for name, m in sys.modules.items() if _top(name) in packages}
    try:
        yield
    finally:
        for name in [name for name in sys.modules if _top(name) in packages]:
            del sys.modules[name]
        sys.modules.update(saved)


def module_globals(packages: Sequence[str]) -> dict[str, str]:
    """`module.name -> repr(value)` for each plain-data global of the packages' loaded modules."""
    seen: dict[str, str] = {}
    loaded: list[tuple[str, ModuleType]] = [
        (name, module) for name, module in list(sys.modules.items()) if module is not None
    ]
    for name, module in loaded:
        if _top(name) not in packages:
            continue
        for attr, value in vars(module).items():
            if attr.startswith("__") or isinstance(value, bool):
                continue
            if isinstance(value, KEPT_TYPES):
                seen[f"{name}.{attr}"] = repr(value)
    return seen


def default_ctx(index: int) -> dict[str, Any]:
    """The `ctx` of request `index`: the same on the fresh and the reused pod."""
    return {
        "request_id": f"stateless-{index}",
        "trace_id": f"stateless-trace-{index}",
        "idempotency_key": f"stateless-{index}",
    }


@dataclass(frozen=True)
class KeptDataReport:
    """What the kept-data check ran: each order, and each request's events."""

    fresh_order: tuple[int, ...]
    reused_order: tuple[int, ...]
    outputs: tuple[tuple[dict[str, Any], ...], ...] = field(default=())


def _kept_error(
    container: str,
    requests: Sequence[dict[str, Any]],
    reused_order: Sequence[int],
    differed: Sequence[int],
    moved: Mapping[str, tuple[str | None, str | None]],
) -> StatelessError:
    lines = [f"stateless check, kept data: container {container!r} kept data between calls."]
    for index in differed:
        before = list(reused_order[: reused_order.index(index)])
        lines.append(
            f"call {index} {requests[index]!r} differed on the reused pod (after calls "
            f"{before}) from a fresh pod"
        )
    lines.extend(
        f"module global {name} moved: {old} -> {new}" for name, (old, new) in moved.items()
    )
    lines.append("Keep what must outlive a call behind the chassis (StatePort).")
    return StatelessError("\n".join(lines))


async def check_kept_data(
    pods: Pods,
    requests: Sequence[dict[str, Any]],
    *,
    seed: int = DEFAULT_SEED,
    ctx_for: Callable[[int], dict[str, Any]] = default_ctx,
    ignore_globals: frozenset[str] = frozenset(),
) -> KeptDataReport:
    """Run `requests` on fresh pods and on one reused pod, in shuffled orders, each call under
    the disk check. Fails when an output differs or a module global moves on the reused pod."""
    rng = random.Random(seed)
    fresh_order = rng.sample(range(len(requests)), len(requests))
    reused_order = rng.sample(range(len(requests)), len(requests))
    fresh: dict[int, list[dict[str, Any]]] = {}
    reused: dict[int, list[dict[str, Any]]] = {}
    with _modules_put_back(pods.own):
        for index in fresh_order:
            handle = pods.start()
            fresh[index] = await run_call(
                handle, requests[index], ctx_for(index), container=pods.container
            )
        handle = pods.start()
        before = module_globals(pods.own)
        for index in reused_order:
            reused[index] = await run_call(
                handle, requests[index], ctx_for(index), container=pods.container
            )
        after = module_globals(pods.own)
    differed = [index for index in reused_order if reused[index] != fresh[index]]
    moved = {
        name: (before.get(name), after.get(name))
        for name in sorted(before.keys() | after.keys())
        if before.get(name) != after.get(name) and name not in ignore_globals
    }
    if differed or moved:
        raise _kept_error(pods.container, requests, reused_order, differed, moved)
    outputs = tuple(tuple(fresh[index]) for index in range(len(requests)))
    return KeptDataReport(tuple(fresh_order), tuple(reused_order), outputs)
