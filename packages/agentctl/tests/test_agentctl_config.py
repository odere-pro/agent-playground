"""The rendered chassis configs load with the chassis config model, and agentctl's trust rule is
the chassis's. A monorepo test may import the chassis; agentctl's own code may not (lint-imports).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from agentctl import rules
from agentctl.scaffold import Answers, render
from chassis.server.config import load_config

ROOT = Path(__file__).resolve().parents[3]


def _agent(tmp_path: Path, framework: str, connector: str) -> Path:
    answers = Answers(
        agent_name="my-agent",
        kind="tool",
        framework=framework,
        connector=connector,
        platform_path=ROOT,
    )
    return render(answers, tmp_path)


@pytest.mark.parametrize("framework", rules.FRAMEWORKS)
@pytest.mark.parametrize("connector", rules.CONNECTORS)
def test_agent_and_fake_configs_load(tmp_path: Path, framework: str, connector: str) -> None:
    try:
        rules.check_lane(framework, connector)
    except rules.UsageError:
        pytest.skip("refused by the trust rule; covered below")
    agent = _agent(tmp_path, framework, connector)
    config = load_config(agent / "config/agent.yaml")
    assert config.agent.name == "my-agent"
    assert config.spec.engine.connector == connector
    assert config.spec.trust == rules.trust_of(framework)
    assert "trust" in config.spec.model_fields_set, "spec.trust is named, not defaulted"
    fake = load_config(agent / "config/fake.yaml")
    assert fake.profile == "fake"
    assert fake.agent.name == "my-agent"
    assert fake.spec.trust == rules.trust_of(framework)
    assert fake.spec.engine.connector in ("sidecar", "remote")
    assert fake.spec.engine.url and fake.spec.engine.url.startswith("http://127.0.0.1:")


@pytest.mark.parametrize("framework", rules.FRAMEWORKS)
@pytest.mark.parametrize("connector", rules.CONNECTORS)
def test_agentctl_refuses_what_the_chassis_refuses(framework: str, connector: str) -> None:
    """The same answer from both: agentctl refuses a lane exactly when the config loader would."""
    data = {
        "profile": "local",
        "agent": {"name": "x", "version": "0.1.0"},
        "spec": {
            "trust": rules.trust_of(framework),
            "engine": {"connector": connector, "url": "http://127.0.0.1:9000"}
            | ({"auth": {"token_env": "REMOTE_TOKEN"}} if connector == "remote" else {}),
        },
    }
    try:
        load_config(data)
        chassis_ok = True
    except ValueError:
        chassis_ok = False
    try:
        rules.check_lane(framework, connector)
        agentctl_ok = True
    except rules.UsageError:
        agentctl_ok = False
    assert agentctl_ok == chassis_ok
