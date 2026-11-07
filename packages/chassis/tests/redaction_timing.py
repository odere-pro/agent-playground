"""Time checks for `chassis.redaction` that hold on a loaded machine (baseline B-1).

A wall-clock bound fails when other gates load the Mac: one case took 1.4 s of wall time at a load
average of 24. So the checks measure CPU time, and they check growth, not speed. The clock is
`time.thread_time`, the calling thread's CPU only: other processes and any other thread in the
pytest process add nothing to it. Catastrophic backtracking still fails them: it costs seconds
to minutes of CPU on these inputs, and it grows much faster than the input.
"""

from __future__ import annotations

import time
from collections.abc import Callable

TARGET_SECONDS = 0.1
"""suggested: the target for one `redact` call on a 100k to 200k-character text on an idle
machine. Documented, not asserted (baseline B-1)."""

CPU_CAP_SECONDS = 2.0
"""The asserted CPU time for one call on the doubled text: 20 times the target, far above what a
loaded machine adds and far below what backtracking costs."""

GROWTH_CAP = 3.0
"""Doubling the input may at most about triple the CPU time. Quadratic growth is 4 times."""

GROWTH_FLOOR_SECONDS = 0.05
"""Slack for timer noise, so a call of a few milliseconds cannot fail the growth check."""


def cpu_seconds(run: Callable[[], object]) -> float:
    """The CPU time `run` takes on this thread."""
    start = time.thread_time()
    run()
    return time.thread_time() - start


def assert_linear(redact: Callable[[str], str], build: Callable[[int], str], count: int) -> None:
    """`redact` on `build(2 * count)` stays under the CPU cap and costs at most about three
    times `redact` on `build(count)`."""
    short, long = build(count), build(2 * count)
    once = cpu_seconds(lambda: redact(short))
    twice = cpu_seconds(lambda: redact(long))

    assert twice < CPU_CAP_SECONDS, f"{twice:.3f} s of CPU for the doubled text"
    assert twice < GROWTH_CAP * once + GROWTH_FLOOR_SECONDS, (
        f"doubling the text took {twice:.3f} s of CPU after {once:.3f} s"
    )
