"""The load matrix's pure parts: the plan it prints, how it reads `docker stats`, and how it merges
results. Nothing here starts a container or Locust."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from load_tools import run_matrix

ROOT = Path(__file__).resolve().parents[3]
POC04_ENTRY = ROOT / "pocs" / "poc-04-stateless-scalable" / "load" / "run_matrix.py"
DRY_RUN = ["--dry-run", "--engine", "echo-python", "--pairs", "1", "--only", "main"]


def dry_run(command: list[str]) -> str:
    """The plan `command` prints; it fails the test when the command exits non-zero."""
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr[-2000:]
    return result.stdout


def test_load_tools_run_matrix_finds_its_stack_script_and_locustfile() -> None:
    """The package resolves the scale stack script and the Locust file from the repo root."""
    paths = (run_matrix.SCALE_SH, run_matrix.LOCUSTFILE)

    present = [path.is_file() for path in paths]

    assert present == [True, True]


@pytest.mark.slow
def test_load_tools_run_matrix_plan_is_the_same_from_the_poc_04_entry_script() -> None:
    """The kept PoC-4 entry script prints the same plan as `python -m load_tools.run_matrix`."""
    module_plan = dry_run([sys.executable, "-m", "load_tools.run_matrix", *DRY_RUN])

    script_plan = dry_run([sys.executable, str(POC04_ENTRY), *DRY_RUN])

    assert script_plan == module_plan


@pytest.mark.parametrize(
    ("text", "expected"),
    [("12.5MiB", 12.5 * 1024**2), ("1GiB", 1024.0**3), ("3kB", 3e3), ("", 0.0)],
)
def test_load_tools_parse_bytes_reads_docker_units(text: str, expected: float) -> None:
    """`docker stats` sizes become bytes, binary and decimal units alike."""
    value = run_matrix.parse_bytes(text)

    assert value == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("poc04-chassis-2-1", "chassis"),
        ("poc04-workload-4-1", "workload"),
        ("poc04-fake-model-1", "fake-model"),
        ("poc04-valkey-1", "valkey"),
    ],
)
def test_load_tools_role_groups_replicas_by_service(name: str, expected: str) -> None:
    """A container name maps to its role; the pair number of a chassis or workload is dropped."""
    role = run_matrix.role(name)

    assert role == expected


def test_load_tools_ok_rps_leaves_out_failed_calls() -> None:
    """Successful calls per second: 10 rps with a quarter failed is 7.5 ok rps."""
    value = run_matrix.ok_rps(requests=100, failures=25, rps="10")

    assert value == 7.5


def test_load_tools_merge_computes_the_hop_from_both_lanes() -> None:
    """The hop is the sidecar run minus the inprocess run, at p50 and p95."""
    results: dict[str, object] = {"version": 1}
    sidecar = run_matrix.Scenario("hop-sidecar", "hop", "echo-python", 1, 1)
    inprocess = run_matrix.Scenario("hop-inprocess", "hop", "inprocess", 1, 1)
    run_matrix.merge(results, sidecar, {"p50_ms": 12.0, "p95_ms": 30.0})

    run_matrix.merge(results, inprocess, {"p50_ms": 10.0, "p95_ms": 25.0})

    assert results["hop"] == {
        "engine": "echo-python",
        "pairs": 1,
        "sidecar": {"p50_ms": 12.0, "p95_ms": 30.0},
        "inprocess": {"p50_ms": 10.0, "p95_ms": 25.0},
        "hop_ms": {"p50_ms": 2.0, "p95_ms": 5.0},
        "chassis_hop_ms": None,
    }


def test_load_tools_per_role_records_the_p95_of_every_sample_next_to_mean_and_max() -> None:
    """122 X-5 reads the sidecar's CPU and memory at p95: per role, the p95 over every sample of
    every replica (nearest rank), next to the mean of the replica means and the max."""
    rows = [
        {"name": "poc04-chassis-1-1", "vcpu": v, "mib": m}
        for v, m in zip([0.1] * 19 + [1.0], [100.0] * 19 + [200.0], strict=True)
    ] + [{"name": "poc04-chassis-2-1", "vcpu": 0.3, "mib": 150.0}]

    roles = run_matrix.per_role(rows)

    chassis = roles["chassis"]
    assert chassis["replicas"] == 2
    assert chassis["vcpu_p95"] == 0.3
    assert chassis["mib_p95"] == 150.0
    assert chassis["vcpu_max"] == 1.0 and chassis["mib_max"] == 200.0


PROM = """# HELP chassis_engine_run_duration_seconds Engine call latency, by lane and status.
# TYPE chassis_engine_run_duration_seconds histogram
chassis_engine_run_duration_seconds_bucket{agent="echo",lane="sidecar",status="ok",le="0.01"} 0
chassis_engine_run_duration_seconds_bucket{agent="echo",lane="sidecar",status="ok",le="0.02"} 5
chassis_engine_run_duration_seconds_bucket{agent="echo",lane="sidecar",status="ok",le="0.04"} 10
chassis_engine_run_duration_seconds_bucket{agent="echo",lane="sidecar",status="ok",le="+Inf"} 10
chassis_engine_run_duration_seconds_sum{agent="echo",lane="sidecar",status="ok"} 0.25
chassis_engine_run_duration_seconds_count{agent="echo",lane="sidecar",status="ok"} 10
chassis_engine_run_duration_seconds_bucket{agent="echo",lane="sidecar",status="error",le="+Inf"} 1
chassis_engine_run_duration_seconds_sum{agent="echo",lane="sidecar",status="error"} 9
chassis_engine_run_duration_seconds_count{agent="echo",lane="sidecar",status="error"} 1
"""


def test_load_tools_reads_the_engine_run_histogram_of_ok_runs_from_the_metrics_text() -> None:
    """The chassis-side timer (122 X-5): the ok series of `chassis_engine_run_duration_seconds`,
    its buckets, sum, and count; errors are left out."""
    hist = run_matrix.engine_histogram(PROM)

    assert hist == run_matrix.Histogram(buckets={0.01: 0, 0.02: 5, 0.04: 10}, total=0.25, count=10)


def test_load_tools_histogram_delta_and_quantiles_give_the_measured_window_in_ms() -> None:
    """The scrape before the measured run is taken off the one after it (the warm-up is not
    kept), then the mean is exact and p50 and p95 are interpolated inside their bucket, as
    Prometheus does, in milliseconds."""
    before = run_matrix.Histogram(buckets={0.01: 0, 0.02: 1, 0.04: 2}, total=0.05, count=2)
    after = run_matrix.Histogram(buckets={0.01: 0, 0.02: 6, 0.04: 12}, total=0.30, count=12)

    window = run_matrix.engine_timer(after, before)

    assert window == {"count": 10, "mean_ms": 25.0, "p50_ms": 20.0, "p95_ms": 38.0}


def test_load_tools_merge_adds_the_chassis_side_hop_next_to_the_locust_one() -> None:
    """The hop from the chassis's own timer: sidecar minus inprocess at the mean, p50, and p95."""
    results: dict[str, object] = {"version": 1}
    sidecar = run_matrix.Scenario("hop-sidecar", "hop", "echo-python", 1, 1)
    inprocess = run_matrix.Scenario("hop-inprocess", "hop", "inprocess", 1, 1)
    timer_s = {"count": 10, "mean_ms": 25.0, "p50_ms": 24.0, "p95_ms": 40.0}
    timer_i = {"count": 10, "mean_ms": 23.5, "p50_ms": 22.0, "p95_ms": 36.0}
    run_matrix.merge(results, sidecar, {"p50_ms": 12.0, "p95_ms": 30.0, "engine_run": timer_s})

    run_matrix.merge(results, inprocess, {"p50_ms": 10.0, "p95_ms": 25.0, "engine_run": timer_i})

    hop = results["hop"]
    assert isinstance(hop, dict)
    assert hop["hop_ms"] == {"p50_ms": 2.0, "p95_ms": 5.0}
    assert hop["chassis_hop_ms"] == {"mean_ms": 1.5, "p50_ms": 2.0, "p95_ms": 4.0}
