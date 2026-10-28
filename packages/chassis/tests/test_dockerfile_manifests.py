"""Every Dockerfile that runs `uv sync --locked` over the workspace copies the `pyproject.toml` of
every workspace member. uv refuses a locked sync when a member's manifest is missing, so a new
member that is not added here breaks every image build in CI (PoC-7: `fake-langfuse` and
`guardrail-server` did).
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
MEMBERS = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["uv"]["workspace"]["members"]
DOCKERFILES = [
    ROOT / name
    for name in subprocess.run(
        ["git", "ls-files", "*Dockerfile*"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    if "uv sync --locked" in (ROOT / name).read_text()
    and "COPY pyproject.toml uv.lock" in (ROOT / name).read_text()
]


def test_there_are_workspace_dockerfiles() -> None:
    assert len(DOCKERFILES) >= 5


@pytest.mark.parametrize("dockerfile", DOCKERFILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_every_member_manifest_is_copied(dockerfile: Path) -> None:
    copied = set(re.findall(r"^COPY (packages/\S+)/pyproject\.toml ", dockerfile.read_text(), re.M))
    missing = sorted(set(MEMBERS) - copied)
    assert not missing, f"{dockerfile.relative_to(ROOT)} does not copy {missing}"
