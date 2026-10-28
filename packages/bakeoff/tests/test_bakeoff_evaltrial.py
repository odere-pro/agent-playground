"""The eval trial launcher's child environment and argv (no process is started here)."""

from __future__ import annotations

from pathlib import Path

import pytest
from bakeoff.evaltrial import TELEMETRY_OFF, tool_argv, tool_env


@pytest.fixture
def parent_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:3128")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("GH_TOKEN", "x")


def _env(tmp_path: Path, offline: bool) -> dict[str, str]:
    return tool_env(
        tmp_path,
        tmp_path,
        engine="echo-python",
        chassis_url="http://127.0.0.1:1",
        judge_url="http://127.0.0.1:2/v1",
        offline=offline,
    )


@pytest.mark.usefixtures("parent_env")
def test_bakeoff_evaltrial_env_turns_telemetry_off_and_holds_no_secret(tmp_path: Path) -> None:
    env = _env(tmp_path, offline=False)
    assert TELEMETRY_OFF.items() <= env.items()
    assert env["DEEPEVAL_TELEMETRY_OPT_OUT"] == "1"
    assert env["PROMPTFOO_DISABLE_TELEMETRY"] == "1"
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:3128"
    assert not any(n.startswith(("ANTHROPIC_", "CLAUDE_")) or n.endswith("_TOKEN") for n in env)
    assert env["HOME"] == str(tmp_path)


@pytest.mark.usefixtures("parent_env")
def test_bakeoff_evaltrial_offline_drops_the_network_variables(tmp_path: Path) -> None:
    env = _env(tmp_path, offline=True)
    assert "HTTPS_PROXY" not in env
    assert env["UV_OFFLINE"] == "1" and env["npm_config_offline"] == "true"


def test_bakeoff_evaltrial_pins_both_tools(tmp_path: Path) -> None:
    assert "promptfoo@0.125.1" in tool_argv("promptfoo", tmp_path)
    deepeval = tool_argv("deepeval", tmp_path)
    assert "deepeval==4.3.2" in deepeval and "--no-project" in deepeval
