"""Poll for a condition; fail the test on timeout, never return quietly.

`wait_until` is the one poll loop. The rest are thin wrappers over it, named for what they wait
on: an HTTP status (`wait_ready`, `wait_ready_async`) and a child process that serves a path
(`wait_until_serving`). The async twin is `chassis_contracts.events.wait_until`, in
`(condition, label, timeout_s)` order.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest
from chassis_contracts import events

POLL_S = 0.02
PROBE_TIMEOUT_S = 1.0
"""One probe request's own timeout, so a hung listener cannot outlast the wait's deadline."""
REAP_TIMEOUT_S = 5.0


def wait_until(
    condition: Callable[[], bool],
    label: str,
    timeout_s: float,
    *,
    interval: float = POLL_S,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Block until `condition()` is true. On timeout, fail with `timeout_s` and `label`.

    `clock` and `sleep` are for a test of this helper; callers keep the defaults."""
    deadline = clock() + timeout_s
    while not condition():
        if clock() > deadline:
            pytest.fail(f"timed out after {timeout_s} s waiting for {label}")
        sleep(interval)


async def wait_until_async(
    condition: Callable[[], bool | Awaitable[bool]],
    label: str,
    timeout_s: float,
    *,
    interval: float = POLL_S,
) -> None:
    """`wait_until` on the event loop. `condition` may be sync or async."""
    await events.wait_until(condition, timeout_s, label, interval_s=interval)


def _status_label(client: httpx.Client | httpx.AsyncClient, path: str, status: int) -> str:
    return f"{client.base_url.join(path)} to answer {status}"


def _last_seen(response: httpx.Response | None) -> str:
    return "no answer" if response is None else f"last answer {response.status_code}"


def wait_ready(
    client: httpx.Client,
    timeout_s: float,
    *,
    path: str = "/ready",
    status: int = 200,
    interval: float = POLL_S,
) -> httpx.Response:
    """Poll `GET path` until it answers `status`; an HTTP error counts as not yet. Returns the
    answer. On timeout, fail with the label and the last answer seen."""
    seen: list[httpx.Response] = []

    def answers() -> bool:
        try:
            seen.append(client.get(path))
        except httpx.HTTPError:
            return False
        return seen[-1].status_code == status

    try:
        wait_until(answers, _status_label(client, path, status), timeout_s, interval=interval)
    except pytest.fail.Exception as failed:
        pytest.fail(f"{failed.msg}; {_last_seen(seen[-1] if seen else None)}")
    return seen[-1]


async def wait_ready_async(
    client: httpx.AsyncClient,
    timeout_s: float,
    *,
    path: str = "/ready",
    status: int = 200,
    interval: float = POLL_S,
) -> httpx.Response:
    """`wait_ready` on the event loop."""
    seen: list[httpx.Response] = []

    async def answers() -> bool:
        try:
            seen.append(await client.get(path))
        except httpx.HTTPError:
            return False
        return seen[-1].status_code == status

    label = _status_label(client, path, status)
    try:
        await wait_until_async(answers, label, timeout_s, interval=interval)
    except pytest.fail.Exception as failed:
        pytest.fail(f"{failed.msg}; {_last_seen(seen[-1] if seen else None)}")
    return seen[-1]


def _serves(probe: httpx.Client, path: str) -> bool:
    try:
        return probe.get(path, timeout=PROBE_TIMEOUT_S).status_code == 200
    except httpx.HTTPError:
        return False


def wait_until_serving(
    proc: subprocess.Popen[Any],
    probe: httpx.Client,
    path: str,
    timeout_s: float,
    *,
    failure: str,
    interval: float = POLL_S,
) -> None:
    """Poll `GET path` on `probe` until it answers 200. If `proc` exits first or the time runs
    out, kill it and raise `RuntimeError(failure)` with its output."""
    try:
        wait_until(
            lambda: proc.poll() is not None or _serves(probe, path),
            f"{failure}: {path} to answer 200",
            timeout_s,
            interval=interval,
        )
    except pytest.fail.Exception:
        pass
    else:
        if proc.poll() is None:
            return
    proc.kill()
    raise RuntimeError(f"{failure}:\n{proc.communicate(timeout=REAP_TIMEOUT_S)[0]}")
