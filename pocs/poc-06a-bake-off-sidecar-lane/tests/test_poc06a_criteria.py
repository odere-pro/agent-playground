"""PoC-6a, one test per exit criterion in the README (numbered 1 to 8 in order). Each one is
either a real check, or a skip that records why and which later task covers it. Nothing is
silent: a criterion with no evidence yet is a skip with its reason, not a missing test.

The scenario evidence for the lanes lives in `test_poc06a_engine_contract.py` (the `EnginePort`
suite), `test_poc06a_tasks.py` (smoke, simplifier, lookup, `traceparent`), and
`test_poc06a_interface_contract.py` (the PoC-3 interface suite). The tests here tie them to the
criteria and check the rest: the load-matrix registration and the chassis core.
"""

from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path

import pytest
from poc06_harness import ENGINES, SMOKE
from poc06a_harness import (
    LANES,
    Backends,
    SidecarAddress,
    python_engines,
    run_task,
    sidecar_engines,
    wire_python_engine,
)

ROOT = Path(__file__).resolve().parents[3]
TESTS = Path(__file__).resolve().parent
MATRIX = ROOT / "pocs/poc-04-stateless-scalable/load/run_matrix.py"
SCALE_SH = ROOT / "deploy/compose/scale.sh"
CORE = ROOT / "packages/chassis/src/chassis/core"


def _collected(*files: str) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *files],
        cwd=TESTS,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-2000:]
    return result.stdout


# --- Criterion 1 ---


@pytest.mark.parametrize("lane", LANES)
@pytest.mark.parametrize("name", list(python_engines()))
async def test_criterion_1_every_python_workload_runs_offline_in_both_lanes(
    name: str, lane: str, backends: Backends, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Criterion 1: every new Python workload passes the `EnginePort` contract suite over A2A on
    localhost and in memory, and runs offline against the fake model server. Here: each Python
    engine runs the smoke task in memory and over A2A on a Unix socket, and the answer comes from
    the fake model server (it saw the call). The suite itself is
    `test_poc06a_engine_contract.py`."""
    wire_python_engine(monkeypatch, ENGINES[name], backends)
    _, verdict, _ = await run_task(ENGINES[name], lane, SMOKE)
    assert verdict.passed, verdict.problems
    assert backends.model_calls(), "the fake model server was never called"


def test_criterion_1_the_contract_suite_is_bound_for_every_python_engine() -> None:
    """Criterion 1: the `EngineConnectorContract` bindings are parametrized by the registry, so
    each Python engine has the suite's cases in memory and over A2A, and the TypeScript agent over
    A2A. Read from pytest's own collection of the binding."""
    listing = _collected("test_poc06a_engine_contract.py")
    for name in python_engines():
        assert f"TestEngineInMemory::test_stream_starts_and_ends[{name}]" in listing, name
        assert f"TestEngineOverA2A::test_stream_starts_and_ends[{name}]" in listing, name
        assert f"TestEngineOverA2A::test_cancel_is_clean[{name}]" in listing, name
    assert "TestEngineOverA2A::test_stream_starts_and_ends[echo-typescript]" in listing


@pytest.mark.filterwarnings("ignore:A test tried to use socket")
def test_criterion_1_the_gate_blocks_tcp(request: pytest.FixtureRequest) -> None:
    """Criterion 1 (offline): under `make test` a TCP connection is refused, so a pass above
    cannot have used a real socket. Skips when run without the gate's `--disable-socket`."""
    if not request.config.getoption("--disable-socket", default=False):
        pytest.skip("run through `make test-poc POC=06a` to prove the gate blocks sockets")
    with pytest.raises(Exception, match=r"(?i)socket"):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)


# --- Criterion 2 ---


async def test_criterion_2_the_typescript_agent_passes_through_the_generic_sidecar_connector(
    typescript: SidecarAddress,
) -> None:
    """Criterion 2 (the non-Python agent): the TypeScript agent passes the same suite
    (`TestEngineOverA2A[echo-typescript]`) behind the chassis's generic `SidecarConnector`, runs
    the tool task, and nothing in `chassis.core` is written for it."""
    engine = ENGINES["echo-typescript"]
    assert engine.language == "typescript" and engine.lane == "sidecar"
    _, verdict, _ = await run_task(engine, "sidecar", SMOKE, typescript)
    assert verdict.passed, verdict.problems
    for path in CORE.rglob("*.py"):
        text = path.read_text().lower()
        assert "typescript" not in text and "echo-ts" not in text, f"{path} names the TS agent"


def test_criterion_2_the_remote_solution_is_6b() -> None:
    """Criterion 2 (the remote solution) belongs to part 6b: B4, kagent on kind."""
    pytest.skip("the remote solution (kagent) is part 6b, task B4; not a 6a sidecar engine")


# --- Criterion 3 ---


def test_criterion_3_every_engine_is_scored_on_every_criterion() -> None:
    """Criterion 3: the scorecard. Skipped: it is written in W4 (plan A5, `eval-expert` and
    `docs-editor`), after the hosted-model numbers from the Mac run."""
    pytest.skip("the scorecard is W4 / plan A5; the numbers come from `make poc06-mac`")


# --- Criterion 4 ---


def _matrix(*args: str) -> str:
    result = subprocess.run(
        [sys.executable, str(MATRIX), "--dry-run", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    return result.stdout


@pytest.mark.parametrize("name", list(sidecar_engines()))
def test_criterion_4_the_load_matrix_accepts_every_sidecar_engine(name: str) -> None:
    """Criterion 4 (load suite): `make load-test ARGS="--engine <engine>"` is accepted for every
    sidecar-lane engine, and plans `scale.sh up <engine>` at 1, 2, and 4 pairs. The run itself needs
    Docker (the Mac); `--dry-run` runs nothing."""
    plan = _matrix("--engine", name)
    for pairs in (1, 2, 4):
        assert f"scale.sh up {name} {pairs}" in plan, plan
    assert "docker" not in plan.split("scale.sh up")[0].lower()


def test_criterion_4_the_default_load_matrix_covers_every_sidecar_engine() -> None:
    """Criterion 4 (load suite): with no `--engine`, the matrix runs every sidecar engine."""
    plan = _matrix()
    for name in sidecar_engines():
        assert f"scale.sh up {name} 1" in plan, name


def test_criterion_4_the_scale_stack_builds_and_names_every_sidecar_engine() -> None:
    """Criterion 4 (load suite): `scale.sh` accepts the engine and builds its image. The
    TypeScript agent has its own build line; each Python engine is in the build loop."""
    text = SCALE_SH.read_text()
    engines_line = next(line for line in text.splitlines() if line.startswith("ENGINES=")).replace(
        '"', " "
    )
    loop = next(line for line in text.splitlines() if "for engine in echo-python" in line)
    for name, engine in sidecar_engines().items():
        assert name in engines_line.split(), name
        if engine.language == "python":
            assert name in loop.replace(";", " ").split(), name
            assert (ROOT / f"packages/workloads/{name}/Dockerfile").is_file(), name
        else:
            assert f"agent-platform/{name}:$TAG" in text, name


def test_criterion_4_the_hostile_suite_on_kind() -> None:
    """Criterion 4 (hostile suite): the PoC-5 sidecar kind suite for each engine. Skipped: it
    needs a kind cluster (CI's kind job and the Mac run, plan A4), not this offline task."""
    pytest.skip("the PoC-5 sidecar kind suite runs on kind (plan A4, kind CI job); not offline")


# --- Criterion 5 to 8 ---


def test_criterion_5_token_overhead_on_a_hosted_model() -> None:
    """Criterion 5: the token overhead against plain Python, on a hosted big model. Skipped: the
    numbers come from the Mac run (`make poc06-mac`, W5), with the provider key on the Mac."""
    pytest.skip("hosted-model numbers come from the Mac run (`make poc06-mac`, W5)")


def test_criterion_6_what_the_chassis_cannot_control_for_the_remote_solution() -> None:
    """Criterion 6: the list for the remote solution. Skipped: part 6b, task B4."""
    pytest.skip("the remote solution is part 6b, task B4")


def test_criterion_7_the_adr_names_default_supported_and_rejected_engines() -> None:
    """Criterion 7: the bake-off ADR, a draft in 6a. Skipped: written in W4 (plan A5, ADR-006)."""
    pytest.skip("the ADR draft is W4 / plan A5 (ADR-006), after the scorecard")


def test_criterion_8_handle_and_the_event_schema_are_frozen_as_v1() -> None:
    """Criterion 8: the freeze. Skipped: part 6b, task B6 (contract v5, `events.v1.json`)."""
    pytest.skip("the freeze is part 6b, task B6")
