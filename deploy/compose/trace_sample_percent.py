"""`trace_sample_rate` to the OTel Collector's sampling percent (014 H-7, ADR-007 item 8).

The epic's `observability.trace_sample_rate` is a share from 0 to 1. The Collector's
`tail_sampling` `probabilistic` policy takes `sampling_percentage`, from 0 to 100, and its config
cannot multiply. This is the one conversion. Each `stack.sh` runs it and exports the result as
`TRACE_SAMPLE_PERCENT`, which Compose passes to the Collector and the config reads as
`${env:TRACE_SAMPLE_PERCENT}`.

    TRACE_SAMPLE_RATE=0.1 python3 -I deploy/compose/trace_sample_percent.py   # prints 10

The rate comes from `TRACE_SAMPLE_RATE`, else `DEFAULT_RATE`. A value that is not a number from
0 to 1 exits 2 with a message on stderr and prints nothing. Standard library only, so `stack.sh`
runs it with the host's `python3 -I`.
Offline check: `packages/chassis/tests/test_trace_sampling.py`.
"""

from __future__ import annotations

import math
import os
import sys
from collections.abc import Mapping

RATE_VAR = "TRACE_SAMPLE_RATE"
DEFAULT_RATE = "1.0"
"""suggested: keep every trace when no rate is set, as the chassis marked every run sampled."""
_DIGITS = 6


def percent(rate: str) -> str:
    """The Collector's `sampling_percentage` for `rate`. `ValueError` unless 0 <= rate <= 1."""
    try:
        value = float(rate)
    except ValueError:
        value = math.nan
    if not (math.isfinite(value) and 0 <= value <= 1):
        raise ValueError(f"{RATE_VAR} must be a number from 0 to 1, got {rate!r}")
    return f"{round(value * 100, _DIGITS):g}"


def from_env(environ: Mapping[str, str]) -> str:
    """`percent` of `TRACE_SAMPLE_RATE` in `environ`, or of `DEFAULT_RATE` when it is unset."""
    return percent(environ.get(RATE_VAR, DEFAULT_RATE))


def main() -> int:
    try:
        print(from_env(os.environ))
    except ValueError as exc:
        print(f"trace_sample_percent: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
