"""The plugin as an agent's repo uses it: `pytest_plugins` in the top conftest, the agent named
in `[tool.agent-testkit]`, and the session fixtures `workload` and `chassis`. Run in a child
pytest (`runpytest_subprocess`) with the same offline flags as the gate."""

from __future__ import annotations

import os
import pwd
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

REPO = Path(__file__).resolve().parents[3]

AGENT_TEST = """
def test_session_fixtures(agent_handle_ref, workload, chassis):
    assert agent_handle_ref.handle == "echo_python:handle"
    assert workload.agent_card()["name"] == "echo-python"
    envelope = chassis.run({"text": "hello big world"})
    assert envelope["status"] == "ok", envelope
    assert envelope["agent"] == "echo-python"
"""


@pytest.mark.slow
def test_an_agent_repo_gets_the_session_fixtures(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    # pytester points HOME at a temp folder; keep uv on the real cache so --offline finds wheels.
    real_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    monkeypatch.setenv(
        "UV_CACHE_DIR", os.environ.get("UV_CACHE_DIR") or str(real_home / ".cache/uv")
    )
    pytester.makepyprojecttoml(
        f'[project]\nname = "my-agent"\n\n[tool.agent-testkit]\n'
        f'handle = "echo_python:handle"\nname = "echo-python"\nplatform_path = "{REPO}"\n'
    )
    pytester.makeconftest('pytest_plugins = ["agent_testkit.plugin"]\n')
    pytester.makepyfile(test_my_agent=AGENT_TEST)
    result = pytester.runpytest_subprocess(
        "-p", "no:cacheprovider", "--disable-socket", "--allow-unix-socket", timeout=300
    )
    result.assert_outcomes(passed=1)
