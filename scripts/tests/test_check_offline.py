"""check_offline.sh: no test inherits GIT_* from a git hook."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "check_offline.sh"


def test_check_offline_strips_git_variables_when_a_git_hook_runs_it(tmp_path: Path) -> None:
    """Under a hook, GIT_DIR would send a test's `git init` to the real repository."""
    (tmp_path / "scripts").mkdir()
    shutil.copy(SCRIPT, tmp_path / "scripts" / SCRIPT.name)
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir()
    stub = bin_dir / "uv"
    stub.write_text("#!/usr/bin/env bash\nenv\n")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    env = {
        **os.environ,
        "GIT_DIR": str(tmp_path / "real.git"),
        "GIT_INDEX_FILE": str(tmp_path / "index"),
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
    }

    result = subprocess.run(
        ["bash", str(tmp_path / "scripts" / SCRIPT.name)],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    names = {line.split("=", 1)[0] for line in result.stdout.splitlines()}
    assert "GIT_DIR" not in names
    assert "GIT_INDEX_FILE" not in names
