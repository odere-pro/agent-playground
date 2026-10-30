"""Poll for a condition; fail the test on timeout, never return quietly.

The async twin is `chassis_contracts.events.wait_until`, in `(condition, label, timeout_s)` order.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

import pytest
from chassis_contracts import events

POLL_S = 0.02


def wait_until(
    condition: Callable[[], bool], label: str, timeout_s: float, *, interval: float = POLL_S
) -> None:
    """Block until `condition()` is true. On timeout, fail with `timeout_s` and `label`."""
    deadline = time.monotonic() + timeout_s
    while not condition():
        if time.monotonic() > deadline:
            pytest.fail(f"timed out after {timeout_s} s waiting for {label}")
        time.sleep(interval)


async def wait_until_async(
    condition: Callable[[], bool | Awaitable[bool]],
    label: str,
    timeout_s: float,
    *,
    interval: float = POLL_S,
) -> None:
    """`wait_until` on the event loop. `condition` may be sync or async."""
    await events.wait_until(condition, timeout_s, label, interval_s=interval)
