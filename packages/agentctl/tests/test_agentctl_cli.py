"""`agentctl new`: argument validation, the trust rule, and the first commit."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml
from agentctl import rules
from agentctl.cli import main
from agentctl.scaffold import TEMPLATE


def _new(tmp_path: Path, platform: Path, *args: str) -> int:
    # `--no-lock`: no `uv lock` here; the PoC-9 scaffold scenario test locks offline.
    return main(
        ["new", *args, "--dest", str(tmp_path), "--platform-path", str(platform), "--no-lock"]
    )


@pytest.mark.parametrize(
    "name",
    ["Invoice", "invoice_reader", "1st-agent", "agent-", "a" * 64, "", "in voice"],
)
def test_a_name_that_is_not_a_dns_label_is_refused(
    tmp_path: Path, platform: Path, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    assert _new(tmp_path, platform, name, "--kind", "transformer") == 2
    err = capsys.readouterr().err
    assert err.startswith("agentctl: name")
    assert "DNS label" in err or "limit" in err
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("name", ["agents", "mcp", "openai", "chassis", "tests", "class"])
def test_a_name_that_shadows_a_module_is_refused(
    tmp_path: Path, platform: Path, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    assert _new(tmp_path, platform, name, "--kind", "tool") == 2
    assert "reserved" in capsys.readouterr().err


def test_kind_is_required_and_checked(tmp_path: Path, platform: Path) -> None:
    with pytest.raises(SystemExit) as missing:
        _new(tmp_path, platform, "reader")
    assert missing.value.code == 2
    with pytest.raises(SystemExit) as bad:
        _new(tmp_path, platform, "reader", "--kind", "planner")
    assert bad.value.code == 2


def test_an_unknown_framework_is_refused(tmp_path: Path, platform: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        _new(tmp_path, platform, "reader", "--kind", "tool", "--framework", "crewai")
    assert exc.value.code == 2


@pytest.mark.parametrize("framework", sorted(rules.UNTRUSTED))
def test_an_untrusted_framework_is_refused_in_the_sidecar_lane(
    tmp_path: Path, platform: Path, capsys: pytest.CaptureFixture[str], framework: str
) -> None:
    code = _new(tmp_path, platform, "coder", "--kind", "tool", "--framework", framework)
    assert code == 2
    err = capsys.readouterr().err
    assert "untrusted" in err and "--connector remote" in err
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("framework", ["langgraph", *sorted(rules.UNTRUSTED)])
def test_trust_untrusted_with_connector_sidecar_is_refused_and_writes_nothing(
    tmp_path: Path, platform: Path, capsys: pytest.CaptureFixture[str], framework: str
) -> None:
    """059 H-15 box 3 (P-15, decided 2026-11-13): a clear error, exit 2, no output folder."""
    args = ("--framework", framework, "--trust", "untrusted", "--connector", "sidecar")
    assert _new(tmp_path, platform, "coder", "--kind", "tool", *args) == 2
    err = capsys.readouterr().err
    assert err.startswith("agentctl: ")
    assert "untrusted" in err and "--connector remote" in err and "'sidecar'" in err
    assert list(tmp_path.iterdir()) == [], "nothing is written"


def test_trust_untrusted_alone_picks_the_remote_lane(tmp_path: Path, platform: Path) -> None:
    """059 H-15 box 2: `--trust untrusted` with no `--connector` renders the remote lane."""
    assert _new(tmp_path, platform, "coder", "--kind", "tool", "--trust", "untrusted") == 0
    agent = tmp_path / "coder"
    answers = yaml.safe_load((agent / ".copier-answers.yml").read_text())
    assert (answers["framework"], answers["trust"], answers["connector"]) == (
        "langgraph",
        "untrusted",
        "remote",
    )
    config = yaml.safe_load((agent / "config/agent.yaml").read_text())
    assert config["spec"]["trust"] == "untrusted"
    assert config["spec"]["engine"]["connector"] == "remote"
    assert not (agent / "deploy/compose").exists(), "Compose is the sidecar shape"


@pytest.mark.parametrize("framework", sorted(rules.UNTRUSTED))
def test_trust_trusted_on_an_untrusted_framework_is_refused(
    tmp_path: Path, platform: Path, capsys: pytest.CaptureFixture[str], framework: str
) -> None:
    """The flag can only lower trust: an engine that runs model-written code stays untrusted."""
    args = ("--framework", framework, "--trust", "trusted", "--connector", "remote")
    assert _new(tmp_path, platform, "coder", "--kind", "tool", *args) == 2
    assert "untrusted" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_without_trust_the_framework_decides(tmp_path: Path, platform: Path) -> None:
    """The default is unchanged: no `--trust` means `rules.trust_of(framework)`, lane sidecar."""
    assert _new(tmp_path, platform, "reader", "--kind", "tool") == 0
    answers = yaml.safe_load((tmp_path / "reader/.copier-answers.yml").read_text())
    assert (answers["trust"], answers["connector"]) == ("trusted", "sidecar")


def test_a_generated_route_the_key_does_not_list_is_refused(
    tmp_path: Path, platform: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """059 H-15 box 7: refuse, do not rewrite. Exit 2, the route named, nothing written."""
    args = ("--key-route", "small-default", "--key-route", "local-small")
    assert _new(tmp_path, platform, "reader", "--kind", "tool", *args) == 2
    err = capsys.readouterr().err
    assert err.startswith("agentctl: ")
    assert "'big-default'" in err and "small-default" in err and "--key-route" in err
    assert list(tmp_path.iterdir()) == [], "nothing is written"


def test_a_key_that_lists_the_generated_route_passes(tmp_path: Path, platform: Path) -> None:
    args = ("--key-route", "local-small", "--key-route", "big-default")
    assert _new(tmp_path, platform, "reader", "--kind", "tool", *args) == 0
    assert (tmp_path / "reader/config/agent.yaml").is_file()


def test_the_key_routes_default_to_the_generated_routes() -> None:
    """agentctl has no offline source for the key; the default is what the template names."""
    assert rules.DEFAULT_KEY_ROUTES == rules.GENERATED_ROUTES
    rules.check_routes(rules.GENERATED_ROUTES, rules.DEFAULT_KEY_ROUTES)


def test_the_trust_table_matches_adr_006() -> None:
    assert rules.DEFAULT_FRAMEWORK == "langgraph"  # amended 2026-11-06
    assert rules.DEFAULT_CONNECTOR == "sidecar"
    trusted = {f for f in rules.FRAMEWORKS if rules.trust_of(f) == "trusted"}
    assert trusted == {"python", "pydanticai", "langgraph", "langchain", "openai-agents"}
    for framework in rules.FRAMEWORKS:
        rules.check_lane(framework, "remote")  # every engine may run remote
    for framework in trusted:
        rules.check_lane(framework, "sidecar")


def test_the_template_default_framework_matches_the_rules() -> None:
    """`copier copy` without agentctl gets the same default (ADR-006 decision 1)."""
    framework = yaml.safe_load((TEMPLATE / "copier.yml").read_text())["framework"]
    assert framework["default"] == rules.DEFAULT_FRAMEWORK
    assert rules.DEFAULT_FRAMEWORK in framework["help"]
    assert list(framework["choices"]) == list(rules.FRAMEWORKS)


def test_the_defaults_scaffold_a_langgraph_sidecar_agent_with_a_first_commit(
    tmp_path: Path, platform: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """ADR-006 decision 1, amended 2026-11-06: LangGraph is the default engine."""
    assert _new(tmp_path, platform, "invoice-reader", "--kind", "transformer") == 0
    agent = tmp_path / "invoice-reader"
    src = agent / "src/invoice_reader"
    for file in ("handle.py", "mapping.py", "telemetry.py", "tools.py"):
        assert (src / file).is_file(), f"{file}: the LangGraph partial was not rendered"
    assert "StateGraph" in (src / "handle.py").read_text()
    answers = (agent / ".copier-answers.yml").read_text()
    assert "framework: langgraph" in answers and "connector: sidecar" in answers
    log = _git(agent, "log", "--format=%s")
    assert log.splitlines() == ["chore: scaffold invoice-reader with agentctl"]
    assert _git(agent, "status", "--porcelain") == ""
    assert _git(agent, "branch", "--show-current").strip() == "main"
    assert _git(agent, "remote") == ""
    assert "invoice-reader" in capsys.readouterr().out


def test_pydanticai_still_renders_when_asked_for(tmp_path: Path, platform: Path) -> None:
    """PydanticAI stays supported in the sidecar lane (ADR-006 decision 2)."""
    code = _new(tmp_path, platform, "reader", "--kind", "tool", "--framework", "pydanticai")
    assert code == 0
    src = tmp_path / "reader/src/reader"
    assert (src / "handle.py").is_file() and (src / "mapping.py").is_file()
    assert not (src / "tools.py").exists(), "tools.py belongs to the LangGraph partial"
    answers = (tmp_path / "reader/.copier-answers.yml").read_text()
    assert "framework: pydanticai" in answers and "connector: sidecar" in answers


def test_an_existing_folder_that_is_not_empty_is_refused(
    tmp_path: Path, platform: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "reader").mkdir()
    (tmp_path / "reader" / "keep.txt").write_text("mine")
    assert _new(tmp_path, platform, "reader", "--kind", "tool") == 2
    assert "not empty" in capsys.readouterr().err
    assert [p.name for p in (tmp_path / "reader").iterdir()] == ["keep.txt"]


def test_a_platform_path_without_workload_a2a_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    elsewhere = tmp_path / "not-the-platform"
    elsewhere.mkdir()
    assert _new(tmp_path, elsewhere, "reader", "--kind", "tool") == 2
    assert "workload-a2a" in capsys.readouterr().err


def test_the_platform_path_defaults_to_this_checkout(
    tmp_path: Path, platform: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["new", "reader", "--kind", "evaluator", "--dest", str(tmp_path), "--no-lock"]) == 0
    agent = tmp_path / "reader"
    assert 'path = ".platform/packages/workload-a2a"' in (agent / "pyproject.toml").read_text()
    assert f"PLATFORM_PATH ?= {platform}\n" in (agent / "Makefile").read_text()


def test_inside_a_git_hook_the_callers_repo_is_left_alone(
    tmp_path: Path, platform: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hook sets GIT_DIR and GIT_INDEX_FILE; the first commit must still land in the agent."""
    caller = tmp_path / "caller"
    caller.mkdir()
    _git(caller, "init", "-q", "-b", "main")
    monkeypatch.setenv("GIT_DIR", str(caller / ".git"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(caller / ".git" / "index"))
    agents = tmp_path / "agents"
    agents.mkdir()
    assert _new(agents, platform, "reader", "--kind", "tool") == 0
    monkeypatch.delenv("GIT_DIR")
    monkeypatch.delenv("GIT_INDEX_FILE")
    assert (
        _git(agents / "reader", "log", "--format=%s").strip()
        == "chore: scaffold reader with agentctl"
    )
    done = subprocess.run(["git", "-C", str(caller), "rev-parse", "-q", "--verify", "HEAD"])
    assert done.returncode != 0, "the caller's repository got a commit"


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True, capture_output=True, text=True
    ).stdout
