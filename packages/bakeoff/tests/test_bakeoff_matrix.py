"""The registry, the lane rules, config generation, and the env builder. Offline."""

from __future__ import annotations

from pathlib import Path

import pytest
from bakeoff.config import (
    CHASSIS_TOKEN_ENV,
    WORKLOAD_TOKEN_ENV,
    chassis_config,
    chassis_env_vars,
    workload_argv,
    workload_env_vars,
)
from bakeoff.envs import build_env, is_secret_name
from bakeoff.registry import ENGINES, check_lane, get_engine, lanes_for
from bakeoff.runner import plan
from chassis.server.config import load_config

UNTRUSTED = {"echo-smolagents", "echo-claude-agent", "kagent-adk"}


def test_the_registry_names_every_engine_once() -> None:
    names = [e.name for e in ENGINES]
    assert len(set(names)) == len(names)
    assert set(names) == {
        "echo-python",
        "echo-pydanticai",
        "echo-langgraph",
        "echo-openai-agents",
        "echo-typescript",
        "echo-smolagents",
        "echo-claude-agent",
        "kagent-adk",
    }
    assert {e.name for e in ENGINES if not e.trusted} == UNTRUSTED


def test_untrusted_engines_run_only_in_the_remote_lane() -> None:
    for engine in ENGINES:
        if engine.name in UNTRUSTED:
            assert lanes_for(engine) == ("remote",)
            for lane in ("inprocess", "sidecar"):
                with pytest.raises(ValueError, match="untrusted"):
                    check_lane(engine, lane)
        check_lane(engine, "remote")
    assert all(item.lane == "remote" for item in plan(ENGINES) if not item.engine.trusted)


def test_trusted_python_engines_have_all_three_lanes_and_typescript_has_two() -> None:
    assert lanes_for(get_engine("echo-python")) == ("inprocess", "sidecar", "remote")
    assert lanes_for(get_engine("echo-typescript")) == ("sidecar", "remote")
    with pytest.raises(ValueError, match="does not support"):
        check_lane(get_engine("echo-typescript"), "inprocess")
    with pytest.raises(ValueError, match="unknown lane"):
        check_lane(get_engine("echo-python"), "sidecar2")
    with pytest.raises(KeyError, match="unknown engine"):
        get_engine("nope")


def test_kagent_is_always_a_skip() -> None:
    assert get_engine("kagent-adk").skip_reason() == "runs only on kind (poc06-kind.yml)"
    rows = plan([get_engine("kagent-adk")])
    assert [(r.lane, r.skip) for r in rows] == [("remote", "runs only on kind (poc06-kind.yml)")]


def test_the_plan_filters_by_lane() -> None:
    rows = plan([get_engine("echo-python"), get_engine("echo-smolagents")], ["sidecar"])
    assert [(r.engine.name, r.lane) for r in rows] == [("echo-python", "sidecar")]


def test_every_generated_config_loads_in_the_chassis() -> None:
    for engine in ENGINES:
        for lane in lanes_for(engine):
            if engine.runner == "kind":
                continue
            config = chassis_config(
                engine, lane, workload_port=None if lane == "inprocess" else 9123
            )
            loaded = load_config(config)
            assert loaded.spec.engine.connector == lane
            assert loaded.spec.trust == ("trusted" if engine.trusted else "untrusted")
            assert loaded.spec.adapters is not None
            assert loaded.spec.adapters.model == "litellm"
            assert loaded.spec.adapters.tools == "fake"


def test_the_config_names_the_lane_the_handle_and_the_token_variable() -> None:
    engine = get_engine("echo-python")
    inproc = chassis_config(engine, "inprocess", workload_port=None)["spec"]["engine"]
    assert inproc == {"connector": "inprocess", "handle": "echo_python:handle"}
    side = chassis_config(engine, "sidecar", workload_port=9123)["spec"]["engine"]
    assert side == {"connector": "sidecar", "url": "http://127.0.0.1:9123"}
    remote = chassis_config(engine, "remote", workload_port=9123)["spec"]["engine"]
    assert remote["auth"] == {"scheme": "bearer", "token_env": CHASSIS_TOKEN_ENV}
    assert "token" not in {k for k in remote if k != "auth"}
    with pytest.raises(ValueError, match="needs the workload's port"):
        chassis_config(engine, "sidecar", workload_port=None)


def test_the_config_refuses_an_untrusted_engine_outside_remote() -> None:
    with pytest.raises(ValueError, match="untrusted"):
        chassis_config(get_engine("echo-smolagents"), "sidecar", workload_port=1)
    config = chassis_config(get_engine("echo-smolagents"), "remote", workload_port=1)
    assert config["spec"]["trust"] == "untrusted"


def test_workload_commands_carry_the_token_flag_only_in_the_remote_lane() -> None:
    py = get_engine("echo-python")
    assert "--require-token-env" not in workload_argv(py, "sidecar", port=1)
    remote = workload_argv(py, "remote", port=1)
    assert remote[remote.index("--require-token-env") + 1] == WORKLOAD_TOKEN_ENV
    assert "echo_python:handle" in remote
    node = workload_argv(get_engine("echo-typescript"), "remote", port=1, main_js="/x/main.js")
    assert node[:2] == ["node", "/x/main.js"]
    assert node[-2:] == ["--require-token-env", WORKLOAD_TOKEN_ENV]


def test_the_workload_env_is_an_allow_list() -> None:
    engine = get_engine("echo-smolagents")
    side = workload_env_vars(
        engine, "sidecar", port=1, model_url="http://m/v1", tool_url="http://m/mcp", token="t"
    )
    assert set(side) == {"CHASSIS_MODEL_URL", "CHASSIS_TOOL_URL"}
    remote = workload_env_vars(
        engine, "remote", port=1, model_url="http://m/v1", tool_url="http://m/mcp", token="t"
    )
    assert set(remote) == {
        "CHASSIS_MODEL_URL",
        "CHASSIS_TOOL_URL",
        "CHASSIS_API_TOKEN",
        WORKLOAD_TOKEN_ENV,
    }
    node = workload_env_vars(
        get_engine("echo-typescript"),
        "sidecar",
        port=7,
        model_url="u",
        tool_url="u",
        token="t",
    )
    assert node["PORT"] == "7"
    assert node["HOST"] == "127.0.0.1"


def test_the_chassis_env_holds_a_dummy_model_key_and_no_token_outside_remote() -> None:
    side = chassis_env_vars("sidecar", fake_port=5, proxy_port=6, token="t")
    assert side == {
        "LITELLM_BASE_URL": "http://127.0.0.1:5/v1",
        "LITELLM_API_KEY": "bakeoff-dummy-key",  # pragma: allowlist secret
    }
    inproc = chassis_env_vars("inprocess", fake_port=5, proxy_port=6, token="t")
    assert inproc["CHASSIS_MODEL_URL"] == "http://127.0.0.1:6/v1"
    assert (
        chassis_env_vars("remote", fake_port=5, proxy_port=6, token="t")[CHASSIS_TOKEN_ENV] == "t"
    )


def test_the_env_builder_never_copies_the_parent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "parent-secret")  # pragma: allowlist secret
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://parent")
    monkeypatch.setenv("CLAUDE_CODE_SESSION", "parent-session")
    monkeypatch.setenv("GITHUB_TOKEN", "parent-gh")
    monkeypatch.setenv("SOME_OTHER", "x")
    monkeypatch.setenv("PATH", "/a:/b")
    env = build_env(tmp_path, {"CHASSIS_MODEL_URL": "http://m/v1"})
    assert env == {"PATH": "/a:/b", "HOME": str(tmp_path), "CHASSIS_MODEL_URL": "http://m/v1"}
    for value in env.values():
        assert "parent" not in value


def test_the_env_builder_refuses_to_pass_anthropic_or_claude_names(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY") as caught:
        build_env(
            tmp_path, {"ANTHROPIC_API_KEY": "value-that-must-not-print"}
        )  # pragma: allowlist secret
    assert "value-that-must-not-print" not in str(caught.value)
    with pytest.raises(ValueError, match="CLAUDE_X"):
        build_env(tmp_path, {"CLAUDE_X": "1"})


def test_every_stack_env_is_free_of_the_parent_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The env of every process `running_stack` starts is built from these pieces."""
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "parent")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/parent")
    for engine in ENGINES:
        for lane in lanes_for(engine):
            pieces = [
                chassis_env_vars(lane, fake_port=1, proxy_port=2, token="t"),
                workload_env_vars(engine, lane, port=3, model_url="u", tool_url="u", token="t"),
            ]
            for extra in pieces:
                env = build_env(tmp_path, extra)
                assert not [k for k in env if k.startswith(("ANTHROPIC_", "CLAUDE_"))]
                assert set(env) <= {"PATH", "HOME", *extra}


def test_secret_names() -> None:
    assert is_secret_name("ANTHROPIC_BASE_URL")
    assert is_secret_name("CLAUDE_CONFIG_DIR")
    assert is_secret_name("CHASSIS_API_TOKEN")
    assert is_secret_name("LITELLM_API_KEY")
    assert not is_secret_name("CHASSIS_MODEL_URL")
