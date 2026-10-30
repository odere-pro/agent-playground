"""The workload's environment never needs the chassis: the kit imports none of it, and a workload
that tries to import it does not start."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from agent_testkit.processes import StartError, _guard_dir, start_workload

KIT_MODULES = (
    "agent_testkit",
    "agent_testkit.plugin",
    "agent_testkit.contract",
    "agent_testkit.wire",
    "agent_testkit.lane_cases",
    "agent_testkit.processes",
    "agent_testkit.settings",
)

CHECK = """
import sys
for name in sys.argv[1:]:
    __import__(name)
bad = sorted(m for m in sys.modules if m.split(".")[0] in ("chassis", "chassis_contracts"))
print(bad)
sys.exit(1 if bad else 0)
"""


def test_the_kit_imports_with_the_chassis_unimportable(tmp_path: Path) -> None:
    guard = _guard_dir(tmp_path)
    out = subprocess.run(
        [sys.executable, "-c", CHECK, *KIT_MODULES],
        env={"PYTHONPATH": str(guard), "PATH": ""},
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.returncode == 0, out.stdout + out.stderr
    assert out.stdout.strip() == "[]"


def test_the_guard_makes_chassis_unimportable(tmp_path: Path) -> None:
    guard = _guard_dir(tmp_path)
    out = subprocess.run(
        [sys.executable, "-c", "import chassis"],
        env={"PYTHONPATH": str(guard), "PATH": ""},
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.returncode != 0
    assert "never holds the chassis" in out.stderr


@pytest.mark.slow
def test_a_workload_that_imports_the_chassis_does_not_start(tmp_path: Path) -> None:
    (tmp_path / "uses_chassis.py").write_text(
        "import chassis  # noqa: F401\n\n"
        "async def handle(input, ctx):\n"
        "    yield {'type': 'start', 'request_id': ctx['request_id']}\n"
    )
    with (
        pytest.raises(StartError, match="never holds the chassis"),
        start_workload("uses_chassis:handle", env={"PYTHONPATH": str(tmp_path)}),
    ):
        pass
