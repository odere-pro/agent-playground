"""The rendered chassis configs load with the chassis config model, and agentctl's trust rule is
the chassis's. A monorepo test may import the chassis; agentctl's own code may not (lint-imports).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from agentctl import rules
from agentctl.scaffold import Answers, render
from chassis.agent_class import KIND_CLASS
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


@pytest.mark.parametrize("connector", rules.CONNECTORS)
def test_trust_untrusted_matches_the_chassis_on_a_trusted_framework(connector: str) -> None:
    """`--trust untrusted` on langgraph: agentctl refuses `sidecar` exactly when the loader does."""
    data = {
        "profile": "local",
        "agent": {"name": "x", "version": "0.1.0"},
        "spec": {
            "trust": "untrusted",
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
        rules.check_lane("langgraph", connector, "untrusted")
        agentctl_ok = True
    except rules.UsageError:
        agentctl_ok = False
    assert agentctl_ok == chassis_ok == (connector == "remote")


def test_a_trust_untrusted_render_loads_in_the_chassis(tmp_path: Path) -> None:
    answers = Answers(
        agent_name="my-agent",
        kind="tool",
        framework="langgraph",
        connector="remote",
        platform_path=ROOT,
        trust="untrusted",
    )
    agent = render(answers, tmp_path)
    for name in ("agent.yaml", "fake.yaml"):
        config = load_config(agent / "config" / name)
        assert (config.spec.trust, config.spec.engine.connector) == ("untrusted", "remote")


# One trusted lane and one untrusted: the governance block does not depend on the framework.
@pytest.mark.parametrize("framework", ["langgraph", "smolagents"])
def test_the_starter_config_names_each_governance_field_with_a_placeholder(
    tmp_path: Path, framework: str
) -> None:
    """059 H-15: `spec.governance` holds the chassis schema's fields, each marked to fill in."""
    connector = "remote" if rules.trust_of(framework) == "untrusted" else "sidecar"
    agent = _agent(tmp_path, framework, connector)
    config = load_config(agent / "config/agent.yaml")
    assert "governance" in config.spec.model_fields_set
    schema_fields = set(type(config.spec.governance).model_fields)
    assert schema_fields == {"risk_class", "ai_generated_marking"}
    assert config.spec.governance.model_fields_set == schema_fields
    lines = (agent / "config/agent.yaml").read_text().splitlines()
    for field in sorted(schema_fields):
        line = next(line for line in lines if line.strip().startswith(f"{field}:"))
        assert "fill in" in line, f"{field} has no placeholder note: {line!r}"


# --- 008 H-14: class and kind in a rendered config ---------------------------------------------


def _rendered_data(tmp_path: Path, kind: str) -> dict[str, Any]:
    answers = Answers(
        agent_name="my-agent",
        kind=kind,
        framework="langgraph",
        connector="sidecar",
        platform_path=ROOT,
    )
    text = (render(answers, tmp_path) / "config/agent.yaml").read_text()
    data: dict[str, Any] = yaml.safe_load(text)
    return data


@pytest.mark.parametrize("kind", rules.KINDS)
def test_the_rendered_config_names_its_class_and_kind(tmp_path: Path, kind: str) -> None:
    data = _rendered_data(tmp_path, kind)
    config = load_config(data)
    assert config.spec.kind == kind
    assert config.spec.agent_class == KIND_CLASS[kind] == "stateless"
    assert {"class", "kind"} <= set(data["spec"]), "both are written, not defaulted"


def test_a_rendered_config_refuses_a_kind_that_does_not_match_its_class(tmp_path: Path) -> None:
    data = _rendered_data(tmp_path, "tool")
    data["spec"]["class"] = "data"
    with pytest.raises(ValueError, match=r"spec\.kind: tool needs spec\.class: stateless"):
        load_config(data)


@pytest.mark.parametrize(
    ("block", "flag"), [("harness", "evaluator_gate"), ("events", "result_events")]
)
def test_a_rendered_data_config_refuses_a_module_b5_marks_no(
    tmp_path: Path, block: str, flag: str
) -> None:
    data = _rendered_data(tmp_path, "tool")
    data["spec"]["class"] = "data"
    data["spec"]["kind"] = "data"
    load_config(data)  # the data class with every flag off starts
    data["spec"].setdefault(block, {})[flag] = True
    with pytest.raises(ValueError, match=rf"module {flag} is not allowed for spec\.class: data"):
        load_config(data)
