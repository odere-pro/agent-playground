"""check_secrets.sh: a new secret fails, moved baseline line numbers do not."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "check_secrets.sh"
BASELINE = '{"results": {"a.py": []}}\n'
REFRESHED = '{"results": {"a.py": [{"line_number": 2}]}}\n'


def _env() -> dict[str, str]:
    """The environment with no GIT_*: under a git hook they point git at the real repository."""
    return {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=_env())


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A git repo with one tracked file, a baseline, and a copy of the script."""
    (tmp_path / "scripts").mkdir()
    shutil.copy(SCRIPT, tmp_path / "scripts" / SCRIPT.name)
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / ".secrets.baseline").write_text(BASELINE)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
    return tmp_path


def _run(repo: Path, hook_exit: int, *args: str) -> subprocess.CompletedProcess[str]:
    """Run the script with a stub `uv` that rewrites the baseline on exit 3, as the hook does."""
    bin_dir = repo / "stub-bin"
    bin_dir.mkdir()
    stub = bin_dir / "uv"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f"if [ {hook_exit} -eq 3 ]; then printf '%s' '{REFRESHED}' > .secrets.baseline; fi\n"
        f"exit {hook_exit}\n"
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    env = {**_env(), "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    script = repo / "scripts" / SCRIPT.name
    return subprocess.run(
        ["bash", str(script), *args], cwd=repo, env=env, capture_output=True, text=True
    )


def test_check_secrets_passes_when_the_hook_finds_nothing(repo: Path) -> None:
    """A clean scan exits 0 and leaves the baseline alone."""
    result = _run(repo, hook_exit=0)

    assert result.returncode == 0
    assert (repo / ".secrets.baseline").read_text() == BASELINE


def test_check_secrets_fails_when_the_hook_finds_a_new_secret(repo: Path) -> None:
    """A new secret fails the gate."""
    result = _run(repo, hook_exit=1)

    assert result.returncode == 1


def test_check_secrets_passes_and_restores_the_baseline_when_only_line_numbers_moved(
    repo: Path,
) -> None:
    """Moved line numbers are not a finding, and the check leaves no edit behind."""
    result = _run(repo, hook_exit=3)

    assert result.returncode == 0
    assert "make lint-fix" in result.stdout
    assert (repo / ".secrets.baseline").read_text() == BASELINE


def test_check_secrets_keeps_the_new_baseline_when_refreshing(repo: Path) -> None:
    """`--refresh`, which `make lint-fix` uses, writes the moved line numbers."""
    result = _run(repo, 3, "--refresh")

    assert result.returncode == 0
    assert (repo / ".secrets.baseline").read_text() == REFRESHED


def test_check_secrets_still_fails_on_a_new_secret_when_refreshing(repo: Path) -> None:
    """`--refresh` never accepts a new secret."""
    result = _run(repo, 1, "--refresh")

    assert result.returncode == 1
