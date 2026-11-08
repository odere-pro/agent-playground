"""The ready marker names the process that wrote it (ADR-012, H14; the #107 `platform-security`
LOW). A container restart keeps the emptyDir, so a marker from a chassis that was killed (SIGKILL,
OOM) survives until `chassis.entry` deletes it. In that window the chart's startup probe must
not pass. So the marker holds the writer's pid and start time (`/proc/<pid>/stat` field 22,
clock ticks since boot), and the probe passes only while a live process has both. An empty or
foreign marker never passes.

`/proc` is faked with a directory: the probe's reader takes the root as an argument.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from chassis import ready_marker

# A real line, with a comm field that holds spaces and a parenthesis, which `str.split` would
# mangle. Field 22 (start time) is 4242.
STAT_LINE = (
    "1 (chassis (a) b) S 0 1 1 0 -1 4194560 2000 0 0 0 10 5 0 0 20 0 1 0 4242 10000000 500 "
    "18446744073709551615 1 1 0 0 0 0 0 0 0 0 0 0 17 0 0 0 0 0 0 0 0 0 0 0 0 0 0\n"
)


def fake_proc(root: Path, pid: int, start: str) -> Path:
    stat = root / str(pid) / "stat"
    stat.parent.mkdir(parents=True, exist_ok=True)
    stat.write_text(STAT_LINE.replace(" 4242 ", f" {start} ", 1), encoding="utf-8")
    return root


def test_the_start_time_is_field_22_even_when_the_command_name_holds_spaces(
    tmp_path: Path,
) -> None:
    proc = fake_proc(tmp_path / "proc", 1, "4242")
    assert ready_marker.process_start(1, proc) == "4242"


def test_no_such_process_or_no_procfs_gives_no_start_time(tmp_path: Path) -> None:
    assert ready_marker.process_start(1, tmp_path / "proc") is None
    assert ready_marker.process_start(7, fake_proc(tmp_path / "proc", 1, "4242")) is None


def test_the_marker_holds_the_writer_pid_and_start_time(tmp_path: Path) -> None:
    proc = fake_proc(tmp_path / "proc", 1, "4242")
    marker = tmp_path / "chassis-ready"
    ready_marker.write(marker, pid=1, proc=proc)
    assert marker.read_text(encoding="utf-8") == "1 4242\n"


def test_the_marker_is_moved_into_place_so_a_probe_never_sees_it_half_written(
    tmp_path: Path,
) -> None:
    proc = fake_proc(tmp_path / "proc", 1, "4242")
    marker = tmp_path / "chassis-ready"
    ready_marker.write(marker, pid=1, proc=proc)
    assert [p.name for p in tmp_path.iterdir() if p.is_file()] == ["chassis-ready"], "no .tmp left"


def test_a_marker_from_a_live_process_passes(tmp_path: Path) -> None:
    proc = fake_proc(tmp_path / "proc", 1, "4242")
    marker = tmp_path / "chassis-ready"
    ready_marker.write(marker, pid=1, proc=proc)
    assert ready_marker.is_live(marker, proc)


def test_a_marker_from_a_killed_chassis_fails_once_the_container_restarts(tmp_path: Path) -> None:
    """The restarted container's pid 1 is a new process with a later start time; the marker in
    the kept emptyDir still names the old one. The probe must not pass on it."""
    proc = fake_proc(tmp_path / "proc", 1, "4242")
    marker = tmp_path / "chassis-ready"
    ready_marker.write(marker, pid=1, proc=proc)
    fake_proc(proc, 1, "9000")  # the new pid 1
    assert not ready_marker.is_live(marker, proc)


def test_a_marker_whose_process_is_gone_fails(tmp_path: Path) -> None:
    proc = fake_proc(tmp_path / "proc", 1, "4242")
    marker = tmp_path / "chassis-ready"
    ready_marker.write(marker, pid=1, proc=proc)
    (proc / "1" / "stat").unlink()
    assert not ready_marker.is_live(marker, proc)


@pytest.mark.parametrize(
    "content",
    [
        "",
        "1\n",
        "1 -\n",
        "x 4242\n",
        "1 4242 extra\n",
        "-1 4242\n",
        "1 4242x\n",
        "9" * 5000 + " 1\n",
    ],
)
def test_an_empty_or_malformed_marker_never_passes(tmp_path: Path, content: str) -> None:
    """The old empty marker, a marker written where `/proc` was unreadable (`-`), and garbage
    all fail closed. Only `<pid> <start>` from a live process passes."""
    proc = fake_proc(tmp_path / "proc", 1, "4242")
    marker = tmp_path / "chassis-ready"
    marker.write_text(content, encoding="utf-8")
    assert not ready_marker.is_live(marker, proc)


def test_a_missing_marker_fails(tmp_path: Path) -> None:
    assert not ready_marker.is_live(
        tmp_path / "chassis-ready", fake_proc(tmp_path / "proc", 1, "1")
    )


def test_this_process_is_live_where_procfs_exists(tmp_path: Path) -> None:
    """The real `/proc`: on Linux this process passes its own check. Elsewhere there is no start
    time, the marker records `-`, and the check fails closed."""
    marker = tmp_path / "chassis-ready"
    ready_marker.write(marker)
    pid, start = marker.read_text(encoding="utf-8").split()
    assert pid == str(os.getpid())
    if sys.platform == "linux":
        assert start.isdigit()
        assert ready_marker.is_live(marker)
    else:
        assert start == "-"
        assert not ready_marker.is_live(marker)


def test_the_probe_command_imports_only_the_standard_library(tmp_path: Path) -> None:
    """`python -m chassis.ready_marker PATH` is the chart's startup probe, run every 2 s. It must
    not import the server, and it exits 1 on a stale marker, 2 on a bad argument."""
    marker = tmp_path / "chassis-ready"
    marker.write_text("", encoding="utf-8")
    code = (
        "import sys, runpy; sys.argv = ['chassis.ready_marker', sys.argv[1]]; "
        "import chassis.ready_marker; "
        "heavy = [m for m in sys.modules if m.startswith(('chassis.server', 'uvicorn', 'httpx'))]; "
        "assert not heavy, heavy; "
        "runpy.run_module('chassis.ready_marker', run_name='__main__')"
    )
    stale = subprocess.run(
        [sys.executable, "-c", code, str(marker)], capture_output=True, text=True, check=False
    )
    assert stale.returncode == 1, (stale.returncode, stale.stderr)
    bad = subprocess.run(
        [sys.executable, "-m", "chassis.ready_marker", "chassis-ready"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert bad.returncode == 2, (bad.returncode, bad.stderr)
    none = subprocess.run(
        [sys.executable, "-m", "chassis.ready_marker"], capture_output=True, text=True, check=False
    )
    assert none.returncode == 2
