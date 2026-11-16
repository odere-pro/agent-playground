"""The stateless check (057 H-19) as processes in the `sidecar` lane: the workload and the chassis
each run with a read-only root (`agent_testkit.readonly_root`), and the same requests run on fresh
pods and on one reused pod, in shuffled order. The simplifier (`echo_python`) passes; a workload
that writes a file, one that keeps the last request, and one that leaves a file in its scratch
volume fail, with the container named. All offline: Unix sockets and loopback only.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from agent_testkit.processes import CHASSIS_SERVE, chassis_argv, read_only, workload_argv
from agent_testkit.readonly_root import LAUNCHER, counts, refused_writes
from agent_testkit.settings import CURRENT_PYTHON, AgentRef, ChassisSource, source_for
from agent_testkit.stateless import StatelessError
from agent_testkit.stateless_lane import LanePods, ScratchLeftover, check_lane

BAD_DIR = Path(__file__).parent / "workloads"
WRITE_DIR = "AGENT_TESTKIT_WRITE_DIR"
REQUESTS = [{"text": f"simplify: request {i}", "data": {}} for i in range(3)]
SCRIPT = [{"match": "simplify", "reply": "Plain words. Short sentences. Same facts."}]
START_S = 120.0
"""suggested: the workload start budget here; a loaded, shared Mac can take over 30 s."""

WRITES = """
import sys
from pathlib import Path
try:
    Path(sys.argv[1]).write_text("kept")
except OSError as exc:
    print("refused", exc.errno)
"""


def _bad(handle: str, name: str, **env: str) -> LanePods:
    path = os.pathsep.join(p for p in (str(BAD_DIR), os.environ.get("PYTHONPATH", "")) if p)
    return LanePods(
        AgentRef(handle=handle, name=name),
        source_for(CURRENT_PYTHON, Path.cwd()),
        env={"PYTHONPATH": path, **env},
        start_s=START_S,
    )


def _launch(tmp_path: Path, *flags: str) -> tuple[subprocess.CompletedProcess[str], Path]:
    script = tmp_path / "writes.py"
    script.write_text(WRITES)
    target = tmp_path / "out" / "kept.txt"
    target.parent.mkdir()
    argv = [sys.executable, str(LAUNCHER), *flags, str(script), str(target)]
    return subprocess.run(argv, capture_output=True, text=True, check=True), target


def test_the_launcher_refuses_a_write_and_names_the_container_and_the_path(
    tmp_path: Path,
) -> None:
    done, target = _launch(tmp_path, "--container", "chassis")

    assert "refused 30" in done.stdout  # EROFS, as a read-only root answers
    assert not target.exists()
    writes = refused_writes(done.stderr)
    assert [(w.container, w.path) for w in writes] == [("chassis", str(target))]


def test_the_launcher_allows_writes_under_a_scratch_folder(tmp_path: Path) -> None:
    done, target = _launch(tmp_path, "--container", "workload", "--allow", str(tmp_path / "out"))

    assert "refused" not in done.stdout
    assert target.read_text() == "kept"
    assert refused_writes(done.stderr) == []


REMOVES_A_TREE = """
import os, shutil, sys
from pathlib import Path
home = Path(sys.argv[1], "home")
(home / "a" / "b").mkdir(parents=True)
(home / "a" / "b" / "f.json").write_text("{}")
os.chdir(sys.argv[2])  # rmtree removes by name relative to a folder fd, never to the cwd
shutil.rmtree(home)
"""


def test_a_tree_removed_inside_a_scratch_folder_is_not_a_write_to_the_root(
    tmp_path: Path,
) -> None:
    scratch, elsewhere = tmp_path / "scratch", tmp_path / "elsewhere"
    scratch.mkdir()
    elsewhere.mkdir()
    script = tmp_path / "rm.py"
    script.write_text(REMOVES_A_TREE)
    argv = [sys.executable, str(LAUNCHER), "--allow", str(scratch), str(script)]

    done = subprocess.run([*argv, str(scratch), str(elsewhere)], capture_output=True, text=True)

    assert done.returncode == 0, done.stderr
    assert refused_writes(done.stderr) == []
    assert list(scratch.iterdir()) == []


@pytest.mark.parametrize(
    "path",
    [
        "/app/__pycache__/notes.json",
        "/app/__pycache__/sub/kept.pyc",
        "/app/__pycache__",
        "/app/kept.pyc",
    ],
)
def test_only_bytecode_files_in_a_pycache_folder_are_not_writes(path: str) -> None:
    assert counts(path)


@pytest.mark.parametrize(
    "path", ["/app/__pycache__/m.cpython-312.pyc", "/app/__pycache__/m.cpython-312.pyc.4371"]
)
def test_a_bytecode_file_and_its_atomic_temp_are_not_writes(path: str) -> None:
    assert not counts(path)


def test_both_processes_start_under_the_launcher_with_only_their_socket_allowed(
    tmp_path: Path,
) -> None:
    source = ChassisSource(("python",), "test", isolated=True)
    chassis = chassis_argv(
        source, tmp_path / "c.yaml", "/s/c.sock", 1, None, read_only("chassis", ["/s/c.sock"])
    )
    workload = workload_argv(
        AgentRef("a:handle", "a"), ["--uds", "/s/w.sock"], read_only("workload", ["/s/w.sock"])
    )

    assert chassis[:7] == [
        "python",
        "-I",
        str(LAUNCHER),
        "--container",
        "chassis",
        "--allow",
        "/s/c.sock",
    ]
    assert chassis[7] == str(CHASSIS_SERVE)
    assert workload[1:7] == [str(LAUNCHER), "--container", "workload", "--allow", "/s/w.sock", "-m"]


@pytest.mark.slow
def test_the_simplifier_passes_on_fresh_and_reused_sidecar_pods_with_a_read_only_root() -> None:
    pods = LanePods(
        AgentRef(handle="echo_python:handle", name="echo-python"),
        source_for(CURRENT_PYTHON, Path.cwd()),
        model_script=SCRIPT,
        start_s=START_S,
    )

    report = check_lane(pods, REQUESTS, seed=1)

    assert report.read_only == ("workload", "chassis")
    assert report.reused_order != report.fresh_order
    assert [out["status"] for out in report.outputs] == ["ok"] * len(REQUESTS)


@pytest.mark.slow
def test_a_workload_that_writes_a_file_fails_naming_the_workload_and_the_path(
    tmp_path: Path,
) -> None:
    pods = _bad("agent_testkit_lane_writer:handle", "writer", **{WRITE_DIR: str(tmp_path)})

    with pytest.raises(StatelessError, match=r"container 'workload' wrote \S*last-request\.txt"):
        check_lane(pods, REQUESTS[:1])
    assert not (tmp_path / "last-request.txt").exists()


@pytest.mark.slow
def test_a_workload_that_keeps_the_last_request_fails_on_the_reused_pod() -> None:
    pods = _bad("agent_testkit_lane_keeper:handle", "keeper")

    with pytest.raises(StatelessError, match=r"call \d .* differed on the reused pod"):
        check_lane(pods, REQUESTS, seed=1)


@pytest.mark.slow
def test_a_file_left_in_the_scratch_volume_after_a_call_fails() -> None:
    pods = replace(_bad("agent_testkit_lane_writer:handle", "writer"), scratch_var=WRITE_DIR)

    with pytest.raises(ScratchLeftover, match=r"container 'workload' left \S*last-request\.txt"):
        check_lane(pods, REQUESTS[:1])


@pytest.mark.slow
def test_a_deferred_scratch_leftover_is_raised_only_after_every_call_ran() -> None:
    writer = _bad("agent_testkit_lane_writer:handle", "writer")
    pods = replace(writer, scratch_var=WRITE_DIR, defer_scratch=True)

    with pytest.raises(ScratchLeftover) as caught:
        check_lane(pods, REQUESTS[:2], seed=1)
    # Each of the 2 fresh pods and both calls on the reused pod left the file: 4 findings.
    assert str(caught.value).count("last-request.txt in its scratch volume") == 4


@pytest.mark.slow
def test_deferring_the_scratch_check_never_hides_a_write_outside_it(tmp_path: Path) -> None:
    writer = _bad("agent_testkit_lane_writer:handle", "writer", **{WRITE_DIR: str(tmp_path)})
    pods = replace(writer, scratch_var="AGENT_TESTKIT_UNUSED_SCRATCH", defer_scratch=True)

    with pytest.raises(StatelessError, match=r"container 'workload' wrote") as caught:
        check_lane(pods, REQUESTS[:1])
    assert not isinstance(caught.value, ScratchLeftover)


@pytest.mark.slow
def test_a_write_while_the_workload_stops_fails(tmp_path: Path) -> None:
    pods = _bad("agent_testkit_lane_atexit:handle", "atexit", **{WRITE_DIR: str(tmp_path)})

    with pytest.raises(StatelessError, match=r"container 'workload' wrote \S*flushed-at-exit"):
        check_lane(pods, REQUESTS[:1])
    assert not (tmp_path / "flushed-at-exit.txt").exists()
