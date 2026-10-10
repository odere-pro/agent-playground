"""PoC-6b on kind, exit criterion 4 (load half): each remote engine takes concurrent smoke runs
through its own chassis, in its lane, and every run ends `end{status: ok}`.

What this measures: the chassis, the remote lane, and the gVisor sandbox under concurrency. The
model is the fake model server (`Hello.` on the smoke task), so the numbers say nothing about model
speed. Latency is the wall time of one `POST /v1/run` with `stream: true`, from the test through
`kubectl exec` into the chassis container (so it includes the exec start, a fixed cost shared by
every run).

Shape: `ROUNDS` rounds of `N` concurrent runs per engine, one engine at a time. Round 1 is cold
(first runs after the pod went ready); round 2 is warm. The summary line counts both rounds.

N per engine (`suggested:`, not from the epic):
- 8 for `echo-smolagents`, `echo-typescript`, and `kagent-adk`: the CI runner has 4 CPUs, so 8
  in flight already queues, which is the point of a load run, and none of the three pods is near
  its memory limit.
- 3 for `echo-claude-agent`: each run starts a `claude` CLI process of 175 to 240 MB resident
  (notes/2026-10-09-lanes-b-kind.md, decision 5), and the pod limit is 1Gi, so 3 in flight is
  about 720 MB at most. More would test the OOM killer, not the lane.

Each engine prints one line, for the note (copy it from the CI log):

    LOAD engine=<name> lane=<lane> n=<runs> ok=<k> err=<e> p50_ms=<x> p95_ms=<y> wall_s=<z>

Grep: `grep -E '^LOAD engine=' <log>`. Percentiles are nearest-rank over all `n` runs.
The line is printed past pytest's capture, so a passing run shows it in the log.

A kind test: marked `kind` (and `network`), skipped unless `POC06_KIND=1`. Run:
`deploy/kind/poc06/run.sh test`. `test_nearest_rank_percentile` is offline.
"""

from __future__ import annotations

import math
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import pytest
from poc06_harness import SMOKE
from poc06b_kind import REMOTES, KindEngine, run_stream

ROUNDS = 2
DEFAULT_N = 8
N_FOR = {"echo-claude-agent": 3}
"""Runs in flight per round, by engine; see the module docstring for why Claude is 3."""
BUDGET_S = 90.0
"""suggested: wall time for one engine, both rounds, on a CI runner."""


def nearest_rank(values: Sequence[float], pct: float) -> float:
    """The nearest-rank percentile: the smallest value with at least `pct` percent at or below."""
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


def test_nearest_rank_percentile() -> None:
    xs = [float(i) for i in range(1, 11)]
    assert nearest_rank(xs, 50) == 5.0
    assert nearest_rank(xs, 95) == 10.0
    assert nearest_rank([7.0], 95) == 7.0


@dataclass(frozen=True)
class Sample:
    ok: bool
    ms: float
    why: str


def one_run(engine: KindEngine) -> Sample:
    start = time.monotonic()
    try:
        result = run_stream(engine, engine.text(SMOKE))
    except AssertionError as err:  # the exec itself failed
        return Sample(False, (time.monotonic() - start) * 1000, f"exec: {err}")
    ms = (time.monotonic() - start) * 1000
    end = result.events[-1] if result.events else {}
    ok = result.status == 200 and end.get("type") == "end" and end.get("status") == "ok"
    return Sample(ok, ms, "" if ok else f"status {result.status}, last {dict(end)}, {result.head}")


@pytest.mark.kind
@pytest.mark.parametrize("engine", REMOTES, ids=lambda e: e.id)
def test_the_remote_engine_takes_concurrent_runs(
    engine: KindEngine, capsys: pytest.CaptureFixture[str]
) -> None:
    n = N_FOR.get(engine.name, DEFAULT_N)
    samples: list[Sample] = []
    wall_start = time.monotonic()
    for _ in range(ROUNDS):
        with ThreadPoolExecutor(max_workers=n) as pool:
            samples.extend(pool.map(lambda _i: one_run(engine), range(n)))
    wall_s = time.monotonic() - wall_start
    latencies = [s.ms for s in samples]
    ok = sum(s.ok for s in samples)
    line = (
        f"LOAD engine={engine.name} lane={engine.lane} n={len(samples)} ok={ok} "
        f"err={len(samples) - ok} p50_ms={nearest_rank(latencies, 50):.0f} "
        f"p95_ms={nearest_rank(latencies, 95):.0f} wall_s={wall_s:.1f}"
    )
    with capsys.disabled():  # a passing test's output is hidden otherwise; the CI log needs it
        print(f"\n{line}")
    failed = [s.why for s in samples if not s.ok]
    assert not failed, failed[:3]
    assert wall_s < BUDGET_S, f"{wall_s:.1f}s over the {BUDGET_S}s budget"
