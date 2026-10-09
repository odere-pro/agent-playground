"""Hosted mode: `--model-url`, `--model-key-env`, `--route`. Offline; no process starts."""

from __future__ import annotations

from pathlib import Path

import pytest
from bakeoff import cli
from bakeoff.config import Hosted, chassis_config, chassis_env_vars
from bakeoff.procs import Cleanup
from bakeoff.registry import ENGINES, check_hosted, get_engine
from bakeoff.stack import running_stack
from chassis.server.config import load_config

URL = "http://127.0.0.1:14000/v1"
KEY_ENV = "POC06_LITELLM_KEY"
KEY = "litellm-test-value-1"  # pragma: allowlist secret
UNTRUSTED = ["echo-smolagents", "echo-claude-agent", "kagent-adk"]


def test_check_hosted_refuses_every_untrusted_engine_and_passes_the_trusted_ones() -> None:
    for engine in ENGINES:
        if engine.trusted:
            check_hosted(engine)
        else:
            with pytest.raises(ValueError, match=r"untrusted.*model URL"):
                check_hosted(engine)


@pytest.mark.parametrize("name", UNTRUSTED)
def test_the_stack_refuses_an_untrusted_engine_before_it_starts_anything(name: str) -> None:
    cleanup = Cleanup()
    with (
        pytest.raises(ValueError, match="untrusted"),
        running_stack(get_engine(name), "remote", cleanup, Hosted(URL, KEY, "big-default")),
    ):
        raise AssertionError("the stack started")
    assert cleanup.procs == []
    assert cleanup.dirs == []


@pytest.mark.parametrize("name", UNTRUSTED)
def test_the_cli_refuses_an_untrusted_engine_with_a_model_url(
    name: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setenv(KEY_ENV, KEY)
    argv = ["run", "--engine", name, "--model-url", URL, "--model-key-env", KEY_ENV]
    assert cli.main([*argv, "--out", str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert "untrusted" in err
    assert KEY not in err
    assert not (tmp_path / "results.json").exists()


def test_the_cli_needs_the_key_variable_and_never_prints_its_value(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(KEY_ENV, raising=False)
    assert cli.main(["run", "--model-url", URL, "--model-key-env", KEY_ENV]) == 2
    assert KEY_ENV in capsys.readouterr().err
    assert cli.main(["run", "--model-url", URL]) == 2
    assert "--model-key-env" in capsys.readouterr().err


def test_route_and_key_flags_need_a_model_url(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["run", "--route", "local-small"]) == 2
    assert "--model-url" in capsys.readouterr().err
    assert cli.main(["run", "--model-url", "ftp://x", "--model-key-env", KEY_ENV]) == 2


def test_the_cli_refuses_a_model_url_with_target(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(KEY_ENV, KEY)
    argv = [
        "run",
        "--model-url",
        URL,
        "--model-key-env",
        KEY_ENV,
        "--target",
        "echo-python=http://x",
    ]
    assert cli.main(argv) == 2
    assert "--target" in capsys.readouterr().err


def test_route_is_one_of_the_two_routes(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main(["run", "--route", "gpt-9"])
    assert "invalid choice" in capsys.readouterr().err


def test_hosted_mode_defaults_to_the_trusted_engines_and_the_non_remote_lanes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(KEY_ENV, KEY)
    args = cli._parser().parse_args(["run", "--model-url", URL, "--model-key-env", KEY_ENV])
    hosted = cli._hosted(args)
    assert hosted == Hosted(URL, KEY, "big-default")
    engines = cli._engines(args.engine, hosted)
    assert engines
    assert all(e.trusted for e in engines)
    assert {e.name for e in engines} == {e.name for e in ENGINES if e.trusted}
    assert cli._lanes(args.lane, hosted) == ["inprocess", "sidecar"]
    assert cli._lanes(["remote"], hosted) == ["remote"]
    assert cli._lanes(None, None) is None


def test_the_hosted_key_is_not_in_the_repr() -> None:
    assert KEY not in repr(Hosted(URL, KEY, "big-default"))


def test_the_chassis_env_points_at_the_url_with_the_litellm_key_and_nothing_else() -> None:
    hosted = Hosted(URL, KEY, "local-small")
    env = chassis_env_vars("sidecar", fake_port=0, proxy_port=6, token="t", hosted=hosted)
    assert env == {"LITELLM_BASE_URL": URL, "LITELLM_API_KEY": KEY}
    inproc = chassis_env_vars("inprocess", fake_port=0, proxy_port=6, token="t", hosted=hosted)
    assert inproc["LITELLM_BASE_URL"] == URL
    assert inproc["CHASSIS_MODEL_URL"] == "http://127.0.0.1:6/v1"


def test_the_route_is_the_only_change_in_the_chassis_config() -> None:
    engine = get_engine("echo-python")
    big = chassis_config(engine, "sidecar", workload_port=9123)
    small = chassis_config(engine, "sidecar", workload_port=9123, route="local-small")
    assert big["spec"]["model"] == {"route": "big-default"}
    assert small["spec"]["model"] == {"route": "local-small"}
    big["spec"]["model"] = small["spec"]["model"]
    assert big == small
    assert load_config(small).spec.model.route == "local-small"


def _argv(*extra: str) -> list[str]:
    return ["run", "--model-url", URL, "--model-key-env", KEY_ENV, *extra]


@pytest.mark.parametrize(
    "url", ["http://litellm.example.com/v1", "https://203.0.113.5:4000/v1", "http://10.0.0.2/v1"]
)
def test_the_cli_refuses_a_model_url_that_is_not_this_host(
    url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(KEY_ENV, KEY)
    assert cli.main(["run", "--model-url", url, "--model-key-env", KEY_ENV]) == 2
    err = capsys.readouterr().err
    assert "this host" in err
    assert KEY not in err


def test_the_cli_refuses_the_remote_lane_in_hosted_mode(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(KEY_ENV, KEY)
    assert cli.main(_argv("--lane", "sidecar,remote")) == 2
    assert "remote lane" in capsys.readouterr().err


@pytest.mark.parametrize("name", ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "MY_TOKEN", "OTHER"])
def test_the_cli_reads_only_the_run_key_variable(
    name: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(name, "provider-value-123")
    assert cli.main(["run", "--model-url", URL, "--model-key-env", name]) == 2
    err = capsys.readouterr().err
    assert KEY_ENV in err
    assert "provider-value-123" not in err
