"""Locust installs from a committed, hashed lock into its own venv, never by name (`--with`).

Static except one test, which records the commands `main` runs and starts nothing.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
from load_tools import run_matrix
from load_tools.run_matrix import (
    LOCUST_LOCK,
    LOCUST_VERSION,
    Scenario,
    locust_bin,
    locust_cmd,
    locust_install_argv,
)

_PIN = re.compile(r"^([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?==(\S+)")
_NAME = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.\-]*)")  # not an option line (`-r`, `--index-url`)
ROOT = Path(__file__).resolve().parents[3]
SCENARIO = Scenario("echo-python-1p", "main", "echo-python", 1, 4)


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _hashes(lock: Path) -> dict[str, list[str]]:
    """Each `==` pinned name of `lock` and its `--hash` values."""
    found: dict[str, list[str]] = {}
    name = ""
    for line in lock.read_text(encoding="utf-8").splitlines():
        pin = _PIN.match(line)
        if pin:
            name = _canonical(pin.group(1))
            found[name] = []
        elif line.strip().startswith("--hash=sha256:"):
            found[name].append(line.strip())
    return found


def test_load_tools_locust_lock_pins_and_hashes_every_requirement() -> None:
    """Every requirement in the Locust lock is `==` pinned and carries a sha256."""
    reqs = _hashes(LOCUST_LOCK)

    assert {"locust", "gevent", "flask"} <= reqs.keys()
    assert [name for name, hashes in reqs.items() if not hashes] == []


def test_load_tools_locust_lock_holds_the_pinned_version() -> None:
    """The lock holds the Locust version the matrix names, and so does the input file."""
    lock = LOCUST_LOCK.read_text(encoding="utf-8")
    inputs = LOCUST_LOCK.with_suffix(".in").read_text(encoding="utf-8")

    assert f"\nlocust=={LOCUST_VERSION} \\" in lock
    assert f"\nlocust=={LOCUST_VERSION}\n" in inputs


def test_load_tools_locust_lock_pins_every_input() -> None:
    """Each name in `requirements.in` is pinned in `requirements.txt`: the lock is not behind."""
    inputs = LOCUST_LOCK.with_suffix(".in").read_text(encoding="utf-8").splitlines()
    names = {_canonical(m.group(1)) for line in inputs if (m := _NAME.match(line.strip()))}

    assert names and names - _hashes(LOCUST_LOCK).keys() == set()


def test_load_tools_locust_installs_with_require_hashes_and_no_deps(tmp_path: Path) -> None:
    """Locust installs from the lock only: a fresh venv, then `--require-hashes --no-deps -r`."""
    create, install = locust_install_argv(tmp_path)

    assert create[:3] == ["uv", "venv", "--clear"] and create[-1] == str(tmp_path)
    assert install[:3] == ["uv", "pip", "install"]
    assert install[install.index("--python") + 1] == str(tmp_path / "bin" / "python")
    assert {"--require-hashes", "--no-deps"} <= set(install)
    assert install[install.index("-r") + 1] == str(LOCUST_LOCK)


def test_load_tools_locust_runs_the_installed_binary_never_uv_with(tmp_path: Path) -> None:
    """Each Locust run starts the venv's `locust`; no `uv run --with` remains."""
    cmd = locust_cmd(SCENARIO, 10, None, 1, tmp_path)

    assert cmd[0] == str(locust_bin(tmp_path)) == str(tmp_path / "bin" / "locust")
    assert "--with" not in cmd and "uv" not in cmd


def test_load_tools_matrix_installs_locust_before_the_first_scenario(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`main` installs Locust once, from the lock, into a new temp dir, before it brings the first
    stack up; the dir is gone when `main` returns."""
    calls: list[list[str]] = []

    def record(cmd: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(list(cmd))
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(run_matrix, "host_info", dict)
    monkeypatch.setattr(subprocess, "run", record)

    code = run_matrix.main(["--engine", "echo-python", "--pairs", "1", "--only", "main"])

    venv = Path(calls[0][-1])
    assert code == 1
    assert calls == [locust_install_argv(venv)[0]]
    assert venv.name == "venv" and venv.parent.name.startswith("load-tools-locust-")
    assert not venv.parent.exists()


def test_load_tools_dry_run_prints_the_install_and_installs_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--dry-run` prints the hashed install and runs no command."""
    monkeypatch.setattr(subprocess, "run", None)

    code = run_matrix.main(["--dry-run", "--engine", "echo-python", "--pairs", "1"])

    plan = capsys.readouterr().out
    assert code == 0
    assert "--require-hashes --no-deps -r" in plan and str(LOCUST_LOCK) in plan
    assert "--with" not in plan


CALLERS = [
    ROOT / ".github/workflows/poc06-load.yml",
    ROOT / "Makefile",
    *sorted((ROOT / "deploy/compose/poc06").glob("*.py")),
]
"""The callers outside the PoC folders (`test_load_tools_callers` keeps those names out)."""


@pytest.mark.parametrize("path", CALLERS, ids=lambda p: p.name)
def test_load_tools_no_caller_installs_locust_by_name(path: Path) -> None:
    """No caller of the matrix fetches Locust by name (`--with`, `locust==`)."""
    text = path.read_text(encoding="utf-8")

    assert not re.search(r"--with\b|locust==", text)
