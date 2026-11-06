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
    }
