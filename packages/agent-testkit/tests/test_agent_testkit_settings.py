"""`[tool.agent-testkit]` and the chassis source: pure, no process."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from agent_testkit import settings
from agent_testkit.settings import (
    CHASSIS_ENV,
    CURRENT_PYTHON,
    AgentRef,
    SettingsError,
    agent_ref,
    chassis_source,
    source_for,
)

REPO = Path(__file__).resolve().parents[3]


def _pyproject(root: Path, body: str) -> Path:
    (root / "pyproject.toml").write_text(f'[project]\nname = "x"\n\n[tool.agent-testkit]\n{body}')
    return root


def test_agent_ref_reads_handle_and_name(tmp_path: Path) -> None:
    root = _pyproject(tmp_path, 'handle = "my_agent:handle"\nname = "my-agent"\n')
    assert agent_ref(root) == AgentRef("my_agent:handle", "my-agent")


def test_the_name_defaults_to_the_module(tmp_path: Path) -> None:
    root = _pyproject(tmp_path, 'handle = "my_agent.core:handle"\n')
    assert agent_ref(root).name == "my-agent"
    assert AgentRef.of("echo_python:handle") == AgentRef("echo_python:handle", "echo-python")


def test_a_missing_handle_says_what_to_set(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match=r"\[tool.agent-testkit\] handle"):
        agent_ref(tmp_path)


def test_a_handle_without_an_attribute_is_refused() -> None:
    with pytest.raises(SettingsError, match="module:attribute"):
        AgentRef.of("my_agent")


def test_current_python_is_the_shortcut(tmp_path: Path) -> None:
    source = source_for(CURRENT_PYTHON, tmp_path)
    assert source.command == (sys.executable,)
    assert not source.isolated


def test_a_platform_checkout_is_an_isolated_env_from_its_lock(tmp_path: Path) -> None:
    source = source_for(str(REPO), tmp_path)
    assert source.isolated
    for flag in ("--isolated", "--frozen", "--offline", "--no-dev"):
        assert flag in source.command
    assert source.command[source.command.index("--package") + 1] == "chassis"


def test_anything_else_is_a_uv_tool_env(tmp_path: Path) -> None:
    source = source_for("chassis==0.1.0", tmp_path)
    assert source.command[1:] == ("tool", "run", "--offline", "--from", "chassis==0.1.0", "python")


def test_the_env_wins_then_the_table_then_the_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _pyproject(tmp_path, f'handle = "a:h"\nplatform_path = "{REPO}"\n')
    monkeypatch.setenv(CHASSIS_ENV, CURRENT_PYTHON)
    assert chassis_source(root).command == (sys.executable,)
    monkeypatch.delenv(CHASSIS_ENV)
    assert str(REPO) in chassis_source(root).command
    assert "--isolated" in chassis_source(tmp_path / "nowhere").command  # found from the kit


def test_no_source_anywhere_says_what_to_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(CHASSIS_ENV, raising=False)
    monkeypatch.setattr(settings, "_find_checkout", lambda start: None)
    with pytest.raises(SettingsError, match=CHASSIS_ENV):
        chassis_source(tmp_path)
