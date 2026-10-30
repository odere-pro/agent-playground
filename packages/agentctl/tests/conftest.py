"""Shared fixtures: the monorepo root as the platform path, and git with no user config."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from agentctl.scaffold import GIT_LOCATION

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def isolated_git(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The first commit must not depend on the developer's git config (identity, signing, hooks)."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for name in (
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
        # Set when the tests run inside a git hook; the helpers' own git calls must not see them.
        *GIT_LOCATION,
    ):
        monkeypatch.delenv(name, raising=False)
    yield


@pytest.fixture
def platform() -> Path:
    return ROOT
