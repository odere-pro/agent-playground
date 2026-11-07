"""The eval trial launcher's child environment and argv (no process is started here)."""

from __future__ import annotations

from pathlib import Path

import pytest
from eval_kit.evaltrial import (
    DEEPEVAL_VERSION,
    PROMPTFOO_VERSION,
    TELEMETRY_OFF,
    install_env,
    tool_argv,
    tool_env,
    write_npmrc,
)


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
def test_eval_kit_evaltrial_env_turns_telemetry_off_and_holds_no_secret(tmp_path: Path) -> None:
    env = _env(tmp_path, offline=False)
    assert TELEMETRY_OFF.items() <= env.items()
    assert env["DEEPEVAL_TELEMETRY_OPT_OUT"] == "1"
    assert env["PROMPTFOO_DISABLE_TELEMETRY"] == "1"
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:3128"
    assert not any(n.startswith(("ANTHROPIC_", "CLAUDE_")) or n.endswith("_TOKEN") for n in env)
    assert env["HOME"] == str(tmp_path)


@pytest.mark.usefixtures("parent_env")
def test_eval_kit_evaltrial_offline_drops_the_network_variables(tmp_path: Path) -> None:
    env = _env(tmp_path, offline=True)
    assert "HTTPS_PROXY" not in env
    assert env["UV_OFFLINE"] == "1" and env["npm_config_offline"] == "true"


def test_eval_kit_evaltrial_pins_both_tools() -> None:
    """The tool versions stay those of the trial; the locks hold them (test_eval_kit_tool_locks)."""
    assert (PROMPTFOO_VERSION, DEEPEVAL_VERSION) == ("0.125.1", "4.3.2")


def test_eval_kit_evaltrial_runs_each_tool_from_its_install_in_the_work_dir(tmp_path: Path) -> None:
    """Each tool runs from what `tool_install_argv` installed under the run's work dir."""
    assert tool_argv("deepeval", tmp_path)[0] == str(tmp_path / "deepeval-venv/bin/deepeval")
    assert tool_argv("promptfoo", tmp_path)[0] == str(
        tmp_path / "promptfoo-tool/node_modules/.bin/promptfoo"
    )


NPM_NETWORK = (
    "HTTPS_PROXY", "https_proxy", "npm_config_https_proxy",
    "HTTP_PROXY", "http_proxy", "npm_config_proxy",
    "NO_PROXY", "no_proxy", "npm_config_noproxy",
)  # fmt: skip


@pytest.fixture
def no_npm_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """None of the proxy variables `write_npmrc` reads is set."""
    for name in NPM_NETWORK:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def user_npmrc(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A user `~/.npmrc` with a registry token, named by `NPM_CONFIG_USERCONFIG`."""
    npmrc = tmp_path / "user.npmrc"
    npmrc.write_text("//registry.npmjs.org/:_authToken=placeholder\n", encoding="utf-8")
    monkeypatch.setenv("NPM_CONFIG_USERCONFIG", str(npmrc))
    return npmrc


@pytest.mark.usefixtures("parent_env")
def test_eval_kit_evaltrial_install_reads_its_own_npmrc_never_the_users(
    tmp_path: Path, user_npmrc: Path
) -> None:
    """The install step points npm at the run's `.npmrc`; the tool run never names the user's."""
    home = tmp_path / "home"

    install = install_env(home, offline=False)
    run = _env(home, offline=False)

    assert install["NPM_CONFIG_USERCONFIG"] == str(home / ".npmrc")
    assert str(user_npmrc) not in install.values()
    assert str(user_npmrc) not in run.values()


@pytest.mark.usefixtures("no_npm_network")
def test_eval_kit_evaltrial_npmrc_holds_the_registry_and_the_proxy_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, user_npmrc: Path
) -> None:
    """The run's `.npmrc` names the registry and the proxies from the environment, nothing else."""
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:3128")
    monkeypatch.setenv("NO_PROXY", "localhost")
    monkeypatch.setenv("npm_config_registry", "https://npm.example.test/")

    path = write_npmrc(tmp_path, offline=False)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert path == tmp_path / ".npmrc"
    assert lines == [
        "registry=https://npm.example.test/",
        "https-proxy=http://127.0.0.1:3128",
        "noproxy=localhost",
    ]


@pytest.mark.usefixtures("no_npm_network")
def test_eval_kit_evaltrial_offline_npmrc_names_the_default_registry_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With `--offline` the run's `.npmrc` holds no proxy."""
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:3128")
    monkeypatch.delenv("npm_config_registry", raising=False)

    path = write_npmrc(tmp_path, offline=True)

    assert path.read_text(encoding="utf-8") == "registry=https://registry.npmjs.org/\n"


@pytest.mark.usefixtures("no_npm_network")
def test_eval_kit_evaltrial_npmrc_refuses_a_value_that_adds_a_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A proxy value with a line break would add an npm setting; it fails, naming the key only."""
    monkeypatch.setenv("HTTPS_PROXY", "http://p\n//registry.npmjs.org/:_authToken=x")

    with pytest.raises(ValueError, match=r"^refusing a line break in the npm https-proxy setting$"):
        write_npmrc(tmp_path, offline=False)

    assert not (tmp_path / ".npmrc").exists()


@pytest.mark.usefixtures("no_npm_network")
def test_eval_kit_evaltrial_npmrc_refuses_a_registry_with_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A registry URL with a user or password in it is refused, naming the key only."""
    registry = "https://me:placeholder@npm.example.test/"  # pragma: allowlist secret
    monkeypatch.setenv("npm_config_registry", registry)

    with pytest.raises(ValueError, match=r"^refusing credentials in the npm registry setting$"):
        write_npmrc(tmp_path, offline=False)

    assert not (tmp_path / ".npmrc").exists()
